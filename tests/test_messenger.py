"""Self-check phần thuần logic: chữ ký HMAC (security) + bóc sự kiện + gộp cảnh báo.
Chạy: python tests/test_messenger.py"""
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MSGR_APP_SECRET"] = "s3cret"
os.environ["MSGR_VERIFY_TOKEN"] = "vtok"

import config
config.APP_SECRET = "s3cret"        # config đã load trước khi set env ở trên nên gán lại
config.VERIFY_TOKEN = "vtok"
import alerts
import messenger


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()


def test_signature():
    body = b'{"object":"page"}'
    assert messenger.verify_signature(body, _sig(body)) is True
    assert messenger.verify_signature(body, _sig(b"tampered")) is False   # sai chữ ký
    assert messenger.verify_signature(body, "") is False                   # thiếu header
    assert messenger.verify_signature(body, "sha256=deadbeef") is False    # rác


def test_verify_webhook():
    assert messenger.verify_webhook("subscribe", "vtok", "42") == "42"
    assert messenger.verify_webhook("subscribe", "wrong", "42") is None


def test_parse_events():
    payload = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": "123"}, "message": {"text": "hi"}},
        {"sender": {"id": "123"}, "message": {"text": "echo", "is_echo": True}},
        {"sender": {"id": "9"}, "message": {}},
    ]}]}
    assert messenger.parse_events(payload) == [("123", "hi")]
    assert messenger.parse_events({"object": "user"}) == []


def test_alert_throttle():
    """Cùng key: bắn 1 lần rồi im; hết cửa sổ bắn lại KÈM số lần dồn. Key khác không ảnh hưởng.

    Gộp hỏng = 1 sự cố (token chết) đẻ ra 1 tin/khách -> admin tắt thông báo, mất cảnh báo."""
    sent: list[str] = []
    orig_notify = alerts.notify
    alerts.notify = sent.append
    alerts._ALERTS.clear()
    try:
        for _ in range(3):
            alerts.alert("fb:send:400", "token chết")
        assert len(sent) == 1, f"phải gộp còn 1, thực tế {len(sent)}"

        alerts.alert("brain:ClientError", "hết quota")
        assert len(sent) == 2, "key khác phải bắn riêng"

        alerts._ALERTS["fb:send:400"]["until"] = 0.0    # ép cửa sổ đã hết
        alerts.alert("fb:send:400", "token chết")
        assert len(sent) == 3, "hết cửa sổ phải bắn lại"
        assert "+2 lần nữa" in sent[2], f"phải kèm số dồn, thực tế: {sent[2]}"
    finally:
        alerts.notify = orig_notify
        alerts._ALERTS.clear()


