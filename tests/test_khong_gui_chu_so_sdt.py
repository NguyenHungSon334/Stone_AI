"""Tin gửi khách KHÔNG được chứa chữ số SĐT.

Ca thật 29/08/2026: bot nhắn "chuyên gia sẽ liên hệ Bác qua số 0919019385" -> Messenger tự gắn
thẻ "Transfer requested / BacABank / account number 0919019385". Khách phản ứng ngay: "Chưa gặp
gỡ giao dịch đã cần tài khoản là sao". Thẻ do client Messenger dựng, API không tắt được.
"""
import util


def test_che_moi_dang_sdt_khach_hay_gui():
    for so in ("0919019385", "0919 019 385", "091.901.9385", "+84919019385",
               "84919019385", "0919-019-385"):
        cau = f"Chuyên gia sẽ liên hệ Bác qua số {so} ạ."
        assert not any(c.isdigit() for c in util.che_sdt(cau)), so
        assert "số của Bác" in util.che_sdt(cau)


def test_giu_nguyen_so_do_khong_phai_sdt():
    # Kích thước, số lượng, giá: mất mấy số này là hỏng câu báo giá.
    cau = "Bể dài 2.5m rộng 1.8m, 3 con cá, giá 15000000 đồng ạ."
    assert util.che_sdt(cau) == cau


def test_cau_chot_moi_lien_he_khong_co_chu_so():
    import brain
    assert not any(c.isdigit() for c in brain._cau_xin_lien_he("0919019385"))


def test_khong_lap_chu_so():
    """Thay tran thi ra "goi qua so so cua Bac" -> nuot luon chu dan truoc."""
    for cau, mong in (("Chuyên gia liên hệ Bác qua số 0919019385 ạ.",
                       "Chuyên gia liên hệ Bác qua số của Bác ạ."),
                      ("Em gửi qua Zalo 0919019385 nhé.", "Em gửi qua số của Bác nhé."),
                      ("Bác cho SĐT 0919019385.", "Bác cho số của Bác.")):
        assert util.che_sdt(cau) == mong
