"""Firebase chậm KHÔNG được kéo cả bot đứng.

Sự cố thật 30/07/2026: state/stats đọc Firebase ĐỒNG BỘ ngay trong coroutine -> event loop
đứng, 3 webhook FB bị reset sau 13.7s/9.4s/3.0s, /admin treo 173s, khách mất tin, phải
restart mới sống lại. Cảnh báo lúc đó ghi "TUNNEL CHẾT" nên đi soi nhầm ngrok/Caddy.

Test đo đúng thứ đã hỏng: trong lúc một đường chạm Firebase đang chờ, event loop có còn
chạy tiếp việc khác không. Chặn ở đây rẻ hơn nhiều so với phát hiện qua log Caddy.
Chạy: python -m pytest tests/test_event_loop_khong_bi_chan.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MSGR_APP_SECRET", "s3cret")

import config
import fb
import messenger
import state

_FB_CHAM_S = 0.6      # giả lập Firebase ì (thật thì tới 10s+)
_NGUONG_S = 0.2       # event loop được phép nghẽn tối đa ngần này


async def _do_nghen(chay) -> tuple[float, object]:
    """Chạy `chay()` song song với 1 nhịp tick 10ms. Trả (lần nghẽn dài nhất, kết quả)."""
    nghen = 0.0

    async def tick():
        nonlocal nghen
        while True:
            t = time.monotonic()
            await asyncio.sleep(0.01)
            nghen = max(nghen, time.monotonic() - t)

    t = asyncio.create_task(tick())
    await asyncio.sleep(0.05)     # cho tick chạy TRƯỚC: create_task chưa chạy gì cho tới lần
    nghen = 0.0                   # await kế tiếp, thiếu bước này thì code chặn đo ra 0.00s
    try:
        ket_qua = await chay()
        await asyncio.sleep(0.05)   # cho tick TỈNH LẠI để ghi nhận đoạn vừa bị nghẽn; cancel
    finally:                        # ngay sau chay() thì lần nghẽn dài nhất không ai đo được
        t.cancel()
    return nghen, ket_qua


def _fb_cham(monkeypatch):
    """Mọi đường đọc Firebase đều ì. Cache local phải MISS thì mới chạm tới nó."""
    def cham(*a, **kw):
        time.sleep(_FB_CHAM_S)
        return None
    monkeypatch.setattr(fb, "fetch_state", cham)
    monkeypatch.setattr(fb, "merge_state", lambda *a, **kw: None)
    monkeypatch.setattr(state, "_ghi_cache", lambda *a, **kw: None)   # ép miss mãi


def test_send_text_khong_chan_event_loop(monkeypatch, tmp_path):
    """send_text -> state.bi_khoa -> Firebase. Chờ thì chờ, nhưng bot phải vẫn nhận việc khác."""
    _fb_cham(monkeypatch)
    monkeypatch.setattr(state, "_DIR", tmp_path)          # không có cache -> luôn hỏi Firebase
    monkeypatch.setattr(config, "PAGE_TOKEN", "")         # dừng ngay sau bước kiểm tra khoá

    async def chay():
        return await messenger.send_text("psid-test", "xin chào")

    nghen, _ = asyncio.run(_do_nghen(chay))
    assert nghen < _NGUONG_S, (
        f"event loop bị chặn {nghen:.2f}s khi gửi tin - state/Firebase đang chạy thẳng trong "
        f"coroutine. Phải bọc messenger._off / asyncio.to_thread.")


def test_route_dashboard_khong_chan_event_loop():
    """Route admin chạm Firebase phải là `def` (FastAPI tự đẩy sang threadpool), không `async def`.

    Đây là ca đã gây sự cố: dashboard tự làm mới nền, mỗi lượt kéo cả kho stats về."""
    import inspect

    import admin
    phai_dong_bo = ("overview", "get_settings", "get_config", "delete_customer",
                    "mo_khoa_khach", "clean_data")
    for ten in phai_dong_bo:
        fn = getattr(admin, ten)
        assert not inspect.iscoroutinefunction(fn), (
            f"admin.{ten} là async def mà bên trong đọc Firebase/đĩa đồng bộ -> khoá event loop. "
            f"Để `def` cho FastAPI chạy ở threadpool, hoặc bọc asyncio.to_thread.")


def test_fb_treo_khong_cho_mai(monkeypatch):
    """Refresh OAuth của firebase_admin không đặt được timeout -> phải có trần chờ ở fb._doc."""
    monkeypatch.setattr(fb, "_READ_TIMEOUT_S", 0.3)
    monkeypatch.setattr(fb.alerts, "alert", lambda *a, **kw: None)

    def treo():
        time.sleep(30)
        return "khong-bao-gio-toi"

    t = time.monotonic()
    assert fb._doc(treo, "test treo", default="local") == "local"
    assert time.monotonic() - t < 5, "fb._doc không cắt được lời gọi treo"


def test_ghi_firebase_khong_de_thread_vo_han():
    """_run phải dùng pool cố định. Bản cũ đẻ 1 thread/lần ghi -> burst tin là hàng trăm thread."""
    assert fb._POOL._max_workers <= 8, "pool ghi Firebase quá lớn/không giới hạn"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
