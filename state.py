"""
Cờ trạng thái 1 khách (khoá nhắn, ý định, follow-up, đã chốt, ảnh đã gửi...).

NGUỒN THẬT = Firebase `khach_state/<psid>`. File local `<psid>.state.json` chỉ là CACHE.

Trước đây mỗi cờ nằm 1 file rời (.noise.state, .followup, .missed, .images.json, .profile.json,
.quaylai) và KHÔNG lên cloud -> VPS dựng lại container là mất sạch: khách đã gắt bị nhắn lại,
khách đã chốt bị nhắc lại. Gom hết vào 1 node + mirror Firebase để sống sót redeploy.

Đọc: cache local -> miss thì kéo Firebase -> vẫn miss thì DỰNG TỪ SIDECAR CŨ (migrate 1 lần).
Ghi: cập nhật cache ngay (đồng bộ) + đẩy Firebase ở thread nền (fb._run).

ponytail: đọc thẳng file mỗi lần, không cache RAM. File nhỏ, vài lượt/giây. Thêm cache khi đo
thấy chậm thật.
"""
import sys
import time
from pathlib import Path

import config
import fb
import util

_DIR = config.ROOT / "conversations"

# Cờ nhiễu/khoá dùng chung tên với bản cũ để bảng điều khiển và code cũ đọc được y nguyên.
_NOISE_KEYS = ("stopped", "count", "recent", "paused_until", "reason")


def _path(psid: str) -> Path:
    return _DIR / f"{util.safe_psid(psid)}.state.json"


def _old(psid: str, suffix: str) -> Path:
    return _DIR / f"{util.safe_psid(psid)}{suffix}"


def _doc_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _tu_sidecar_cu(psid: str) -> dict:
    """Dựng state từ các file cờ đời cũ. Chạy 1 lần/khách, khi chưa có state ở local lẫn cloud.

    Không migrate = mọi khách đang chốt/đang khoá bị coi như khách mới ngay lúc deploy."""
    out: dict = {}
    noise = util.read_json(_old(psid, ".noise.state"), {})
    if isinstance(noise, dict) and noise:
        out.update({k: noise[k] for k in _NOISE_KEYS if k in noise})
        # Bản cũ chỉ có paused_until (tạm dừng vì khách khó chịu) -> chính là khoá nhắn.
        if float(noise.get("paused_until") or 0) > time.time():
            out["no_contact"] = True
            out["ly_do"] = str(noise.get("reason") or "Khách khó chịu (dữ liệu cũ)")
            out["nguon"] = "migrate"
    profile = util.read_json(_old(psid, ".profile.json"), {})
    if isinstance(profile, dict) and profile:
        out["profile"] = profile
        if profile.get("y_dinh"):
            out["y_dinh"] = str(profile["y_dinh"]).strip().lower()
    imgs = util.read_json(_old(psid, ".images.json"), {})
    if isinstance(imgs, dict) and imgs:
        out["img_index"] = imgs
    if _old(psid, ".crm.json").exists():
        out["crm_done"] = True
    followed = _doc_text(_old(psid, ".followup"))
    if followed:
        out["followed_at"] = followed
        out["followup_count"] = 1     # bản cũ không đếm; coi như đã nhắc 1 lần
    missed = _doc_text(_old(psid, ".missed"))
    if missed:
        out["missed_at"] = missed
    quaylai = _doc_text(_old(psid, ".quaylai"))
    if quaylai:
        out["returning_at"] = quaylai
    return out


def get(psid: str) -> dict:
    """Toàn bộ cờ của 1 khách. Luôn trả dict (rỗng = khách chưa có cờ nào)."""
    local = util.read_json(_path(psid), None)
    if isinstance(local, dict):
        return local
    remote = fb.fetch_state(psid)
    if not isinstance(remote, dict) or not remote:
        remote = _tu_sidecar_cu(psid)
        if remote:
            fb.merge_state(psid, remote)          # cờ đời cũ chưa từng lên cloud -> đẩy lên
    _ghi_cache(psid, remote)
    return remote


