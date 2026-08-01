"""Gửi ảnh lên FB: thử lại khi hỏng, giãn cách giữa các ảnh, cảnh báo nói đúng thiệt hại."""
import asyncio
import io

import pytest
from PIL import Image

import config
import messenger


def _png(size=(40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (10, 120, 200, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(config, "PAGE_TOKEN", "x")
    monkeypatch.setattr(messenger, "_IMG_RETRY_GAP_S", 0)
    monkeypatch.setattr(messenger, "_SEND_GAP_S", 0)


def test_hong_lan_dau_thi_thu_lai_va_ep_jpeg(monkeypatch):
    """FB trả lỗi chập chờn -> lượt 2 gửi lại dạng JPEG, không báo admin vì đã thành công."""
    calls, alerts = [], []

    async def fake_post(url, *, data=None, files=None, im_lang=False, **kw):
        calls.append((files["filedata"][0], files["filedata"][2], im_lang))
        return len(calls) > 1                       # lượt đầu hỏng, lượt sau OK

    monkeypatch.setattr(messenger, "_fb_post", fake_post)
    monkeypatch.setattr(messenger, "_send_failed",
                        lambda *a, **k: alerts.append(a) or asyncio.sleep(0))

    asyncio.run(messenger.send_image_bytes("p1", _png(), "image/png"))

    assert len(calls) == 2, "phải thử lại đúng 1 lần"
    assert calls[0] == ("image.png", "image/png", True), "lượt đầu im lặng, không báo admin"
    assert calls[1][:2] == ("image.jpg", "image/jpeg"), "lượt 2 phải ép về JPEG"
    assert calls[1][2] is False, "lượt cuối hỏng thì mới được báo admin"
    assert not alerts


def test_thanh_cong_ngay_thi_khong_thu_lai(monkeypatch):
    calls = []

    async def fake_post(url, *, files=None, **kw):
        calls.append(files["filedata"][0])
        return True

    monkeypatch.setattr(messenger, "_fb_post", fake_post)
    asyncio.run(messenger.send_image_bytes("p1", _png(), "image/png"))
    assert len(calls) == 1


def test_gian_cach_giua_cac_anh(monkeypatch):
    """4 ảnh -> 3 lần nghỉ. Dội liên tiếp là FB nghẹn attachment."""
    ngu = []
    that = asyncio.sleep                            # giữ bản gốc, không thì lambda tự gọi lại mình
    monkeypatch.setattr(messenger.asyncio, "sleep", lambda s: ngu.append(s) or that(0))
    monkeypatch.setattr(messenger, "send_image_bytes", lambda *a, **k: that(0))
    imgs = [(b"x", "image/jpeg")] * 4
    asyncio.run(messenger._send_images("p1", imgs))
    assert len(ngu) == 3


def test_canh_bao_anh_noi_dung_khach_van_nhan_duoc_chu(monkeypatch):
    """Ảnh gửi SAU text -> cảnh báo không được nói 'khách KHÔNG nhận được tin'."""
    tins = []
    monkeypatch.setattr(messenger, "alert_admins",
                        lambda key, msg: tins.append(msg) or asyncio.sleep(0))
    monkeypatch.setattr(messenger, "_label", lambda psid: _tra("Khach (psid 1)"))

    detail = '{"error":{"message":"(#100) Upload attachment failure.","code":100,"error_subcode":2018047}}'
    asyncio.run(messenger._send_failed("img upload", 400, detail, "1"))
    assert "THIẾU ẢNH" in tins[0]
    assert "KHÔNG nhận được tin" not in tins[0]

    tins.clear()
    asyncio.run(messenger._send_failed("send", 400, '{"error":{"code":551}}', "1"))
    assert "KHÔNG nhận được tin" in tins[0], "lỗi gửi CHỮ vẫn phải báo nặng như cũ"


def test_khong_nen_duoc_anh_thi_phai_bao_admin(monkeypatch):
    """Ảnh không nén được = đi nguyên bản ~20MB, FB từ chối sạch mà bot vẫn 'chạy bình thường'.
    Ca hỏng âm thầm này bắt buộc phải báo, không được nuốt."""
    keu = []
    monkeypatch.setattr(messenger.alerts, "alert", lambda key, msg: keu.append((key, msg)))

    data, ctype = messenger._shrink_image(b"khong-phai-anh" * 100_000, "image/png")

    assert (data, ctype) == (b"khong-phai-anh" * 100_000, "image/png"), "hỏng thì gửi nguyên gốc"
    assert keu and "KHÔNG NÉN ĐƯỢC ẢNH" in keu[0][1]


def test_anh_nen_xong_van_qua_nang_thi_canh_bao(monkeypatch):
    """Nén rồi vẫn > ngưỡng -> FB dễ trả #100. Báo trước, khỏi đoán mò quanh token."""
    keu = []
    monkeypatch.setattr(messenger.alerts, "alert", lambda key, msg: keu.append((key, msg)))
    monkeypatch.setattr(messenger, "_IMG_CANH_BAO_MB", 0.000_001)   # ép mọi ảnh thành "nặng"

    messenger._shrink_image(_png((2000, 2000)), "image/png")

    assert keu and keu[0][0] == "img:qua-nang"


def test_anh_nho_thi_gui_nguyen_khong_canh_bao(monkeypatch):
    keu = []
    monkeypatch.setattr(messenger.alerts, "alert", lambda key, msg: keu.append(key))
    goc = _png((40, 40))
    assert messenger._shrink_image(goc, "image/png") == (goc, "image/png")
    assert keu == []


async def _tra(v):
    return v


def test_loai_tep_khong_phai_anh_khoi_cot_anh():
    """Ca thật LD12: cột Ảnh có IMG_5789.MOV 45MB -> tải về Pillow không mở nổi, khách mất
    ảnh. Lọc ngay ở đầu nguồn. Lark trả type RỖNG cho .HEIC nên phải xét cả đuôi tên file."""
    from bot_tools.lark_image import _la_anh

    assert _la_anh({"name": "a.png", "type": "image/png"})
    assert _la_anh({"name": "IMG_5808.HEIC", "type": ""}), "HEIC type rỗng vẫn là ảnh"
    assert _la_anh({"name": "IMG_5808 (2).heic", "type": None})
    assert not _la_anh({"name": "IMG_5789.MOV", "type": "video/quicktime"})
    assert not _la_anh({"name": "bao-gia.pdf", "type": "application/pdf"})
    assert not _la_anh({"name": "khong-duoi", "type": ""})


def test_heic_doc_duoc_sau_khi_bat_pillow_heif():
    """Thiếu pillow-heif thì .HEIC ném UnidentifiedImageError -> anh di nguyen ban, FB tu choi."""
    from PIL import Image
    assert "HEIF" in Image.OPEN or "HEIC" in Image.OPEN, "chưa bật được HEIC opener"


def test_canh_bao_chi_dung_thu_pham_khi_file_khong_phai_anh(monkeypatch):
    """Bao 'kiem tra Pillow' trong khi Pillow van tot = admin di soi nham cho."""
    keu = []
    monkeypatch.setattr(messenger.alerts, "alert", lambda key, msg: keu.append(msg))
    messenger._shrink_image(b"day-khong-phai-anh" * 100_000, "image/png")
    assert keu and "HEIC" in keu[0] and "Lark Base" in keu[0]
