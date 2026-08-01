"""Bot không được đeo bám khách: cờ khoá nằm ở database và chặn ngay tại cửa gửi tin."""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
import config
import messenger
import state


def _chat_im_lang(tmp_path: Path, psid: str, gio: float = 5.0) -> str:
    """Dựng log: khách nhắn, bot trả lời xong, rồi im ngần ấy giờ."""
    moc = (datetime.now() - timedelta(hours=gio)).strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / f"{psid}.json").write_text(json.dumps([
        {"role": "user", "content": "cho xem mau", "at": moc},
        {"role": "assistant", "content": "da em gui Bac", "at": moc},
    ]), encoding="utf-8")
    return moc


def test_moi_khach_chi_bi_nhac_dung_mot_lan(monkeypatch, tmp_path):
    """Lỗi đeo bám cũ: mark tính theo mốc tin khách cuối -> khách nhắn lại là bị nhắc lại,
    mỗi ngày một lần, vô hạn. Nay có trần _MAX_FOLLOWUPS cho cả đời khách."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    moc = _chat_im_lang(tmp_path, "k1")

    assert [p for p, _ in brain.followup_candidates(4.0)] == ["k1"]
    brain.mark_followed("k1", moc)
    assert brain.followup_candidates(4.0) == [], "nhắc rồi thì thôi"

    # Khách nhắn thêm 1 tin (mốc mới) rồi lại im -> bản cũ nhắc tiếp, bản mới thì không.
    moc2 = _chat_im_lang(tmp_path, "k1", gio=6.0)
    assert moc2 != moc
    assert brain.followup_candidates(4.0) == [], "đã đủ trần -> không đeo bám nữa"


def test_khach_da_chot_hoac_bi_khoa_khong_bi_nhac(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    _chat_im_lang(tmp_path, "chot")
    _chat_im_lang(tmp_path, "khoa")
    _chat_im_lang(tmp_path, "thuong")

    brain.mark_closed("chot")
    state.khoa_nhan("khoa", "khách gắt", "TAMDUNG", gio=24)

    assert [p for p, _ in brain.followup_candidates(4.0)] == ["thuong"]


def _bat_tin_gui(monkeypatch) -> list:
    """Thay _fb_post bằng bản ghi lại - test không đụng FB thật."""
    da_gui: list = []

    async def _fake_post(url, **kw):
        da_gui.append(kw.get("payload") or kw.get("data"))
        return True

    monkeypatch.setattr(messenger, "_fb_post", _fake_post)
    monkeypatch.setattr(config, "PAGE_TOKEN", "test-token")
    return da_gui


def test_khach_kho_chiu_thi_moi_duong_gui_deu_bi_chan(monkeypatch, tmp_path):
    """Khoá đọc từ database ngay tại send_text/send_image_bytes -> không luồng nào lọt."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    da_gui = _bat_tin_gui(monkeypatch)

    messenger._pause_bot("kk", "khách gắt vì bị hỏi lặp")
    asyncio.run(messenger.send_text("kk", "Bác ơi còn phân vân mẫu nào không ạ?"))
    asyncio.run(messenger.send_image_bytes("kk", b"\xff\xd8\xff", "image/jpeg"))
    assert da_gui == [], "khách đang khó chịu mà vẫn gửi được"

    # Tin xoa dịu (tin đặt ra cái khoá) vẫn phải đi.
    asyncio.run(messenger.send_text("kk", messenger._XOA_DIU_REPLY, force=True))
    assert len(da_gui) == 1


def test_khach_tu_choi_van_duoc_tra_loi_nhung_khong_bi_nhan_chu_dong(monkeypatch, tmp_path):
    """Từ chối khác khó chịu: khách hỏi thì vẫn trả lời, chỉ cấm bot TỰ đi nhắc."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    da_gui = _bat_tin_gui(monkeypatch)

    state.patch("tc", y_dinh="tu choi")
    asyncio.run(messenger.send_text("tc", messenger._FOLLOWUP_TEXT, chu_dong=True))
    assert da_gui == [], "khách đã từ chối mà bot vẫn tự nhắn"

    asyncio.run(messenger.send_text("tc", "Dạ mẫu M01 khoảng 10,7 triệu ạ."))
    assert len(da_gui) == 1, "khách hỏi thì phải trả lời"


def test_khach_doi_ngung_nhan_bi_bat_bang_luat_cung():
    """Không chờ AI chấm ý định: câu đòi ngừng phải bị code bắt ngay."""
    for cau in ("đừng nhắn nữa nhé", "sao nhắn hoài vậy", "phiền quá đi mất", "bỏ theo dõi trang"):
        assert messenger._forced_handoff_reason(cau) == messenger._LY_DO_DOI_NGUNG, cau
    assert messenger._LY_DO_DOI_NGUNG in messenger._LY_DO_KHO_CHIU
    # Câu tư vấn bình thường KHÔNG được dính.
    for cau in ("phiền Bác cho em xin kích thước ạ", "mộ đá giá bao nhiêu", "em cần mẫu khác"):
        assert messenger._forced_handoff_reason(cau) is None, cau


def test_khach_noi_khong_co_nhu_cau_thi_khong_bi_nhac_lai(monkeypatch, tmp_path):
    """Luật cứng chấm ý định, không phụ thuộc AI (AI lỗi là khách bị nhắc oan)."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    _chat_im_lang(tmp_path, "kn")
    assert messenger._y_dinh_tu_khoa("thôi anh không có nhu cầu đâu em") == "tu choi"
    assert messenger._y_dinh_tu_khoa("cho anh xin giá mộ đôi") == ""

    state.patch("kn", y_dinh=messenger._y_dinh_tu_khoa("anh nhắn nhầm"))
    assert brain.followup_candidates(4.0) == []


def test_co_khoa_song_sot_khi_mat_cache_local(monkeypatch, tmp_path):
    """VPS dựng lại container = mất file local. Cờ phải kéo lại được từ database."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    state.khoa_nhan("mat", "khách gắt", "TAMDUNG", gio=24)
    tren_db = state.get("mat")

    state._path("mat").unlink()                       # giả lập mất cache local
    monkeypatch.setattr(state.fb, "fetch_state", lambda psid: tren_db)

    assert state.bi_khoa("mat")[0] is True, "mất cache là mất khoá -> nhắn lại khách đã gắt"
