"""
Thống kê bot: ghi sự kiện JSONL (1 dòng/sự kiện) + đọc tổng hợp cho dashboard.

Sự kiện: ok (trả lời xong), error (lỗi), handoff (chuyển chuyên gia), rate_limited (bị chặn spam).
File: stats/events.jsonl - append-only, đọc lại khi dashboard hỏi. Nhỏ gọn, không cần DB.
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta
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


def log_usage(psid: str, tok_in: int, tok_out: int) -> None:
    """Ghi token 1 câu trả lời (input gồm cache, output gồm thinking)."""
    _append({"ts": time.time(), "kind": "usage", "psid": str(psid),
             "tin": int(tok_in), "tout": int(tok_out)})


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
        r = out.setdefault(d, {"date": d, "tin": 0, "tout": 0, "ok": 0, "error": 0,
                               "handoff": 0, "rate_limited": 0,
                               "dur_sum": 0.0, "dur_n": 0, "dur_max": 0.0, "psids": 0})
        kind = ev.get("kind", "")
        if kind == "usage":
            r["tin"] += int(ev.get("tin") or 0)
            r["tout"] += int(ev.get("tout") or 0)
            continue
        if kind in ("ok", "error", "handoff", "rate_limited"):
            r[kind] += 1
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


def _usd(tin: int, tout: int) -> float:
    return tin / 1e6 * config.PRICE_IN_USD + tout / 1e6 * config.PRICE_OUT_USD


def cost_breakdown(days: int = 30) -> dict:
    """Bóc chi phí để KIỂM SOÁT tiền, không chỉ xem tổng.

    Trả: chi phí hôm nay/hôm qua/7 ngày/kỳ, trung bình mỗi câu trả lời, dự phóng 30 ngày,
    và TOP khách tốn nhất (1 khách hỏi lan man có thể ăn hết ngân sách mà tổng vẫn nhìn 'ổn').
    """
    events = _read_events(days)
    today = datetime.now().date()
    per_day: dict[str, list[int]] = {}
    per_psid: dict[str, list[int]] = {}
    replies_per_psid: dict[str, int] = {}
    for ev in events:
        if ev.get("kind") == "ok":
            replies_per_psid[ev.get("psid", "")] = replies_per_psid.get(ev.get("psid", ""), 0) + 1
        if ev.get("kind") != "usage":
            continue
        tin, tout = int(ev.get("tin") or 0), int(ev.get("tout") or 0)
        d = datetime.fromtimestamp(ev["ts"]).date().isoformat()
        per_day.setdefault(d, [0, 0])
        per_day[d][0] += tin
        per_day[d][1] += tout
        psid = ev.get("psid", "")
        per_psid.setdefault(psid, [0, 0])
        per_psid[psid][0] += tin
        per_psid[psid][1] += tout

    # Ngày đã bị dọn -> lấy từ bản ghi tổng. CHỈ nhận ngày chưa có sự kiện thô nào, không thì
    # ngày giao thời (vừa gộp vừa còn event) bị đếm 2 lần.
    replies_gop = 0
    co_thô = {datetime.fromtimestamp(e["ts"]).date().isoformat() for e in events if e.get("ts")}
    for d, r in _doc_daily(time.time() - days * 86400).items():
        if d in co_thô:
            continue
        per_day[d] = [int(r.get("tin") or 0), int(r.get("tout") or 0)]
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
        "daily": [{"date": (today - timedelta(days=i)).isoformat(),
                   "usd": round(day_usd(today - timedelta(days=i)), 4)} for i in range(days - 1, -1, -1)],
        "top_customers": [{"psid": p, "usd": round(c, 4), "replies": n} for p, c, n in top if c > 0],
    }


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


def summary(days: int = 7) -> dict:
    """Tổng hợp cho dashboard: đếm theo loại, tỉ lệ thành công, thời gian trả lời, chuỗi theo ngày."""
    events = _read_events(days)
    counts = {"ok": 0, "error": 0, "handoff": 0, "rate_limited": 0}
    durs: list[float] = []
    daily: dict[str, dict] = {}
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = {"ok": 0, "error": 0, "handoff": 0}

    tok_in = tok_out = 0
    psids: set[str] = set()
    for ev in events:
        kind = ev.get("kind", "")
        if kind == "usage":
            tok_in += int(ev.get("tin") or 0)
            tok_out += int(ev.get("tout") or 0)
            continue
        if kind in counts:
            counts[kind] += 1
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
        if d in co_thô:
            continue
        tok_in += int(r.get("tin") or 0)
        tok_out += int(r.get("tout") or 0)
        for k in counts:
            counts[k] += int(r.get(k) or 0)
        dur_sum += float(r.get("dur_sum") or 0)
        dur_n += int(r.get("dur_n") or 0)
        dur_max = max(dur_max, float(r.get("dur_max") or 0))
        khach_gop += int(r.get("psids") or 0)
        if d in daily:
            for k in ("ok", "error", "handoff"):
                daily[d][k] = daily[d].get(k, 0) + int(r.get(k) or 0)

    answered = counts["ok"] + counts["handoff"]          # handoff = bot xử lý đúng (chuyển người)
    total = answered + counts["error"]
    cost_usd = tok_in / 1e6 * config.PRICE_IN_USD + tok_out / 1e6 * config.PRICE_OUT_USD
    return {
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "cost_usd": round(cost_usd, 4),
        "days": days,
        "counts": counts,
        "total_handled": total,
        "success_rate": round(answered / total * 100, 1) if total else None,
        # Ngày đã gộp chỉ còn ĐẾM khách, không còn danh sách psid -> khách quay lại nhiều ngày
        # bị tính trùng. Số này để nhìn xu hướng, không dùng đối soát.
        "active_customers": len(psids) + khach_gop,
        "avg_reply_s": round(dur_sum / dur_n, 1) if dur_n else None,
        "max_reply_s": round(dur_max, 1) if dur_max else None,
        "daily": [{"date": d, **v} for d, v in daily.items()],
    }
