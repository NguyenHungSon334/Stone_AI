"""
Ghi lead vào Lark Base CRM khi bot thu đủ thông tin khách (handoff).

- Base/Table CRM RIÊNG (khác Base ảnh sản phẩm) -> có config riêng LARK_CRM_*.
- Chống trùng: record_id Lark lưu vào conversations/<psid>.crm.json ngay khi tạo. Lần sau có
  thông tin mới -> UPDATE thẳng record_id đó (không search SĐT - search Lark lag, dễ tạo trùng).
  Mất file / lần đầu -> fallback search SĐT 1 lần; vẫn không thấy thì tạo mới + lưu id.
- Single-select (Khu vực/Nguồn lead): chỉ set khi value khớp option có sẵn (lấy động từ
  field meta, cache) -> không đẻ option rác vào Base. Tỉnh/Thành phố là cascade -> API không ghi.

Cần app Lark có quyền GHI Base: scope bitable:record:create + bitable:record:update,
và Base chia sẻ cho app với quyền "chỉnh sửa".
"""
import sys
import threading
import time

import config
import util
from bot_tools.lark_image import _tenant_token, request_retry   # tái dùng token + retry transient

_NGUON_LEAD = "Facebook"          # bot chạy trên Messenger -> nguồn cố định
_OPT_CACHE: dict[str, tuple[float, set[str]]] = {}   # field_name -> (ts, {option})
_OPT_TTL_S = 3600
_OPT_LOCK = threading.Lock()


def _crm_base() -> str:
    return config.LARK_CRM_APP_TOKEN


def _crm_table() -> str:
    return config.LARK_CRM_TABLE_ID


def _api(path: str) -> str:
    return f"{config.LARK_DOMAIN}/open-apis/bitable/v1/apps/{_crm_base()}/tables/{_crm_table()}/{path}"


def _select_options(field_name: str) -> set[str]:
    """Tên các option của 1 single-select (cache 1h). Rỗng nếu field không phải select/lỗi."""
    now = time.time()
    with _OPT_LOCK:
        hit = _OPT_CACHE.get(field_name)
        if hit and now - hit[0] < _OPT_TTL_S:
            return hit[1]
    opts: set[str] = set()
    try:
        tok = _tenant_token()
        r = request_retry("GET", _api("fields"), headers={"Authorization": f"Bearer {tok}"},
                          params={"page_size": 100}, timeout=15.0)
        d = r.json()
        for f in (d.get("data", {}).get("items") or []):
            if f.get("field_name") == field_name:
                for o in (f.get("property") or {}).get("options", []) or []:
                    if o.get("name"):
                        opts.add(o["name"])
                break
    except Exception as e:
        print(f"[crm] đọc option '{field_name}' lỗi: {type(e).__name__}: {e}", file=sys.stderr)
    with _OPT_LOCK:
        _OPT_CACHE[field_name] = (now, opts)
    return opts


# Cột cascade/khoá-ghi: API record create/update từ chối (SingleSelectFieldConvFail) dù value
# khớp option - Lark chỉ điền qua UI hoặc automation. Vẫn THỬ ghi; fail thì bỏ RIÊNG cột này,
# giá trị không mất (còn trong Địa chỉ + Ghi chú). Cột nào hết cascade thì tự ghi được, khỏi sửa code.
_CASCADE_COLS = ("Tỉnh/Thành phố",)
_ERR_SELECT_CONV = 1254062        # mã Lark: value không convert được sang option


def _build_fields(lead: dict) -> dict:
    """dict lead -> fields Lark. Single-select chỉ set khi khớp option; text set thẳng.

    Không khớp option -> để RỖNG (bỏ field), không đẻ option rác, không bịa."""
    fields: dict = {}
    for key, col in (("ten", "Tên khách hàng"), ("sdt", "Số điện thoại"),
                     ("dia_chi", "Địa chỉ"), ("tom_tat", "Ghi chú")):
        v = (lead.get(key) or "").strip()
        if v:
            fields[col] = v
    for key, col in (("khu_vuc", "Khu vực"), ("tinh", "Tỉnh/Thành phố")):
        v = (lead.get(key) or "").strip()
        if v and v in _select_options(col):
            fields[col] = v
    if _NGUON_LEAD in _select_options("Nguồn lead"):
        fields["Nguồn lead"] = _NGUON_LEAD
    return fields


def _write(method: str, path: str, tok: str, fields: dict) -> str:
    """POST/PUT record, ĐỌC code Lark thật (không tin mỗi HTTP 200). Trả record_id.

    Cascade fail (SingleSelectFieldConvFail) -> bỏ cột cascade, thử LẠI 1 lần để lead vẫn lưu."""
    def _call(fs: dict):
        r = request_retry(method, _api(path), headers={"Authorization": f"Bearer {tok}"},
                          json={"fields": fs}, timeout=20.0)
        return r.json()

    d = _call(fields)
    if d.get("code") == _ERR_SELECT_CONV and any(c in fields for c in _CASCADE_COLS):
        stripped = {k: v for k, v in fields.items() if k not in _CASCADE_COLS}
        print(f"[crm] cột cascade từ chối ghi ({[c for c in _CASCADE_COLS if c in fields]}) "
              f"-> ghi lại bỏ cột đó (giá trị vẫn trong Địa chỉ/Ghi chú)", file=sys.stderr)
        d = _call(stripped)
    if d.get("code") != 0:
        raise RuntimeError(f"Lark {d.get('code')}: {d.get('msg')}")
    return (d.get("data", {}).get("record", {}) or {}).get("record_id", "")


