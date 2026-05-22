"""
Session context — load/save conversation state and message history from Supabase.

One conversation row per messenger_user_id (upserted on first contact).
Messages stored in append-only messages table.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from loguru import logger

from app.db.supabase import get_client


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str           # user | assistant | tool
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    model_used: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass
class ConversationContext:
    messenger_user_id: str
    name: str | None = None
    state: str = "greeting"
    filled_slots: dict[str, Any] = field(default_factory=dict)
    intent: str | None = None
    personality: dict[str, Any] = field(default_factory=dict)
    is_escalated: bool = False
    assigned_agent: str | None = None
    # Recent messages loaded into memory (not persisted here — already in DB)
    history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Load / save conversation
# ---------------------------------------------------------------------------

async def load_context(messenger_user_id: str, history_limit: int = 14) -> ConversationContext:
    """
    Load conversation state + recent message history for a user.
    Creates a new conversation row if this is the first contact.
    """
    db = await get_client()

    # Must run first to ensure the row exists before parallel selects
    await (
        db.table("conversations")
        .upsert(
            {"messenger_user_id": messenger_user_id},
            on_conflict="messenger_user_id",
            ignore_duplicates=True,
        )
        .execute()
    )

    row_result, msgs_result = await asyncio.gather(
        db.table("conversations")
        .select("*")
        .eq("messenger_user_id", messenger_user_id)
        .single()
        .execute(),
        db.table("messages")
        .select("role,content,tool_name,tool_input")
        .eq("messenger_user_id", messenger_user_id)
        .order("created_at", desc=True)
        .limit(history_limit)
        .execute(),
    )

    data = row_result.data
    raw_msgs = list(reversed(msgs_result.data or []))
    history = [
        {"role": m["role"], "content": m["content"] or ""}
        for m in raw_msgs
        if m["role"] in ("user", "assistant")
    ]

    ctx = ConversationContext(
        messenger_user_id=messenger_user_id,
        name=data.get("name"),
        state=data.get("state", "greeting"),
        filled_slots=data.get("filled_slots") or {},
        intent=data.get("intent"),
        personality=data.get("personality") or {},
        is_escalated=data.get("is_escalated", False),
        assigned_agent=data.get("assigned_agent"),
        history=history,
    )
    logger.debug("load_context user={} state={} history_len={}", messenger_user_id, ctx.state, len(history))
    return ctx


async def save_context(ctx: ConversationContext) -> None:
    """Persist updated conversation state (not messages — use append_message for that)."""
    db = await get_client()
    await (
        db.table("conversations").update({
            "name": ctx.name,
            "state": ctx.state,
            "filled_slots": ctx.filled_slots,
            "intent": ctx.intent,
            "personality": ctx.personality,
            "is_escalated": ctx.is_escalated,
            "assigned_agent": ctx.assigned_agent,
            "updated_at": "now()",
        }).eq("messenger_user_id", ctx.messenger_user_id).execute()
    )
    logger.debug("save_context user={} state={}", ctx.messenger_user_id, ctx.state)


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

async def append_message(
    messenger_user_id: str,
    msg: Message,
) -> None:
    """Append one message to the audit log."""
    db = await get_client()
    await (
        db.table("messages").insert({
            "messenger_user_id": messenger_user_id,
            "role": msg.role,
            "content": msg.content,
            "tool_name": msg.tool_name,
            "tool_input": msg.tool_input,
            "model_used": msg.model_used,
            "tokens_input": msg.tokens_input,
            "tokens_output": msg.tokens_output,
            "cost_usd": msg.cost_usd,
            "latency_ms": msg.latency_ms,
        }).execute()
    )


# ---------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------

async def get_daily_cost(messenger_user_id: str) -> float:
    """Return total cost_usd spent by this user today (UTC)."""
    db = await get_client()
    try:
        result = await db.rpc(
            "get_daily_cost",
            {"p_user_id": messenger_user_id},
        ).execute()
        if result.data is not None:
            return float(result.data)
    except Exception:
        pass
    # Fallback: manual sum via postgrest filter
    try:
        rows = await (
            db.table("messages")
            .select("cost_usd")
            .eq("messenger_user_id", messenger_user_id)
            .gte("created_at", date.today().isoformat())
            .execute()
        )
        return sum(r["cost_usd"] or 0 for r in (rows.data or []))
    except Exception:
        logger.warning("get_daily_cost DB unavailable, failing open user={}", messenger_user_id)
        return 0.0
