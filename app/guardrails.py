"""
Safety guardrails using LLM classification.
"""
from __future__ import annotations

from app.llm import classify

UNSAFE_REPLY = (
    "Em xin lỗi, em không thể hỗ trợ yêu cầu này. "
)


async def is_unsafe(text: str) -> bool:
    try:
        result = await classify(text)
        return result == "unsafe"
    except Exception:
        return False
