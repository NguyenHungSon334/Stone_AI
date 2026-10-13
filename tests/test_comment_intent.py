"""Self-check: comment xếp nhóm theo bộ mẫu, trả lời/nhắn riêng/ẩn đúng nhóm.
Chạy: python tests/test_comment_intent.py"""
import asyncio
import os
import sys
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain
import messenger


def _fake_resp(text: str):
    """Giả cấu trúc resp của google-genai: candidates[0].content.parts[i].text"""
    part = pytypes.SimpleNamespace(text=text)
    content = pytypes.SimpleNamespace(parts=[part])
    return pytypes.SimpleNamespace(candidates=[pytypes.SimpleNamespace(content=content)])


def test_parse_kem_noi_dung():
    """Không có nội dung comment thì không phân loại được -> phải bóc kèm text."""
    import tempfile
    from pathlib import Path

    payload = {"object": "page", "entry": [{"id": "PAGE", "changes": [
        {"field": "feed", "value": {"item": "comment", "verb": "add", "comment_id": "CX1",
                                    "from": {"id": "U1"}, "message": "giá bao nhiêu vậy shop"}},
    ]}]}
    # Dedupe ghi ra đĩa và sống qua restart -> chạy test lần 2 sẽ bị coi là trùng. Tách state.
    orig = (messenger._SEEN_PATH, dict(messenger._SEEN_COMMENTS), messenger._seen_loaded)
    messenger._SEEN_PATH = Path(tempfile.mkdtemp()) / "_comments_seen.state"
    messenger._SEEN_COMMENTS.clear()
    messenger._seen_loaded = False
    try:
        assert messenger.parse_comment_events(payload) == [("CX1", "U1", "giá bao nhiêu vậy shop")]
    finally:
        messenger._SEEN_PATH, seen, messenger._seen_loaded = orig
        messenger._SEEN_COMMENTS.clear()
        messenger._SEEN_COMMENTS.update(seen)


def test_ma_khong_goi_api_khi_khong_co_chu():
    """Rỗng/chỉ icon -> E4 (im), không tốn lượt gọi model."""
    def _no_api(*a, **kw):
        raise AssertionError("không được gọi model khi comment không có chữ")
    orig = brain._generate
    brain._generate = _no_api
    try:
        assert brain._comment_ma_sync("") == "E4"
        assert brain._comment_ma_sync("👍👍❤️") == "E4"
    finally:
        brain._generate = orig


def test_ma_hoi_model_va_du_phong():
    """Model trả mã -> dùng mã. Mã lạ / lỗi -> A1 nếu có từ khoá nhu cầu, không thì C1."""
    orig = brain._generate
    try:
        brain._generate = lambda *a, **kw: _fake_resp("B1")
        assert brain._comment_ma_sync("Xưởng ở đâu vậy") == "B1"
        brain._generate = lambda *a, **kw: _fake_resp("Z9")
        assert brain._comment_ma_sync("Công trình trông bề thế thật") == "C1"

        def _boom(*a, **kw):
            raise RuntimeError("API sập")
        brain._generate = _boom
        assert brain._comment_ma_sync("giá bao nhiêu ạ") == "A1"
        assert brain._comment_ma_sync("Công trình trông bề thế thật") == "C1"
    finally:
        brain._generate = orig


def _chay_comment(ma: str, pub_ok: bool = True, priv_ok: bool = True) -> dict:
    """Chạy handle_comment với mã cố định, ghi lại kênh nào được gọi + câu công khai."""
    calls = {"pub": [], "priv": 0, "hide": 0, "alert": 0, "notify": 0}

    async def _pub(cid, msg):
        calls["pub"].append(msg)
        return pub_ok

    async def _priv(cid):
        calls["priv"] += 1
        return priv_ok

    async def _hide(cid):
        calls["hide"] += 1
        return True

    async def _alert(key, msg):
        calls["alert"] += 1

    async def _notify(msg):
        calls["notify"] += 1

    async def _ma(text):
        return ma

    names = ("reply_public", "reply_private", "hide_comment", "alert_admins", "notify_admins")
    orig = [getattr(messenger, n) for n in names] + [messenger.stats.log_event, brain.comment_ma]
    for n, f in zip(names, (_pub, _priv, _hide, _alert, _notify)):
        setattr(messenger, n, f)
    messenger.stats.log_event = lambda *a, **kw: None
    brain.comment_ma = _ma
    try:
        asyncio.run(messenger.handle_comment("C1", "U1", "x"))
    finally:
        for n, f in zip(names, orig):
            setattr(messenger, n, f)
        messenger.stats.log_event, brain.comment_ma = orig[-2], orig[-1]
    return calls


def test_handle_comment_theo_nhom():
    # Khen: trả lời công khai, KHÔNG nhắn riêng (đỡ làm phiền + giữ quyền private reply).
    c = _chay_comment("C1")
    assert len(c["pub"]) == 1 and c["priv"] == 0 and c["alert"] == 0, c
    # Hỏi giá: nhắn riêng + câu "đã nhắn riêng".
    c = _chay_comment("A5")
    assert c["priv"] == 1 and "đã nhắn tin" in c["pub"][0], c
    # Nhắn riêng hỏng -> KHÔNG được nói "đã nhắn riêng", đổi sang mời nhắn trang.
    c = _chay_comment("A5", priv_ok=False)
    assert "nhắn trang" in c["pub"][0] and "đã nhắn" not in c["pub"][0], c
    # Lộ SĐT: báo người thật, trả lời A4, rồi ẩn.
    c = _chay_comment("A4")
    assert c["notify"] == 1 and c["hide"] == 1 and len(c["pub"]) == 1, c
    # Chửi: ẩn, không trả lời, không phải sự cố.
    c = _chay_comment("E1")
    assert c["pub"] == [] and c["priv"] == 0 and c["hide"] == 1 and c["alert"] == 0, c
    # Chỉ gắn tên: im hẳn.
    c = _chay_comment("E4")
    assert c["pub"] == [] and c["hide"] == 0 and c["alert"] == 0, c
    # Public hỏng + không nhắn riêng = khách không nhận được gì -> phải cảnh báo.
    c = _chay_comment("C1", pub_ok=False)
    assert c["alert"] == 1, c


if __name__ == "__main__":
    test_parse_kem_noi_dung()
    test_ma_khong_goi_api_khi_khong_co_chu()
    test_ma_hoi_model_va_du_phong()
    test_handle_comment_theo_nhom()
    print("OK - comment theo nhóm")
