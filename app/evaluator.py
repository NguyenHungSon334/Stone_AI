"""
Evaluator agent — checks if the generated response matches the customer's intent
and the planner's action plan. Returns (passed, feedback) for the retry loop.
"""
from __future__ import annotations

from loguru import logger

from app.llm import chat

_EVALUATOR_SYSTEM = """Bạn kiểm tra chất lượng câu trả lời của bot tư vấn đá lăng mộ Hồn Đá.

Đánh giá xem câu trả lời có phù hợp với yêu cầu khách và kế hoạch đã đặt ra không.

Trả lời ĐÚNG format sau — không giải thích thêm:
VERDICT: <PASS | FAIL>
REASON: <lý do ngắn gọn nếu FAIL, hoặc "OK" nếu PASS>
FIX: <hướng dẫn cụ thể để sửa nếu FAIL, hoặc "-" nếu PASS>

Tiêu chí FAIL (chỉ cần 1 là đủ):
- Kế hoạch yêu cầu goi_search_products nhưng bot không gợi ý sản phẩm cụ thể nào
- Bot hỏi thêm thông tin trong khi đã có đủ dữ liệu để trả lời/search
- Bot tự bịa giá hoặc thông số sản phẩm không có trong kết quả tool
- Câu trả lời hoàn toàn không liên quan đến yêu cầu khách
- Bot giải thích loại đá chung chung khi khách đã nêu rõ loại đá muốn mua

Tiêu chí PASS:
- Bot gợi ý sản phẩm cụ thể (có tên, kích thước, giá) khi plan yêu cầu search
- Bot trả lời đúng trọng tâm yêu cầu của khách
- Bot dùng thông tin từ tool result, không bịa"""


async def evaluate_response(
    user_text: str,
    plan: str,
    response: str,
    tools_called: list[str],
    tool_results_summary: str = "",
) -> tuple[bool, str]:
    """
    Evaluate the generated response.
    Returns (passed, feedback_for_retry).
    """
    prompt_parts = [f"Yêu cầu khách: {user_text}"]
    if plan:
        prompt_parts.append(f"Kế hoạch:\n{plan}")
    prompt_parts.append(f"Tools đã gọi: {tools_called or 'không có'}")
    if tool_results_summary:
        prompt_parts.append(f"Kết quả tool (tóm tắt): {tool_results_summary[:300]}")
    prompt_parts.append(f"Câu trả lời bot:\n{response}")

    try:
        verdict_text, _ = await chat(
            system=_EVALUATOR_SYSTEM,
            user="\n\n".join(prompt_parts),
            alias="fast",
            temperature=0.0,
            max_tokens=150,
        )

        passed = "VERDICT: PASS" in verdict_text
        feedback = ""
        if not passed:
            for line in verdict_text.splitlines():
                if line.startswith("FIX:"):
                    feedback = line[4:].strip()
                    break
            if not feedback:
                feedback = verdict_text

        logger.info("evaluator verdict={} feedback={!r}", "PASS" if passed else "FAIL", feedback)
        return passed, feedback

    except Exception as e:
        logger.warning("evaluator failed err={} — defaulting PASS", e)
        return True, ""
