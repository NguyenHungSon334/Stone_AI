"""
Planner agent — analyzes customer intent and produces a structured action plan
that guides the main LLM toward the correct tool calls and response direction.
"""
from __future__ import annotations

from loguru import logger

from app.llm import chat
from app.context import ConversationContext

_PLANNER_SYSTEM = """Bạn phân tích tin nhắn khách hàng cho bot tư vấn đá lăng mộ Hồn Đá.

Đọc tin nhắn và ngữ cảnh, trả lời ĐÚNG format dưới đây — không giải thích thêm:

INTENT: <mua_san_pham | hoi_gia | hoi_chi_tiet | xem_anh_video | chao_hoi | thac_mac_khac>
SAN_PHAM: <loại sản phẩm + hình dạng + loại đá + kích thước nếu có, hoặc "chưa rõ">
HANH_DONG: <goi_search_products | goi_get_detail | goi_get_media | chi_tra_loi | hoi_them_truoc>
ARGS: <tham số cụ thể: query, project_type, stone_type, ma_sp, v.v.>
HUONG_TRA_LOI: <tập trung gợi ý điểm gì, câu hỏi tiếp theo nên hỏi gì>

Quy tắc phân loại HANH_DONG:
- goi_search_products: khách nêu loại sản phẩm (mộ tròn, cổng...) hoặc hỏi giá/mẫu/so sánh
- goi_get_detail: khách hỏi chi tiết 1 sản phẩm cụ thể (đã biết mã SP)
- goi_get_media: khách muốn xem ảnh/video
- chi_tra_loi: câu hỏi chung về đá/quy trình không cần tra kho
- hoi_them_truoc: chưa đủ thông tin để search (ví dụ chỉ nói "mua đá" không biết loại gì)

Ví dụ:
Khách: "tôi muốn mua mộ tròn đá xanh đen"
INTENT: mua_san_pham
SAN_PHAM: mộ tròn, đá xanh đen
HANH_DONG: goi_search_products
ARGS: query="mộ tròn đá xanh đen", project_type="mộ tròn", stone_type="xanh đen"
HUONG_TRA_LOI: gợi ý sản phẩm mộ tròn xanh đen cụ thể, hỏi thêm kích thước hoặc địa điểm"""


async def plan_response(user_text: str, ctx: ConversationContext) -> str:
    """
    Analyze customer intent and return a structured action plan.
    Uses fast model — cheap and sub-second.
    """
    history_summary = ""
    if ctx.history:
        recent = ctx.history[-4:]
        lines = []
        for m in recent:
            role = "Khách" if m["role"] == "user" else "Bot"
            lines.append(f"{role}: {m['content'][:100]}")
        history_summary = "\n".join(lines)

    slots_summary = ""
    s = ctx.filled_slots
    parts = []
    if s.get("stone_type"):
        parts.append(f"đá: {s['stone_type']}")
    if s.get("project_type"):
        parts.append(f"loại: {s['project_type']}")
    if s.get("budget"):
        parts.append(f"ngân sách: {s['budget']}")
    if parts:
        slots_summary = "Đã biết: " + ", ".join(parts)

    user_prompt = f"Tin nhắn khách: {user_text}"
    if history_summary:
        user_prompt += f"\n\nLịch sử gần nhất:\n{history_summary}"
    if slots_summary:
        user_prompt += f"\n\n{slots_summary}"

    try:
        plan, _ = await chat(
            system=_PLANNER_SYSTEM,
            user=user_prompt,
            alias="fast",
            temperature=0.0,
            max_tokens=200,
        )
        logger.info("planner plan={!r}", plan)
        return plan
    except Exception as e:
        logger.warning("planner failed err={} — skipping plan", e)
        return ""
