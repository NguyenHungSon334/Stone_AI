"""
Escalation detection — AI intent classification with fast-path heuristics.
"""
from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from app.llm import chat

if TYPE_CHECKING:
    from app.context import ConversationContext


def _normalize(text: str) -> str:
    """NFC-normalize so Messenger NFD text matches Python NFC source strings."""
    return unicodedata.normalize("NFC", text)


_ESCALATE_SYSTEM = (
    "Bạn phân loại ý định của khách hàng khi nhắn tin cho chatbot bán đá lăng mộ.\n"
    "Trả về 'escalate' nếu khách:\n"
    "- Muốn gặp/nói chuyện với nhân viên, người thật, con người\n"
    "- Yêu cầu hỗ trợ từ người (không phải bot)\n"
    "- Tức giận, bực bội, thất vọng rõ ràng với dịch vụ\n"
    "- Khiếu nại, muốn gặp quản lý/supervisor\n"
    "Trả về 'normal' cho mọi trường hợp khác.\n"
    "Chỉ trả về đúng một từ: escalate hoặc normal. Không giải thích."
)


async def should_escalate(user_text: str, ctx: "ConversationContext") -> bool:
    """
    Return True when the message warrants human escalation.
    Uses AI intent classification with fast-path heuristics for free checks.
    """
    # Already escalated — stay escalated until admin resolves
    if ctx.is_escalated:
        return True

    text = _normalize(user_text)

    # Anger punctuation fast-path (free, no LLM)
    if text.count("!") >= 4 or text.count("?") >= 4:
        return True

    # Message repetition fast-path (free, no LLM)
    normalized = _normalize(user_text.strip().lower())
    recent_user_msgs = [
        _normalize(m["content"].strip().lower())
        for m in ctx.history[-6:]
        if m.get("role") == "user"
    ]
    if recent_user_msgs.count(normalized) >= 2:
        return True

    # AI intent classification
    try:
        reply, _ = await chat(
            system=_ESCALATE_SYSTEM,
            user=text,
            alias="guard",
            temperature=0.0,
            max_tokens=10,
        )
        return reply.lower().strip() == "escalate"
    except Exception:
        # Fallback: don't escalate on LLM failure
        return False


ESCALATE_NOTIFY = (
    "Em đã ghi nhận và chuyển cuộc hội thoại đến nhân viên Hồn Đá. "
    "Anh/chị vui lòng chờ, nhân viên sẽ liên hệ lại ngay ạ!"
)

ALREADY_ESCALATED = (
    "Anh/chị đang được nhân viên Hồn Đá hỗ trợ. "
    "Vui lòng chờ phản hồi ạ!"
)
