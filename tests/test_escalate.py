"""
Unit tests for escalation detection logic.
LLM calls are mocked — tests cover fast-path heuristics and AI intent routing.
"""
import pytest
from unittest.mock import AsyncMock, patch
from app.context import ConversationContext
from app.tools.escalate import should_escalate


def _ctx(**kwargs) -> ConversationContext:
    return ConversationContext(messenger_user_id="u1", **kwargs)


def _history(*texts: str) -> list[dict]:
    result = []
    for t in texts:
        result.append({"role": "user", "content": t})
        result.append({"role": "assistant", "content": "Dạ..."})
    return result


def _mock_chat(response: str):
    """Return a context manager that patches llm.chat to return `response`."""
    return patch(
        "app.tools.escalate.chat",
        new=AsyncMock(return_value=(response, 0.0001)),
    )


# ---------------------------------------------------------------------------
# Fast-path: already escalated (no LLM needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_escalated_flag():
    assert await should_escalate("hello", _ctx(is_escalated=True)) is True


# ---------------------------------------------------------------------------
# Fast-path: anger punctuation (no LLM needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_four_exclamations_trigger():
    with _mock_chat("normal"):  # LLM should not be reached but mock for safety
        assert await should_escalate("sai rồi!!!!", _ctx()) is True


@pytest.mark.asyncio
async def test_three_exclamations_ok_falls_through_to_ai():
    with _mock_chat("normal"):
        assert await should_escalate("ôi không!!!", _ctx()) is False


@pytest.mark.asyncio
async def test_four_questions_trigger():
    with _mock_chat("normal"):
        assert await should_escalate("sao vậy????", _ctx()) is True


# ---------------------------------------------------------------------------
# Fast-path: message repetition (no LLM needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_message_triggers():
    ctx = _ctx(history=_history("giá bao nhiêu", "giá bao nhiêu"))
    with _mock_chat("normal"):
        assert await should_escalate("giá bao nhiêu", ctx) is True


@pytest.mark.asyncio
async def test_repeated_message_once_ok():
    ctx = _ctx(history=_history("giá bao nhiêu"))
    with _mock_chat("normal"):
        assert await should_escalate("giá bao nhiêu", ctx) is False


@pytest.mark.asyncio
async def test_repetition_case_insensitive():
    ctx = _ctx(history=[
        {"role": "user", "content": "Giá Bao Nhiêu"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "giá bao nhiêu"},
        {"role": "assistant", "content": "..."},
    ])
    with _mock_chat("normal"):
        assert await should_escalate("giá bao nhiêu", ctx) is True


# ---------------------------------------------------------------------------
# AI intent: explicit human request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_detects_human_request():
    with _mock_chat("escalate"):
        assert await should_escalate("tôi muốn gặp người thật ngay", _ctx()) is True


@pytest.mark.asyncio
async def test_ai_detects_manager_request():
    with _mock_chat("escalate"):
        assert await should_escalate("cho tôi gặp manager", _ctx()) is True


@pytest.mark.asyncio
async def test_ai_detects_complaint():
    with _mock_chat("escalate"):
        assert await should_escalate("tôi muốn khiếu nại", _ctx()) is True


# ---------------------------------------------------------------------------
# AI intent: normal messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_normal_product_query():
    with _mock_chat("normal"):
        assert await should_escalate("cho tôi xem giá đá obsidian", _ctx()) is False


@pytest.mark.asyncio
async def test_ai_normal_greeting():
    with _mock_chat("normal"):
        assert await should_escalate("xin chào", _ctx()) is False


# ---------------------------------------------------------------------------
# AI intent: frustration (now handled by AI, not hardcoded keywords)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_detects_frustration():
    with _mock_chat("escalate"):
        assert await should_escalate("vô dụng tệ quá không giúp được gì", _ctx()) is True


@pytest.mark.asyncio
async def test_ai_single_frustration_no_escalate():
    with _mock_chat("normal"):
        assert await should_escalate("tôi tức quá", _ctx()) is False


# ---------------------------------------------------------------------------
# LLM failure fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_failure_returns_false():
    with patch("app.tools.escalate.chat", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        assert await should_escalate("tôi muốn gặp người", _ctx()) is False
