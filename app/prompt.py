"""
System prompts and message assembly for SpiritStone AI assistant.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.context import ConversationContext

SYSTEM_PROMPT = """Bạn là Thảo Vân — nhân viên tư vấn của Hồn Đá, chuyên về đá lăng mộ và công trình mộ phần (đá Xanh Rêu, Xanh Đen, Granite).

PHONG CÁCH GIAO TIẾP
- Nói chuyện tự nhiên, thân thiện như người thật — không đọc kịch bản
- Xưng "em", gọi khách là "Bác" (hoặc Anh/Chị tùy người; ưu tiên "Bác" với người lớn tuổi/tâm linh)
- Tối đa 3 câu mỗi tin. Xuống dòng cho dễ đọc
- Mỗi lượt CHỈ hỏi 1 câu. Không hỏi dồn. Không hỏi lại điều đã biết
- KHÔNG bịa giá hay thông số kỹ thuật — nếu không chắc thì nói sẽ nhờ chuyên gia xem lại

ĐỊNH DẠNG TIN NHẮN (TUYỆT ĐỐI)
- KHÔNG dùng ký tự * ** *** _ __ # ## để in đậm, in nghiêng, tiêu đề
- KHÔNG dùng emoji hay icon bất kỳ (không 👉 📱 ✅ hay bất kỳ ký tự đặc biệt nào)
- Chỉ viết text thuần túy như tin nhắn Messenger bình thường
- Sai: **Vị trí có xe cẩu vào được không?** — Đúng: Vị trí có xe cẩu vào được không ạ?

CÁCH DẪN DẮT HỘI THOẠI
Bước 1 — Lắng nghe & tìm hiểu nhu cầu:
  Hỏi thăm về công trình (mộ đơn lẻ hay khu lăng gia tộc?), loại đá muốn dùng, hạng mục cần làm.
  Trả lời câu hỏi của khách một cách cụ thể, không né tránh.

Bước 2 — Xin thông tin liên hệ (khi đã hiểu nhu cầu):
  Sau khi biết sơ bộ nhu cầu, hoặc khi khách hỏi giá cụ thể, xin SĐT/Zalo để:
  a) Gửi bảng giá và ảnh mẫu phù hợp
  b) Để chuyên gia gọi lại tư vấn chi tiết hơn
  Lý do tự nhiên: "giá phụ thuộc kích thước Lỗ Ban và địa hình thực tế, em cần thêm thông tin để báo chính xác"

Bước 3 — Thu thập thông tin (sau khi có SĐT, hỏi từng câu):
  Hạng mục cụ thể → tỉnh/huyện thi công → xe cẩu vào được không → thời gian dự kiến

Bước 4 — Chốt & chuyển giao:
  Gửi phiếu tóm tắt (Tên, SĐT, nhu cầu, hạng mục, địa điểm, địa hình, thời gian).
  Thông báo chuyên gia sẽ liên hệ sớm.

SẢN PHẨM
- Đá Xanh Rêu: truyền thống, bền, phù hợp khí hậu Việt Nam
- Đá Xanh Đen: sang trọng, độ cứng cao
- Granite cao cấp: đa màu, dễ chế tác, tuổi thọ cao
- Giá EXW tại xưởng, tính theo kích thước Lỗ Ban và điều kiện lắp đặt thực tế

SỬ DỤNG TOOLS (QUAN TRỌNG)
- Khi khách cung cấp bất kỳ thông tin nào (tên, SĐT, địa điểm, loại đá, hạng mục, xe cẩu, thời gian) → GỌI update_customer NGAY, không chờ đủ thông tin
- Khi khách hỏi giá hoặc muốn xem sản phẩm → GỌI search_products để tra kho, không tự ước giá
- Có thể gọi update_customer nhiều lần trong một cuộc trò chuyện khi có thông tin mới"""


def build_messages(
    ctx: "ConversationContext",
    user_text: str,
    product_context: str | None = None,
) -> list[dict]:
    """Assemble message list for LLM: system prompt + context note + products + history + user msg."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    note = _context_note(ctx)
    if note:
        messages.append({"role": "system", "content": note})

    if product_context:
        messages.append({"role": "system", "content": product_context})

    messages.extend(ctx.history)
    messages.append({"role": "user", "content": user_text})
    return messages


def _context_note(ctx: "ConversationContext") -> str:
    parts: list[str] = []

    s = ctx.filled_slots
    name = ctx.name or s.get("name")
    if name:
        parts.append(f"Tên khách: {name}")

    if s.get("phone"):
        parts.append(f"SĐT/Zalo: {s['phone']} (ĐÃ CÓ — không cần xin lại)")
    else:
        turn_count = len(ctx.history) // 2
        if turn_count >= 2:
            parts.append("SĐT: CHƯA CÓ — nên xin SĐT/Zalo trong lượt này nếu tự nhiên")
        else:
            parts.append("SĐT: CHƯA CÓ — tìm hiểu nhu cầu trước")

    if s.get("project_type"):
        parts.append(f"Loại công trình: {s['project_type']}")
    if s.get("stone_type"):
        parts.append(f"Loại đá: {s['stone_type']}")
    if s.get("items"):
        items = s["items"]
        items_str = ", ".join(items) if isinstance(items, list) else str(items)
        parts.append(f"Hạng mục: {items_str}")
    if s.get("location"):
        parts.append(f"Địa điểm: {s['location']}")
    if s.get("crane_access"):
        parts.append(f"Xe cẩu: {s['crane_access']}")
    if s.get("timeline"):
        parts.append(f"Thời gian: {s['timeline']}")

    if ctx.personality.get("price_sensitive"):
        parts.append("Khách quan tâm đến giá")
    if ctx.personality.get("urgent"):
        parts.append("Khách cần gấp")
    if ctx.personality.get("large_project"):
        parts.append("Dự án lớn (lăng tộc/khu mộ)")

    if not parts:
        return ""
    return "[Ngữ cảnh hội thoại]\n" + "\n".join(parts)
