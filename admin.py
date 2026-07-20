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
import control
import fb
import messenger
import stats
import util

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
            if p.name.endswith((".crm.json", ".sum.json")):   # sidecar CRM/tóm tắt, không phải log tin
                continue
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
    p = _HIST_DIR / f"{util.safe_psid(psid)}.json"
    if not p.exists():
        raise HTTPException(404, "không có khách này")
    msgs = json.loads(p.read_text(encoding="utf-8"))
    # Chỉ trả turn text (log sạch của brain.py đã là text, phòng hờ lọc block).
    clean = [{"role": m.get("role"), "text": m["content"], "at": m.get("at", "")}
             for m in msgs if isinstance(m.get("content"), str)]
    return {"psid": psid, "name": await _profile_name(util.safe_psid(psid)), "messages": clean}


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
            "LARK_WEBHOOK": bool(config.LARK_WEBHOOK_URL),
        },
        "public_url": config.PUBLIC_URL,
        "restart_needed_keys": ["BOT_MAX_CONCURRENT"],
    }


@router.post("/api/test-lark")
async def test_lark(request: Request):
    """Nút 'Test bot admin Lark': gửi 1 tin test vào group Lark, trả kết quả kết nối."""
    _check_token(request)
    return await messenger.lark_ping()


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


# --- Cấu hình bằng Ô NHẬP thay vì sửa file .env tay ---
# 1 nguồn sự thật: schema định nghĩa ở đây, giao diện tự dựng ô theo nó -> thêm biến mới chỉ
# sửa 1 chỗ, không phải sửa cả HTML lẫn Python (kiểu đó chắc chắn lệch nhau sau vài lần).
# `bi_mat`: chỉ trả về dạng che, để TRỐNG khi lưu = giữ nguyên giá trị cũ (không bao giờ lộ ra web).
_F = lambda key, nhan, nhom, **kw: {"key": key, "nhan": nhan, "nhom": nhom, **kw}   # noqa: E731

