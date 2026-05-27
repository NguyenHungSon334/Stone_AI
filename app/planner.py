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
TOOLS: <danh sách tên tool thực cần gọi, phân cách bằng dấu phẩy, hoặc "none">
ARGS: <tham số cụ thể cho từng tool: query, project_type, stone_type, ma_sp, v.v.>
HUONG_TRA_LOI: <tập trung gợi ý điểm gì, câu hỏi tiếp theo nên hỏi gì>

Tên tools thực tế (dùng đúng tên này):
- search_products: khách nêu loại sản phẩm (mộ tròn, cổng...) hoặc hỏi giá/mẫu/so sánh
- get_product_detail: khách hỏi chi tiết 1 sản phẩm cụ thể (đã biết mã SP)
- get_media: khách muốn xem ảnh/hình mẫu/video. Nếu chưa có mã SP thì dùng search_products trước, sau đó get_media
- update_customer: khách cung cấp tên, SĐT, địa điểm, loại đá, hạng mục
- none: câu hỏi chung về đá/quy trình không cần tra kho, hoặc chưa đủ thông tin để search

Ví dụ:
Khách: "tôi muốn mua mộ tròn đá xanh đen"
INTENT: mua_san_pham
SAN_PHAM: mộ tròn, đá xanh đen
TOOLS: search_products
ARGS: query="mộ tròn đá xanh đen", project_type="mộ tròn", stone_type="xanh đen"
HUONG_TRA_LOI: gợi ý sản phẩm mộ tròn xanh đen cụ thể, hỏi thêm kích thước hoặc địa điểm

Khách: "cho tôi xem ảnh mẫu"
INTENT: xem_anh_video
SAN_PHAM: chưa rõ
TOOLS: search_products, get_media
ARGS: query="sản phẩm đá lăng mộ", ma_sp=(dùng mã SP từ kết quả search)
HUONG_TRA_LOI: tìm sản phẩm phù hợp với ngữ cảnh, gửi ảnh ngay"""


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
