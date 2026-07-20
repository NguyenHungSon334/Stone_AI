"""
Firebase Realtime DB = NGUỒN CHÍNH cho conversations + stats. Local = cache.

- Ghi: local (cache, nhanh) + Firebase (nguồn chính, thread nền).
- Đọc: cache local trước; miss -> kéo từ Firebase, ghi cache, dùng lại lần sau.
Bot là writer DUY NHẤT nên cache local không stale. Thiếu cấu hình
(FIREBASE_CRED/FIREBASE_DB_URL) -> no-op, bot chạy thuần local như cũ.
Lỗi Firebase KHÔNG BAO GIỜ được kéo bot chết -> bọc try + thread nền cho write.

ponytail: 1 thread daemon / lần ghi; fetch đồng bộ chỉ khi cache miss (hiếm).
Tải cao thì gom batch hoặc hàng đợi sau.
"""
import sys
import threading

import alerts
import config
import util

_app = None
_ready = False
_lock = threading.Lock()


def _init() -> bool:
    """Init 1 lần (lazy). True nếu Firebase sẵn sàng."""
    global _app, _ready
    if _ready:
        return _app is not None
    with _lock:
        if _ready:
            return _app is not None
        _ready = True
        if not (config.FIREBASE_CRED and config.FIREBASE_DB_URL):
            return False
        try:
            import firebase_admin
            from firebase_admin import credentials
            cred = credentials.Certificate(config.FIREBASE_CRED)
            # httpTimeout: default 120s quá dài - Firebase chậm/chết sẽ treo op tới 2 phút.
            # 10s: hỏng nhanh, cache local đỡ, write nền chết lặng lẽ (tự lành lượt sau).
            _app = firebase_admin.initialize_app(
                cred, {"databaseURL": config.FIREBASE_DB_URL, "httpTimeout": 10})
        except Exception as e:
            print(f"[fb] init lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            # Init hỏng = TẮT HẲN Firebase cả phiên (_ready đã True, không thử lại): mọi hội
            # thoại chỉ còn local, admin vẫn tưởng có backup. Phải báo.
            alerts.alert("fb:init", f"🔴 FIREBASE KHÔNG KHỞI ĐỘNG ĐƯỢC - hội thoại chỉ lưu local, "
                                    f"KHÔNG có backup cloud.\n{type(e).__name__}: {e}\n"
                                    f"➡️ Kiểm tra FIREBASE_CRED (file json) và FIREBASE_DB_URL, rồi restart.")
            _app = None
        return _app is not None


_safe = util.safe_psid   # psid -> key hợp lệ RTDB (cấm . $ # [ ] /)


def _run(fn) -> None:
    def wrap() -> None:
        try:
            if _init():
                fn()
        except Exception as e:
            print(f"[fb] mirror lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            alerts.alert(f"fb:mirror:{type(e).__name__}",
                         f"⚠️ GHI FIREBASE LỖI - hội thoại/stats lượt này KHÔNG lên cloud "
                         f"(local vẫn còn).\n{type(e).__name__}: {e}")
    threading.Thread(target=wrap, daemon=True).start()


def mirror_conversation(psid: str, msgs: list) -> None:
    """Ghi đè toàn bộ lịch sử 1 khách vào conversations/<psid> (khớp file local)."""
    def fn() -> None:
        from firebase_admin import db
        db.reference(f"conversations/{_safe(psid)}").set(msgs)
    _run(fn)


def append_conversation(psid: str, start_index: int, new_msgs: list) -> None:
    """APPEND tin mới vào conversations/<psid> theo index số (start_index, start_index+1...).

    RTDB coi key số 0..n liền mạch là MẢNG -> ghi child(str(idx)) tương thích y hệt dữ liệu cũ
    (mirror_conversation set cả list cũng sinh key số). Mỗi lượt chỉ ghi phần mới -> O(1), không
    up lại cả mảng. Ghi tuần tự từng index để không tạo lỗ hổng (get vẫn trả list liền mạch)."""
    def fn() -> None:
        from firebase_admin import db
        ref = db.reference(f"conversations/{_safe(psid)}")
        for i, m in enumerate(new_msgs):
            ref.child(str(start_index + i)).set(m)
    _run(fn)


def mirror_event(row: dict) -> None:
    """Append 1 sự kiện stats vào stats/events (push -> key tự sinh)."""
    def fn() -> None:
        from firebase_admin import db
        db.reference("stats/events").push(row)
    _run(fn)


def fetch_conversation(psid: str) -> list | None:
    """Kéo lịch sử 1 khách từ Firebase (dùng khi cache local miss).

    None nếu: Firebase tắt / khách chưa có / lỗi. Đồng bộ - chỉ gọi lúc miss (hiếm).
    ponytail: log là mảng liền mạch nên RTDB trả về list; dạng khác -> coi như chưa có.
    """
    if not _init():
        return None
    try:
        from firebase_admin import db
        data = db.reference(f"conversations/{_safe(psid)}").get()
        if isinstance(data, list):
            return [m for m in data if m is not None]   # RTDB chèn None ở index thiếu -> lọc
        if isinstance(data, dict):
            # Có lỗ hổng index (1 lượt append lỗi) -> RTDB trả dict thay list. Xếp theo key số.
            items = sorted(((int(k), v) for k, v in data.items() if str(k).isdigit()),
                           key=lambda x: x[0])
            return [v for _, v in items] or None
        return None
    except Exception as e:
        print(f"[fb] fetch lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # Miss cache + fetch hỏng = bot coi như khách MỚI, mất sạch ngữ cảnh cũ (hỏi lại từ đầu).
        alerts.alert(f"fb:fetch:{type(e).__name__}",
                     f"⚠️ ĐỌC FIREBASE LỖI - bot mất lịch sử khách, trả lời như khách mới.\n"
                     f"{type(e).__name__}: {e}")
        return None


def clear_all() -> bool:
    """Xóa TOÀN BỘ conversations + stats trên Firebase (reset từ đầu). KHÔNG hồi được.

    True = đã xóa (Firebase bật + chạy xong). False = Firebase tắt/lỗi. Đồng bộ (gọi từ admin)."""
    if not _init():
        return False
    try:
        from firebase_admin import db
        db.reference("conversations").delete()
        db.reference("stats").delete()
        return True
    except Exception as e:
        print(f"[fb] clear_all lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return False
