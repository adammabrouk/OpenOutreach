# linkedin/tasks/connect.py
"""Connect task — resolves one candidate from the campaign pool and acts.

Lazy: the task payload carries only ``campaign_id``. The handler picks
its candidate at execution time via the campaign's ``ConnectStrategy``.
No self-rescheduling — pacing is owned by ``tasks/scheduler.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from termcolor import colored

from linkedin.db.deals import increment_connect_attempts, set_profile_state
from linkedin.db.leads import disqualify_lead
from linkedin.models import ActionLog
from linkedin_appium.enums import ProfileState
from linkedin_appium.exceptions import ProfileInaccessibleError, ReachedConnectionLimit, SkipProfile

logger = logging.getLogger(__name__)

MAX_CONNECT_ATTEMPTS = 3


@dataclass
class ConnectStrategy:
    find_candidate: Callable
    pre_connect: Callable | None
    qualifier: object


def strategy_for(campaign, qualifiers):
    """Build the right ConnectStrategy based on campaign type."""
    qualifier = qualifiers.get(campaign.pk)

    if campaign.is_freemium:
        from linkedin.db.deals import create_freemium_deal
        from linkedin.pipeline.freemium_pool import find_freemium_candidate

        return ConnectStrategy(
            find_candidate=lambda s: find_freemium_candidate(s, qualifier),
            pre_connect=lambda s, pid: create_freemium_deal(s, pid),
            qualifier=qualifier,
        )

    from linkedin.pipeline.pools import find_candidate

    return ConnectStrategy(
        find_candidate=lambda s: find_candidate(s, qualifier),
        pre_connect=None,
        qualifier=qualifier,
    )


def handle_connect(task, session, qualifiers):
    from linkedin_appium.actions.connect import send_connection_request
    from linkedin_appium.actions.status import read_status
    from linkedin_appium.actions.profile import open_profile
    from linkedin_appium.enums import ConnectionStatus
    from linkedin_appium.exceptions import NoConnectButton

    campaign = session.campaign
    strategy = strategy_for(campaign, qualifiers)

    if not session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT):
        logger.info("[%s] connect: daily limit reached — slot skipped", campaign)
        return

    candidate = strategy.find_candidate(session)
    if candidate is None:
        logger.info("[%s] connect: no candidate available — slot skipped", campaign)
        return

    public_id = candidate["public_identifier"]

    # Freemium campaigns need a Deal before set_profile_state
    if strategy.pre_connect:
        strategy.pre_connect(session, public_id)

    from crm.models import Deal

    deal = Deal.objects.filter(
        lead__public_identifier=public_id,
        campaign=session.campaign,
    ).first()
    reason = deal.reason if deal else ""
    stats = strategy.qualifier.explain(candidate, session) if strategy.qualifier else ""
    logger.info("[%s] %s", campaign, colored("▶ connect", "cyan", attrs=["bold"]))
    logger.info("[%s] %s (%s) — %s", campaign, public_id, stats, reason or "")

    driver = session.ensure_driver()

    try:
        open_profile(driver, public_id)
        status = read_status(driver)

        if status in (ConnectionStatus.CONNECTED, ConnectionStatus.PENDING):
            # set_profile_state fires on_deal_state_entered, which stamps
            # next_check_pending_at on PENDING and no-ops on CONNECTED.
            state = ProfileState.CONNECTED if status == ConnectionStatus.CONNECTED else ProfileState.PENDING
            set_profile_state(session, public_id, state.value)
            return

        # Not connected — the profile is already open, so send the invite.
        try:
            send_connection_request(driver)  # returns PENDING or raises
            set_profile_state(session, public_id, ProfileState.PENDING.value)
            session.linkedin_profile.record_action(
                ActionLog.ActionType.CONNECT, session.campaign,
            )
        except NoConnectButton:
            # No invite affordance — track attempt, disqualify after the cap.
            attempts = increment_connect_attempts(session, public_id)
            if attempts >= MAX_CONNECT_ATTEMPTS:
                reason = f"Unreachable: no Connect button after {attempts} attempts"
                disqualify_lead(public_id)
                set_profile_state(session, public_id, ProfileState.FAILED.value, reason=reason)
                logger.warning("Disqualified %s — %s", public_id, reason)
            else:
                set_profile_state(session, public_id, ProfileState.QUALIFIED.value)
                logger.debug("%s: connect attempt %d/%d — no button found", public_id, attempts, MAX_CONNECT_ATTEMPTS)

    except ReachedConnectionLimit as e:
        logger.warning("Rate limited: %s", e)
        session.linkedin_profile.mark_exhausted(ActionLog.ActionType.CONNECT)
    except ProfileInaccessibleError as e:
        logger.warning("Profile inaccessible — marking FAILED: %s", e)
        set_profile_state(session, public_id, ProfileState.FAILED.value,
                          reason=f"Profile inaccessible: {e}")
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)
