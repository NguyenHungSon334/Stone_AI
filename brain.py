"""
Não bot = Google Gemini API (google-genai SDK).

- System instruction = persona (Personal.md) + bảng sản phẩm CSV (Gemini 2.5 tự cache ngầm).
- Tool duy nhất: suggest_products (tra theo tầm giá / mã). Tool là hàm python chạy ngay
  trong process này -> không mở shell, an toàn với bot khách.
- Mỗi khách (psid) giữ lịch sử hội thoại riêng trong conversations/<psid>.json.
  Format log giữ nguyên như bản Anthropic ({"role": "user"|"assistant", "content": str})
  -> dashboard + log cũ dùng tiếp, không migrate.
"""
import asyncio
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

import config
import fb
import stats
from bot_tools import lark_image
from bot_tools.find_by_price import parse_money, render, rows_by_ids, search

# 1 file/khách, GIỮ TOÀN BỘ log hội thoại. API chỉ nạp _MAX_TURNS lượt cuối để chặn token.
_HIST_DIR = config.ROOT / "conversations"
_MAX_TURNS = 12          # số lượt gần nhất GỬI cho API (log vẫn giữ đủ)
_MAX_TOOL_LOOPS = 5      # trần số vòng gọi tool trong 1 câu trả lời

# BOT_MODEL: alias -> model id API. Có dấu '.' hoặc bắt đầu 'gemini' thì coi là id đầy đủ.
_MODEL_ALIAS = {"flash": "gemini-3.5-flash", "pro": "gemini-2.5-pro",
                "lite": "gemini-2.5-flash-lite"}

_MAX_NEW_IMAGES = 4      # trần ảnh gửi kèm 1 tin (mỗi sản phẩm nhắc lần đầu = 1 ảnh)

_TOOLS = [types.Tool(function_declarations=[types.FunctionDeclaration(
    name="suggest_products",
    description=("Gợi ý sản phẩm đá mỹ nghệ. Dùng khi: khách hỏi theo tầm giá/ngân sách "
                 "(truyền 'max', kèm min/stone/category nếu rõ), HOẶC khi muốn giới thiệu "
                 "mẫu cụ thể (truyền 'product_ids'). Kết quả là danh sách mã+tên+giá. "
                 "Ảnh sản phẩm hệ thống TỰ ĐỘNG gửi kèm khi tin nhắn nhắc tới mã lần đầu, "
                 "không cần làm gì thêm."),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "max": {"type": "string", "description": "Giá tối đa, vd '100tr', '1.5 tỷ', '100000000'"},
            "min": {"type": "string", "description": "Giá tối thiểu, mặc định 0"},
            "stone": {"type": "string", "description": "Loại đá: xanh đen, xanh rêu, xám BĐ, GRN, xanh Bình Định, trắng Yên Bái"},
            "category": {"type": "string", "description": "Danh mục, vd 'Trường Tồn'"},
            "limit": {"type": "integer", "description": "Số kết quả tối đa (mặc định 15)"},
            "product_ids": {"type": "array", "items": {"type": "string"},
                            "description": "Danh sách mã cụ thể (vd ['M01','LD03']) - dùng thay cho lọc giá"},
        },
    },
)])]


class BrainError(RuntimeError):
    """Lỗi khi gọi API (thiếu key, không ra nội dung, quá nhiều vòng tool). Layer trên bắt để báo admin."""


_client = None


def _get_client() -> genai.Client:
    global _client
    if not config.GEMINI_API_KEY:
        raise BrainError("Thiếu GEMINI_API_KEY trong .env.")
    if _client is None:
        # timeout 60s: request treo không giữ mãi lock khách + slot semaphore (chống đơ toàn bộ bot).
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=60_000))
    return _client


def _model_id() -> str:
    m = config.MODEL
    return _MODEL_ALIAS.get(m, m)


def _system_text() -> str:
    """persona + bảng sản phẩm + ngày giờ thật. Gemini tự cache implicit phần prefix lặp lại.

    Ngày giờ đặt CUỐI (sau phần tĩnh) để không phá cache prefix; độ chi tiết tới phút là đủ."""
    now = datetime.now()
    return (config.persona() + "\n\n# BẢNG SẢN PHẨM (tra cứu chi tiết mã/tên/kích thước/giá)\n"
            + config.catalog_csv()
            + f"\n\n# THỜI GIAN THỰC\nBây giờ là {now:%H:%M} ngày {now.day}/{now.month}/{now.year}."
            + " Dùng mốc này khi khách nói thời gian tương đối (tháng này, cuối năm...).")


# Regex mã sản phẩm dựng từ CSV, cache theo nội dung catalog (đổi CSV -> tự dựng lại).
_CODE_RE_CACHE: tuple[int, re.Pattern] | None = None


