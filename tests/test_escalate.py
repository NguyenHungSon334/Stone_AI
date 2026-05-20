"""
Unit tests for escalation detection logic.
All tests are pure Python — no external dependencies.
"""
import pytest
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


# ---------------------------------------------------------------------------
# Explicit human request
# ---------------------------------------------------------------------------

def test_explicit_human_request_triggers():
    assert should_escalate("tôi muốn gặp người thật ngay", _ctx()) is True


def test_human_request_keyword_manager():
    assert should_escalate("cho tôi gặp manager", _ctx()) is True


def test_human_request_keyword_khieu_nai():
    assert should_escalate("tôi muốn khiếu nại", _ctx()) is True


def test_normal_message_no_escalation():
    assert should_escalate("cho tôi xem giá đá obsidian", _ctx()) is False


# ---------------------------------------------------------------------------
# Already escalated stays escalated
# ---------------------------------------------------------------------------

def test_already_escalated_flag():
    assert should_escalate("hello", _ctx(is_escalated=True)) is True


def test_not_escalated_with_normal_text():
    assert should_escalate("xin chào", _ctx(is_escalated=False)) is False


# ---------------------------------------------------------------------------
# Frustration keywords
# ---------------------------------------------------------------------------

def test_single_frustration_keyword_ok():
    # one keyword is not enough
    assert should_escalate("tôi tức quá", _ctx()) is False


def test_two_frustration_keywords_trigger():
    assert should_escalate("vô dụng tệ quá không giúp được gì", _ctx()) is True


def test_two_frustration_keywords_different_words():
    assert should_escalate("sai hết rồi tức bực lắm", _ctx()) is True


# ---------------------------------------------------------------------------
# Anger punctuation
# ---------------------------------------------------------------------------

def test_four_exclamations_trigger():
    assert should_escalate("sai rồi!!!!", _ctx()) is True


def test_three_exclamations_ok():
    assert should_escalate("ôi không!!!", _ctx()) is False


def test_four_questions_trigger():
    assert should_escalate("sao vậy????", _ctx()) is True


# ---------------------------------------------------------------------------
# Message repetition
# ---------------------------------------------------------------------------

def test_repeated_message_triggers():
    ctx = _ctx(history=_history("giá bao nhiêu", "giá bao nhiêu"))
    assert should_escalate("giá bao nhiêu", ctx) is True


def test_repeated_message_once_ok():
    ctx = _ctx(history=_history("giá bao nhiêu"))
    assert should_escalate("giá bao nhiêu", ctx) is False


def test_repetition_case_insensitive():
    ctx = _ctx(history=[
        {"role": "user", "content": "Giá Bao Nhiêu"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "giá bao nhiêu"},
        {"role": "assistant", "content": "..."},
    ])
    assert should_escalate("giá bao nhiêu", ctx) is True
