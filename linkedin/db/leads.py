import logging
import random
import time
from typing import Dict, Any, Optional

from django.db import transaction

from linkedin_appium.url_utils import url_to_public_id, public_id_to_url
from linkedin_appium.enums import ProfileState

logger = logging.getLogger(__name__)


def lead_exists(url: str) -> bool:
    """Check if Lead already exists for this LinkedIn URL."""
    from crm.models import Lead

    pid = url_to_public_id(url)
    if not pid:
        return False
    return Lead.objects.filter(public_identifier=pid).exists()


def create_enriched_lead(session, url: str, profile: Dict[str, Any]) -> Optional[int]:
    """Create Lead with full profile data and embedding.

    Returns lead PK or None if exists.
    Does NOT create Deal — that comes at qualification.
    """
    from crm.models import Lead

    # Use canonical public_identifier from Voyager response when available.
    canonical_pid = profile.get("public_identifier")
    public_id = canonical_pid or url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    urn = profile.get("urn") or None

    with transaction.atomic():
        if Lead.objects.filter(public_identifier=public_id).exists():
            return None
        if urn and Lead.objects.filter(urn=urn).exists():
            logger.info(
                "Lead with URN %s already exists — skipping duplicate %s",
                urn, public_id,
            )
            return None
        lead = Lead.objects.create(linkedin_url=clean_url, public_identifier=public_id)
        _cache_urn_from_profile(lead, profile)

    lead.embed_from_profile(profile)

    logger.debug("Created enriched lead for %s (pk=%d)", public_id, lead.pk)
    return lead.pk


@transaction.atomic
def promote_lead_to_deal(session, public_id: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from crm.models import Lead, Deal

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=ProfileState.QUALIFIED,
        reason=reason,
    )

    from termcolor import colored
    logger.info("%s %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]))
    return deal


def get_leads_for_qualification(session) -> list:
    """Leads eligible for qualification in the current campaign.

    Returns profile dicts for leads that are not permanently disqualified
    and have no Deal in this campaign.
    """
    from crm.models import Lead

    leads = Lead.objects.filter(
        disqualified=False,
    ).exclude(
        deal__campaign=session.campaign,
    )

    return [lead.to_profile_dict() for lead in leads]


def update_lead_slug(old_public_id: str, new_public_id: str):
    """Update a Lead after LinkedIn redirected its vanity URL."""
    from crm.models import Lead

    new_url = public_id_to_url(new_public_id)
    updated = Lead.objects.filter(public_identifier=old_public_id).update(
        public_identifier=new_public_id,
        linkedin_url=new_url,
    )
    if updated:
        logger.info("Lead slug updated: %s → %s", old_public_id, new_public_id)
    return updated


def disqualify_lead(public_id: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from crm.models import Lead

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", public_id)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])


def discover_and_enrich(session, hits):
    """For each search hit, open the profile, resolve its slug, create a Lead.

    Mobile search rows carry no vanity slug, so each hit must be opened to
    resolve identity (via More → Contact info) and read its fields. Caps at
    enrich_max_per_page (LinkedIn's own relevance order) and pauses a human-ish
    [enrich_min_delay_seconds, enrich_max_delay_seconds] between profiles.

    TODO(phone-validate): the per-hit navigation (tap row → resolve slug →
    return to results) and the profile richness that feeds the embedding still
    need an on-device pass — see ``read_full_profile``.
    """
    from linkedin_appium.actions.search import open_hit, back_to_results
    from linkedin_appium.actions.profile import read_full_profile, resolve_public_identifier
    from linkedin.conf import CAMPAIGN_CONFIG

    if not hits:
        return

    max_per_page = CAMPAIGN_CONFIG["enrich_max_per_page"]
    hits = hits[:max_per_page]

    logger.info("Discovered %d candidate hit(s) to enrich", len(hits))

    min_delay = CAMPAIGN_CONFIG["enrich_min_delay_seconds"]
    max_delay = CAMPAIGN_CONFIG["enrich_max_delay_seconds"]
    driver = session.ensure_driver()
    enriched = 0

    for hit in hits:
        if not open_hit(driver, hit):
            continue

        try:
            profile = read_full_profile(driver)          # header fields (main profile)
            slug = resolve_public_identifier(driver)     # navigates to the contact sheet
            driver.back()                                # contact sheet → profile

            if not slug:
                logger.warning("No slug resolved for %r — skipping", hit.name)
            else:
                url = public_id_to_url(slug)
                profile["public_identifier"] = slug
                if not lead_exists(url) and create_enriched_lead(session, url, profile) is not None:
                    enriched += 1
        except Exception:
            logger.warning("Enrich failed for %r — skipping", hit.name)

        back_to_results(driver)
        time.sleep(random.uniform(min_delay, max_delay))

    logger.info("Enriched %d/%d new profiles", enriched, len(hits))


def _cache_urn_from_profile(lead, profile: Dict[str, Any]):
    """Promote ``profile['urn']`` onto the Lead row if not already cached.

    The only durable field we extract from a fresh scrape — everything
    else lives in memory for the lifetime of the caller's dict.
    """
    urn = profile.get("urn") or None
    if urn and lead.urn != urn:
        lead.urn = urn
        lead.save(update_fields=["urn"])


def register_self_lead(session, profile: Dict[str, Any]):
    """Persist the logged-in member's own profile as a disqualified Lead.

    The CRM-side layer over the self-discovery primitive: marks the real profile
    disqualified (so auto-discovery never targets it) and links it as
    ``linkedin_profile.self_lead``. Idempotent per profile. On mobile the own
    slug is best-effort — with no slug there's nothing to key the self-Lead on,
    so we skip (the only cost is losing the self-exclusion dedup, an unlikely edge).
    """
    from crm.models import Lead

    public_id = profile.get("public_identifier") or ""
    if not public_id:
        logger.warning("register_self_lead: no self slug available — skipping self-Lead")
        return
    lead, _ = Lead.objects.update_or_create(
        public_identifier=public_id,
        defaults={"linkedin_url": public_id_to_url(public_id), "disqualified": True},
    )
    _cache_urn_from_profile(lead, profile)

    session.linkedin_profile.self_lead = lead
    session.linkedin_profile.save(update_fields=["self_lead"])
    logger.info("Registered self-profile as disqualified Lead: %s", public_id)
