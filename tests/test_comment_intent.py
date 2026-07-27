"""Self-check: chỉ nhắn RIÊNG khi comment có nhu cầu, còn lại chỉ cảm ơn công khai.
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


def test_intent_khong_goi_api():
    """Tầng rẻ: rỗng/icon -> False, từ khoá/SĐT -> True. Gọi API ở 2 ca này là phí tiền."""
    def _no_api(*a, **kw):
        raise AssertionError("không được gọi model ở tầng từ khoá")
    orig = brain._generate
    brain._generate = _no_api
    try:
        assert brain._comment_intent_sync("") is False
        assert brain._comment_intent_sync("👍👍❤️") is False
        assert brain._comment_intent_sync("giá bao nhiêu ạ") is True
        assert brain._comment_intent_sync("0912345678") is True
        assert brain._comment_intent_sync("ib mình với") is True
    finally:
        brain._generate = orig


def test_intent_hoi_model_va_fail_open():
    """Không khớp từ khoá -> hỏi model. Model lỗi -> True (thà nhắn thừa hơn mất khách)."""
    orig = brain._generate
    try:
        brain._generate = lambda *a, **kw: _fake_resp("KHONG")
        assert brain._comment_intent_sync("Công trình trông bề thế thật") is False

        brain._generate = lambda *a, **kw: _fake_resp("CO")
        assert brain._comment_intent_sync("Nhà mình ở Nam Định có nhận không") is True

        def _boom(*a, **kw):
            raise RuntimeError("API sập")
        brain._generate = _boom
        assert brain._comment_intent_sync("Công trình trông bề thế thật") is True
    finally:
        brain._generate = orig


def test_handle_comment_bo_qua_private_khi_khong_co_nhu_cau():
    """Comment khen suông: cảm ơn công khai, KHÔNG nhắn riêng (đỡ làm phiền + giữ quyền
    private reply, FB chỉ cho 1 lần/comment). Và không được báo động 'comment chết'."""
    calls = {"pub": 0, "priv": 0, "alert": 0}

    async def _pub(cid):
        calls["pub"] += 1
        return True

    async def _priv(cid):
        calls["priv"] += 1
        return True

    async def _alert(key, msg):
        calls["alert"] += 1

    orig = (messenger.reply_public, messenger.reply_private, messenger.alert_admins,
            messenger.stats.log_event, brain.comment_has_intent)
    messenger.reply_public, messenger.reply_private = _pub, _priv
    messenger.alert_admins = _alert
    messenger.stats.log_event = lambda *a, **kw: None
    try:
        async def _no_intent(text):
            return False
        brain.comment_has_intent = _no_intent
        asyncio.run(messenger.handle_comment("C1", "U1", "Đẹp quá"))
        assert calls == {"pub": 1, "priv": 0, "alert": 0}, calls

        async def _intent(text):
            return True
        brain.comment_has_intent = _intent
        asyncio.run(messenger.handle_comment("C2", "U1", "giá bao nhiêu"))
        assert calls == {"pub": 2, "priv": 1, "alert": 0}, calls

        # Public hỏng + không nhắn riêng = khách không nhận được gì -> phải cảnh báo.
        async def _pub_fail(cid):
            return False
        messenger.reply_public = _pub_fail
        brain.comment_has_intent = _no_intent
        asyncio.run(messenger.handle_comment("C3", "U1", "Đẹp quá"))
        assert calls["alert"] == 1, calls
    finally:
        (messenger.reply_public, messenger.reply_private, messenger.alert_admins,
         messenger.stats.log_event, brain.comment_has_intent) = orig


if __name__ == "__main__":
    test_parse_kem_noi_dung()
    test_intent_khong_goi_api()
    test_intent_hoi_model_va_fail_open()
    test_handle_comment_bo_qua_private_khi_khong_co_nhu_cau()
    print("OK - comment intent")
