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
from pathlib import Path

import alerts
import config
import fb

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


def _read_events(days: int = 30) -> list[dict]:
    """Đọc sự kiện trong N ngày gần nhất. File hỏng dòng nào bỏ dòng đó."""
    cutoff = time.time() - days * 86400
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

    def day_usd(d) -> float:
        v = per_day.get(d.isoformat())
        return _usd(*v) if v else 0.0

    d7 = sum(day_usd(today - timedelta(days=i)) for i in range(7))
    total = sum(_usd(*v) for v in per_day.values())
    replies = sum(replies_per_psid.values())
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
        "active_customers": len(psids),
        "avg_reply_s": round(sum(durs) / len(durs), 1) if durs else None,
        "max_reply_s": round(max(durs), 1) if durs else None,
        "daily": [{"date": d, **v} for d, v in daily.items()],
    }
