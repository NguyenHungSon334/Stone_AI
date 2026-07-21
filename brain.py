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
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import alerts
import config
import fb
import stats
import util
from bot_tools import lark_image
from bot_tools.find_by_price import (catalog_index, kinds_available, parse_money, render,
                                     rows_by_ids, search)

# 1 file/khách, GIỮ TOÀN BỘ log (admin xem đủ). AI đọc NHẸ = tóm tắt phần cũ + đuôi verbatim:
# token/lượt bị chặn, KHÔNG phình theo độ dài chat. Tóm tắt = sidecar <psid>.sum.json (local,
# tự dựng lại được nên không cần mirror Firebase).
_HIST_DIR = config.ROOT / "conversations"
_MAX_TURNS = 12          # số lượt gần nhất GỬI cho API nguyên văn (chặn token)
_KEEP_VERBATIM = _MAX_TURNS * 2   # số TIN cuối luôn gửi nguyên văn (~12 lượt)
_SUMMARY_TRIGGER = 20    # phần chưa-tóm vượt _KEEP_VERBATIM + ngần này -> cập nhật tóm tắt
_MAX_TOOL_LOOPS = 5      # trần số vòng gọi tool trong 1 câu trả lời

# BOT_MODEL: alias -> model id API. Có dấu '.' hoặc bắt đầu 'gemini' thì coi là id đầy đủ.
_MODEL_ALIAS = {"flash": "gemini-3.5-flash", "pro": "gemini-2.5-pro",
                "lite": "gemini-2.5-flash-lite"}

# Model chính "high demand" (503) là quá tải của RIÊNG model đó -> đổi sang model khác thường
# chạy được ngay. Chỉ dùng khi model chính đã thua hết lượt retry.
# Phải KHÁC model chính mới có nghĩa. Model chính đang là bản GA ổn định; dự phòng lấy bản
# lite (pool riêng, nhẹ nhất) - hợp vì nhịp dự phòng nhồi thẳng persona, cần model nhanh.
# ĐỪNG đặt bản preview làm model chính: đo thực tế gemini-3-flash-preview dính 504/503 liên
# tục ngay cả với tin "Xin chào", phải rơi xuống dự phòng.
_FALLBACK_MODEL = "gemini-2.5-flash-lite"

# Tắt thinking: tư vấn bán hàng theo kịch bản không cần suy luận sâu, mà thinking tính tiền như
# output + kéo dài thời gian sinh -> chậm, đắt, dễ dính deadline 504 lúc Gemini tải cao.
_NO_THINK = types.ThinkingConfig(thinking_budget=0)

_MAX_NEW_IMAGES = 4      # trần ảnh gửi kèm 1 tin (mỗi sản phẩm nhắc lần đầu = 1 ảnh)

