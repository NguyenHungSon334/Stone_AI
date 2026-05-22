"""
Admin dashboard — REST API for human agents.

Auth: pass X-Admin-Key header matching ADMIN_API_KEY env var.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.context import load_context, save_context
from app.db.supabase import get_client
from app.messenger import send_text

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _require_auth(x_admin_key: str | None) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/escalated")
async def list_escalated(x_admin_key: str | None = Header(None)):
    """List all conversations currently flagged for human escalation."""
    _require_auth(x_admin_key)
    db = await get_client()
    result = await (
        db.table("conversations")
        .select("messenger_user_id,name,state,intent,assigned_agent,updated_at")
        .eq("is_escalated", True)
        .order("updated_at", desc=True)
        .execute()
    )
    return result.data or []


@router.get("/conversations/{user_id}")
async def get_conversation(
    user_id: str,
    x_admin_key: str | None = Header(None),
    limit: int = 50,
):
    """Fetch conversation metadata + recent messages for a user."""
    _require_auth(x_admin_key)
    db = await get_client()
    conv = await (
        db.table("conversations")
        .select("*")
        .eq("messenger_user_id", user_id)
        .single()
        .execute()
    )
    msgs = await (
        db.table("messages")
        .select("role,content,tool_name,model_used,cost_usd,latency_ms,created_at")
        .eq("messenger_user_id", user_id)
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return {"conversation": conv.data, "messages": msgs.data or []}


class ReplyBody(BaseModel):
    text: str
    agent_name: str = "Nhân viên Hồn Đá"


@router.post("/reply/{user_id}")
async def admin_reply(
    user_id: str,
    body: ReplyBody,
    x_admin_key: str | None = Header(None),
):
    """Send a human-agent reply to the user via Messenger and log it."""
    _require_auth(x_admin_key)
    await send_text(user_id, body.text)
    db = await get_client()
    await (
        db.table("messages").insert({
            "messenger_user_id": user_id,
            "role": "assistant",
            "content": body.text,
            "tool_name": "human_agent",
            "model_used": body.agent_name,
        }).execute()
    )
    return {"status": "sent"}


@router.post("/assign/{user_id}")
async def assign_agent(
    user_id: str,
    agent_name: str,
    x_admin_key: str | None = Header(None),
):
    """Assign a named human agent to the escalated conversation."""
    _require_auth(x_admin_key)
    ctx = await load_context(user_id)
    ctx.assigned_agent = agent_name
    await save_context(ctx)
    return {"status": "assigned", "agent": agent_name}


@router.post("/resolve/{user_id}")
async def resolve_escalation(
    user_id: str,
    x_admin_key: str | None = Header(None),
):
    """Resolve escalation and return conversation to bot handling."""
    _require_auth(x_admin_key)
    ctx = await load_context(user_id)
    ctx.is_escalated = False
    ctx.state = "active"
    ctx.assigned_agent = None
    await save_context(ctx)
    await send_text(
        user_id,
        "Cảm ơn anh/chị đã liên hệ Hồn Đá! "
        "Em có thể tiếp tục hỗ trợ anh/chị về sản phẩm đá không ạ?",
    )
    return {"status": "resolved"}
