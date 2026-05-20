"""
Slot extraction — pull structured info from free-form customer text.
"""
from __future__ import annotations

import json

from loguru import logger

from app.llm import chat

_SLOTS = frozenset({
    "project_type",  # mộ đơn | lăng tộc
    "stone_type",    # xanh rêu | xanh đen | granite
    "items",         # danh sách hạng mục (mộ, lăng thờ, cổng, lan can...)
    "phone",         # SĐT hoặc Zalo
    "location",      # tỉnh/huyện thi công
    "crane_access",  # xe cẩu vào được (true/false/unknown)
    "timeline",      # tháng/năm dự kiến
    "name",          # tên khách
})

_SYSTEM = """Trích xuất thông tin từ tin nhắn khách hàng về công trình đá lăng mộ. Chỉ lấy thông tin được đề cập rõ ràng, không suy đoán.
Trả về JSON với các trường (bỏ qua nếu không có):
- project_type: loại công trình ("mộ đơn" hoặc "lăng tộc")
- stone_type: loại đá ("xanh rêu", "xanh đen", "granite")
- items: danh sách hạng mục cần làm (array, ví dụ: ["mộ", "lăng thờ", "cổng", "lan can"])
- phone: số điện thoại hoặc Zalo (chuỗi số)
- location: tỉnh hoặc huyện thi công
- crane_access: xe cẩu có vào được không ("có", "không", hoặc bỏ qua nếu chưa biết)
- timeline: thời gian dự kiến thi công (ví dụ: "tháng 8/2025")
- name: tên khách hàng

Chỉ trả về JSON, không giải thích."""


async def extract_slots(user_text: str) -> dict:
    """Return dict of extracted slots (only present, known keys)."""
    try:
        reply, _ = await chat(
            system=_SYSTEM,
            user=user_text,
            alias="fast",
            temperature=0.0,
            max_tokens=150,
        )
        raw = reply.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        data = json.loads(raw)
        return {k: v for k, v in data.items() if k in _SLOTS and v}
    except Exception:
        logger.exception("slot extraction failed text={!r}", user_text[:80])
        return {}