def _code_pattern() -> re.Pattern:
    """Regex khớp mọi mã trong catalog (cột 1), kèm biến thể .N (M01.2). Ưu tiên mã dài trước."""
    global _CODE_RE_CACHE
    csv_text = config.catalog_csv()
    key = hash(csv_text)
    if _CODE_RE_CACHE and _CODE_RE_CACHE[0] == key:
        return _CODE_RE_CACHE[1]
    codes = set()
    for line in csv_text.splitlines()[1:]:
        code = line.split(",", 1)[0].strip().upper()
        if re.fullmatch(r"[A-Z]{1,5}\d{1,4}", code):
            codes.add(code)
    alts = "|".join(re.escape(c) for c in sorted(codes, key=len, reverse=True)) or r"(?!x)x"
    pat = re.compile(rf"\b({alts})(?:\.\d+)?\b", re.IGNORECASE)
    _CODE_RE_CACHE = (key, pat)
    return pat


def _codes_in(text: str) -> set[str]:
    """Các mã gốc (biến thể quy về gốc) xuất hiện trong text."""
    return {m.group(1).upper() for m in _code_pattern().finditer(text or "")}


def _new_image_markers(history: list, reply: str) -> str:
    """Marker ảnh cho các mã nhắc LẦN ĐẦU trong hội thoại (chưa từng xuất hiện ở lượt trước).

    1 mã = 1 ảnh, tối đa _MAX_NEW_IMAGES/tin. Mã không có ảnh bỏ qua im lặng.
    """
    seen: set[str] = set()
    for m in history:
        if isinstance(m.get("content"), str):
            seen |= _codes_in(m["content"])
    new_codes = sorted(_codes_in(reply) - seen)
    markers: list[str] = []
    for code in new_codes:
        if len(markers) >= _MAX_NEW_IMAGES:
            break
        try:
            toks = lark_image.get_image_tokens(code)
        except Exception as e:
            print(f"[img] lấy ảnh {code} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            toks = []
        if toks:
            markers.append(f"<<IMG:{toks[0]}>>")
    return " ".join(markers)


def _run_tool(name: str, inp: dict) -> str:
    if name != "suggest_products":
        return f"Tool {name} không tồn tại."
    ids = inp.get("product_ids")
    if ids:
        results = rows_by_ids(ids if isinstance(ids, list) else [ids])
    else:
        mx = parse_money(inp.get("max", ""))
        if mx <= 0:
            return "Cần 'max' (tầm giá) hoặc 'product_ids'."
        mn = parse_money(inp.get("min", "0"))
        results = search(mx, mn if mn > 0 else 0.0, inp.get("stone"), inp.get("category"), int(inp.get("limit") or 15))
    if not results:
        return "Không có sản phẩm phù hợp."
    return render(results)


_HIST_LOCKS: dict[str, threading.Lock] = {}
_HIST_LOCKS_GUARD = threading.Lock()


def _psid_lock(psid: str) -> threading.Lock:
    """1 lock/khách: chặn 2 tin cùng khách ghi đè file (khác khách = khác file, không đua)."""
    with _HIST_LOCKS_GUARD:
        if len(_HIST_LOCKS) > 5000:                 # dọn lock rảnh -> dict không phình vô hạn
            for k in [k for k, l in _HIST_LOCKS.items() if not l.locked()]:
                _HIST_LOCKS.pop(k, None)
        lk = _HIST_LOCKS.get(psid)
        if lk is None:
            lk = _HIST_LOCKS[psid] = threading.Lock()
        return lk


def _psid_path(psid: str) -> Path:
    """conversations/<psid>.json. Làm sạch psid -> tên file an toàn."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(psid))[:80] or "unknown"
    return _HIST_DIR / f"{safe}.json"


def is_new_customer(psid: str) -> bool:
    """Khách lần đầu nhắn (báo admin). Cache local miss -> hỏi Firebase (đĩa mới,
    khách có thể đã tồn tại). Chỉ 1 lần/khách/đĩa; Firebase tắt -> fetch trả None ngay."""
    if _psid_path(psid).exists():
        return False
    return fb.fetch_conversation(psid) is None


def _followup_mark(psid: str) -> Path:
    return _HIST_DIR / (_psid_path(psid).stem + ".followup")


def followup_candidates(after_h: float, max_h: float = 23.0) -> list[tuple[str, str]]:
    """Khách cần nhắc: bot đã trả lời cuối, khách im trong [after_h, max_h) giờ, CHƯA chốt.

    max_h < 24 để còn trong cửa sổ 24h của FB (gửi RESPONSE hợp lệ). Trả [(psid, last_user_at)].
    Đã nhắc lượt này (mark == last_user_at) hoặc đã chốt (có .crm.json) -> bỏ.
    """
    out: list[tuple[str, str]] = []
    if not _HIST_DIR.exists():
        return out
    now = datetime.now()
    for p in _HIST_DIR.glob("*.json"):
        if p.name.endswith(".crm.json"):              # bỏ file meta CRM
            continue
        psid = p.stem
        try:
            msgs = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not msgs or msgs[-1].get("role") != "assistant":   # khách nhắn cuối -> đang chờ bot, bỏ
            continue
        last_user_at = next((m.get("at") for m in reversed(msgs)
                             if m.get("role") == "user" and m.get("at")), None)
        if not last_user_at:
            continue
        try:
            age_h = (now - datetime.strptime(last_user_at, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        except ValueError:
            continue
        if not (after_h <= age_h < max_h):
            continue
        if (_HIST_DIR / f"{psid}.crm.json").exists():          # đã chốt phiếu -> thôi
            continue
        mark = _followup_mark(psid)
        if mark.exists() and mark.read_text(encoding="utf-8").strip() == last_user_at:
            continue                                           # đã nhắc đúng lượt này
        out.append((psid, last_user_at))
    return out


def mark_followed(psid: str, last_user_at: str) -> None:
    """Đánh dấu đã nhắc khách ở lượt này (theo mốc tin khách cuối) -> không nhắc lặp."""
    try:
        _HIST_DIR.mkdir(parents=True, exist_ok=True)
        _followup_mark(psid).write_text(last_user_at, encoding="utf-8")
    except Exception as e:
        print(f"[followup] mark lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)


def _load_hist(psid: str) -> list:
    """Toàn bộ log 1 khách. Cache local trước; miss -> kéo Firebase, ghi cache. [] nếu chưa có."""
    try:
        return json.loads(_psid_path(psid).read_text(encoding="utf-8"))
    except Exception:
        pass
    remote = fb.fetch_conversation(psid)            # cache miss -> nguồn chính Firebase
    if remote:
        try:
            _write_local_hist(psid, remote)         # nạp lại cache cho lần sau
        except Exception as e:
            print(f"[hist] cache miss, ghi local lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        return remote
    return []


def _write_local_hist(psid: str, full_msgs: list) -> None:
    """Ghi cache local (atomic tmp+rename -> không hỏng file khi chết giữa chừng)."""
    _HIST_DIR.mkdir(parents=True, exist_ok=True)
    p = _psid_path(psid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(full_msgs, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _save_hist(psid: str, full_msgs: list) -> None:
    """Ghi TOÀN BỘ log khách: cache local + Firebase (nguồn chính, thread nền)."""
    try:
        _write_local_hist(psid, full_msgs)
        fb.mirror_conversation(psid, full_msgs)
    except Exception as e:
        print(f"[hist] ghi lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)


def _to_contents(msgs: list) -> list[types.Content]:
    """Log ({"role": user|assistant, "content": str}) -> Content Gemini (assistant -> model)."""
    return [types.Content(role="user" if m["role"] == "user" else "model",
                          parts=[types.Part.from_text(text=m["content"])])
            for m in msgs if isinstance(m.get("content"), str) and m["content"]]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _answer_sync(psid: str, text: str) -> str:
    client = _get_client()
    user_at = _now_str()                              # mốc khách gửi (lúc bắt đầu xử lý)
    with _psid_lock(psid):
        full = _load_hist(psid)                       # toàn bộ log
        contents = _to_contents(full[-_MAX_TURNS * 2:])  # chỉ nạp N lượt cuối cho API
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        # 8192: Gemini 3.x "thinking" ngầm tính chung vào output budget - 1024 làm cụt câu trả lời.
        gen_cfg = types.GenerateContentConfig(
            system_instruction=_system_text(), tools=_TOOLS, max_output_tokens=8192,
        )
        tok_in = tok_out = 0
        for _ in range(_MAX_TOOL_LOOPS):
            resp = client.models.generate_content(model=_model_id(), contents=contents,
                                                  config=gen_cfg)
            u = resp.usage_metadata
            if u:
                tok_in += u.prompt_token_count or 0
                # thinking tính giá như output -> gộp chung
                tok_out += (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
            cand = (resp.candidates or [None])[0]
            if cand is None or cand.content is None:
                raise BrainError(f"API không trả candidate. feedback={resp.prompt_feedback}")
            parts = cand.content.parts or []
            fcalls = [p.function_call for p in parts if p.function_call]
            if fcalls:
                contents.append(cand.content)
                contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(
                        name=fc.name, response={"result": _run_tool(fc.name, dict(fc.args or {}))})
                    for fc in fcalls]))
                continue
            reply = "".join(p.text for p in parts if p.text).strip()
            if not reply:
                raise BrainError(f"API không trả nội dung. finish_reason={cand.finish_reason}")
            # Sản phẩm nhắc lần đầu trong hội thoại -> tự kèm marker ảnh (messenger bóc ra gửi).
            markers = _new_image_markers(full, reply)
            if markers:
                reply = reply + " " + markers
            stats.log_usage(psid, tok_in, tok_out)
            # Nối vào log SẠCH: chỉ text turn (bỏ vòng tool trung gian) -> không orphan tool block.
            # "at" = thời gian cụ thể: khách gửi lúc nào, bot trả lúc nào.
            _save_hist(psid, full + [{"role": "user", "content": text, "at": user_at},
                                     {"role": "assistant", "content": reply, "at": _now_str()}])
            return reply
        raise BrainError("Quá nhiều vòng tool, không chốt được câu trả lời.")


_LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "ten": {"type": "string", "description": "Tên thật của khách (không phải 'Bác'/'Anh'). Rỗng nếu chưa rõ."},
        "sdt": {"type": "string", "description": "Số điện thoại khách. Rỗng nếu chưa có."},
        "dia_chi": {"type": "string", "description": "Địa chỉ/nơi thi công đầy đủ như khách nói. Rỗng nếu chưa rõ."},
        "tinh": {"type": "string", "description": "Tỉnh/Thành phố (1 trong danh sách cho sẵn, khớp CHÍNH XÁC). Rỗng nếu không rõ."},
        "khu_vuc": {"type": "string", "enum": ["Miền Bắc", "Miền Trung", "Miền Nam", ""],
                    "description": "Suy từ tỉnh. Rỗng nếu không rõ tỉnh."},
        "tom_tat": {"type": "string", "description": "Tóm tắt hội thoại 2-4 câu: nhu cầu, hạng mục, loại đá, thời gian, điểm cần lưu ý cho sale."},
    },
    "required": ["ten", "sdt", "dia_chi", "tinh", "khu_vuc", "tom_tat"],
}

_TINH_OPTIONS = ("Hà Nội, Hải Phòng, Quảng Ninh, Bắc Giang, Bắc Ninh, Hải Dương, Hưng Yên, Vĩnh Phúc, "
                 "Hà Nam, Nam Định, Ninh Bình, Thái Bình, Phú Thọ, Thái Nguyên, Tuyên Quang, Lào Cai, "
                 "Yên Bái, Điện Biên, Lai Châu, Sơn La, Hòa Bình, Hà Giang, Cao Bằng, Bắc Kạn, Lạng Sơn, "
                 "Thanh Hóa, Nghệ An, Hà Tĩnh, Quảng Bình, Quảng Trị, Thừa Thiên Huế, Da Nang, Quảng Nam, "
                 "Quảng Ngãi, Bình Định, Phú Yên, Khánh Hòa, Ninh Thuận, Bình Thuận, Kon Tum, Gia Lai, "
                 "Đắk Lắk, Đắk Nông, Lâm Đồng, Ho Chi Minh City, Cần Thơ, Bình Dương, Đồng Nai, "
                 "Bà Rịa - Vũng Tàu, Tây Ninh, Bình Phước, Long An, Tiền Giang, Bến Tre, Trà Vinh, "
                 "Vĩnh Long, Đồng Tháp, An Giang, Kiên Giang, Hậu Giang")


def _extract_lead_sync(psid: str) -> dict | None:
    """Trích thông tin lead + tóm tắt từ hội thoại khách. None nếu không có SĐT / lỗi."""
    hist = _load_hist(psid)
    if not hist:
        return None
    convo = "\n".join(f"{'Khách' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')}"
                      for m in hist if isinstance(m.get("content"), str))
    prompt = (f"Trích thông tin khách từ hội thoại dưới đây thành JSON. "
              f"Tỉnh/Thành phố PHẢI chọn khớp chính xác 1 trong: {_TINH_OPTIONS}.\n\n{convo}")
    try:
        resp = _get_client().models.generate_content(
            model=_model_id(),
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=_LEAD_SCHEMA,
                max_output_tokens=2048),
        )
        cand = (resp.candidates or [None])[0]
        raw = "".join(p.text for p in (cand.content.parts or []) if p.text) if cand and cand.content else ""
        lead = json.loads(raw)
        return lead if (lead.get("sdt") or "").strip() else None
    except Exception as e:
        print(f"[lead] trích lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


async def answer(psid: str, text: str) -> str:
    """Trả lời 1 tin của khách. Chạy API trong thread để không chặn event loop."""
    return await asyncio.to_thread(_answer_sync, psid, text)


async def extract_lead(psid: str) -> dict | None:
    """Trích lead (async wrapper). Gọi khi handoff để ghi vào CRM."""
    return await asyncio.to_thread(_extract_lead_sync, psid)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Xin chào, bạn là ai?"
    print(asyncio.run(answer("__cli_test__", q)))
