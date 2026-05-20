"""
Safety guardrails using llama-guard classification.
"""
from __future__ import annotations

UNSAFE_REPLY = (
    "Em xin lỗi, em không thể hỗ trợ yêu cầu này. "
    "Anh/chị cần tư vấn sản phẩm đá gì không ạ?"
)


async def is_unsafe(text: str) -> bool:  # noqa: ARG001
    # TODO: guardrails temporarily disabled
    return False
