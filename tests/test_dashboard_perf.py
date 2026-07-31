"""Trang admin không được nổ request theo số khách: crawl tên có cooldown, list psid có cache."""
import asyncio

import pytest

import config
import fb
import messenger


@pytest.fixture(autouse=True)
def _sach(monkeypatch):
    monkeypatch.setattr(config, "PAGE_TOKEN", "x")
    monkeypatch.setattr(messenger, "_NAME_CACHE", {})
    monkeypatch.setattr(messenger, "_NAMES_LOADED_AT", 0.0)
    fb.quen_cache_psids()


def test_50_khach_la_chi_crawl_ten_1_lan(monkeypatch):
    """Khách cũ không nằm trong 500 hội thoại gần nhất -> mãi không có tên. Không được vì thế
    mà mỗi psid kích một crawl (trang admin 50 dòng = 50 crawl x 5 trang = treo)."""
    dem = []

    async def fake_crawl():
        dem.append(1)

    monkeypatch.setattr(messenger, "_names_from_conversations", fake_crawl)

    async def chay():
        return [await messenger.profile_name(f"psid{i}") for i in range(50)]

    ten = asyncio.run(chay())
    assert dem == [1], f"phải crawl đúng 1 lần, thực tế {len(dem)}"
    assert ten == [""] * 50, "không tìm ra tên thì trả rỗng, không nổ lỗi"


def test_crawl_lai_sau_khi_het_cooldown(monkeypatch):
    dem = []

    async def fake_crawl():
        dem.append(1)

    monkeypatch.setattr(messenger, "_names_from_conversations", fake_crawl)
    asyncio.run(messenger.profile_name("a"))
    monkeypatch.setattr(messenger, "_NAMES_LOADED_AT",
                        messenger._NAMES_LOADED_AT - messenger._NAMES_COOLDOWN_S - 1)
    asyncio.run(messenger.profile_name("b"))
    assert len(dem) == 2


def test_ten_da_biet_thi_khong_crawl(monkeypatch):
    async def no(*a):
        raise AssertionError("đã có trong cache, không được gọi mạng")

    monkeypatch.setattr(messenger, "_names_from_conversations", no)
    messenger._cache_name("p1", "Nguyễn Văn A")
    assert asyncio.run(messenger.profile_name("p1")) == "Nguyễn Văn A"


def test_list_psids_dung_cache_va_bo_cache_khi_xoa(monkeypatch):
    dem = []

    def fake_init():
        dem.append(1)
        return False                                   # dừng ngay sau _init, đủ đếm lượt gọi thật

    monkeypatch.setattr(fb, "_init", fake_init)
    fb.list_psids()
    fb.list_psids()
    assert len(dem) == 1 or fb._PSIDS_CACHE is None    # _init=False -> không cache được, chấp nhận

    # có cache thì lần 2 không đụng tới _init
    monkeypatch.setattr(fb, "_PSIDS_CACHE", (__import__("time").time(), ["a", "b"]))
    truoc = len(dem)
    assert fb.list_psids() == ["a", "b"]
    assert len(dem) == truoc, "đang có cache còn hạn thì không được gọi Firebase"

    fb.quen_cache_psids()
    assert fb._PSIDS_CACHE is None
