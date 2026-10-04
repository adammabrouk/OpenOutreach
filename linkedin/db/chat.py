import logging


logger = logging.getLogger(__name__)


def _get_lead_and_ct(public_identifier: str):
    """Return (lead, content_type) for a public identifier."""
    from django.contrib.contenttypes.models import ContentType
    from crm.models import Lead

    lead = Lead.objects.get(public_identifier=public_identifier)
    ct = ContentType.objects.get_for_model(lead)
    return lead, ct


def sync_conversation(session, public_identifier: str) -> list[dict]:
    """Read the phone conversation thread and upsert into ChatMessage.

    Returns messages as a list of {sender, text, timestamp, is_outgoing} dicts
    from the DB (always the source of truth after sync). Newly-synced messages
    are also folded into the campaign Deal's `chat_summary` (mem0-style facts).
    """
    lead, ct = _get_lead_and_ct(public_identifier)
    new_messages = _sync_from_thread(session, public_identifier, lead, ct)
    _update_deal_chat_summary(session, lead, new_messages)

    return _read_from_db(public_identifier)


def _update_deal_chat_summary(session, lead, new_messages):
    """Fold newly-synced ChatMessages into the campaign Deal's chat_summary."""
    if not new_messages:
        return
    from crm.models import Deal
    from linkedin.db.summaries import seller_name_from, update_chat_summary

    deal = Deal.objects.filter(lead=lead, campaign=session.campaign).first()
    if not deal:
        return
    update_chat_summary(deal, new_messages, seller_name=seller_name_from(session))


def _synthetic_urn(public_identifier: str, is_outgoing: bool, text: str) -> str:
    """Deterministic dedup key for a mobile message (no Voyager entityUrn).

    The Android app exposes no per-message id, so we synthesize a stable one from
    the lead + direction + a content hash. Re-syncing the same message yields the
    same key, keeping upserts idempotent; two identical messages in the same
    direction to the same lead collapse to one row (acceptable).
    """
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"appium:{public_identifier}:{'out' if is_outgoing else 'in'}:{digest}"


def _sync_from_thread(session, public_identifier: str, lead, ct) -> list:
    """Read the phone conversation thread and upsert into DB.

    Returns the list of newly-created ``ChatMessage`` rows (in arrival order),
    so callers can incrementally update derived caches like ``chat_summary``.
    """
    from chat.models import ChatMessage
    from linkedin_appium.actions.thread import open_thread, read_thread

    driver = session.ensure_driver()
    self_name = session.self_profile.get("name") or ""

    open_thread(driver, public_identifier)
    messages = read_thread(driver, self_name)
    if not messages:
        logger.debug("sync: no messages found for %s", public_identifier)
        return []

    new_messages: list = []
    for m in messages:
        if not m.text:
            continue
        urn = _synthetic_urn(public_identifier, m.is_outgoing, m.text)
        obj, created = ChatMessage.objects.get_or_create(
            linkedin_urn=urn,
            defaults={
                "content_type": ct,
                "object_id": lead.pk,
                "content": m.text,
                "is_outgoing": m.is_outgoing,
                "owner": session.django_user,
            },
        )
        if created:
            new_messages.append(obj)
            logger.debug("sync: new %s message for %s",
                         "outgoing" if m.is_outgoing else "incoming", public_identifier)

    new_messages.sort(key=lambda m: m.creation_date or m.pk)
    logger.debug("sync: processed %d messages for %s (%d new)",
                 len(messages), public_identifier, len(new_messages))
    return new_messages


def _read_from_db(public_identifier: str) -> list[dict]:
    """Read all ChatMessages for a lead, sorted chronologically."""
    from chat.models import ChatMessage

    lead, ct = _get_lead_and_ct(public_identifier)
    lead_name = lead.public_identifier or "them"

    messages = ChatMessage.objects.filter(
        content_type=ct, object_id=lead.pk,
    ).select_related("owner").order_by("creation_date")

    result = []
    for msg in messages:
        if not msg.content:
            continue
        if msg.is_outgoing:
            owner = msg.owner
            sender = f"{owner.first_name or ''} {owner.last_name or ''}".strip() if owner else "me"
        else:
            sender = lead_name
        result.append({
            "sender": sender or "me",
            "text": msg.content,
            "timestamp": msg.creation_date.strftime("%Y-%m-%d %H:%M") if msg.creation_date else "",
            "is_outgoing": msg.is_outgoing,
        })
    return result
