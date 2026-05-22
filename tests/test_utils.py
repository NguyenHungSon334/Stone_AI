"""
Unit tests for pure utility functions:
  - search._parse_size_mm, _resolve_the_loai, _resolve_stone_col, _extract_keywords
  - orchestrator._pick_alias, _update_personality
  - messenger._split_message
"""
import pytest

from app.tools.search import _parse_size_mm, _resolve_the_loai, _resolve_stone_col, _extract_keywords
from app.orchestrator import _pick_alias
from app.messenger import _split_message


# ---------------------------------------------------------------------------
# _parse_size_mm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1200mm", 1200),
    ("1.2m", 1200),
    ("120cm", 1200),
    ("800", 800),
    ("2.5m", 2500),
    (None, None),
    ("", None),
])
def test_parse_size_mm(raw, expected):
    assert _parse_size_mm(raw) == expected


# ---------------------------------------------------------------------------
# _resolve_the_loai
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("mộ", "Mộ"),
    ("mo", "Mộ"),
    ("lăng", "Long đình"),
    ("cổng", "Cổng"),
    ("cong", "Cổng"),
    ("hàng rào", "Hàng rào"),
    ("cuốn thư", "Cuốn thư"),
    ("tam sơn", "Tam sơn"),
    ("không rõ", None),
    (None, None),
])
def test_resolve_the_loai(raw, expected):
    slots = {"project_type": raw} if raw is not None else {}
    assert _resolve_the_loai(slots) == expected


# ---------------------------------------------------------------------------
# _resolve_stone_col
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_col", [
    ("xanh đen", "gia_da_xanh_den"),
    ("đen", "gia_da_xanh_den"),
    ("xanh rêu", "gia_da_xanh_reu"),
    ("xanh", "gia_da_xanh_reu"),
    ("xám", "gia_da_xam_bd"),
    ("granite", "gia_da_grn_an_do"),
    ("ấn độ", "gia_da_grn_an_do"),
    ("không rõ", None),
    (None, None),
])
def test_resolve_stone_col(raw, expected_col):
    slots = {"stone_type": raw} if raw is not None else {}
    assert _resolve_stone_col(slots) == expected_col


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------

def test_extract_keywords_filters_stopwords():
    kws = _extract_keywords("cho tôi xem giá mộ đôi")
    assert "tôi" not in kws
    assert "cho" not in kws
    assert "giá" not in kws  # in stopwords


def test_extract_keywords_max_four():
    text = "mộ lăng cổng rào bàn thờ granite"
    kws = _extract_keywords(text)
    assert len(kws) <= 4


def test_extract_keywords_min_length():
    kws = _extract_keywords("a b c đá mộ")
    assert "a" not in kws
    assert "b" not in kws
    assert "c" not in kws


def test_extract_keywords_strips_punctuation():
    kws = _extract_keywords("mộ, lăng? cổng!")
    for kw in kws:
        assert kw not in {",", "?", "!"}


# ---------------------------------------------------------------------------
# _pick_alias
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,alias", [
    ("ok", "fast"),
    ("vâng", "fast"),
    ("cảm ơn", "fast"),
    ("hi", "fast"),
    ("giá mộ đôi granite bao nhiêu", "smart"),
    ("cho tôi xem sản phẩm", "smart"),
    ("3tr", "smart"),   # price with suffix → smart
    ("1.5triệu", "smart"),
    ("tôi muốn biết giá đá xanh đen", "smart"),
])
def test_pick_alias(text, alias):
    assert _pick_alias(text) == alias


def test_pick_alias_short_no_keywords_is_fast():
    assert _pick_alias("xem thử") == "fast"


def test_pick_alias_short_with_digit_is_smart():
    assert _pick_alias("2 mộ") == "smart"


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------

def test_split_message_short():
    assert _split_message("hello") == ["hello"]


def test_split_message_exact_limit():
    text = "a" * 1900
    assert _split_message(text) == [text]


def test_split_message_splits_long():
    text = "a" * 1901
    chunks = _split_message(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1900


def test_split_message_prefers_newline():
    text = "aaa\n" + "b" * 1900
    chunks = _split_message(text)
    assert chunks[0] == "aaa"


def test_split_message_reassembly():
    text = ("word " * 400).strip()
    chunks = _split_message(text)
    reassembled = " ".join(chunks)
    assert reassembled == text