_CONFIG_FIELDS = [
    _F("MSGR_PAGE_TOKEN", "Page Access Token", "Facebook", bi_mat=True, restart=True,
       goi_y="Token của PAGE (không phải USER). Hết hạn là bot chết câm."),
    _F("MSGR_VERIFY_TOKEN", "Verify Token", "Facebook", bi_mat=True, restart=True,
       goi_y="Phải khớp ô Verify Token bên Meta Developers."),
    _F("MSGR_APP_SECRET", "App Secret", "Facebook", bi_mat=True, restart=True,
       goi_y="Dùng ký webhook. Sai là bot chặn hết tin của FB."),
    _F("MSGR_GRAPH_VER", "Phiên bản Graph API", "Facebook", mau=r"^v\d+\.\d+$", restart=True),
    _F("PUBLIC_URL", "URL công khai của bot", "Facebook", mau=r"^(https?://\S+)?$",
       goi_y="Domain thật hoặc ngrok. Dùng để canh tunnel chết."),

    _F("GEMINI_API_KEY", "Gemini API Key", "AI", bi_mat=True, restart=True,
       goi_y="Lấy ở aistudio.google.com/apikey."),
    _F("BOT_MODEL", "Model", "AI", kieu="chon",
       chon=[["lite", "Flash-Lite (rẻ nhất)"], ["flash", "Flash (cân bằng)"], ["pro", "Pro (đắt nhất)"]]),
    _F("GEMINI_PRICE_IN_USD", "Giá token VÀO (USD/1 triệu)", "AI", kieu="so",
       goi_y="Chỉ để tính tiền trên dashboard. Google đổi giá thì sửa ở đây."),
    _F("GEMINI_PRICE_OUT_USD", "Giá token RA (USD/1 triệu)", "AI", kieu="so"),

    _F("BOT_ADMIN_UIDS", "PSID admin", "Cảnh báo", mau=r"^[0-9, ]*$",
       goi_y="Nhiều admin cách nhau dấu phẩy."),
    _F("LARK_WEBHOOK_URL", "Lark webhook (nhận cảnh báo)", "Cảnh báo", bi_mat=True,
       goi_y="TRỐNG = mọi cảnh báo lỗi bị nuốt, không ai được báo."),
    _F("LARK_WEBHOOK_SECRET", "Lark webhook secret", "Cảnh báo", bi_mat=True,
       goi_y="Chỉ điền khi bật 'ký' ở webhook Lark."),

    _F("LARK_APP_ID", "Lark App ID", "Lark Base", bi_mat=True, restart=True),
    _F("LARK_APP_SECRET", "Lark App Secret", "Lark Base", bi_mat=True, restart=True),
    _F("LARK_BASE_APP_TOKEN", "Base ảnh sản phẩm", "Lark Base", restart=True),
    _F("LARK_TABLE_ID", "Bảng ảnh sản phẩm", "Lark Base", restart=True),
    _F("LARK_CRM_APP_TOKEN", "Base CRM (lead)", "Lark Base", restart=True,
       goi_y="TRỐNG = khách để lại SĐT nhưng lead không vào CRM."),
    _F("LARK_CRM_TABLE_ID", "Bảng CRM", "Lark Base", restart=True),
    _F("LARK_PRODUCT_FIELD", "Tên cột mã sản phẩm", "Lark Base", restart=True),
    _F("LARK_IMAGE_FIELD", "Tên cột ảnh", "Lark Base", restart=True),
    _F("LARK_DOMAIN", "Domain Lark", "Lark Base", restart=True,
       goi_y="open.larksuite.com (quốc tế) hoặc open.feishu.cn (Trung Quốc)."),

    _F("BOT_FOLLOWUP_ENABLED", "Tự nhắc khách im", "Chăm khách", kieu="bat_tat"),
    _F("BOT_FOLLOWUP_AFTER_H", "Im bao lâu thì nhắc (giờ)", "Chăm khách", kieu="so",
       goi_y="Giữ dưới 24 cho hợp cửa sổ tin nhắn của Facebook."),
    _F("BOT_FOLLOWUP_CHECK_MIN", "Chu kỳ quét (phút)", "Chăm khách", kieu="so"),
    _F("BOT_MISSED_AFTER_MIN", "Báo tin rơi sau (phút)", "Chăm khách", kieu="so",
       goi_y="Khách nhắn mà bot chưa trả lời quá ngần này phút thì xử lý."),
    _F("BOT_MISSED_AUTOREPLY", "Bot tự trả lời bù", "Chăm khách", kieu="bat_tat",
       goi_y="Bật: bot tự trả lời khách bị bỏ sót (đọc lại lịch sử nên khớp ngữ cảnh). "
             "Không áp dụng cho khách đã handoff và tin quá 24h - những ca đó chỉ báo admin."),

    _F("BOT_PER_PSID_RATE_S", "Giãn tin mỗi khách (giây)", "Vận hành", kieu="so"),
    _F("BOT_MAX_CONCURRENT", "Số khách xử lý cùng lúc", "Vận hành", kieu="so", restart=True),
    _F("BOT_TUNNEL_WATCH", "Canh tunnel chết", "Vận hành", kieu="bat_tat"),
    _F("BOT_TUNNEL_CHECK_MIN", "Chu kỳ canh tunnel (phút)", "Vận hành", kieu="so"),
    _F("BOT_DASH_TOKEN", "Token trang quản trị", "Vận hành", bi_mat=True,
       goi_y="ĐỔI LÀ LINK DASHBOARD HIỆN TẠI HẾT HIỆU LỰC - phải mở lại bằng token mới."),

    _F("FIREBASE_CRED", "File key Firebase", "Firebase", restart=True),
    _F("FIREBASE_DB_URL", "Realtime DB URL", "Firebase", restart=True,
       goi_y="TRỐNG = tắt backup cloud, lịch sử chỉ nằm trên máy."),
]
_CONFIG_BY_KEY = {f["key"]: f for f in _CONFIG_FIELDS}