_TOOLS = [types.Tool(function_declarations=[types.FunctionDeclaration(
    name="suggest_products",
    description=("Tra sản phẩm đá mỹ nghệ. NGUỒN DUY NHẤT cho giá, kích thước, trọng lượng, "
                 "ghi chú - danh sách trong system prompt CHỈ có mã/tên/danh mục. Gọi được nhiều "
                 "lần trong một lượt, và PHẢI gọi lại cho từng hạng mục khi khách hỏi cả công "
                 "trình (mộ, long đình, hàng rào, cổng...). Kết hợp tự do các bộ lọc: 'kind' "
                 "(thể loại), 'q' (từ khoá tên, vd 'mộ tròn'), 'max'/'min' (tầm giá), 'stone', "
                 "'category', hoặc 'product_ids' cho mẫu đã biết mã. Kết quả gồm mã, tên, danh "
                 "mục, kích thước, trọng lượng và giá THEO TỪNG LOẠI ĐÁ, sắp xếp giá tăng dần. "
                 "Ảnh sản phẩm hệ thống TỰ ĐỘNG gửi kèm khi tin nhắn nhắc tới mã lần đầu, "
                 "không cần làm gì thêm."),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": kinds_available(),
                     "description": "Thể loại sản phẩm - dùng khi khách hỏi cả một nhóm hàng"},
            "q": {"type": "string", "description": "Từ khoá tên sản phẩm, vd 'mộ tròn', 'mộ 3 cấp', 'cổng tứ trụ'. Mọi từ phải khớp"},
            "max": {"type": "string", "description": "Giá tối đa, vd '100tr', '1.5 tỷ', '100000000'"},
            "min": {"type": "string", "description": "Giá tối thiểu, mặc định 0"},
            "stone": {"type": "string", "description": "Loại đá: xanh đen, xanh rêu, xám BĐ, GRN, xanh Bình Định, trắng Yên Bái"},
            "category": {"type": "string", "description": "Danh mục, vd 'Trường Tồn'"},
            "limit": {"type": "integer", "description": "Số kết quả tối đa (mặc định 15)"},
            "product_ids": {"type": "array", "items": {"type": "string"},
                            "description": "Danh sách mã cụ thể (vd ['M01','LD03']) - dùng thay cho các bộ lọc trên"},
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
    """persona + INDEX sản phẩm (TĨNH - dùng cho explicit cache). Thời gian tách riêng
    (_time_note_text) nhét vào contents mỗi lượt để prefix cache không đổi.

    Trước nhồi NGUYÊN CSV (~26k token) -> prefill 33k MỖI lượt, kể cả tin 'Xin chào'. Cache
    không cứu được: vẫn phải nạp từng ấy KV trước khi nhả chữ đầu -> hàng đợi dài -> 504.
    Nay chỉ index mã|tên|danh mục (~5.5k): đủ để bot BIẾT có mẫu gì mà gọi tool, còn
    giá/kích thước/ghi chú lấy qua suggest_products."""
    return (config.persona()
            + "\n\n# DANH SÁCH SẢN PHẨM (mã | tên | danh mục)\n"
            + "BẢNG NÀY KHÔNG CÓ GIÁ VÀ KÍCH THƯỚC. Muốn biết giá, kích thước, trọng lượng hay "
              "ghi chú của BẤT KỲ mã nào: BẮT BUỘC gọi tool suggest_products (truyền product_ids), "
              "TUYỆT ĐỐI không tự suy hay ước lượng - báo sai giá cho khách là mất đơn.\n"
            + catalog_index())


def _time_note_text() -> str:
    """Ghi chú thời gian thực - nhét đầu contents mỗi lượt (KHÔNG vào cache tĩnh)."""
    now = datetime.now()
    return (f"[Hệ thống] Bây giờ là {now:%H:%M} ngày {now.day}/{now.month}/{now.year}. "
            "Dùng mốc này khi khách nói thời gian tương đối (tháng này, cuối năm...).")


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


# AI TỰ QUYẾT có gửi lại ảnh hay không: nó viết <<ANH>> vào câu trả lời khi thấy khách đang đòi
# xem ảnh. Bản cũ dò tin khách bằng regex liệt kê cụm ("xem ảnh", "gửi ảnh"...) nên mọi cách nói
# ngoài danh sách ("kèm ảnh", "ảnh đi", "cho ít hình") đều trượt -> bot hứa gửi ảnh rồi gửi chữ
# trơn. Chỉ AI mới đọc được ý đó; nó đang sinh câu trả lời sẵn rồi nên không tốn thêm lượt gọi.
# Bắt LỎNG: marker trông như thẻ XML nên model có lúc tự đóng thẻ - ca thật khách nhận được
# "<<ANH></anh>>" nguyên văn vì regex cũ đòi đúng 2 dấu '>'. Nhận mọi biến thể: thiếu/thừa dấu
# ngoặc, có gạch chéo mở hoặc đóng, hoa thường lẫn lộn.
_WANT_IMG_RE = re.compile(r"<+\s*/?\s*ANH\s*/?\s*>+", re.IGNORECASE)


def _wants_image(reply: str) -> bool:
    """AI có đánh dấu 'lượt này gửi ảnh' không?"""
    return bool(_WANT_IMG_RE.search(reply or ""))


def _bo_marker_anh(reply: str) -> str:
    """Bóc <<ANH>> khỏi câu trả lời - marker nội bộ, KHÔNG cho khách thấy, KHÔNG lưu lịch sử."""
    return _WANT_IMG_RE.sub("", reply or "").strip()


def _image_markers(history: list, reply: str, user_text: str) -> str:
    """Marker ảnh (1 mã = 1 ảnh, tối đa _MAX_NEW_IMAGES/tin, mã không ảnh bỏ im lặng).

    2 trường hợp gửi ảnh:
    - AI đánh dấu <<ANH>> (nó hiểu khách đang đòi ảnh) -> gửi mọi mã nhắc trong câu
      (reply + tin khách), KỂ CẢ đã gửi trước đó.
    - Không đánh dấu -> chỉ mã nhắc LẦN ĐẦU trong hội thoại (chưa từng xuất hiện ở lượt trước).
    """
    if _wants_image(reply):
        codes = _codes_in(reply) | _codes_in(user_text)     # AI đòi gửi -> bỏ qua 'đã seen'
    else:
        seen: set[str] = set()
        for m in history:
            if isinstance(m.get("content"), str):
                seen |= _codes_in(m["content"])
        codes = _codes_in(reply) - seen
    markers: list[str] = []
    thieu: list[str] = []
    for code in sorted(codes):
        if len(markers) >= _MAX_NEW_IMAGES:
            break
        try:
            toks = lark_image.get_image_tokens(code)
        except Exception as e:
            print(f"[img] lấy ảnh {code} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            # Token Lark hết hạn / mất quyền Base -> hỏng CẢ LOẠT: khách xin ảnh chỉ nhận text trơn.
            alerts.alert(f"lark:img:{type(e).__name__}",
                         f"⚠️ LẤY ẢNH LARK LỖI (mã {code}) - khách xin ảnh nhưng bot chỉ trả chữ.\n"
                         f"{type(e).__name__}: {e}\n➡️ Kiểm tra LARK_APP_ID/SECRET và quyền Base.")
            toks = []
        if toks:
            markers.append(f"<<IMG:{toks[0]}>>")
        else:
            thieu.append(code)
    # Bot hứa gửi ảnh mà Base không có ảnh mã đó -> khách chờ hụt, im lặng. Báo admin để bổ sung.
    if _wants_image(reply) and not markers and thieu:
        alerts.alert("lark:img:thieu",
                     f"⚠️ THIẾU ẢNH TRONG LARK BASE - bot hứa gửi ảnh nhưng không có: {', '.join(thieu)}\n"
                     "➡️ Thêm ảnh vào cột Ảnh của các mã này.")
    return " ".join(markers)


def _run_tool(name: str, inp: dict) -> str:
    if name != "suggest_products":
        return f"Tool {name} không tồn tại."
    ids = inp.get("product_ids")
    if ids:
        results = rows_by_ids(ids if isinstance(ids, list) else [ids])
    else:
        kind, q = inp.get("kind"), inp.get("q")
        raw_max = inp.get("max", "")
        mx = parse_money(raw_max) if raw_max else None
        if mx is not None and mx <= 0:
            return f"Không đọc được tầm giá '{raw_max}'. Ghi kiểu '100tr' hoặc '1.5 tỷ'."
        if mx is None and not (kind or q or inp.get("category") or inp.get("stone")):
            return "Cần ít nhất một điều kiện: 'kind', 'q', 'max' hoặc 'product_ids'."
        mn = parse_money(inp.get("min", "0"))
        results = search(mx, mn if mn > 0 else 0.0, inp.get("stone"), inp.get("category"),
                         int(inp.get("limit") or 15), kind, q)
    if not results:
        # Nói RÕ thiếu ở đâu + gợi ý bộ lọc hợp lệ: bot trượt 1 lần thì thử lại được, thay vì
        # bỏ cuộc trả lời chay không mẫu nào (lỗi cũ hay gặp nhất).
        return ("Không có mẫu nào khớp. Thử nới tầm giá, bỏ bớt điều kiện, hoặc lọc bằng "
                f"kind (thể loại có: {', '.join(kinds_available())}) / q (từ khoá tên).")
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
    return _HIST_DIR / f"{util.safe_psid(psid)}.json"


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
        if p.name.endswith((".crm.json", ".sum.json")):   # bỏ sidecar CRM / tóm tắt
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


def _missed_mark(psid: str) -> Path:
    return _HIST_DIR / (_psid_path(psid).stem + ".missed")


def load_history(psid: str) -> list:
    """Lịch sử 1 khách (cache local, miss thì kéo Firebase). Cho lớp ngoài dùng, khỏi đụng _private."""
    return _load_hist(psid)


def is_closed(psid: str) -> bool:
    """Khách đã chốt phiếu CRM (handoff xong / người thật tiếp quản) -> khỏi báo tin rơi."""
    return (_HIST_DIR / f"{_psid_path(psid).stem}.crm.json").exists()


def missed_already_reported(psid: str, at: str) -> bool:
    """Đã báo đúng tin này rồi? -> vòng quét sau không lải nhải cùng 1 khách."""
    mark = _missed_mark(psid)
    try:
        return mark.exists() and mark.read_text(encoding="utf-8").strip() == at
    except Exception:
        return False


def mark_missed_reported(psid: str, last_user_at: str) -> None:
    """Đánh dấu ĐÃ BÁO tin rơi này -> vòng quét sau không báo lại cùng 1 tin."""
    try:
        _HIST_DIR.mkdir(parents=True, exist_ok=True)
        _missed_mark(psid).write_text(last_user_at, encoding="utf-8")
    except Exception as e:
        print(f"[missed] mark lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        alerts.alert(f"missed:mark:{type(e).__name__}",
                     f"⚠️ KHÔNG ĐÁNH DẤU ĐƯỢC TIN RƠI - admin sẽ bị báo lặp cùng 1 khách.\n"
                     f"{type(e).__name__}: {e}")


def mark_followed(psid: str, last_user_at: str) -> None:
    """Đánh dấu đã nhắc khách ở lượt này (theo mốc tin khách cuối) -> không nhắc lặp."""
    try:
        _HIST_DIR.mkdir(parents=True, exist_ok=True)
        _followup_mark(psid).write_text(last_user_at, encoding="utf-8")
    except Exception as e:
        print(f"[followup] mark lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # KHÔNG đánh dấu được = vòng quét sau lại nhắc khách lần nữa, mỗi 15 phút -> SPAM KHÁCH.
        alerts.alert(f"followup:mark:{type(e).__name__}",
                     f"🔴 KHÔNG ĐÁNH DẤU ĐƯỢC FOLLOW-UP - khách sẽ bị nhắc LẶP mỗi vòng quét.\n"
                     f"{type(e).__name__}: {e}\n➡️ Tắt tạm BOT_FOLLOWUP_ENABLED=0 nếu khách kêu spam.")


def _load_hist(psid: str) -> list:
    """Toàn bộ log 1 khách. Cache local trước; miss -> kéo Firebase, ghi cache. [] nếu chưa có."""
    local = util.read_json(_psid_path(psid))
    if local is not None:                           # [] hợp lệ (khác None) -> khỏi hỏi Firebase
        return local
    remote = fb.fetch_conversation(psid)            # cache miss -> nguồn chính Firebase
    if remote:
        try:
            _write_local_hist(psid, remote)         # nạp lại cache cho lần sau
        except Exception as e:
            print(f"[hist] cache miss, ghi local lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
            # Cache không nạp lại được -> lượt sau lại đi Firebase (chậm + tốn quota), lặp mãi.
            alerts.alert(f"hist:cache:{type(e).__name__}",
                         f"⚠️ KHÔNG GHI ĐƯỢC CACHE LỊCH SỬ - mỗi lượt phải kéo lại từ Firebase (chậm).\n"
                         f"{type(e).__name__}: {e}\n➡️ Kiểm tra dung lượng đĩa / quyền ghi.")
        return remote
    return []


def _write_local_hist(psid: str, full_msgs: list) -> None:
    """Ghi cache local (atomic tmp+rename -> không hỏng file khi chết giữa chừng)."""
    util.write_json_atomic(_psid_path(psid), full_msgs)


def _save_hist(psid: str, full_msgs: list, new_msgs: list | None = None) -> None:
    """Ghi TOÀN BỘ log khách (admin xem đủ): cache local (full) + Firebase.
    new_msgs có -> Firebase chỉ APPEND phần mới theo index (O(1)/lượt, không up lại cả mảng).
    AI không đọc nguyên full - đọc tóm tắt + đuôi verbatim."""
    try:
        _write_local_hist(psid, full_msgs)             # local = full (đĩa rẻ), nguồn đọc chính
        if new_msgs:
            fb.append_conversation(psid, len(full_msgs) - len(new_msgs), new_msgs)
        else:
            fb.mirror_conversation(psid, full_msgs)    # fallback: ghi đè cả mảng
    except Exception as e:
        print(f"[hist] ghi lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # Ghi hỏng = lượt này biến mất khỏi lịch sử -> bot quên ngữ cảnh, admin xem log thiếu.
        alerts.alert(f"hist:{type(e).__name__}",
                     f"⚠️ GHI LỊCH SỬ HỎNG - hội thoại mất lượt, bot quên ngữ cảnh khách.\n"
                     f"{type(e).__name__}: {e}\n➡️ Kiểm tra dung lượng đĩa / quyền ghi conversations/.")


_IMG_MARKER_RE = re.compile(r"\s*<<IMG:[^>]+>>")   # marker ảnh: KHÔNG cho model thấy (chống echo token chết)


def _to_contents(msgs: list) -> list[types.Content]:
    """Log ({"role": user|assistant, "content": str}) -> Content Gemini (assistant -> model).

    Strip marker <<IMG:token>> khỏi text: token ảnh dễ chết (ảnh Base bị thay) -> nếu model
    thấy marker cũ trong history nó chép lại -> gửi token 404. Bỏ đi thì model không echo được.
    """
    out = []
    for m in msgs:
        c = m.get("content")
        if not (isinstance(c, str) and c):
            continue
        c = _IMG_MARKER_RE.sub("", c).strip()
        if c:
            out.append(types.Content(role="user" if m["role"] == "user" else "model",
                                     parts=[types.Part.from_text(text=c)]))
    return out


def _sum_path(psid: str) -> Path:
    return _HIST_DIR / (_psid_path(psid).stem + ".sum.json")


def _load_summary(psid: str) -> dict | None:
    """Tóm tắt cuốn chiếu {text, upto}. upto = số tin ĐẦU log đã gộp vào tóm tắt."""
    d = util.read_json(_sum_path(psid))
    return d if isinstance(d, dict) and d.get("text") else None


def _save_summary(psid: str, text: str, upto: int) -> None:
    try:
        util.write_json_atomic(_sum_path(psid), {"text": text, "upto": upto})
    except Exception as e:
        print(f"[sum] ghi lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)


def _msgs_as_text(msgs: list) -> str:
    lines = []
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str) and c:
            lines.append(f"{'Khách' if m.get('role') == 'user' else 'Bot'}: {c}")
    return "\n".join(lines)


def _summarize(client, model_id: str, prev: str, new_msgs: list) -> str:
    """Cập nhật tóm tắt: gộp tóm-tắt-cũ + tin mới -> tóm tắt mới. Input luôn NHỎ (tóm tắt + ~vài chục tin).
    Không tool, không cache -> gọi rẻ. Lỗi -> trả prev (giữ nguyên tóm tắt cũ)."""
    prompt = (
        "Cập nhật bản tóm tắt hội thoại tư vấn đá mỹ nghệ (mộ/lăng/cổng...) dưới đây thành 5-10 gạch "
        "đầu dòng NGẮN, giữ mọi thông tin quan trọng để tư vấn tiếp: nhu cầu/hạng mục, mã sản phẩm đã "
        "tư vấn, giá đã báo, tên/SĐT/địa chỉ/tỉnh khách, mốc thời gian đã hẹn, điểm cần lưu ý. "
        "Chỉ xuất bản tóm tắt, không thêm lời dẫn.\n\n"
        f"# Tóm tắt hiện có\n{prev or '(chưa có)'}\n\n# Tin mới cần gộp vào\n{_msgs_as_text(new_msgs)}")
    try:
        cfg = types.GenerateContentConfig(max_output_tokens=1024, thinking_config=_NO_THINK)
        resp = _generate(client, model=model_id,
                         contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                         config=cfg)
        cand = (resp.candidates or [None])[0]
        out = "".join(p.text for p in (cand.content.parts or []) if p.text).strip() if cand and cand.content else ""
        return out or prev
    except Exception as e:
        print(f"[sum] tóm tắt lỗi psid: {type(e).__name__}: {e}", file=sys.stderr)
        # Tóm tắt hỏng liên tục -> phần chưa-tóm phình mãi -> mỗi tin gửi thêm hàng nghìn token.
        alerts.alert(f"sum:{type(e).__name__}",
                     f"💸 TÓM TẮT HỘI THOẠI LỖI - lịch sử không được nén, token mỗi tin tăng dần.\n"
                     f"{type(e).__name__}: {e}")
        return prev


def _maybe_summarize(client, model_id: str, psid: str, full: list) -> None:
    """Sau khi lưu: nếu phần chưa-tóm quá dài -> cập nhật tóm tắt, chốt upto để đuôi verbatim luôn ~_KEEP_VERBATIM."""
    summ = _load_summary(psid)
    upto = summ.get("upto", 0) if summ else 0
    if len(full) - upto <= _KEEP_VERBATIM + _SUMMARY_TRIGGER:
        return
    cutoff = len(full) - _KEEP_VERBATIM               # gộp mọi tin cũ hơn đuôi verbatim
    new_text = _summarize(client, model_id, summ.get("text", "") if summ else "", full[upto:cutoff])
    _save_summary(psid, new_text, cutoff)


_SUMMARIZING: set[str] = set()
_SUMMARIZING_LOCK = threading.Lock()


def _summarize_bg(client, model_id: str, psid: str, full: list) -> None:
    """Chạy _maybe_summarize ở THREAD NỀN -> không trễ tin trả lời khách. 1 khách chỉ 1 lần chạy 1 lúc."""
    with _SUMMARIZING_LOCK:
        if psid in _SUMMARIZING:                       # đang tóm cho khách này -> bỏ, khỏi đua sidecar
            return
        _SUMMARIZING.add(psid)

    def run() -> None:
        try:
            _maybe_summarize(client, model_id, psid, full)
        except Exception as e:
            print(f"[sum] bg lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            with _SUMMARIZING_LOCK:
                _SUMMARIZING.discard(psid)

    threading.Thread(target=run, daemon=True).start()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Lỗi TẠM của Gemini, thử lại là qua. 499 CANCELLED nằm dải 4xx nên SDK ném ClientError chứ
# không phải ServerError: bắt theo LỚP là trượt, phải soi mã. Đó là lý do đối sánh bằng code.
_RETRY_STATUS = {499, 500, 503, 504}


def _generate(client, tries: int = 4, **kw):
    """generate_content + retry lỗi tạm (499/500/503/504). 4 lần, backoff 2s/4s/8s.

    504 DEADLINE_EXCEEDED, 503 UNAVAILABLE, 499 CANCELLED đều là mặt khác nhau của cùng một
    chuyện: Gemini quá tải, request bị cắt giữa chừng. Thử lại thường qua ngay, đỡ phải báo lỗi
    cho admin + bỏ lượt khách. Trần chờ 14s: lâu hơn thì giữ mãi slot _SEM (4 slot) -> Gemini
    sập là khách khác xếp hàng theo."""
    last = None
    for i in range(tries):
        try:
            return client.models.generate_content(**kw)
        except genai_errors.APIError as e:          # gồm cả ClientError (499) lẫn ServerError (5xx)
            if getattr(e, "code", None) not in _RETRY_STATUS:
                raise
            last = e
            print(f"[gemini] {e.code} thử lại lần {i + 1}/{tries}", file=sys.stderr)
            if i < tries - 1:                      # lần cuối thua thì ném luôn, không chờ vô ích
                time.sleep(2 * 2 ** i)
    raise last


# ===== Explicit context caching =====
# persona+catalog (~30k token TĨNH) cache 1 lần trên Gemini; mỗi request tham chiếu handle
# thay vì nhồi lại -> rẻ token + nhanh hơn (giảm 504). Lỗi cache -> fallback nhồi thẳng.
_CACHE_TTL_S = 3600
_CACHE = {"key": None, "name": None, "exp": 0.0}
_CACHE_LOCK = threading.Lock()


def _sys_key(model_id: str) -> tuple:
    """Khóa cache = model + mtime persona + mtime csv. File đổi -> tạo cache mới."""
    return (model_id,
            (config.DOCS_DIR / "Personal.md").stat().st_mtime,
            config.CATALOG_CSV.stat().st_mtime)


def _invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update(key=None, name=None, exp=0.0)


def _cached_handle(client, model_id: str) -> str | None:
    """Tên CachedContent cho persona+catalog hiện tại. None nếu lỗi -> caller nhồi thẳng.

    ponytail: cache cũ để Google tự hết TTL (1h), không xoá tay - phí không đáng, đơn giản hơn."""
    try:
        key = _sys_key(model_id)
    except OSError:
        return None
    now = time.time()
    with _CACHE_LOCK:
        if _CACHE["name"] and _CACHE["key"] == key and now < _CACHE["exp"]:
            return _CACHE["name"]
        try:
            cache = client.caches.create(
                model=model_id,
                config=types.CreateCachedContentConfig(
                    system_instruction=_system_text(), tools=_TOOLS, ttl=f"{_CACHE_TTL_S}s"),
            )
        except Exception as e:
            print(f"[cache] tạo lỗi, nhồi thẳng: {type(e).__name__}: {e}", file=sys.stderr)
            # Vẫn chạy được, nhưng MỖI TIN nhồi lại ~30k token system -> tiền token tăng vọt.
            # Hỏng lặng lẽ kiểu này chỉ lộ khi xem hoá đơn cuối tháng -> phải báo.
            alerts.alert("gemini:cache", f"💸 TẠO CACHE GEMINI LỖI - bot vẫn trả lời nhưng nhồi lại "
                                         f"toàn bộ persona+bảng SP mỗi tin, CHI PHÍ TOKEN TĂNG MẠNH.\n"
                                         f"{type(e).__name__}: {e}")
            _CACHE.update(key=None, name=None, exp=0.0)
            return None
        _CACHE.update(key=key, name=cache.name, exp=now + _CACHE_TTL_S - 120)   # -120s an toàn
        print(f"[cache] tạo {cache.name} ttl {_CACHE_TTL_S}s", file=sys.stderr)
        return cache.name


def _is_cache_error(e) -> bool:
    """Lỗi do handle cache hỏng/hết (Google xoá) -> cần fallback inline."""
    msg = str(getattr(e, "message", "") or e).lower()
    return getattr(e, "code", None) in (400, 403, 404) and "cach" in msg


def _inline_cfg() -> types.GenerateContentConfig:
    """Config nhồi thẳng persona+bảng SP (không cache). 8192: trần đủ rộng cho câu dài."""
    return types.GenerateContentConfig(system_instruction=_system_text(), tools=_TOOLS,
                                       max_output_tokens=8192, thinking_config=_NO_THINK)


def _gen_answer(client, model_id: str, contents: list, handle: str | None):
    """1 lần generate luồng trả lời. Trả (resp, handle_dùng_tiếp).

    Model chính quá tải kể cả sau retry -> đánh nốt 1 nhịp bằng _FALLBACK_MODEL (pool khác)
    thay vì bỏ lượt khách. Cache gắn chặt với model chính nên nhịp này phải nhồi thẳng:
    đắt hơn, nhưng chỉ chạy lúc sự cố."""
    try:
        return _gen_answer_once(client, model_id, contents, handle)
    except genai_errors.APIError as e:              # 499 là ClientError, không phải ServerError
        if getattr(e, "code", None) not in _RETRY_STATUS:
            raise
        print(f"[gemini] {model_id} thua ({e.code}), đổi {_FALLBACK_MODEL}", file=sys.stderr)
        alerts.alert(f"gemini:fallback:{getattr(e, 'code', '?')}",
                     f"⚠️ {model_id} QUÁ TẢI ({e.code}) - bot tạm chạy {_FALLBACK_MODEL}, nhồi thẳng "
                     f"persona mỗi tin nên tốn token hơn. Tự hết khi Gemini rảnh.")
        # tries=2: fallback cũng chết thì thua thật, chờ thêm chỉ giữ slot _SEM vô ích.
        resp = _generate(client, tries=2, model=_FALLBACK_MODEL, contents=contents,
                         config=_inline_cfg())
        return resp, handle          # handle giữ nguyên: vòng sau thử lại model chính + cache


def _gen_answer_once(client, model_id: str, contents: list, handle: str | None):
    """Gọi model chính. handle hỏng (cache bị xoá) -> huỷ handle, nhồi thẳng, trả None."""
    if handle:
        try:
            cfg = types.GenerateContentConfig(cached_content=handle, max_output_tokens=8192,
                                              thinking_config=_NO_THINK)
            return _generate(client, model=model_id, contents=contents, config=cfg), handle
        except genai_errors.ClientError as e:
            if not _is_cache_error(e):
                raise
            print(f"[cache] handle hỏng, chuyển nhồi thẳng: {e}", file=sys.stderr)
            _invalidate_cache()
    return _generate(client, model=model_id, contents=contents, config=_inline_cfg()), None


def trim_resend(full: list, text: str, user_at: str) -> tuple[list, str]:
    """TRẢ LỜI BÙ: tin này đã nằm cuối lịch sử (bot nhận được nhưng trả lời hỏng) -> bỏ bản cũ ra,
    chỗ gọi ghi lại đúng 1 lần.

    Không có bước này thì lượt khách bị NHÂN ĐÔI trong log, prompt cũng thấy 2 lần -> bot tưởng
    khách nhắc lại nên trả lời kiểu 'dạ em đã nói ở trên'. Giữ mốc giờ GỐC khách gửi.
    """
    if full and full[-1].get("role") == "user" and full[-1].get("content") == text:
        return full[:-1], (full[-1].get("at") or user_at)
    return full, user_at


def _prefetch(text: str) -> str:
    """Khách gõ ĐÍCH DANH mã ("giá LD02 bao nhiêu") -> tra sẵn đúng mã đó, khỏi tốn 1 vòng tool.

    CHỈ khớp mã chính xác. Bản cũ còn đoán thể loại từ từ khoá (rows_by_kind) rồi nhét 8 mẫu
    bán chạy vào đây: gom sai (mọi câu về mộ ra cùng 8 mẫu), sai nghiệp vụ ("lăng mộ" = cả khu
    lại trả 1 ngôi mộ đôi), và câu dẫn "không cần gọi công cụ" làm AI bỏ luôn việc tự lọc.
    Nay AI tự chọn mẫu từ danh sách trong ngữ cảnh rồi gọi suggest_products (có kind/q)."""
    codes = _codes_in(text)
    if not codes:
        return ""
    rows = rows_by_ids(sorted(codes))
    if not rows:
        return ""
    return ("[Hệ thống tra sẵn - số liệu THẬT từ bảng hàng cho mã khách vừa nhắc, dùng ngay. "
            "Cần mẫu khác thì gọi suggest_products, KHÔNG trả lời chay không có mẫu nào]\n"
            + render(rows))


def _answer_sync(psid: str, text: str, images: list[tuple[bytes, str]] | None = None,
                 user_at: str | None = None) -> str:
    client = _get_client()
    model_id = _model_id()
    # user_at: luồng bình thường = None (khách vừa nhắn, giờ xử lý ~ giờ gửi). Trả lời bù truyền
    # mốc THẬT lấy từ FB: tin bot chưa bao giờ nhận thì không có bản cũ trong lịch sử để
    # trim_resend giữ mốc, đóng dấu _now_str() là ghi lệch cả tiếng -> bot tưởng khách vừa nhắn,
    # không biết mình đã bỏ khách bao lâu.
    user_at = user_at or _now_str()
    with _psid_lock(psid):
        full = _load_hist(psid)                       # TOÀN BỘ log (lưu đủ cho admin)
        full, user_at = trim_resend(full, text, user_at)
        # thời gian thực nhét ĐẦU contents (tách khỏi cache tĩnh)
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=_time_note_text())])]
        # AI đọc NHẸ: tóm tắt phần cũ + đuôi verbatim. upto = tin đã gộp vào tóm tắt.
        summ = _load_summary(psid)
        if summ:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(
                text=f"[Tóm tắt hội thoại trước đó - dùng để nhớ ngữ cảnh]\n{summ['text']}")]))
            start = min(summ.get("upto", 0), len(full))
        else:
            start = max(0, len(full) - _KEEP_VERBATIM)
        contents += _to_contents(full[start:])        # đuôi nguyên văn (token bị chặn, không phình)
        # Tra sẵn số liệu cho lượt này: bảng giá KHÔNG còn trong prompt, mà bot thì hay né gọi
        # tool để nhảy sang xin số điện thoại (mục tiêu ưu tiên trong persona) -> trả lời chay
        # không mẫu nào. Tra bằng code là đường chắc chắn có số thật, không phụ thuộc bot tự chọn.
        if (pre := _prefetch(text)):
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=pre)]))
        # Ảnh khách gửi: nhét bytes vào lượt hiện tại cho Gemini vision đọc (không lưu vào lịch sử).
        parts = [types.Part.from_text(text=text)]
        for data, ctype in (images or []):
            parts.append(types.Part.from_bytes(data=data, mime_type=ctype))
        contents.append(types.Content(role="user", parts=parts))

        handle = _cached_handle(client, model_id)     # None -> nhồi thẳng (fallback)
        tok_in = tok_out = 0
        for _ in range(_MAX_TOOL_LOOPS):
            resp, handle = _gen_answer(client, model_id, contents, handle)
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
            # Đọc <<ANH>> TRƯỚC khi bóc; sau đó reply sạch mới đem gửi + lưu lịch sử.
            markers = _image_markers(full, reply, text)
            hua_anh_khong_co = _wants_image(reply) and not markers
            reply = _bo_marker_anh(reply)
            if hua_anh_khong_co:
                # Khách hỏi ảnh mà Base chưa có: nói thật + xin liên hệ, thay vì để khách chờ hụt.
                reply = (reply + " Mẫu này bên em làm thiết kế riêng theo khuôn viên từng nhà "
                         "nên chưa có sẵn ảnh dựng ạ. Bác để lại số điện thoại hoặc Zalo, "
                         "em gửi Bác bộ ảnh công trình thực tế và bản phối riêng ngay hôm nay ạ.").strip()
            if not reply:
                raise BrainError("API chỉ trả marker ảnh, không có chữ.")
            reply_out = f"{reply} {markers}" if markers else reply   # marker CHỈ để gửi, KHÔNG lưu history
            stats.log_usage(psid, tok_in, tok_out)
            # Nối vào log SẠCH: chỉ text turn (bỏ vòng tool trung gian) -> không orphan tool block.
            # "at" = thời gian cụ thể: khách gửi lúc nào, bot trả lúc nào.
            # Ảnh không lưu bytes vào log -> ghi chú số ảnh để lượt sau còn biết khách từng gửi ảnh.
            user_content = text if not images else f"{text}\n[khách gửi {len(images)} ảnh]"
            new_msgs = [{"role": "user", "content": user_content, "at": user_at},
                        {"role": "assistant", "content": reply, "at": _now_str()}]
            new_full = full + new_msgs
            _save_hist(psid, new_full, new_msgs)       # Firebase chỉ append phần mới (O(1))
            # Chat dài -> cập nhật tóm tắt cuốn chiếu ở NỀN (không trễ tin khách; ~1/10 lượt mới chạy).
            _summarize_bg(client, model_id, psid, new_full)
            return reply_out
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
        resp = _generate(
            _get_client(),
            model=_model_id(),
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=_LEAD_SCHEMA,
                max_output_tokens=2048, thinking_config=_NO_THINK),
        )
        cand = (resp.candidates or [None])[0]
        raw = "".join(p.text for p in (cand.content.parts or []) if p.text) if cand and cand.content else ""
        lead = json.loads(raw)
        return lead if (lead.get("sdt") or "").strip() else None
    except Exception as e:
        print(f"[lead] trích lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
        # Trích hỏng -> _save_lead_to_crm thoát sớm, KHÔNG có "lỗi CRM" nào bắn ra: lead im lặng
        # bốc hơi dù khách đã cho SĐT. Đây là đường mất lead khó thấy nhất.
        alerts.alert(f"lead:extract:{type(e).__name__}",
                     f"🔴 TRÍCH LEAD HỎNG - khách đã handoff nhưng KHÔNG lead nào vào CRM.\n"
                     f"{type(e).__name__}: {e}")
        return None


async def answer(psid: str, text: str, images: list[tuple[bytes, str]] | None = None,
                 user_at: str | None = None) -> str:
    """Trả lời 1 tin của khách. Chạy API trong thread để không chặn event loop.

    images: ảnh khách gửi (bytes, content_type) -> Gemini vision đọc trong lượt này.
    user_at: mốc khách gửi THẬT ("%Y-%m-%d %H:%M:%S"); None = lấy giờ hiện tại."""
    return await asyncio.to_thread(_answer_sync, psid, text, images, user_at)


async def extract_lead(psid: str) -> dict | None:
    """Trích lead (async wrapper). Gọi khi handoff để ghi vào CRM."""
    return await asyncio.to_thread(_extract_lead_sync, psid)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Xin chào, bạn là ai?"
    print(asyncio.run(answer("__cli_test__", q)))
