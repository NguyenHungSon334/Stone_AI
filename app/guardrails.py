"""
Safety guardrails using llama-guard classification.
"""
from __future__ import annotations

from loguru import logger

from app.llm import classify

UNSAFE_REPLY = (
    "Em xin lỗi, em không thể hỗ trợ yêu cầu này. "
    "Anh/chị cần tư vấn sản phẩm đá gì không ạ?"
)


async def is_unsafe(text: str) -> bool:
    """Return True if llama-guard classifies text as unsafe. Fails open (safe) on error."""
    try:
        label = await classify(text)
        if label.startswith("unsafe"):
            logger.warning("guardrail blocked label={} text={!r}", label, text[:80])
            return True
        return False
    except Exception:
        logger.exception("guardrail error — defaulting to safe")
        return False