_MAC_DINH = {'MSGR_GRAPH_VER': 'v21.0', 'BOT_MODEL': 'flash', 'GEMINI_PRICE_IN_USD': '1.5', 'GEMINI_PRICE_OUT_USD': '9.0', 'BOT_FOLLOWUP_ENABLED': '1', 'BOT_FOLLOWUP_AFTER_H': '4', 'BOT_FOLLOWUP_CHECK_MIN': '15', 'BOT_MISSED_AFTER_MIN': '10', 'BOT_MISSED_AUTOREPLY': '1', 'BOT_PER_PSID_RATE_S': '3', 'BOT_MAX_CONCURRENT': '4', 'BOT_TUNNEL_WATCH': '1', 'BOT_TUNNEL_CHECK_MIN': '3', 'LARK_DOMAIN': 'https://open.larksuite.com', 'LARK_PRODUCT_FIELD': 'Mã Sản Phẩm', 'LARK_IMAGE_FIELD': 'Ảnh'}


def _read_env_file() -> dict[str, str]:
    """Đọc .env thành dict. utf-8-sig để BOM (Notepad/PowerShell hay thêm) không dính vào key đầu."""
    p = config.ROOT / ".env"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _che(v: str) -> str:
    """Che secret: đủ để nhận ra 'có phải cái mình vừa dán không', không đủ để dùng lại."""
    if not v:
        return ""
    return f"{v[:4]}…{v[-4:]} ({len(v)} ký tự)" if len(v) > 12 else "…đã đặt…"


@router.get("/api/config")
async def get_config(request: Request):
    """Schema + giá trị hiện tại để giao diện tự dựng ô nhập."""
    _check_token(request)
    env = _read_env_file()
    fields = []
    for f in _CONFIG_FIELDS:
        raw = env.get(f["key"], "")
        fields.append({**f,
                       "gia_tri": _che(raw) if f.get("bi_mat") else raw,
                       "mac_dinh": _MAC_DINH.get(f["key"], ""),
                       "da_dat": bool(raw)})
    nhom: list[str] = []
    for f in _CONFIG_FIELDS:                       # giữ thứ tự khai báo, không sort abc
        if f["nhom"] not in nhom:
            nhom.append(f["nhom"])
    return {"nhom": nhom, "fields": fields,
            "ngoai_schema": sorted(k for k in env if k not in _CONFIG_BY_KEY)}


@router.post("/api/config")
async def save_config(request: Request):
    """Lưu từ ô nhập. Secret để TRỐNG = giữ nguyên (không thể vô tình xoá token bằng cách bỏ trống)."""
    _check_token(request)
    body = await request.json()
    vao = body.get("fields") or {}
    updates: dict[str, str] = {}
    for key, val in vao.items():
        f = _CONFIG_BY_KEY.get(key)
        if not f:
            raise HTTPException(400, f"không cho sửa key lạ: {key}")
        val = str(val).strip()
        if f.get("bi_mat") and not val:
            continue                                # bỏ trống secret = giữ giá trị cũ
        if f.get("kieu") == "so" and val and not re.match(r"^\d+(\.\d+)?$", val):
            raise HTTPException(400, f"{f['nhan']}: phải là số")
        if f.get("kieu") == "bat_tat" and val not in ("0", "1"):
            raise HTTPException(400, f"{f['nhan']}: chỉ 0 hoặc 1")
        if f.get("chon") and val not in [c[0] for c in f["chon"]]:
            raise HTTPException(400, f"{f['nhan']}: giá trị không hợp lệ")
        if f.get("mau") and not re.match(f["mau"], val):
            raise HTTPException(400, f"{f['nhan']}: sai định dạng")
        if "\n" in val or "\r" in val:
            raise HTTPException(400, f"{f['nhan']}: không được xuống dòng")
        updates[key] = val
    if not updates:
        return {"ok": True, "doi": 0, "can_restart": False, "message": "không có gì thay đổi"}
    _update_env_file(updates)
    config.reload_env()
    can_restart = [k for k in updates if _CONFIG_BY_KEY[k].get("restart")]
    return {"ok": True, "doi": len(updates), "can_restart": bool(can_restart),
            "keys_can_restart": can_restart,
            "message": f"đã lưu {len(updates)} mục"
                       + (f"; cần RESTART để ăn: {', '.join(can_restart)}" if can_restart else "")}


