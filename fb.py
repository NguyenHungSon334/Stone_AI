"""
Firebase Realtime DB = NGUỒN CHÍNH cho conversations + stats. Local = cache.

- Ghi: local (cache, nhanh) + Firebase (nguồn chính, thread nền).
- Đọc: cache local trước; miss -> kéo từ Firebase, ghi cache, dùng lại lần sau.
Bot là writer DUY NHẤT nên cache local không stale. Thiếu cấu hình
(FIREBASE_CRED/FIREBASE_DB_URL) -> no-op, bot chạy thuần local như cũ.
Lỗi Firebase KHÔNG BAO GIỜ được kéo bot chết -> bọc try + thread nền cho write.

MỌI hàm ở đây là ĐỒNG BỘ và chạm mạng -> caller async PHẢI gọi qua asyncio.to_thread.
Gọi thẳng trong coroutine là khoá event loop: sự cố 30/07/2026 - dashboard kéo stats,
bot đứng >170s, 3 webhook FB bị reset, khách mất tin, phải restart mới sống lại.
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout

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


# Pool CỐ ĐỊNH cho write nền. Trước đây mỗi lần ghi đẻ 1 thread mới, không trần: một lượt
# đông khách là hàng trăm thread tranh GIL, event loop đói theo. 4 worker đủ cho nhịp ghi
# thật (vài lượt/giây) và ép hàng đợi thay vì đẻ thêm.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fb-write")

# Pool RIÊNG cho đọc đồng bộ + trần chờ. firebase_admin không cho đặt timeout cho bước
# refresh OAuth token (httpTimeout chỉ áp cho request DB) nên một lần refresh treo là treo
# vĩnh viễn. Chặn ở đây: quá hạn thì bỏ, caller rơi về cache local. Thread kẹt vẫn kẹt
# nhưng KHÔNG kéo theo ai - hết pool thì lần đọc sau hỏng ngay thay vì chờ mãi.
_READ_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fb-read")
_READ_TIMEOUT_S = 20.0


def _doc(fn, mo_ta: str, default=None):
    """Chạy 1 hàm đọc Firebase với trần chờ. Quá hạn/lỗi -> trả `default` (caller dùng local)."""
    try:
        return _READ_POOL.submit(fn).result(timeout=_READ_TIMEOUT_S)
    except _FutureTimeout:
        print(f"[fb] {mo_ta} quá {_READ_TIMEOUT_S:g}s -> bỏ, dùng cache local", file=sys.stderr)
        alerts.alert("fb:timeout", f"⚠️ FIREBASE TREO khi {mo_ta} - bot rơi về dữ liệu local.\n"
                                   f"➡️ Kiểm tra mạng VPS / hạn mức Firebase.")
        return default


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
    _POOL.submit(wrap)


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


def merge_state(psid: str, patch: dict) -> None:
    """Ghi ĐÈ TỪNG TRƯỜNG cờ trạng thái khách vào khach_state/<psid> (update, không set cả node).

    update() thay vì set(): mỗi luồng chỉ đụng trường của mình (khoá nhắn, follow-up, ảnh...)
    nên hai luồng ghi cùng lúc không xoá cờ của nhau."""
    if not patch:
        return

    def fn() -> None:
        from firebase_admin import db
        db.reference(f"khach_state/{_safe(psid)}").update(patch)
    _run(fn)


def fetch_state(psid: str) -> dict | None:
    """Cờ trạng thái 1 khách từ Firebase. None = Firebase tắt / chưa có / lỗi / treo quá hạn."""
    def fn():
        if not _init():
            return None
        from firebase_admin import db
        data = db.reference(f"khach_state/{_safe(psid)}").get()
        return data if isinstance(data, dict) else None
    try:
        return _doc(fn, f"đọc cờ khách {psid}")
    except Exception as e:
        print(f"[fb] fetch state lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # Mất cờ = bot nhắn lại khách đã yêu cầu ngưng. Phải báo, đừng nuốt.
        alerts.alert(f"fb:state:{type(e).__name__}",
                     f"⚠️ ĐỌC CỜ TRẠNG THÁI KHÁCH LỖI - bot có thể nhắn nhầm khách đã khoá.\n"
                     f"{type(e).__name__}: {e}")
        return None


def delete_state(psid: str) -> bool:
    """Xoá cờ trạng thái 1 khách (đi kèm xoá hội thoại). False = Firebase tắt/lỗi."""
    if not _init():
        return False
    try:
        from firebase_admin import db
        db.reference(f"khach_state/{_safe(psid)}").delete()
        return True
    except Exception as e:
        print(f"[fb] xoá state {psid} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def mirror_event(row: dict) -> None:
    """Append 1 sự kiện stats vào stats/events (push -> key tự sinh)."""
    def fn() -> None:
        from firebase_admin import db
        db.reference("stats/events").push(row)
    _run(fn)


_PSIDS_CACHE: tuple[float, list] | None = None
_PSIDS_TTL_S = 60.0       # danh sách khách đổi chậm; dashboard tự làm mới mỗi vài giây


def list_psids(force: bool = False) -> list | None:
    """Danh sách psid có trên Firebase (shallow - chỉ lấy key, không tải nội dung chat).

    None = Firebase tắt/lỗi. Dùng để dashboard thấy ĐỦ khách của cả hệ thống, không chỉ khách
    mà máy này tình cờ có cache.

    Cache TTL 60s: dashboard làm mới nền liên tục, mỗi lần lại một vòng mạng tới Firebase
    (~1s) chỉ để lấy đúng bộ key gần như không đổi.
    """
    global _PSIDS_CACHE
    if not force and _PSIDS_CACHE and time.time() - _PSIDS_CACHE[0] < _PSIDS_TTL_S:
        return _PSIDS_CACHE[1]

    def fn():
        if not _init():
            return None
        from firebase_admin import db
        data = db.reference("conversations").get(shallow=True)
        return sorted(data.keys()) if isinstance(data, dict) else []
    try:
        out = _doc(fn, "đọc danh sách khách")
        if out is not None:
            _PSIDS_CACHE = (time.time(), out)
        return out
    except Exception as e:
        print(f"[fb] list psids lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def fetch_events(since_ts: float) -> list | None:
    """Kéo sự kiện stats từ Firebase (nguồn CHÍNH) từ mốc `since_ts` trở đi.

    None = Firebase tắt/lỗi -> caller tự rơi về file local. Lọc theo ts ngay trên server
    (cần .indexOn ts trong database.rules.json) để không tải cả kho về mỗi lần mở dashboard.
    """
    def fn():
        if not _init():
            return None
        from firebase_admin import db
        ref = db.reference("stats/events")
        try:
            data = ref.order_by_child("ts").start_at(since_ts).get()
        except Exception as e:
            if "Index not defined" not in str(e):
                print(f"[fb] fetch events lỗi: {type(e).__name__}: {e}", file=sys.stderr)
                return None
            # Rules chưa deploy .indexOn ts -> RTDB từ chối lọc trên server. Tải hết rồi lọc ở
            # client: chậm hơn nhưng CHẠY ĐƯỢC ngay, khỏi bắt user vào Console mới xem được số.
            print("[fb] chưa có .indexOn ts -> tải hết rồi lọc ở client (deploy "
                  "database.rules.json để nhanh hơn)", file=sys.stderr)
            try:
                data = ref.get()
            except Exception as e2:
                print(f"[fb] fetch events lỗi: {type(e2).__name__}: {e2}", file=sys.stderr)
                return None
        rows = (list(data.values()) if isinstance(data, dict)
                else data if isinstance(data, list) else [])
        return [r for r in rows if isinstance(r, dict) and (r.get("ts") or 0) >= since_ts]
    return _doc(fn, "đọc thống kê")


def save_daily(gop: dict) -> bool:
    """Ghi ĐÈ bản ghi tổng theo ngày vào stats/daily/<YYYY-MM-DD>. True = đã ghi.

    Đồng bộ (KHÔNG dùng _run thread nền): stats.prune xoá sự kiện thô ngay sau lời gọi này,
    ghi nền mà hỏng thì số liệu bay mất mà không ai biết. Lỗi -> ném lên cho caller dừng lại.
    """
    if not _init():
        return False
    from firebase_admin import db
    db.reference("stats/daily").update(gop)
    return True


def fetch_daily() -> dict | None:
    """Bản ghi tổng theo ngày. None = Firebase tắt/lỗi -> caller rơi về file local."""
    def fn():
        if not _init():
            return None
        from firebase_admin import db
        d = db.reference("stats/daily").get()
        return d if isinstance(d, dict) else {}
    try:
        return _doc(fn, "đọc tổng theo ngày")
    except Exception as e:
        print(f"[fb] đọc daily lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return None


_PRUNE_BATCH = 500        # số key xoá / request. Gộp cả nghìn key vào 1 update dễ bị RTDB từ chối.


def prune_events(cutoff_ts: float) -> int | None:
    """Xoá sự kiện stats CŨ HƠN `cutoff_ts`. Trả số bản ghi đã xoá. None = Firebase tắt/lỗi.

    CHỈ đụng node stats/events. conversations/ (lịch sử khách + sidecar CRM) KHÔNG bao giờ bị
    chạm tới ở đây - hàm này không có đường nào tới nhánh đó.
    """
    if not _init():
        return None
    try:
        from firebase_admin import db
        ref = db.reference("stats/events")
        try:
            cu = ref.order_by_child("ts").end_at(cutoff_ts).get()
        except Exception as e:
            if "Index not defined" not in str(e):
                raise
            # Chưa deploy .indexOn ts -> tải hết rồi tự lọc (chậm hơn nhưng vẫn dọn được).
            cu = ref.get()
        if not isinstance(cu, dict):
            return 0
        # Lọc lại ở client: end_at của RTDB là INCLUSIVE, để nguyên sẽ xoá cả bản ghi đúng mốc.
        keys = [k for k, v in cu.items()
                if isinstance(v, dict) and (v.get("ts") or 0) < cutoff_ts]
        for i in range(0, len(keys), _PRUNE_BATCH):
            ref.update({k: None for k in keys[i:i + _PRUNE_BATCH]})
        return len(keys)
    except Exception as e:
        print(f"[fb] dọn stats lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        # Dọn hỏng = kho phình mãi -> dashboard chậm dần rồi chạm httpTimeout 10s và tắt số liệu.
        alerts.alert(f"fb:prune:{type(e).__name__}",
                     f"⚠️ DỌN STATS FIREBASE LỖI - kho sự kiện phình mãi, dashboard sẽ chậm dần.\n"
                     f"{type(e).__name__}: {e}")
        return None


def fetch_conversation(psid: str) -> list | None:
    """Kéo lịch sử 1 khách từ Firebase (dùng khi cache local miss).

    None nếu: Firebase tắt / khách chưa có / lỗi. Đồng bộ - chỉ gọi lúc miss (hiếm).
    ponytail: log là mảng liền mạch nên RTDB trả về list; dạng khác -> coi như chưa có.
    """
    def fn():
        if not _init():
            return None
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
    try:
        return _doc(fn, f"đọc lịch sử khách {psid}")
    except Exception as e:
        print(f"[fb] fetch lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # Miss cache + fetch hỏng = bot coi như khách MỚI, mất sạch ngữ cảnh cũ (hỏi lại từ đầu).
        alerts.alert(f"fb:fetch:{type(e).__name__}",
                     f"⚠️ ĐỌC FIREBASE LỖI - bot mất lịch sử khách, trả lời như khách mới.\n"
                     f"{type(e).__name__}: {e}")
        return None


def quen_cache_psids() -> None:
    """Bỏ cache danh sách psid. Gọi sau khi xoá khách, không thì khách vừa xoá còn hiện 60s."""
    global _PSIDS_CACHE
    _PSIDS_CACHE = None


def delete_conversation(psid: str) -> bool:
    """Xoá lịch sử 1 khách trên Firebase. True = đã xoá. False = Firebase tắt/lỗi. Đồng bộ."""
    quen_cache_psids()
    if not _init():
        return False
    try:
        from firebase_admin import db
        db.reference(f"conversations/{_safe(psid)}").delete()
        db.reference(f"khach_state/{_safe(psid)}").delete()   # cờ đi cùng hội thoại
        return True
    except Exception as e:
        print(f"[fb] xoá hội thoại {psid} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def clear_all() -> bool:
    """Xóa TOÀN BỘ conversations + stats trên Firebase (reset từ đầu). KHÔNG hồi được.

    True = đã xóa (Firebase bật + chạy xong). False = Firebase tắt/lỗi. Đồng bộ (gọi từ admin)."""
    quen_cache_psids()
    if not _init():
        return False
    try:
        from firebase_admin import db
        db.reference("conversations").delete()
        db.reference("khach_state").delete()
        db.reference("stats").delete()
        return True
    except Exception as e:
        print(f"[fb] clear_all lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return False
