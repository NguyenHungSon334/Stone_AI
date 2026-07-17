"""
API admin cho dashboard: thống kê, danh sách khách + lịch sử chat, đọc/ghi cài đặt.

Bảo vệ bằng BOT_DASH_TOKEN (.env): mọi request cần ?token=... hoặc header X-Dash-Token.
Token trống = toàn bộ trang admin trả 403 (an toàn mặc định).
"""
import asyncio
import hmac
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
import messenger
import stats

_profile_name = messenger.profile_name   # dùng chung cache tên với thông báo admin

router = APIRouter(prefix="/admin")

_HIST_DIR = config.ROOT / "conversations"
_DASHBOARD_HTML = config.ROOT / "dashboard.html"

# Các key .env chỉnh được từ dashboard (whitelist - không cho sửa token/secret qua UI).
_ENV_EDITABLE = {
    "BOT_MODEL": r"^[a-z0-9.-]+$",
    "BOT_ADMIN_UIDS": r"^[0-9, ]*$",
    "BOT_PER_PSID_RATE_S": r"^\d+(\.\d+)?$",
    "BOT_MAX_CONCURRENT": r"^\d+$",
}


def _check_token(request: Request) -> None:
    token = request.query_params.get("token") or request.headers.get("X-Dash-Token") or ""
    if not config.DASH_TOKEN or not hmac.compare_digest(token, config.DASH_TOKEN):
        raise HTTPException(403, "sai hoặc thiếu token (đặt BOT_DASH_TOKEN trong .env)")


@router.get("")
async def dashboard_page(request: Request):
    _check_token(request)
    if not _DASHBOARD_HTML.exists():
        raise HTTPException(404, "thiếu dashboard.html")
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")


@router.get("/api/overview")
async def overview(request: Request, days: int = 7):
    _check_token(request)
    days = max(1, min(days, 90))
    total_customers = len(list(_HIST_DIR.glob("*.json"))) if _HIST_DIR.exists() else 0
    return {
        "stats": stats.summary(days),
        "total_customers": total_customers,
        "model": config.MODEL,
        "configured": bool(config.PAGE_TOKEN),
    }


def _last_text(msgs: list) -> str:
    """Tin nhắn text cuối cùng trong log (content có thể là str hoặc list block)."""
    for m in reversed(msgs):
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ""


@router.get("/api/customers")
async def customers(request: Request):
    _check_token(request)
    out = []
    if _HIST_DIR.exists():
        for p in _HIST_DIR.glob("*.json"):
            try:
                msgs = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "psid": p.stem,
                    "name": await _profile_name(p.stem),
                    "messages": len(msgs),
                    "last_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes"),
                    "last_text": _last_text(msgs)[:120],
                })
            except Exception as e:
                print(f"[admin] đọc {p.name} lỗi: {e}", file=sys.stderr)
    out.sort(key=lambda r: r["last_at"], reverse=True)
    return {"customers": out}


@router.get("/api/customers/{psid}")
async def customer_detail(request: Request, psid: str):
    _check_token(request)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", psid)[:80]
    p = _HIST_DIR / f"{safe}.json"
    if not p.exists():
        raise HTTPException(404, "không có khách này")
    msgs = json.loads(p.read_text(encoding="utf-8"))
    # Chỉ trả turn text (log sạch của brain.py đã là text, phòng hờ lọc block).
    clean = [{"role": m.get("role"), "text": m["content"]}
             for m in msgs if isinstance(m.get("content"), str)]
    return {"psid": psid, "name": await _profile_name(safe), "messages": clean}


@router.get("/api/settings")
async def get_settings(request: Request):
    _check_token(request)
    persona_path = config.DOCS_DIR / "Personal.md"
    return {
        "persona": persona_path.read_text(encoding="utf-8") if persona_path.exists() else "",
        "env": {
            "BOT_MODEL": config.MODEL,
            "BOT_ADMIN_UIDS": ",".join(config.ADMIN_UIDS),
            "BOT_PER_PSID_RATE_S": str(config.PER_PSID_RATE_S),
            "BOT_MAX_CONCURRENT": str(config.MAX_CONCURRENT),
        },
        # Trạng thái secrets: chỉ báo có/chưa, không lộ giá trị.
        "secrets": {
            "PAGE_TOKEN": bool(config.PAGE_TOKEN),
            "APP_SECRET": bool(config.APP_SECRET),
            "GEMINI_API_KEY": bool(config.GEMINI_API_KEY),
            "LARK_APP_ID": bool(config.LARK_APP_ID),
        },
        "public_url": config.PUBLIC_URL,
        "restart_needed_keys": ["BOT_MAX_CONCURRENT"],
    }


def _update_env_file(updates: dict[str, str]) -> None:
    """Ghi đè key trong .env (giữ nguyên dòng khác), thiếu key thì thêm cuối file."""
    env_path = config.ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.post("/api/settings")
async def save_settings(request: Request):
    _check_token(request)
    body = await request.json()

    if isinstance(body.get("persona"), str):
        persona = body["persona"].strip()
        if not persona:
            raise HTTPException(400, "persona không được trống")
        config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (config.DOCS_DIR / "Personal.md").write_text(persona + "\n", encoding="utf-8")

    env_in = body.get("env") or {}
    updates: dict[str, str] = {}
    for key, pattern in _ENV_EDITABLE.items():
        if key in env_in:
            val = str(env_in[key]).strip()
            if not re.match(pattern, val):
                raise HTTPException(400, f"{key} sai định dạng")
            updates[key] = val
    if updates:
        _update_env_file(updates)
        config.reload_env()

    return {"ok": True, "restart_needed": "BOT_MAX_CONCURRENT" in updates}


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=.*$")


@router.get("/api/env")
async def get_env(request: Request):
    _check_token(request)
    p = config.ROOT / ".env"
    return {"content": p.read_text(encoding="utf-8") if p.exists() else ""}


@router.post("/api/env")
async def save_env(request: Request):
    """Ghi đè nguyên file .env từ editor. Validate từng dòng để không hỏng file."""
    _check_token(request)
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(400, "nội dung .env trống")
    for i, line in enumerate(content.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not _ENV_LINE_RE.match(s):
            raise HTTPException(400, f"dòng {i} sai định dạng KEY=value: {s[:60]}")
    p = config.ROOT / ".env"
    bak = config.ROOT / ".env.bak"
    if p.exists():
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")   # giữ 1 bản lùi
    p.write_text(content.rstrip() + "\n", encoding="utf-8")
    config.reload_env()
    return {"ok": True, "message": "đã lưu (.env.bak giữ bản cũ). Token/secret đổi thì cần Restart."}


@router.post("/api/restart")
async def restart_bot(request: Request):
    """Restart bot: spawn process mới (chờ 2s cho port nhả) rồi tự thoát process này.

    ponytail: mất log stdout của process mới (detached); chạy service/PM2 thì thay bằng cơ chế đó.
    """
    _check_token(request)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    cmd = [sys.executable, "-c",
           ("import time, subprocess, sys; time.sleep(2); "
            f"subprocess.Popen([{sys.executable!r}, {str(config.ROOT / 'app.py')!r}], "
            f"cwd={str(config.ROOT)!r})")]
    subprocess.Popen(cmd, cwd=str(config.ROOT), creationflags=flags, close_fds=True)
    asyncio.get_running_loop().call_later(0.5, os._exit, 0)   # trả response xong mới chết
    return {"ok": True, "message": "đang restart, chờ ~5 giây"}
