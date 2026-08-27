# -*- coding: utf-8 -*-
"""Khách TỰ gửi ảnh mẫu -> chỉ xin số + chuyển chuyên gia, KHÔNG dội mẫu bên em.

Lỗi cũ: _IMG_ONLY_PROMPT bắt bot dò bảng hàng tìm 1-3 mẫu "gần giống" rồi nhắc mã ->
hệ thống tự gửi ảnh mẫu bên em. Khách đưa mẫu của họ mà nhận lại một tràng mẫu khác,
khách khó tính chặn luôn.
"""
import brain
import messenger


def test_prompt_anh_khach_gui_cam_dan_mau():
    khoi = brain._ANH_KHACH_GUI
    for cam in ("KHÔNG nêu mã", "KHÔNG gọi suggest_products", "KHÔNG nêu con số tiền"):
        assert cam in khoi, cam
    assert "<<HANDOFF:" in khoi, "phải báo chuyên gia gọi lại"


def test_img_only_prompt_khong_con_do_bang_hang():
    p = messenger._IMG_ONLY_PROMPT
    assert "KHÔNG dò bảng hàng" in p and "KHÔNG nêu mã" in p
    assert "suggest_products" in p and "KHÔNG gọi suggest_products" in p
    assert "gần nhất với ảnh" not in p, "luật cũ dội mẫu gần giống phải bị gỡ"


class _Part:
    def __init__(self, text):
        self.text, self.function_call = text, None


class _Resp:
    """Đủ hình dạng cho _answer_sync đọc: 1 candidate text, không gọi tool."""
    class _C:
        def __init__(self, text):
            self.content = type("X", (), {"parts": [_Part(text)]})()
            self.finish_reason = "STOP"

    def __init__(self, text):
        self.candidates, self.usage_metadata, self.prompt_feedback = [self._C(text)], None, None


def _chay(monkeypatch, tmp_path, text, images):
    """Gọi thật _answer_sync với model giả trả lời có nhắc mã LD01."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    monkeypatch.setattr(brain, "_get_client", lambda: object())
    monkeypatch.setattr(brain, "_model_id", lambda: "fake")
    monkeypatch.setattr(brain, "_cached_handle", lambda c, m: None)
    monkeypatch.setattr(brain, "_gen_answer",
                        lambda c, m, contents, h: (_Resp("Dạ mẫu LD01 ạ"), h))
    monkeypatch.setattr(brain, "_refresh_profile_bg", lambda *a, **k: None)
    monkeypatch.setattr(brain, "_summarize_bg", lambda *a, **k: None)
    monkeypatch.setattr(brain.lark_image, "get_image_tokens", lambda code: ["tok1"])
    monkeypatch.setattr(brain, "_next_image_token", lambda psid, code, toks: toks[0])
    return brain._answer_sync("p-mau-rieng", text, images)


def test_luot_co_anh_thi_khong_gui_anh_mau(monkeypatch, tmp_path):
    """REGRESSION: khách gửi ảnh + gõ kèm mã cũng KHÔNG được dội ảnh mẫu bên em."""
    khong_anh = _chay(monkeypatch, tmp_path, "cho xem ảnh LD01", None)
    assert "<<IMG:tok1>>" in khong_anh, "khách XIN mẫu bằng chữ thì vẫn gửi ảnh như cũ"

    co_anh = _chay(monkeypatch, tmp_path, "làm giống mẫu này LD01", [(b"x", "image/jpeg")])
    assert "<<IMG:" not in co_anh, "khách gửi mẫu riêng -> cấm dội ảnh mẫu bên em"
