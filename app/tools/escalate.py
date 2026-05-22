"""
Escalation detection — frustration keywords, repetition, explicit requests.
"""
from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.context import ConversationContext


def _normalize(text: str) -> str:
    """NFC-normalize so Messenger NFD text matches hardcoded NFC keywords."""
    return unicodedata.normalize("NFC", text)

# Explicit "I want a human" signals
_HUMAN_REQUEST = [
    "gặp người thật", "người thật", "nhân viên thật", "nói chuyện với người",
    "gặp người khác", "gặp nhân viên",
    "con người", "manager", "quản lý", "giám sát", "supervisor",
    "khiếu nại", "complaint", "chuyển máy", "gặp trực tiếp",
]

# Frustration / anger signals
_FRUSTRATION = [
    "tức", "bực", "chán", "điên", "vô dụng", "không giúp được",
    "không hiểu gì", "tệ quá", "dở quá", "stupid", "useless", "terrible",
    "không ổn", "sai hết", "sai rồi", "lại sai", "sai mãi",
]


def should_escalate(user_text: str, ctx: "ConversationContext") -> bool:
    """
    Return True when the message warrants human escalation.
    Checks: explicit human request, frustration keywords, punctuation anger, repetition.
    """
    text = _normalize(user_text.lower())

    # Explicit escalation request
    if any(kw in text for kw in _HUMAN_REQUEST):
        return True

    # Already escalated — stay escalated until admin resolves
    if ctx.is_escalated:
        return True

    # Multiple frustration signals in one message
    if sum(1 for kw in _FRUSTRATION if kw in text) >= 2:
        return True

    # Anger punctuation: "!!!!" or "????"
    if text.count("!") >= 4 or text.count("?") >= 4:
        return True

    # Message repetition: same text sent 2+ times in recent history
    recent_user_msgs = [
        _normalize(m["content"].strip().lower())
        for m in ctx.history[-6:]
        if m.get("role") == "user"
    ]
    if recent_user_msgs.count(text.strip()) >= 2:
        return True

    return False


ESCALATE_NOTIFY = (
    "Em đã ghi nhận và chuyển cuộc hội thoại đến nhân viên Hồn Đá. "
    "Anh/chị vui lòng chờ, nhân viên sẽ liên hệ lại ngay ạ!"
)

ALREADY_ESCALATED = (
    "Anh/chị đang được nhân viên Hồn Đá hỗ trợ. "
    "Vui lòng chờ phản hồi ạ!"
)
