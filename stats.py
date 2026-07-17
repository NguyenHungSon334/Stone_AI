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

import config

_STATS_DIR = config.ROOT / "stats"
_EVENTS = _STATS_DIR / "events.jsonl"
_LOCK = threading.Lock()


def log_event(kind: str, psid: str, duration_s: float | None = None, note: str = "") -> None:
    """Ghi 1 sự kiện. Không bao giờ ném lỗi ra ngoài (stats chết không được kéo bot chết)."""
    try:
        row = {"ts": time.time(), "kind": kind, "psid": str(psid)}
        if duration_s is not None:
            row["dur"] = round(duration_s, 2)
        if note:
            row["note"] = note[:200]
        with _LOCK:
            _STATS_DIR.mkdir(parents=True, exist_ok=True)
            with _EVENTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[stats] ghi lỗi: {type(e).__name__}: {e}", file=sys.stderr)


def log_usage(psid: str, tok_in: int, tok_out: int) -> None:
    """Ghi token 1 câu trả lời (input gồm cache, output gồm thinking)."""
    try:
        row = {"ts": time.time(), "kind": "usage", "psid": str(psid),
               "tin": int(tok_in), "tout": int(tok_out)}
        with _LOCK:
            _STATS_DIR.mkdir(parents=True, exist_ok=True)
            with _EVENTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[stats] ghi usage lỗi: {type(e).__name__}: {e}", file=sys.stderr)


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
