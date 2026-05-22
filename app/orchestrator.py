"""
AI orchestration loop — guardrails → escalation → LLM with tools → persist.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from loguru import logger

from app.config import settings
from app.context import ConversationContext, Message, append_message, get_daily_cost, load_context, save_context
from app.guardrails import UNSAFE_REPLY, is_unsafe
from app.llm import ModelAlias, llm_call_with_tools, chat
from app.messenger import send_text, send_typing_on
from app.prompt import build_messages
from app.tools.definitions import TOOLS
from app.tools.escalate import should_escalate, ESCALATE_NOTIFY
from app.tools.update_customer import update_customer
from app.tools.search import search_products, format_products_for_llm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COST_CAP_REPLY = (
    "Em xin lỗi, tài khoản đã đạt giới hạn sử dụng hôm nay. "
    "Bác vui lòng thử lại vào ngày mai ạ!"
)
_FALLBACK_REPLY = (
    "Dạ em xin lỗi, có lỗi xảy ra. "
    "Bác vui lòng thử lại sau ít phút ạ!"
)
_NO_PRODUCTS_REPLY = (
    "Em chưa tìm thấy sản phẩm phù hợp ạ. "
    "Bác có thể cho em biết thêm về nhu cầu hoặc ngân sách không?"
)
_NO_PRODUCTS_TOOL_RESULT = "Không tìm thấy sản phẩm phù hợp."
_MAX_INPUT_CHARS = 1000

# Keywords that signal product/complex intent — route to smart model
_PRODUCT_KEYWORDS = frozenset({
    "giá", "mẫu", "kích", "thước", "mộ", "lăng", "đá", "cổng",
    "hàng", "sản phẩm", "đơn", "tộc", "rào", "thờ", "ngân sách",
    "triệu", "nghìn", "báo", "loại", "xanh", "granite",
})

# Simple ack/greeting words — safe to route to fast model
_FAST_EXACT = frozenset({
    "ok", "được", "oke", "okay", "okey",
    "cảm ơn", "cam on", "thanks", "thank you",
    "vâng", "dạ", "à", "ừ", "uh", "uhm",
    "hi", "hello", "chào", "xin chào",
})


# ---------------------------------------------------------------------------
# Model routing
# ---------------------------------------------------------------------------

_SLOT_LABELS: dict[str, str] = {
    "stone_type": "Loại đá",
    "project_type": "Loại công trình",
    "budget": "Ngân sách",
    "chieu_dai": "Chiều dài",
    "chieu_rong": "Chiều rộng",
    "chieu_cao": "Chiều cao",
    "name": "Tên KH",
    "phone": "SĐT",
    "address": "Địa chỉ",
}


def _build_escalation_summary(
    sender_id: str,
    user_text: str,
    ctx: "ConversationContext",
) -> str:
    customer = ctx.name or f"Khách #{sender_id[-6:]}"
    lines = [f"🔔 ESCALATION — {customer} cần hỗ trợ trực tiếp"]

    if ctx.filled_slots:
        slot_lines = [
            f"  • {_SLOT_LABELS.get(k, k)}: {v}"
            for k, v in ctx.filled_slots.items()
            if v
        ]
        if slot_lines:
            lines.append("\nThông tin đã thu thập:")
            lines.extend(slot_lines)

    recent = [m for m in ctx.history[-8:] if m.get("role") == "user"][-3:]
    if recent:
        lines.append("\nTin nhắn gần nhất của khách:")
        for m in recent:
            lines.append(f"  › {m['content'][:120]}")

    lines.append(f"\nTin nhắn kích hoạt: {user_text[:200]}")
    return "\n".join(lines)


async def _notify_admin_escalation(
    sender_id: str,
    user_text: str,
    ctx: "ConversationContext",
) -> None:
    """Ping admin PSID on Messenger when a user first escalates."""
    if not settings.admin_messenger_psid:
        return
    msg = _build_escalation_summary(sender_id, user_text, ctx)
    try:
        await send_text(settings.admin_messenger_psid, msg)
    except Exception:
        logger.warning("admin escalation notification failed sender={}", sender_id)


def _pick_alias(user_text: str, is_first_turn: bool = False) -> ModelAlias:
    """Route simple greetings/acks to fast model; everything else to smart."""
    # First contact always uses smart model — opening response sets the entire conversation tone
    if is_first_turn:
        return "smart"
    text = user_text.strip().lower()
    if text in _FAST_EXACT:
        return "fast"
    words = text.split()
    has_price_suffix = bool(re.search(r"\d+\s*(?:tr|triệu|k|nghìn|ngàn|m)\b", text))
    if len(words) <= 2 and not re.search(r"\d", text) and not has_price_suffix and not any(kw in text for kw in _PRODUCT_KEYWORDS):
        return "fast"
    return "smart"


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
        return _NO_PRODUCTS_TOOL_RESULT

    logger.warning("unknown tool called: {}", name)
    return f"unknown tool: {name}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run(sender_id: str, user_text: str) -> None:
    """Process one incoming Messenger message end-to-end."""
    t0 = time.monotonic()
    user_text = user_text[:_MAX_INPUT_CHARS]

    # Load context and check daily cost in parallel
    ctx, daily_cost = await asyncio.gather(
        load_context(sender_id),
        get_daily_cost(sender_id),
    )

    # --- Cost cap ---
    if daily_cost >= settings.cost_cap_per_user_day:
        logger.warning("cost cap hit sender={}", sender_id)
        await send_text(sender_id, _COST_CAP_REPLY)
        return

    # --- Already escalated: save silently, no LLM ---
    if ctx.is_escalated:
        await append_message(sender_id, Message(role="user", content=user_text))
        logger.info("escalated (silent) sender={}", sender_id)
        return

    # --- Safety + Escalation in parallel ---
    unsafe, escalate = await asyncio.gather(
        is_unsafe(user_text),
        should_escalate(user_text, ctx),
    )

    if not escalate and unsafe:
        await send_text(sender_id, UNSAFE_REPLY)
        await asyncio.gather(
            append_message(sender_id, Message(role="user", content=user_text)),
            append_message(sender_id, Message(role="assistant", content=UNSAFE_REPLY)),
        )
        return

    if escalate:
        ctx.is_escalated = True
        ctx.state = "escalated"
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        await send_text(sender_id, ESCALATE_NOTIFY)
        await _notify_admin_escalation(sender_id, user_text, ctx)
        await asyncio.gather(
            save_context(ctx),
            append_message(sender_id, Message(role="user", content=user_text)),
            append_message(sender_id, Message(role="assistant", content=ESCALATE_NOTIFY, latency_ms=elapsed_ms)),
        )
        logger.info("escalated sender={}", sender_id)
        return

    # --- Personality (heuristic, zero cost) ---
    _update_personality(ctx, user_text)

    # --- Build messages + LLM call with tools ---
    # Escalated users get no tools (they're waiting for a human agent)
    tools = [] if ctx.is_escalated else TOOLS
    alias = _pick_alias(user_text, is_first_turn=len(ctx.history) == 0)
    messages = build_messages(ctx, user_text)
    await send_typing_on(sender_id)
    text_reply, tool_calls, cost = await llm_call_with_tools(messages, tools, alias=alias, temperature=0.0)

    # --- Execute tools, send results back for final reply ---
    if tool_calls:
        # Execute tools in parallel
        results = await asyncio.gather(
            *[_execute_tool(tc, sender_id, ctx) for tc in tool_calls]
        )
        tool_results = [
            {"role": "tool", "tool_call_id": tc["id"], "content": result}
            for tc, result in zip(tool_calls, results)
        ]

        # Skip 2nd LLM call when all calls were search_products and all returned no results
        only_empty_searches = (
            all(tc["function"]["name"] == "search_products" for tc in tool_calls)
            and all(r["content"] == _NO_PRODUCTS_TOOL_RESULT for r in tool_results)
        )

        if only_empty_searches:
            text_reply = _NO_PRODUCTS_REPLY
        else:
            assistant_turn = {
                "role": "assistant",
                "content": text_reply,
                "tool_calls": tool_calls,
            }
            follow_up = messages + [assistant_turn] + tool_results
            # Use smart model — this turn presents product recommendations to customer
            text_reply2, _, cost2 = await llm_call_with_tools(follow_up, [], alias="smart", temperature=0.3)
            cost += cost2
            if text_reply2:
                text_reply = text_reply2

    reply = text_reply or _FALLBACK_REPLY
    ctx.state = "active"

    # --- Respond ---
    await send_text(sender_id, reply)

    # --- Persist (all three writes in parallel after reply is sent) ---
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    await asyncio.gather(
        save_context(ctx),
        append_message(sender_id, Message(role="user", content=user_text)),
        append_message(sender_id, Message(
            role="assistant",
            content=reply,
            cost_usd=cost,
            latency_ms=elapsed_ms,
        )),
    )

    tool_names = [tc["function"]["name"] for tc in tool_calls] if tool_calls else []
    logger.info(
        "orchestrator done sender={} alias={} tools={} cost=${:.5f} latency={}ms",
        sender_id, alias, tool_names, cost, elapsed_ms,
    )
