"""
Upsert customer CRM record whenever new slot data is collected.
Called automatically after slot extraction — not an LLM tool call.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.db.supabase import get_client

_CUSTOMER_FIELDS = frozenset({
    "name", "phone", "project_type", "stone_type",
    "items", "location", "crane_access", "timeline",
})


async def update_customer(messenger_user_id: str, slots: dict[str, Any]) -> None:
    """
    Upsert customers row with any slot fields present.
    Only writes fields that exist in the new slots — never clears existing data.
    """
    payload = {k: v for k, v in slots.items() if k in _CUSTOMER_FIELDS and v}
    if not payload:
        return

    payload["messenger_user_id"] = messenger_user_id

    try:
        db = get_client()
        db.table("customers").upsert(
            payload,
            on_conflict="messenger_user_id",
        ).execute()
        logger.info("update_customer user={} fields={}", messenger_user_id, list(payload.keys()))
    except Exception:
        logger.exception("update_customer failed user={}", messenger_user_id)