@router.get("/api/control")
async def get_control(request: Request, days: int = 30, force: int = 0):
    """Toàn cảnh: khách đang chờ, chi phí, vấn đề cần xử lý."""
    _check_token(request)
    return await control.snapshot(days=max(1, min(days, 90)), force=bool(force))


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=.*$")


@router.get("/api/env")
async def get_env(request: Request):
    _check_token(request)
    p = config.ROOT / ".env"
    # utf-8-sig: .env sửa bằng Notepad/PowerShell hay dính BOM (U+FEFF) ở đầu. Đọc kiểu utf-8
    # thường thì BOM lọt vào editor rồi quay lại validate -> báo "dòng 1 sai định dạng".
    return {"content": p.read_text(encoding="utf-8-sig") if p.exists() else ""}


@router.post("/api/env")
async def save_env(request: Request):
    """Ghi đè nguyên file .env từ editor. Validate từng dòng để không hỏng file."""
    _check_token(request)
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(400, "nội dung .env trống")
    content = content.lstrip("﻿")     # bỏ BOM -> ghi lại file sạch, lần sau khỏi dính
    for i, line in enumerate(content.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not _ENV_LINE_RE.match(s):
            raise HTTPException(400, f"dòng {i} sai định dạng KEY=value: {s[:60]}")
    p = config.ROOT / ".env"
    bak = config.ROOT / ".env.bak"
    if p.exists():
        bak.write_text(p.read_text(encoding="utf-8-sig"), encoding="utf-8")   # giữ 1 bản lùi
    p.write_text(content.rstrip() + "\n", encoding="utf-8")
    config.reload_env()
    return {"ok": True, "message": "đã lưu (.env.bak giữ bản cũ). Token/secret đổi thì cần Restart."}


@router.post("/api/clean-data")
async def clean_data(request: Request):
    """Xóa TOÀN BỘ data khách: conversations + stats ở local VÀ Firebase. KHÔNG hồi được.

    GIỮ nguyên: bảng sản phẩm + persona (data/docs) + CRM lead trên Lark.
    brain đọc history từ đĩa mỗi lượt (không cache RAM) -> xóa đĩa + Firebase là sạch, khỏi restart.
    """
    _check_token(request)
    removed = 0
    for d in (_HIST_DIR, config.ROOT / "stats"):
        if not d.exists():
            continue
        for p in d.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                except Exception as e:
                    print(f"[clean] xóa {p.name} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
    fb_cleared = fb.clear_all()
    msg = f"đã xóa {removed} file local" + (" + Firebase" if fb_cleared else " (Firebase tắt/lỗi)")
    print(f"[clean] {msg}", file=sys.stderr)
    return {"ok": True, "removed_files": removed, "firebase_cleared": fb_cleared, "message": msg}


@router.post("/api/restart")
async def restart_bot(request: Request):
    """Restart bot. 2 chế độ theo môi trường:

    - RESTART_MODE=exit (Docker/systemd): thoát process, cơ chế 'restart: always' dựng lại.
    - mặc định (Windows local dev): spawn process mới rồi thoát process cũ.
    """
    _check_token(request)
    if os.getenv("RESTART_MODE") == "exit":
        asyncio.get_running_loop().call_later(0.5, os._exit, 0)   # compose/systemd dựng lại
        return {"ok": True, "message": "đang restart (container tự dựng lại), chờ ~5 giây"}
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    cmd = [sys.executable, "-c",
           ("import time, subprocess, sys; time.sleep(2); "
            f"subprocess.Popen([{sys.executable!r}, {str(config.ROOT / 'app.py')!r}], "
            f"cwd={str(config.ROOT)!r})")]
    subprocess.Popen(cmd, cwd=str(config.ROOT), creationflags=flags, close_fds=True)
    asyncio.get_running_loop().call_later(0.5, os._exit, 0)   # trả response xong mới chết
    return {"ok": True, "message": "đang restart, chờ ~5 giây"}
