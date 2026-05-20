"""
Unit tests for product search utilities.
Pure functions — no network, no DB.
"""
import pytest
from app.tools.search import _parse_budget, _best_price, format_results, format_products_for_llm


# ---------------------------------------------------------------------------
# _parse_budget
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2tr", 2_000_000),
    ("1.5triệu", 1_500_000),
    ("2triệu", 2_000_000),
    ("500k", 500_000),
    ("300nghìn", 300_000),
    ("300ngàn", 300_000),
    ("800000", 800_000),
    ("1200000", 1_200_000),
    (None, None),
    ("", None),
    ("giá tốt", None),
])
def test_parse_budget(text, expected):
    assert _parse_budget(text) == expected


def test_parse_budget_with_spaces():
    assert _parse_budget("2 tr") == 2_000_000


def test_parse_budget_uppercase_k():
    assert _parse_budget("500K") == 500_000


# ---------------------------------------------------------------------------
# _best_price
# ---------------------------------------------------------------------------

def _product(**kwargs) -> dict:
    base = {
        "gia_da_xanh_den": None,
        "gia_da_xanh_reu": None,
        "gia_da_xam_bd": None,
        "gia_da_grn_an_do": None,
    }
    base.update(kwargs)
    return base


def test_best_price_picks_minimum():
    p = _product(gia_da_xanh_den=1_000_000, gia_da_xanh_reu=500_000)
    assert _best_price(p) == 500_000


def test_best_price_single_price():
    p = _product(gia_da_xam_bd=750_000)
    assert _best_price(p) == 750_000


def test_best_price_all_none():
    p = _product()
    assert _best_price(p) is None


def test_best_price_ignores_zero():
    # 0 is falsy — treated as no price
    p = _product(gia_da_xanh_den=0, gia_da_xanh_reu=200_000)
    assert _best_price(p) == 200_000


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------

def _make_product(name: str, price: int = 300_000, desc: str = "") -> dict:
    return {
        "ten_sp": name,
        "kich_thuoc": "8mm",
        "gia_da_xanh_den": price,
        "gia_da_xanh_reu": None,
        "gia_da_xam_bd": None,
        "gia_da_grn_an_do": None,
        "mo_ta": desc,
        "ghi_chu": None,
    }


def test_format_results_empty():
    result = format_results([])
    assert "chưa tìm thấy" in result


def test_format_results_shows_name_and_price():
    products = [_make_product("Đá Obsidian", 250_000)]
    result = format_results(products)
    assert "Đá Obsidian" in result
    assert "250,000" in result


def test_format_results_shows_size():
    products = [_make_product("Đá Thạch Anh")]
    result = format_results(products)
    assert "8mm" in result


def test_format_results_caps_at_three():
    products = [_make_product(f"SP{i}") for i in range(5)]
    result = format_results(products)
    assert "SP3" not in result
    assert "SP4" not in result
    assert "2 sản phẩm khác" in result


def test_format_results_no_overflow_message_for_three():
    products = [_make_product(f"SP{i}") for i in range(3)]
    result = format_results(products)
    assert "sản phẩm khác" not in result


def test_format_results_truncates_long_description():
    long_desc = "X" * 200
    products = [_make_product("SP1", desc=long_desc)]
    result = format_results(products)
    assert "…" in result


# ---------------------------------------------------------------------------
# format_products_for_llm
# ---------------------------------------------------------------------------

def test_format_for_llm_empty():
    result = format_products_for_llm([])
    assert "Không tìm thấy" in result


def test_format_for_llm_contains_name():
    products = [_make_product("Đá Lapis Lazuli", 500_000)]
    result = format_products_for_llm(products)
    assert "Đá Lapis Lazuli" in result
    assert "500,000" in result


def test_format_for_llm_caps_at_five():
    products = [_make_product(f"SP{i}") for i in range(7)]
    result = format_products_for_llm(products)
    assert "SP5" not in result
    assert "SP6" not in result
