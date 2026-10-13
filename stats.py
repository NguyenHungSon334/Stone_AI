"""
Thống kê bot: ghi sự kiện JSONL (1 dòng/sự kiện) + đọc tổng hợp cho dashboard.

Sự kiện: ok (trả lời xong), error (lỗi), handoff (chuyển chuyên gia), rate_limited (bị chặn spam).
File: stats/events.jsonl - append-only, đọc lại khi dashboard hỏi. Nhỏ gọn, không cần DB.
"""
import json
import sys
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import alerts
import config
import fb
import util

_STATS_DIR = config.ROOT / "stats"
_EVENTS = _STATS_DIR / "events.jsonl"
_LOCK = threading.Lock()


def _append(row: dict) -> None:
    """Ghi 1 dòng JSONL + mirror Firebase. Không bao giờ ném lỗi (stats chết không kéo bot chết)."""
    try:
        with _LOCK:
            _STATS_DIR.mkdir(parents=True, exist_ok=True)
            with _EVENTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        fb.mirror_event(row)
    except Exception as e:
        print(f"[stats] ghi lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        # Stats hỏng = dashboard báo token/chi phí THIẾU -> tưởng đang rẻ trong khi vẫn đốt tiền.
        alerts.alert(f"stats:{type(e).__name__}",
                     f"⚠️ GHI STATS LỖI - số liệu token/chi phí trên dashboard KHÔNG còn đúng.\n"
                     f"{type(e).__name__}: {e}")


def log_event(kind: str, psid: str, duration_s: float | None = None, note: str = "") -> None:
    """Ghi 1 sự kiện (ok/error/handoff/rate_limited/...)."""
    row = {"ts": time.time(), "kind": kind, "psid": str(psid)}
    if duration_s is not None:
        row["dur"] = round(duration_s, 2)
    if note:
        row["note"] = note[:200]
    _append(row)


def log_usage(psid: str, tok_in: int, tok_out: int, tok_cache: int = 0) -> None:
    """Ghi token 1 câu trả lời.

    tin  = TOÀN BỘ token input (Google đã tính cả phần cache vào đây)
    tout = output, gồm cả thinking
    tcache = phần input ĐÃ CACHE nằm trong `tin` - tính theo giá cache, rẻ hơn nhiều.
    Bản ghi cũ không có `tcache` -> coi như 0, tính giá input đầy đủ như trước."""
    _append({"ts": time.time(), "kind": "usage", "psid": str(psid),
             "tin": int(tok_in), "tout": int(tok_out), "tcache": int(tok_cache)})


def _read_local(cutoff: float) -> list[dict]:
    """Đọc sự kiện từ file local. File hỏng dòng nào bỏ dòng đó."""
    out: list[dict] = []
    try:
        with _EVENTS.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if row.get("ts", 0) >= cutoff:
                        out.append(row)
                except Exception:
                    continue
    except OSError:
        pass
    return out


# Firebase là NGUỒN CHÍNH (xem fb.py) nên dashboard phải đọc từ đó, không thì mỗi máy hiện
# một mảnh: chạy local thấy $0 trong khi VPS đang đốt tiền thật, container dựng lại là trắng
# số liệu. Cache ngắn để mở nhiều tab / F5 liên tục không đấm RTDB.
_CACHE: dict = {"at": 0.0, "cutoff": 0.0, "rows": None}
_CACHE_TTL_S = 60.0


def _read_events(days: int = 30) -> list[dict]:
    """Sự kiện trong N ngày. Ưu tiên Firebase (nguồn chính), lỗi/tắt thì rơi về file local."""
    now = time.time()
    cutoff = now - days * 86400
    # Cache theo CỬA SỔ đã tải, không theo `days`: 1 lần mở dashboard hỏi cả 7 ngày lẫn 30 ngày,
    # so bằng days thì trượt cache và tải Firebase 3 lần. Cửa sổ hẹp hơn thì lọc lại từ cache.
    if (_CACHE["rows"] is not None and now - _CACHE["at"] < _CACHE_TTL_S
            and cutoff >= _CACHE["cutoff"]):
        return [r for r in _CACHE["rows"] if (r.get("ts") or 0) >= cutoff]
    rows = fb.fetch_events(cutoff)
    if rows is None:                       # Firebase tắt hoặc lỗi -> số liệu của riêng máy này
        return _read_local(cutoff)
    # Máy này vừa ghi mà Firebase chưa kịp mirror (thread nền) -> gộp thêm local, khử trùng
    # theo (ts, kind, psid). Thiếu bước này thì số vừa phát sinh biến mất khỏi dashboard vài giây.
    seen = {(r.get("ts"), r.get("kind"), r.get("psid")) for r in rows}
    rows = rows + [r for r in _read_local(cutoff)
                   if (r.get("ts"), r.get("kind"), r.get("psid")) not in seen]
    _CACHE.update(at=now, cutoff=cutoff, rows=rows)
    return rows


def _prune_local(cutoff: float) -> int:
    """Viết lại events.jsonl chỉ giữ dòng >= cutoff. Trả số dòng đã bỏ. Atomic (tmp + replace)."""
    with _LOCK:
        try:
            lines = _EVENTS.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        giu = []
        for line in lines:
            try:
                if (json.loads(line).get("ts") or 0) >= cutoff:
                    giu.append(line)
            except Exception:
                continue                       # dòng hỏng -> bỏ luôn, đằng nào cũng không đọc được
        if len(giu) == len(lines):
            return 0
        tmp = _EVENTS.with_name(_EVENTS.name + ".tmp")
        tmp.write_text("\n".join(giu) + ("\n" if giu else ""), encoding="utf-8")
        tmp.replace(_EVENTS)
        return len(lines) - len(giu)


# Loại sự kiện LUÔN có mặt trong `counts` kể cả khi bằng 0 (dashboard đọc thẳng theo tên).
# `rate_limited` giữ lại cho bản ghi cũ - CHƯA có chỗ nào ghi loại này, xem ghi chú ở summary().
_LOAI_LUON_CO = ("ok", "error", "handoff", "khach_kho_chiu", "unclear",
                 "noise_blocked", "noise_stop", "sticker_blocked", "sticker_stop",
                 "comment", "referral", "followup", "image",
                 "missed_check", "missed_autoreply", "rate_limited")
# Khoá trong bản ghi gộp KHÔNG phải là loại sự kiện (đừng cộng nhầm vào `counts`).
_KHONG_PHAI_LOAI = {"date", "tin", "tout", "tcache", "dur_sum", "dur_n", "dur_max", "psids"}

# --- Gộp theo ngày: giữ được biểu đồ 30 ngày mà kho không phình ---
# 1 ngày sự kiện thô (~700 bản ghi) nén còn ĐÚNG 1 bản ghi. Mất chi tiết từng lượt, giữ đủ
# thứ dashboard vẽ: token, chi phí, đếm theo loại, thời gian trả lời.
_DAILY = _STATS_DIR / "daily.json"


def _mocc_ngay(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def gop_theo_ngay(rows: list[dict]) -> dict[str, dict]:
    """Sự kiện thô -> {ngày: bản ghi tổng}. Hàm THUẦN (test được).

    `psids` là số khách hoạt động trong NGÀY đó. Cộng nhiều ngày lại sẽ đếm trùng khách quay
    lại - chấp nhận, vì số này chỉ để nhìn xu hướng chứ không phải để đối soát.
    """
    out: dict[str, dict] = {}
    khach: dict[str, set] = {}
    for ev in rows:
        ts = ev.get("ts") or 0
        if not ts:
            continue
        d = _mocc_ngay(ts)
        r = out.setdefault(d, {"date": d, "tin": 0, "tout": 0, "tcache": 0,
                               "ok": 0, "error": 0, "handoff": 0, "rate_limited": 0,
                               "dur_sum": 0.0, "dur_n": 0, "dur_max": 0.0, "psids": 0})
        kind = ev.get("kind", "")
        if kind == "usage":
            r["tin"] += int(ev.get("tin") or 0)
            r["tout"] += int(ev.get("tout") or 0)
            r["tcache"] += int(ev.get("tcache") or 0)
            continue
        # Đếm MỌI loại sự kiện, không chỉ 4 loại: comment/referral/unclear/khach_kho_chiu...
        # trước đây ghi ra rồi rơi mất khi ngày bị gộp (~40% sự kiện), dashboard không có gì để hiện.
        if kind:
            r[kind] = int(r.get(kind) or 0) + 1
        if kind == "ok" and "dur" in ev:
            dur = float(ev["dur"])
            r["dur_sum"] += dur
            r["dur_n"] += 1
            r["dur_max"] = max(r["dur_max"], dur)
        if kind in ("ok", "error", "handoff"):
            khach.setdefault(d, set()).add(ev.get("psid", ""))
    for d, s in khach.items():
        out[d]["psids"] = len(s)
    for r in out.values():
        r["dur_sum"] = round(r["dur_sum"], 2)
        r["dur_max"] = round(r["dur_max"], 2)
    return out


def _luu_daily_local(gop: dict[str, dict]) -> None:
    """Ghi đè các ngày trong `gop` vào daily.json (giữ nguyên ngày khác)."""
    cu = util.read_json(_DAILY, {})
    if not isinstance(cu, dict):
        cu = {}
    cu.update(gop)
    util.write_json_atomic(_DAILY, cu)


def _doc_daily(cutoff: float) -> dict[str, dict]:
    """Bản ghi tổng theo ngày, từ mốc cutoff. Firebase là nguồn chính, lỗi/tắt -> file local."""
    tu_ngay = _mocc_ngay(cutoff)
    d = fb.fetch_daily()
    if d is None:
        d = util.read_json(_DAILY, {})
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and k >= tu_ngay}


def _moc_nua_dem(keep_days: float) -> float:
    """Mốc cắt = 00:00 của ngày (hôm nay - keep_days).

    Cắt đúng nửa đêm chứ không phải 'now - N ngày' là CỐ Ý: mọi sự kiện bị xoá đều thuộc ngày
    đã đóng trọn vẹn, nên bản ghi tổng của ngày đó là chung cuộc -> ghi ĐÈ được, chạy prune bao
    nhiêu lần cũng ra cùng kết quả. Cắt giữa ngày thì lần dọn sau gộp tiếp phần còn lại của
    cùng ngày đó và phải CỘNG DỒN - hở ra là cộng đôi khi xoá lỗi rồi dọn lại.
    """
    ngay = (datetime.now() - timedelta(days=keep_days)).date()
    return datetime.combine(ngay, dtime.min).timestamp()


def prune(keep_days: float | None = None) -> dict:
    """Gộp sự kiện cũ thành bản ghi/ngày RỒI mới xoá. Cả Firebase lẫn file local.

    CHỈ dọn stats. Lịch sử khách (conversations/) và hồ sơ CRM KHÔNG bị đụng - hàm này không
    tham chiếu tới thư mục đó.

    Thứ tự gộp-trước-xoá-sau là bắt buộc: xoá trước mà ghi bản gộp hỏng là mất số vĩnh viễn.
    Gộp hỏng -> KHÔNG xoá gì cả, để nguyên chờ vòng sau.
    """
    keep = config.STATS_KEEP_DAYS if keep_days is None else keep_days
    cutoff = _moc_nua_dem(keep)

    cu = [r for r in _read_events(days=3650) if (r.get("ts") or 0) < cutoff]
    gop = gop_theo_ngay(cu)
    if gop:
        _luu_daily_local(gop)          # ném lỗi -> dừng ở đây, chưa xoá gì
        fb.save_daily(gop)

    tren_cloud = fb.prune_events(cutoff)
    o_may = _prune_local(cutoff)
    # Cache còn giữ hàng vừa xoá -> dashboard hiện số của dữ liệu không còn tồn tại. Ép nạp lại.
    _CACHE.update(at=0.0, cutoff=0.0, rows=None)
    return {"keep_days": keep, "firebase": tren_cloud, "local": o_may,
            "ngay_da_gop": sorted(gop)}


def _usd(tin: int, tout: int, tcache: int = 0) -> float:
    """Tiền 1 cụm token. Phần đã cache tách khỏi input thường - giá khác hẳn (xem config._GIA)."""
    moi = max(int(tin) - int(tcache), 0)
    return (moi / 1e6 * config.PRICE_IN_USD
            + tcache / 1e6 * config.PRICE_CACHED_IN_USD
            + tout / 1e6 * config.PRICE_OUT_USD)


def cost_breakdown(days: int = 30) -> dict:
    """Bóc chi phí để KIỂM SOÁT tiền, không chỉ xem tổng.

    Trả: chi phí hôm nay/hôm qua/7 ngày/kỳ, trung bình mỗi câu trả lời, dự phóng 30 ngày,
    và TOP khách tốn nhất (1 khách hỏi lan man có thể ăn hết ngân sách mà tổng vẫn nhìn 'ổn').
    """
    events = _read_events(days)
    today = datetime.now().date()
    per_day: dict[str, list[int]] = {}          # [tin, tout, tcache]
    per_psid: dict[str, list[int]] = {}
    replies_per_psid: dict[str, int] = {}
    for ev in events:
        if ev.get("kind") == "ok":
            replies_per_psid[ev.get("psid", "")] = replies_per_psid.get(ev.get("psid", ""), 0) + 1
        if ev.get("kind") != "usage":
            continue
        tin, tout = int(ev.get("tin") or 0), int(ev.get("tout") or 0)
        tcache = int(ev.get("tcache") or 0)
        d = datetime.fromtimestamp(ev["ts"]).date().isoformat()
        per_day.setdefault(d, [0, 0, 0])
        per_day[d][0] += tin
        per_day[d][1] += tout
        per_day[d][2] += tcache
        psid = ev.get("psid", "")
        per_psid.setdefault(psid, [0, 0, 0])
        per_psid[psid][0] += tin
        per_psid[psid][1] += tout
        per_psid[psid][2] += tcache

    # Ngày đã bị dọn -> lấy từ bản ghi tổng. CHỈ nhận ngày chưa có sự kiện thô nào, không thì
    # ngày giao thời (vừa gộp vừa còn event) bị đếm 2 lần.
    replies_gop = 0
    co_thô = {datetime.fromtimestamp(e["ts"]).date().isoformat() for e in events if e.get("ts")}
    for d, r in _doc_daily(time.time() - days * 86400).items():
        if d in co_thô:
            continue
        per_day[d] = [int(r.get("tin") or 0), int(r.get("tout") or 0), int(r.get("tcache") or 0)]
        replies_gop += int(r.get("ok") or 0)

    def day_usd(d) -> float:
        v = per_day.get(d.isoformat())
        return _usd(*v) if v else 0.0

    d7 = sum(day_usd(today - timedelta(days=i)) for i in range(7))
    total = sum(_usd(*v) for v in per_day.values())
    replies = sum(replies_per_psid.values()) + replies_gop
    top = sorted(((p, _usd(*v), replies_per_psid.get(p, 0)) for p, v in per_psid.items()),
                 key=lambda x: x[1], reverse=True)[:10]
    return {
        "today_usd": round(day_usd(today), 4),
        "yesterday_usd": round(day_usd(today - timedelta(days=1)), 4),
        "d7_usd": round(d7, 4),
        "period_usd": round(total, 4),
        "period_days": days,
        "per_reply_usd": round(total / replies, 5) if replies else None,
        "replies": replies,
        # Dự phóng theo nhịp 7 ngày gần nhất - sát thực tế hơn trung bình cả kỳ (kỳ có ngày chết bot).
        "projection_30d_usd": round(d7 / 7 * 30, 2),
        # TOP khách chỉ dựng được từ sự kiện THÔ (ngày đã gộp không còn psid) -> phạm vi thật
        # là STATS_KEEP_DAYS ngày, KHÔNG phải `period_days`. Ghi rõ ra để báo cáo khỏi gắn nhãn sai.
        "top_days": config.STATS_KEEP_DAYS,
        "daily": [{"date": (today - timedelta(days=i)).isoformat(),
                   "usd": round(day_usd(today - timedelta(days=i)), 4)} for i in range(days - 1, -1, -1)],
        "top_customers": [{"psid": p, "usd": round(c, 4), "replies": n} for p, c, n in top if c > 0],
    }


# Ngày bắt đầu ghi cờ handoff lên từng khách (state.danh_dau_handoff). Khách chuyển chuyên gia
# TRƯỚC mốc này không dựng lại được: stats gộp theo ngày đã bỏ psid.
MOC_CO_HANDOFF = "2026-09-09"

# Dashboard tự làm mới vài giây/lần, mà tong_the() quét CẢ kho sự kiện + toàn bộ cờ khách.
# Số "từ trước tới nay" nhích rất chậm -> cache 5 phút là đủ tươi, khỏi đấm Firebase.
_TONG_CACHE: tuple[float, dict] | None = None
_TONG_TTL_S = 300.0


def _dem_states() -> tuple[dict, str]:
    """Đếm cờ trạng thái theo NGƯỜI. Trả (số đếm, nguồn)."""
    states = fb.fetch_states()
    nguon = "firebase"
    if states is None:                       # Firebase tắt/lỗi -> chỉ còn cache của máy này
        nguon = "local"
        states = {}
        for p in (config.ROOT / "conversations").glob("*.state.json"):
            st = util.read_json(p, {})
            if isinstance(st, dict):
                states[p.name] = st
    dem = {"khach_co_co": 0, "da_chot_crm": 0, "tung_handoff": 0,
           "kho_chiu": 0, "bot_ngung_nhieu": 0}
    for st in states.values():
        if not isinstance(st, dict):
            continue
        dem["khach_co_co"] += 1
        dem["da_chot_crm"] += bool(st.get("crm_done"))
        dem["tung_handoff"] += int(st.get("handoff_lan") or 0) > 0
        dem["kho_chiu"] += bool(st.get("no_contact"))
        dem["bot_ngung_nhieu"] += bool(st.get("stopped")) and not st.get("no_contact")
    return dem, nguon


def tong_the() -> dict:
    """Số TỪ TRƯỚC TỚI NAY. Tách bạch hai đơn vị mà dashboard hay trộn lẫn:

    - `luot_*`  = số LƯỢT (đếm sự kiện). Dựng từ bản ghi gộp theo ngày + sự kiện thô còn giữ.
    - `khach_*` = số NGƯỜI (đếm cờ trạng thái). Bản ghi gộp bỏ psid nên số người KHÔNG dựng
      lại được từ stats - phải đếm bên khach_state, nơi cờ sống theo khách chứ không bị dọn.

    Trộn hai thứ này là ra câu "351 khách chuyển chuyên gia" trong khi thật ra là 351 LƯỢT của
    một nhóm khách nhỏ hơn (khách quay lại được tính lại mỗi lần).
    """
    global _TONG_CACHE
    if _TONG_CACHE and time.time() - _TONG_CACHE[0] < _TONG_TTL_S:
        return _TONG_CACHE[1]
    events = _read_events(days=3650)
    luot = {"ok": 0, "handoff": 0, "khach_kho_chiu": 0, "error": 0}
    tin = tout = tcache = 0
    co_thô = set()
    for ev in events:
        kind = ev.get("kind", "")
        if ev.get("ts"):
            co_thô.add(_mocc_ngay(ev["ts"]))
        if kind == "usage":
            tin += int(ev.get("tin") or 0)
            tout += int(ev.get("tout") or 0)
            tcache += int(ev.get("tcache") or 0)
        elif kind in luot:
            luot[kind] += 1
    ngay = _doc_daily(0.0)
    for d, r in ngay.items():
        if d in co_thô:                      # ngày còn sự kiện thô -> đã đếm ở trên rồi
            continue
        for k in luot:
            luot[k] += int(r.get(k) or 0)
        tin += int(r.get("tin") or 0)
        tout += int(r.get("tout") or 0)
        tcache += int(r.get("tcache") or 0)
    dem, nguon = _dem_states()
    psids = fb.list_psids()
    moc = sorted(set(ngay) | co_thô)
    out = {
        "tu_ngay": moc[0] if moc else None,   # ngày đầu tiên CÓ THỐNG KÊ (không phải ngày mở bot)
        "khach_tong": len(psids) if psids is not None else dem["khach_co_co"],
        "khach_da_chot_crm": dem["da_chot_crm"],
        "khach_tung_handoff": dem["tung_handoff"],
        # Cờ handoff trên từng khách chỉ bắt đầu ghi từ ngày này -> ô "khách từng chuyển chuyên
        # gia" KHÔNG bao gồm khách cũ. Đừng bỏ mốc đi: số nhỏ hơn thực tế mà không ai biết.
        "handoff_tu_ngay": MOC_CO_HANDOFF,
        "khach_kho_chiu": dem["kho_chiu"],
        "khach_bot_ngung_nhieu": dem["bot_ngung_nhieu"],
        "nguon_khach": nguon,
        "luot_ok": luot["ok"],
        "luot_handoff": luot["handoff"],
        "luot_kho_chiu": luot["khach_kho_chiu"],
        "luot_error": luot["error"],
        "tokens": tin + tout,
        "cost_usd": round(_usd(tin, tout, tcache), 2),
    }
    _TONG_CACHE = (time.time(), out)
    return out


def recent_errors(days: int = 7, limit: int = 20) -> dict:
    """Lỗi gần đây gom theo loại + vài dòng mới nhất. Để biết bot đang hỏng KIỂU gì, không chỉ 'có lỗi'."""
    rows = [e for e in _read_events(days) if e.get("kind") == "error"]
    groups: dict[str, int] = {}
    for e in rows:
        key = (e.get("note") or "không rõ").split(":")[0][:60]
        groups[key] = groups.get(key, 0) + 1
    rows.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return {
        "total": len(rows),
        "by_type": sorted(({"type": k, "count": v} for k, v in groups.items()),
                          key=lambda x: x["count"], reverse=True),
        "recent": [{"at": datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M"),
                    "psid": e.get("psid", ""), "note": e.get("note", "")} for e in rows[:limit]],
    }


def _ngay_trong_thang(thang: str) -> list[str]:
    """Danh sách ngày ISO của 1 tháng 'YYYY-MM'. Tháng hiện tại thì dừng ở HÔM NAY - kéo dài
    tới cuối tháng là biểu đồ có một dãy cột 0 của những ngày CHƯA XẢY RA."""
    nam, thg = (int(x) for x in thang.split("-")[:2])
    d = date(nam, thg, 1)
    hom_nay = datetime.now().date()
    out = []
    while d.month == thg and d <= hom_nay:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def thang_co_du_lieu() -> list[str]:
    """Các tháng CÓ SỐ, mới nhất trước - để dashboard dựng ô chọn tháng.

    Lấy từ bản ghi gộp theo ngày (giữ toàn bộ lịch sử) + sự kiện thô của những ngày chưa gộp.
    """
    ngay = set(_doc_daily(0.0))
    ngay |= {_mocc_ngay(e["ts"]) for e in _read_events(days=3650) if e.get("ts")}
    return sorted({d[:7] for d in ngay if len(d) >= 7}, reverse=True)


def summary(days: int = 7, thang: str | None = None) -> dict:
    """Tổng hợp cho dashboard: đếm theo loại, tỉ lệ thành công, thời gian trả lời, chuỗi theo ngày.

    `thang="YYYY-MM"` -> chỉ tính đúng tháng đó (bỏ qua `days`). Số của tháng cũ vẫn dựng được
    vì bản ghi gộp theo ngày giữ cả lịch sử; chỉ những thứ cần psid (khách riêng biệt) là không.
    """
    if thang:
        moc_ngay = _ngay_trong_thang(thang)
        if not moc_ngay:                       # tháng ở tương lai / chuỗi sai -> không có gì
            moc_ngay = []
        days = max(1, (datetime.now().date() - date(*(int(x) for x in thang.split("-")[:2]), 1)).days + 1)
    else:
        today = datetime.now().date()
        moc_ngay = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    trong_ky = set(moc_ngay)
    # Đọc rộng theo `days` rồi LỌC lại theo đúng tập ngày: cửa sổ tháng không trùng cửa sổ
    # "N ngày gần nhất" (tháng trước nằm hoàn toàn ngoài 7 ngày).
    events = [e for e in _read_events(days) if _mocc_ngay(e.get("ts") or 0) in trong_ky]
    counts: dict[str, int] = {k: 0 for k in _LOAI_LUON_CO}
    durs: list[float] = []
    daily: dict[str, dict] = {d: {"ok": 0, "error": 0, "handoff": 0} for d in moc_ngay}

    tok_in = tok_out = tok_cache = 0
    psids: set[str] = set()
    for ev in events:
        kind = ev.get("kind", "")
        if kind == "usage":
            tok_in += int(ev.get("tin") or 0)
            tok_out += int(ev.get("tout") or 0)
            tok_cache += int(ev.get("tcache") or 0)
            continue
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
        if kind == "ok" and "dur" in ev:
            durs.append(float(ev["dur"]))
        if kind in ("ok", "error", "handoff"):
            psids.add(ev.get("psid", ""))
            d = datetime.fromtimestamp(ev["ts"]).date().isoformat()
            if d in daily:
                daily[d][kind] = daily[d].get(kind, 0) + 1

    # Ngày đã bị dọn -> cộng từ bản ghi tổng. Bỏ qua ngày còn sự kiện thô để khỏi đếm 2 lần.
    co_thô = {datetime.fromtimestamp(e["ts"]).date().isoformat() for e in events if e.get("ts")}
    dur_sum, dur_n = sum(durs), len(durs)
    dur_max = max(durs) if durs else 0.0
    khach_gop = 0
    for d, r in _doc_daily(time.time() - days * 86400).items():
        if d in co_thô or d not in trong_ky:      # ngoài kỳ đang xem (lọc tháng) -> bỏ
            continue
        tok_in += int(r.get("tin") or 0)
        tok_out += int(r.get("tout") or 0)
        tok_cache += int(r.get("tcache") or 0)
        for k, v in r.items():
            if k not in _KHONG_PHAI_LOAI and isinstance(v, (int, float)):
                counts[k] = counts.get(k, 0) + int(v)
        dur_sum += float(r.get("dur_sum") or 0)
        dur_n += int(r.get("dur_n") or 0)
        dur_max = max(dur_max, float(r.get("dur_max") or 0))
        khach_gop += int(r.get("psids") or 0)
        if d in daily:
            for k in ("ok", "error", "handoff"):
                daily[d][k] = daily[d].get(k, 0) + int(r.get(k) or 0)

    answered = counts["ok"] + counts["handoff"]          # handoff = bot xử lý đúng (chuyển người)
    # Hỏng = lỗi kỹ thuật + ca khách bực tới mức bot phải tự tắt. Trước đây mẫu số chỉ có
    # `error` nên tỉ lệ luôn 100% kể cả tuần có khách bỏ đi vì bot.
    hong = counts["error"] + counts["khach_kho_chiu"]
    total = answered + hong
    cost_usd = _usd(tok_in, tok_out, tok_cache)
    return {
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "tokens_cached": tok_cache,
        "cost_usd": round(cost_usd, 4),
        "gia": {"in": config.PRICE_IN_USD, "out": config.PRICE_OUT_USD,
                "cached": config.PRICE_CACHED_IN_USD, "model": config.model_id()},
        "days": len(moc_ngay),
        "thang": thang,
        "counts": counts,
        "total_handled": total,
        "failed": hong,
        "success_rate": round(answered / total * 100, 1) if total else None,
        # Ngày đã gộp chỉ còn ĐẾM khách, không còn danh sách psid -> khách quay lại nhiều ngày
        # bị tính trùng. Số này để nhìn xu hướng, không dùng đối soát.
        "active_customers": len(psids) + khach_gop,
        "avg_reply_s": round(dur_sum / dur_n, 1) if dur_n else None,
        "max_reply_s": round(dur_max, 1) if dur_max else None,
        "daily": [{"date": d, **v} for d, v in daily.items()],
    }
