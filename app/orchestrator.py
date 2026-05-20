"""
AI orchestration loop — guardrails → escalation → LLM with tools → persist.
"""
from __future__ import annotations

import json
import time

from loguru import logger

from app.config import settings
from app.context import ConversationContext, Message, append_message, get_daily_cost, load_context, save_context
from app.guardrails import UNSAFE_REPLY, is_unsafe
from app.llm import llm_call_with_tools, chat
from app.messenger import send_text
from app.prompt import build_messages
from app.tools.definitions import TOOLS
from app.tools.escalate import should_escalate, ESCALATE_NOTIFY, ALREADY_ESCALATED
from app.tools.update_customer import update_customer
from app.tools.search import search_products, format_products_for_llm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COST_CAP_REPLY = (
    "Em xin lỗi, tài khoản đã đạt giới hạn sử dụng hôm nay. "
    "Bác vui lòng thử lại vào ngày mai ạ!"
)
_ESCALATE_REPLY = (
    "Dạ em đã ghi nhận. "
    "Chuyên gia Hồn Đá sẽ liên hệ lại Bác sớm nhất có thể ạ!"
)
_FALLBACK_REPLY = (
    "Dạ em xin lỗi, có lỗi xảy ra. "
    "Bác vui lòng thử lại sau ít phút ạ!"
)


# ---------------------------------------------------------------------------
# Personality detection (heuristic, zero LLM cost)
# ---------------------------------------------------------------------------

def _update_personality(ctx: ConversationContext, user_text: str) -> None:
    text = user_text.lower()
    p = ctx.personality
    if any(w in text for w in ["tôi", "mình", "bạn"]):
        p["register"] = "casual"
    elif any(w in text for w in ["em", "anh", "chị", "bác", "ạ"]):
        p.setdefault("register", "formal")
    if any(w in text for w in ["giá", "rẻ", "đắt", "tiền", "bao nhiêu", "ngân sách", "báo giá"]):
        p["price_sensitive"] = True
    if any(w in text for w in ["gấp", "ngay", "hôm nay", "sớm", "tháng này"]):
        p["urgent"] = True
    if any(w in text for w in ["lăng tộc", "gia tộc", "dòng họ", "khu mộ", "nhiều mộ"]):
        p["large_project"] = True


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def _execute_tool(
    tool_call: dict,
    sender_id: str,
    ctx: ConversationContext,
) -> str:
    name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"]["arguments"])
    except Exception:
        logger.warning("tool {} bad JSON args={!r}", name, tool_call["function"]["arguments"])
        return "error: invalid arguments"

    if name == "update_customer":
        filled = {k: v for k, v in args.items() if v}
        ctx.filled_slots.update(filled)
        await update_customer(sender_id, args)
        logger.info("tool update_customer sender={} fields={}", sender_id, list(filled.keys()))
        return "Đã lưu thông tin khách hàng."

    if name == "search_products":
        slots = {k: args[k] for k in ("stone_type", "project_type", "budget", "chieu_dai", "chieu_cao", "chieu_rong") if args.get(k)}
        products = await search_products(args["query"], slots)
        if products:
            return format_products_for_llm(products)
        return "Không tìm thấy sản phẩm phù hợp."

    logger.warning("unknown tool called: {}", name)
    return f"unknown tool: {name}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run(sender_id: str, user_text: str) -> None:
    """Process one incoming Messenger message end-to-end."""
    t0 = time.monotonic()
    ctx = await load_context(sender_id)

    # --- Safety ---
    if await is_unsafe(user_text):
        await send_text(sender_id, UNSAFE_REPLY)
        await append_message(sender_id, Message(role="user", content=user_text))
        await append_message(sender_id, Message(role="assistant", content=UNSAFE_REPLY))
        return

    # --- Cost cap ---
    if (await get_daily_cost(sender_id)) >= settings.cost_cap_per_user_day:
        logger.warning("cost cap hit sender={}", sender_id)
        await send_text(sender_id, _COST_CAP_REPLY)
        return

    # --- Escalation check (heuristic, zero LLM cost) ---
    if should_escalate(user_text, ctx):
        reply = ALREADY_ESCALATED if ctx.is_escalated else ESCALATE_NOTIFY
        ctx.is_escalated = True
        ctx.state = "escalated"
        await send_text(sender_id, reply)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        await save_context(ctx)
        await append_message(sender_id, Message(role="user", content=user_text))
        await append_message(sender_id, Message(role="assistant", content=reply, latency_ms=elapsed_ms))
        logger.info("escalated sender={}", sender_id)
        return

    # --- Personality (heuristic, zero cost) ---
    _update_personality(ctx, user_text)

    # --- Build messages + LLM call with tools ---
    messages = build_messages(ctx, user_text)
    text_reply, tool_calls, cost = await llm_call_with_tools(messages, TOOLS)

    # --- Execute tools, send results back for final reply ---
    if tool_calls:
        tool_results: list[dict] = []
        for tc in tool_calls:
            result = await _execute_tool(tc, sender_id, ctx)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        assistant_turn = {
            "role": "assistant",
            "content": text_reply,
            "tool_calls": tool_calls,
        }
        follow_up = messages + [assistant_turn] + tool_results
        # Force text response — pass empty tools to prevent another tool loop
        text_reply2, _, cost2 = await llm_call_with_tools(follow_up, [])
        cost += cost2
        if text_reply2:
            text_reply = text_reply2

    reply = text_reply or _FALLBACK_REPLY
    ctx.state = "active"

    # --- Respond ---
    await send_text(sender_id, reply)

    # --- Persist ---
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    await save_context(ctx)
    await append_message(sender_id, Message(role="user", content=user_text))
    await append_message(sender_id, Message(
        role="assistant",
        content=reply,
        cost_usd=cost,
        latency_ms=elapsed_ms,
    ))

    tool_names = [tc["function"]["name"] for tc in tool_calls] if tool_calls else []
    logger.info(
        "orchestrator done sender={} tools={} cost=${:.5f} latency={}ms",
        sender_id, tool_names, cost, elapsed_ms,
    )