def _ghi_cache(psid: str, data: dict) -> None:
    try:
        util.write_json_atomic(_path(psid), data)
    except Exception as e:
        # Cache hỏng KHÔNG chặn bot: lượt sau đọc lại từ Firebase (chậm hơn, vẫn đúng).
        print(f"[state] ghi cache lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)


def patch(psid: str, **fields) -> dict:
    """Cập nhật vài cờ, giữ nguyên cờ khác. Trả state sau khi cập nhật.

    Ghi cache local NGAY (lượt kế đọc thấy liền) + đẩy Firebase nền."""
    if not fields:
        return get(psid)
    data = dict(get(psid))
    data.update(fields)
    _ghi_cache(psid, data)
    fb.merge_state(psid, fields)
    return data


def khoa_nhan(psid: str, ly_do: str, nguon: str, gio: float = 0.0) -> None:
    """Đánh dấu khách KHÔNG ĐƯỢC NHẮN nữa.

    gio > 0: IM HOÀN TOÀN tới hạn đó (khách khó chịu - trả lời tiếp cũng là đổ dầu vào lửa).
    gio = 0: chỉ CẤM NHẮN CHỦ ĐỘNG vĩnh viễn (khách đòi ngừng/từ chối) - khách tự nhắn lại thì
             bot vẫn trả lời, đúng phép lịch sự; chỉ không bao giờ tự đi nhắc nữa.

    Đặt luôn cờ 'stopped' cũ để bảng điều khiển và vòng follow-up (đều đã lọc theo cờ này)
    tự bỏ qua khách, khỏi thêm nhánh mới."""
    patch(psid, no_contact=True, ly_do=ly_do, nguon=nguon, stopped=True,
          khoa_luc=time.time(), paused_until=(time.time() + gio * 3600) if gio else 0)


def mo_khoa(psid: str, boi: str = "admin") -> None:
    """Mở khoá, cho bot nhắn lại khách này."""
    patch(psid, no_contact=False, stopped=False, paused_until=0, count=0, recent=[],
          mo_khoa_boi=boi, mo_khoa_luc=time.time())


def bi_khoa(psid: str) -> tuple[bool, str]:
    """Khách đang trong hạn IM HOÀN TOÀN? (chặn cả tin trả lời). Hết hạn -> tự mở phần trả lời.

    Hết hạn CHỈ mở lại quyền trả lời, KHÔNG xoá no_contact: khách đã tỏ ra khó chịu một lần
    thì không bao giờ nên bị bot tự đi nhắc nữa, dù 24h sau họ có nguôi."""
    st = get(psid)
    het_han = float(st.get("paused_until") or 0)
    if not het_han:
        return (False, "")
    if time.time() >= het_han:
        patch(psid, paused_until=0, stopped=False, count=0, recent=[])
        return (False, "")
    return (True, str(st.get("ly_do") or "khách khó chịu"))


def cam_nhan_chu_dong(psid: str) -> tuple[bool, str]:
    """Cấm bot TỰ nhắn (follow-up, trả lời bù, tin quay lại)? = đang im hoàn toàn, đã đánh dấu
    no_contact, hoặc ý định là từ chối/hoãn lại."""
    khoa, ly_do = bi_khoa(psid)
    if khoa:
        return (True, ly_do)
    st = get(psid)
    if st.get("no_contact"):
        return (True, str(st.get("ly_do") or "khách yêu cầu ngưng"))
    if st.get("stopped"):
        # Bot đã CỐ Ý ngưng trả lời (nhiễu/sticker liên tiếp). Vòng follow-up tự lọc cờ này rồi,
        # nhưng phải chặn ở CỬA GỬI nữa: luồng nhắn chủ động thêm sau này sẽ không nhớ tự lọc.
        return (True, str(st.get("reason") or "bot đã ngưng trả lời (tin nhiễu liên tiếp)"))
    if str(st.get("y_dinh") or "").strip().lower() in ("tu choi", "hoan lai"):
        return (True, f"ý định khách: {st.get('y_dinh')}")
    return (False, "")


def xoa(psid: str) -> None:
    """Xoá cờ 1 khách (dùng khi admin xoá khách khỏi dashboard)."""
    try:
        _path(psid).unlink(missing_ok=True)
    except Exception as e:
        print(f"[state] xoá cache lỗi psid={psid}: {type(e).__name__}: {e}", file=sys.stderr)
    fb.delete_state(psid)


def _selftest() -> None:
    """Chạy: python state.py — dùng thư mục tạm, KHÔNG đụng dữ liệu khách thật."""
    import tempfile
    global _DIR
    with tempfile.TemporaryDirectory() as tmp:
        _DIR = Path(tmp)
        fb.merge_state = lambda *a, **k: None          # Firebase tắt trong test
        fb.fetch_state = lambda psid: None
        fb.delete_state = lambda psid: True
        psid = "123"
        assert get(psid) == {}
        assert bi_khoa(psid) == (False, "") and cam_nhan_chu_dong(psid) == (False, "")
        khoa_nhan(psid, "khách gắt", "TAMDUNG", gio=24)
        assert bi_khoa(psid)[0] is True, "khoá xong phải chặn cả tin trả lời"
        patch(psid, followup_count=2)
        assert get(psid)["no_contact"] is True, "patch trường khác không được xoá cờ khoá"
        assert get(psid)["followup_count"] == 2
        mo_khoa(psid)
        assert bi_khoa(psid) == (False, ""), "mở khoá xong phải cho nhắn"

        # Hết hạn -> tự mở phần TRẢ LỜI, nhưng vẫn cấm bot tự đi nhắc.
        khoa_nhan(psid, "hết hạn", "test", gio=24)
        patch(psid, paused_until=time.time() - 1)
        assert bi_khoa(psid)[0] is False, "quá hạn phải trả lời lại được"
        assert cam_nhan_chu_dong(psid)[0] is True, "khách từng gắt thì đừng bao giờ nhắc lại"
        mo_khoa(psid)                                  # chỉ admin mới xoá hẳn dấu
        assert cam_nhan_chu_dong(psid) == (False, "")

        # Khách đòi ngừng (gio=0): vẫn trả lời khi khách nhắn, nhưng CẤM tự đi nhắc.
        khoa_nhan(psid, "khách đòi ngừng nhắn", "regex")
        assert bi_khoa(psid)[0] is False, "cấm chủ động không được chặn tin trả lời"
        assert cam_nhan_chu_dong(psid)[0] is True, "đã đòi ngừng thì cấm nhắn chủ động"

        # Ý định từ chối/hoãn lại (AI chấm) cũng chặn nhắn chủ động, dù không khoá.
        moi = "456"
        patch(moi, y_dinh="hoan lai")
        assert bi_khoa(moi)[0] is False and cam_nhan_chu_dong(moi)[0] is True

        # Migrate từ sidecar đời cũ: khách đã chốt + đang tạm dừng phải giữ nguyên trạng thái.
        old = "999"
        util.write_json_atomic(_old(old, ".noise.state"),
                               {"stopped": True, "paused_until": time.time() + 3600,
                                "reason": "khách gắt"})
        util.write_json_atomic(_old(old, ".crm.json"), {"record_id": "abc"})
        util.write_json_atomic(_old(old, ".profile.json"), {"ten": "A", "y_dinh": "tu choi"})
        _old(old, ".followup").write_text("2026-01-01 10:00:00", encoding="utf-8")
        st = get(old)
        assert st["crm_done"] and st["no_contact"] and st["y_dinh"] == "tu choi"
        assert st["followup_count"] == 1 and st["profile"]["ten"] == "A"
        assert bi_khoa(old)[0] is True, "khách đang tạm dừng ở bản cũ phải còn bị khoá"
    print("state selftest OK")


if __name__ == "__main__":
    _selftest()