# --- Lưu record_id Lark theo khách: conversations/<psid>.crm.json ---
_META_DIR = config.ROOT / "conversations"


def _meta_path(psid: str):
    return _META_DIR / f"{util.safe_psid(psid)}.crm.json"


def _load_meta(psid: str) -> dict:
    return util.read_json(_meta_path(psid), {})


def _save_meta(psid: str, record_id: str, lead: dict, lead_code: str = "") -> None:
    """Ghi record_id + mã Lead/Chance + tham chiếu nhanh (tên/sđt). Atomic tmp+rename.

    lead_code trống (autonumber chưa sinh kịp) -> giữ mã cũ đã lưu, không ghi đè bằng rỗng."""
    try:
        if not lead_code:
            lead_code = _load_meta(psid).get("lead_code", "")
        data = {"record_id": record_id, "lead_code": lead_code,
                "ten": lead.get("ten", ""), "sdt": lead.get("sdt", ""),
                "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        util.write_json_atomic(_meta_path(psid), data)
    except Exception as e:
        print(f"[crm] lưu meta psid={psid} lỗi: {type(e).__name__}: {e}", file=sys.stderr)


def _fetch_lead_code(record_id: str, tok: str, retry: bool = False) -> str:
    """Đọc mã Lead/Chance của record. Autonumber sinh async -> thử lại 1 lần sau 2s nếu rỗng."""
    for attempt in range(2 if retry else 1):
        if attempt:
            time.sleep(2)
        try:
            r = request_retry("GET", _api(f"records/{record_id}"), headers={"Authorization": f"Bearer {tok}"},
                              timeout=15.0)
            v = (r.json().get("data", {}).get("record", {}).get("fields", {}) or {}).get("Lead/Chance")
            if isinstance(v, list):                    # phòng field trả dạng block
                v = "".join(x.get("text", "") for x in v if isinstance(x, dict))
            if v:
                return str(v)
        except Exception as e:
            print(f"[crm] đọc Lead/Chance {record_id} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
    return ""


def lead_code(psid: str) -> str:
    """Mã Lead/Chance đã lưu của khách (cho thông báo admin). Rỗng nếu chưa có."""
    return _load_meta(psid).get("lead_code", "")


def _record_exists(record_id: str, tok: str) -> bool:
    """record_id còn tồn tại trong Base không (phòng bị xóa tay -> tránh update record ma)."""
    try:
        r = request_retry("GET", _api(f"records/{record_id}"), headers={"Authorization": f"Bearer {tok}"},
                          timeout=15.0)
        return r.json().get("code") == 0
    except Exception:
        return False


def _find_by_phone(phone: str, tok: str) -> str | None:
    """record_id của lead có SĐT trùng (record đầu tiên). None nếu chưa có."""
    body = {"filter": {"conjunction": "and", "conditions": [
        {"field_name": "Số điện thoại", "operator": "is", "value": [phone]}]},
        "field_names": ["Số điện thoại"], "automatic_fields": False}
    r = request_retry("POST", _api("records/search"), headers={"Authorization": f"Bearer {tok}"},
                      json=body, timeout=20.0)
    for it in (r.json().get("data", {}).get("items") or []):
        return it.get("record_id")
    return None


def upsert_lead(psid: str, lead: dict) -> str:
    """Tạo/cập nhật lead của 1 khách. Trả 'created'/'updated'/'skipped'/'error'.

    record_id lưu trong conversations/<psid>.crm.json -> lần sau update thẳng, không search.
    KHÔNG ném lỗi ra ngoài.
    """
    phone = (lead.get("sdt") or "").strip()
    if not phone:
        return "skipped"
    if not (config.LARK_APP_ID and _crm_base() and _crm_table()):
        print("[crm] thiếu cấu hình LARK_CRM_* -> bỏ qua ghi lead", file=sys.stderr)
        return "skipped"
    try:
        tok = _tenant_token()
        fields = _build_fields(lead)
        if not fields:
            return "skipped"

        # 1) record_id đã lưu của khách -> update thẳng (nhanh, chắc, không đua search)
        rid = _load_meta(psid).get("record_id")
        if rid and _record_exists(rid, tok):
            _write("PUT", f"records/{rid}", tok, fields)
            _save_meta(psid, rid, lead, _fetch_lead_code(rid, tok))
            return "updated"

        # 2) chưa có / record bị xóa -> fallback tìm SĐT 1 lần (chống trùng khi mất file)
        rid = _find_by_phone(phone, tok)
        if rid:
            _write("PUT", f"records/{rid}", tok, fields)
            _save_meta(psid, rid, lead, _fetch_lead_code(rid, tok))
            return "updated"

        # 3) tạo mới, lưu record_id + mã Lead/Chance (autonumber -> retry đọc)
        new_id = _write("POST", "records", tok, fields)
        _save_meta(psid, new_id, lead, _fetch_lead_code(new_id, tok, retry=True))
        return "created"
    except Exception as e:
        print(f"[crm] ghi lead {phone} lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return "error"
