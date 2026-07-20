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


def test_moc_goc_khi_tra_loi_bu():
    """Trả lời bù phải ghi lịch sử theo mốc khách gửi THẬT, không phải lúc bù.

    Ghi lệch -> bot đọc prompt tưởng khách vừa nhắn, không biết đã bỏ khách cả tiếng; follow-up
    và mọi thứ tính theo mốc này cũng trễ theo."""
    import asyncio
    from datetime import datetime

    goc = "2026-07-20T03:12:28+0000"
    mong_doi = (datetime.strptime(goc, "%Y-%m-%dT%H:%M:%S%z")
                .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    assert messenger._fb_time_to_local(goc) == mong_doi
    assert messenger._fb_time_to_local("rác") is None, "mốc hỏng không được nổ"

    # Mốc phải đi hết chặng handle_event -> buffer -> _process (chỗ gọi brain.answer).
    thay = {}

    async def gia_lap():
        async def bat(psid, text, at=None):
            thay.update(psid=psid, text=text, at=at)

        orig = messenger._process
        messenger._process = bat
        try:
            await messenger.handle_event("PSID1", "xin giá", mong_doi)
            await asyncio.sleep(messenger._DEBOUNCE_S + 0.5)
        finally:
            messenger._process = orig
            messenger._BUFFERS.pop("PSID1", None)

    asyncio.run(gia_lap())
    assert thay.get("at") == mong_doi, f"mốc gốc phải tới _process, nhận {thay.get('at')!r}"


def test_ai_quyet_gui_anh():
    """AI đánh dấu <<ANH>> -> gửi lại cả mã ĐÃ gửi. Không đánh dấu -> chỉ mã nhắc lần đầu.

    Bản cũ dò tin khách bằng regex liệt kê cụm nên 'kèm ảnh' trượt -> bot liệt kê 6 mã, gửi 0 ảnh.
    Marker phải bị bóc sạch: khách KHÔNG được thấy, lịch sử KHÔNG được lưu."""
    import brain

    assert brain._wants_image("Dạ em gửi Bác mẫu M01 ạ <<ANH>>")
    assert brain._wants_image("mẫu M01 << anh >>"), "khoảng trắng/hoa thường vẫn phải nhận"
    assert not brain._wants_image("Dạ mẫu M01 giá 33.9 triệu ạ")

    assert brain._bo_marker_anh("Dạ mẫu M01 ạ <<ANH>>") == "Dạ mẫu M01 ạ", "marker phải bị bóc"
    assert brain._bo_marker_anh("Dạ mẫu M01 ạ") == "Dạ mẫu M01 ạ"

    # Model coi <<ANH>> như thẻ XML rồi tự đóng thẻ. Ca THẬT: khách đọc được "<<ANH></anh>>"
    # nguyên văn và không nhận được ảnh nào, vì regex cũ đòi đúng 2 dấu '>'.
    for lech in ("<<ANH></anh>>", "<<ANH>", "<ANH>", "<<anh />>", "<< ANH >>", "</ANH>>"):
        assert brain._wants_image("Dạ mẫu LD12 ạ " + lech), f"phải hiểu là đòi ảnh: {lech!r}"
        assert brain._bo_marker_anh("Dạ mẫu LD12 ạ " + lech) == "Dạ mẫu LD12 ạ", \
            f"phải bóc sạch, không để khách đọc được: {lech!r}"

    lich_su = [{"role": "assistant", "content": "Mẫu M01 giá 33.9 triệu", "at": "10:00"}]
    assert brain._image_markers(lich_su, "Mẫu M01 ạ <<ANH>>", "kèm ảnh"), "có <<ANH>> -> gửi lại mã cũ"
    assert not brain._image_markers(lich_su, "Mẫu M01 ạ", "xin giá"), "không marker -> mã cũ thôi gửi"


def test_bo_marker_thua():
    """Lưới chặn cuối: marker viết lệch kiểu gì cũng KHÔNG được lọt tới khách."""
    orig = alerts.notify
    alerts.notify = lambda *a, **k: None
    alerts._ALERTS.clear()
    try:
        assert messenger._bo_marker_thua("Dạ mẫu LD12 ạ <<ANH></anh>>") == "Dạ mẫu LD12 ạ"
        assert messenger._bo_marker_thua("Dạ em gửi ạ <<HANDOFF chốt đơn>") == "Dạ em gửi ạ"

        # Câu thường KHÔNG được đụng vào - chặn quá tay là cắt mất chữ của khách.
        for sach in ("Dạ mẫu LD12 giá 77.835.000đ ạ",
                     "Bác cho em hỏi kích thước 127x79cm có hợp không ạ",
                     "Giá < 100 triệu thì bên em có mẫu M01 ạ"):
            assert messenger._bo_marker_thua(sach) == sach, f"không được sửa câu sạch: {sach!r}"
    finally:
        alerts.notify = orig
        alerts._ALERTS.clear()


def test_danh_gia_token():
    """Token page chết = bot im hoàn toàn. Phải kêu TRƯỚC khi khách mất tin.

    Ca thật: token sinh từ tài khoản cá nhân chết vì chủ tài khoản đổi mật khẩu (#190/460) -
    chưa tới hạn nhưng đã hỏng, chỉ lộ khi gửi tin cho khách và nhận HTTP 400."""
    now = 1_800_000_000.0
    ngay = 86400

    danh = messenger.danh_gia_token({"is_valid": False,
                                     "error": {"message": "session invalidated"}}, now)
    assert "CHẾT" in danh and "session invalidated" in danh, danh

    assert messenger.danh_gia_token({"is_valid": True, "expires_at": 0}, now) == "", \
        "token vĩnh viễn (System User) không được kêu"
    assert messenger.danh_gia_token({"is_valid": True, "expires_at": now + 60 * ngay}, now) == "", \
        "còn 60 ngày thì im"

    sap = messenger.danh_gia_token({"is_valid": True, "expires_at": now + 3 * ngay}, now)
    assert "còn 3 ngày" in sap, sap

    # Đã quá hạn nhưng FB chưa kịp đánh is_valid=False -> vẫn phải kêu, không được ra số âm.
    qua = messenger.danh_gia_token({"is_valid": True, "expires_at": now - ngay}, now)
    assert "còn 0 ngày" in qua, qua

    assert messenger.danh_gia_token({}, now), "thiếu is_valid phải coi là chết, không im lặng"


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
    test_moc_goc_khi_tra_loi_bu()
    test_ai_quyet_gui_anh()
    test_bo_marker_thua()
    test_danh_gia_token()
    print("OK - all messenger self-checks passed")