def test_pick_unanswered():
    """Lưới tin rơi: chỉ nhặt thread mà KHÁCH nhắn cuối và đã quá ngưỡng chờ.

    Sai hướng nào cũng hỏng: bỏ sót = mất khách; nhặt thừa = admin tắt thông báo."""
    from datetime import datetime, timedelta, timezone

    PAGE = "604876159375634"
    now = datetime.now(timezone.utc)

    def thread(sender_id, name, text, mins_ago):
        at = (now - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        return {"messages": {"data": [{"from": {"id": sender_id, "name": name},
                                       "message": text, "created_time": at}]}}

    threads = [
        thread("KH1", "Hùng", "xin giá", 60),          # khách chờ 60 phút -> PHẢI nhặt
        thread(PAGE, "Metory", "dạ em chào", 60),      # page trả lời cuối -> bỏ
        thread("KH2", "Linh", "alo", 2),               # mới 2 phút, bot đang xử lý -> bỏ
        thread("KH3", "Cũ", "lâu rồi", 10 * 24 * 60),  # 10 ngày, quá hạn đào lại -> bỏ
        {"messages": {"data": []}},                    # thread rỗng -> không được nổ
    ]
    got = messenger.pick_unanswered(threads, PAGE, after_min=10, now=now)
    assert [r[0] for r in got] == ["KH1"], f"chỉ KH1 mới là tin rơi, thực tế: {[r[0] for r in got]}"
    assert got[0][2] == "xin giá", f"phải kèm nội dung tin khách, thực tế: {got[0][2]}"


def test_comment_dedupe_survives_restart():
    """Dedupe comment phải sống qua restart.

    Chỉ giữ RAM thì FB gửi lại event sau deploy -> bot trả lời CÔNG KHAI lần 2 dưới comment
    khách (private reply được FB chặn #10900, public thì không). Khách nhìn thấy."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    orig_path, orig_seen, orig_loaded = messenger._SEEN_PATH, dict(messenger._SEEN_COMMENTS), messenger._seen_loaded
    messenger._SEEN_PATH = tmp / "_comments_seen.json"
    messenger._SEEN_COMMENTS.clear()
    messenger._seen_loaded = False
    try:
        assert messenger._comment_seen("C1") is False, "comment mới phải được xử lý"
        assert messenger._comment_seen("C1") is True, "cùng comment lần 2 phải bỏ qua"

        messenger._SEEN_COMMENTS.clear()          # giả lập restart: RAM trắng
        messenger._seen_loaded = False
        assert messenger._comment_seen("C1") is True, "sau restart vẫn phải nhớ -> không reply trùng"
        assert messenger._comment_seen("C2") is False, "comment mới sau restart vẫn phải xử lý"
    finally:
        messenger._SEEN_PATH = orig_path
        messenger._SEEN_COMMENTS.clear()
        messenger._SEEN_COMMENTS.update(orig_seen)
        messenger._seen_loaded = orig_loaded


def test_trim_resend():
    """Trả lời bù không được nhân đôi lượt khách trong lịch sử.

    Nhân đôi -> prompt thấy khách hỏi 2 lần -> bot trả lời kiểu 'dạ em đã nói ở trên'."""
    import brain

    da_co = [{"role": "user", "content": "xin giá", "at": "2026-07-20 10:00:00"}]
    full, at = brain.trim_resend(da_co, "xin giá", "2026-07-20 14:00:00")
    assert full == [], "tin trùng ở cuối phải được bỏ ra"
    assert at == "2026-07-20 10:00:00", "phải giữ mốc GỐC khách gửi, không phải lúc trả bù"

    # Bot đã trả lời rồi, khách hỏi lại đúng câu đó -> KHÔNG được cắt (là lượt mới thật)
    da_tra = [{"role": "user", "content": "xin giá", "at": "10:00"},
              {"role": "assistant", "content": "dạ", "at": "10:01"}]
    full, at = brain.trim_resend(da_tra, "xin giá", "14:00")
    assert full == da_tra and at == "14:00", "lượt mới thật thì giữ nguyên lịch sử"

    assert brain.trim_resend([], "hi", "14:00") == ([], "14:00"), "lịch sử rỗng không được nổ"


def test_khong_tra_loi_bu_khi_da_co_reply():
    """Chốt cuối trước khi trả lời bù: đã có câu trả lời SAU tin đó thì thôi.

    Thiếu chốt này thì người thật vừa rep tay xong, 15 phút sau bot rep đè lên - khách nhận
    2 câu trả lời cho 1 câu hỏi, người thật thì mất công."""
    import json, tempfile
    from datetime import datetime, timedelta
    from pathlib import Path
    import brain

    tmp = Path(tempfile.mkdtemp())
    orig = brain._HIST_DIR
    brain._HIST_DIR = tmp
    try:
        def iso(m):
            return (datetime.now().astimezone() - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S%z")

        def loc(m):
            return (datetime.now() - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")

        def conv(name, msgs):
            (tmp / f"{name}.json").write_text(json.dumps(msgs), encoding="utf-8")

        conv("A", [{"role": "user", "content": "xin giá", "at": loc(60)},
                   {"role": "assistant", "content": "dạ", "at": loc(50)}])
        assert messenger._da_tra_loi_sau("A", iso(60)) is True, "đã trả lời rồi thì KHÔNG bù nữa"

        conv("B", [{"role": "user", "content": "xin giá", "at": loc(60)}])
        assert messenger._da_tra_loi_sau("B", iso(60)) is False, "chưa ai trả lời thì phải bù"

        conv("C", [{"role": "assistant", "content": "dạ", "at": loc(120)},
                   {"role": "user", "content": "còn hàng không", "at": loc(30)}])
        assert messenger._da_tra_loi_sau("C", iso(30)) is False, "reply cũ hơn tin mới -> vẫn phải bù"
    finally:
        brain._HIST_DIR = orig


def test_merge_bo_tin_trung():
    """Webhook và luồng trả lời bù có thể cùng đẩy 1 tin vào buffer -> phải gộp còn 1.

    Không lọc thì prompt là 'xin giá\\nxin giá', bot tưởng khách hỏi 2 lần."""
    assert messenger._merge_texts(["xin giá", "xin giá"]) == "xin giá"
    assert messenger._merge_texts(["xin giá", "còn hàng"]) == "xin giá\ncòn hàng"


if __name__ == "__main__":
    test_signature()
    test_verify_webhook()
    test_parse_events()
    test_alert_throttle()
    test_pick_unanswered()
    test_comment_dedupe_survives_restart()
    test_trim_resend()
    test_khong_tra_loi_bu_khi_da_co_reply()
    test_merge_bo_tin_trung()
    print("OK - all messenger self-checks passed")
