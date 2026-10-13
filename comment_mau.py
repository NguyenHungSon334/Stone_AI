"""Bộ trả lời mẫu BÌNH LUẬN công khai (bản 09/10/2026, phần 1 mục 1-5).

AI chỉ CHỌN MÃ, chữ trả lời lấy nguyên văn ở đây -> không bịa giá, không hứa quá tay.
Mẫu gốc có ô [ ] (B3 tên đá, B8 diện tích) cần hồ sơ công trình mà bot không có -> dùng bản
mời nhắn trang. Mẫu ⏳ (chờ CEO chốt) dùng bản tạm như tài liệu cho phép.
"""
import random

# mã -> (khi nào dùng, để AI chọn)
NHOM = {
    "A1": "hỏi giá chung: 'xin giá', 'giá sao', 'mẫu này bao nhiêu'",
    "A2": "hỏi giá kèm kích thước/số ngôi/ngân sách nhà mình: 'đất 5x7 hết bao nhiêu', '300 triệu làm được gì'",
    "A3": "trách không để giá: 'sao không để giá', 'hỏi giá mà không báo'",
    "A4": "KHÁCH tự để lại số điện thoại của mình công khai",
    "A5": "'ib', 'inbox', 'quan tâm', 'tư vấn giúp', xin tư vấn chung",
    "A6": "kêu đã nhắn mà chưa ai trả lời, hỏi mấy hôm chưa thấy",
    "B1": "hỏi xưởng/cơ sở ở đâu, muốn đến xem",
    "B2": "hỏi ở tỉnh mình có làm không, xa có làm được không",
    "B3": "hỏi công trình trong bài làm bằng đá gì",
    "B4": "hỏi đá nào tốt nhất, so xanh rêu với granite",
    "B5": "hỏi nguyên khối hay ghép, nghi đổ bê tông bên trong",
    "B6": "nghi hàng Trung Quốc, 'chạy máy CNC thôi'",
    "B7": "xin mẫu, catalog, xem thêm công trình",
    "B8": "hỏi khu trong bài rộng bao nhiêu m2, ở đâu",
    "B9": "hỏi đất nhỏ làm được không, có làm mộ tròn/kiểu này không",
    "B10": "hỏi làm mất bao lâu, đặt giờ kịp Tết không",
    "B11": "hỏi để lâu có rêu, nứt, phai màu không",
    "B12": "hỏi phong thuỷ: khắc chữ gì, hướng nào, thước Lỗ Ban",
    "B13": "hỏi khắc chữ, gắn ảnh lên bia được không",
    "B14": "hỏi làm lại mộ cũ bằng đá, bốc mộ, làm vừa quách",
    "B15": "hỏi chính quyền có cho xây to thế không",
    "B16": "hỏi có bán bàn ghế đá, cối đá, linh vật không",
    "B17": "hỏi có hợp đồng không, ký thế nào",
    "C1": "khen đẹp",
    "C2": "'A Di Đà Phật', 'Nam mô', 'ông bà ấm lòng'",
    "C3": "khen con cháu hiếu thảo, 'uống nước nhớ nguồn'",
    "D1": "chê lãng phí, tốn đất, 'sống không cho ăn chết làm to'",
    "D2": "chê đắt, 'tiền đấy xây nhà'",
    "D3": "nghi giá rẻ không thật, 'có đúng như quảng cáo không'",
    "D4": "nghi lừa đảo, 'quay công trình nhà người khác'",
    "D5": "so với làng đá Ninh Vân hay bên khác rẻ/đẹp hơn",
    "D6": "chê đá loại C, đá xấu",
    "D7": "chê thiết kế xấu, rườm rà",
    "E1": "chửi, thô tục",
    "E2": "bên khác/đối thủ chào hàng, để số điện thoại hay đường dẫn của họ",
    "E4": "chỉ gắn tên bạn bè, sticker, không có nội dung",
    "E5": "xin việc, chào hợp tác, chào bán đá",
}

# Nhắn riêng (private reply - FB cho 1 lần/comment) chỉ với khách có nhu cầu / có câu hỏi.
NHAN_RIENG = {c for c in NHOM if c[0] in "AB"}
# Ẩn bình luận: lộ SĐT khách (giữ số khỏi người lạ), chửi, đối thủ chào hàng.
AN = {"A4", "E1", "E2"}
# Phải có người thật vào ngay, không chỉ trả lời.
BAO_ADMIN = {"A4", "A6"}

_A1_CHUA_NHAN = ("Dạ, giá khu lăng tùy kích thước đất, dòng đá và các hạng mục nhà mình chọn ạ. "
                 "Bác nhắn trang kích thước khu đất và vài tấm ảnh, Hồn Đá gửi bảng giá bóc tách "
                 "từng hạng mục để bác xem.")
# Các mẫu nói "đã nhắn riêng" -> chỉ dùng khi private reply THẬT SỰ đi được.
_CAN_DA_NHAN = {"A1", "A2", "A5"}

MAU: dict[str, list[str] | None] = {
    "A1": ["Dạ, Hồn Đá vừa nhắn tin riêng cho bác rồi ạ. Bác mở tin nhắn giúp, gửi kích thước "
           "khu đất và vài tấm ảnh, tư vấn viên gửi bác bảng giá bóc tách từng hạng mục."],
    "A2": ["Dạ, đất nhà bác như vậy là bên tư vấn tính được rồi ạ. Hồn Đá đã nhắn riêng cho bác, "
           "bác gửi thêm vài tấm ảnh khu đất và cho biết nhà mình muốn làm những hạng mục nào nhé."],
    "A3": ["Dạ, Hồn Đá không để một con số chung vì mỗi nhà một khu đất, một dòng đá, một cách làm, "
           "để một giá chung dễ làm bác hiểu sai ạ. Bác nhắn trang kích thước đất, bên tư vấn báo giá "
           "bóc tách từng phần để bác thấy rõ tiền nằm ở đâu."],
    "A4": ["Dạ, Hồn Đá đã nhận và chuyển tư vấn viên gọi lại cho bác ạ. Hồn Đá xin phép ẩn bình "
           "luận này để giữ số của bác khỏi người lạ."],
    "A5": ["Dạ, Hồn Đá đã nhắn tin cho bác rồi ạ, bác mở tin nhắn giúp."],
    "A6": ["Dạ, Hồn Đá xin lỗi bác vì để bác chờ ạ. Hồn Đá đang chuyển ngay cho tư vấn viên nhắn lại bác."],
    "B1": ["Dạ, văn phòng Hồn Đá ở Hà Nội, xưởng ở Ninh Bình và Thanh Hoá, mỏ đá ở Hà Trung, Thanh "
           "Hoá ạ. Bác muốn về xem tận nơi thì nhắn trang báo trước ngày, Hồn Đá gửi định vị và bố "
           "trí người đón bác."],
    "B2": ["Dạ, Hồn Đá làm công trình ở nhiều tỉnh ạ. Bác nhắn trang địa chỉ khu đất, tư vấn viên "
           "xem đường vào rồi báo bác cụ thể."],
    "B3": ["Dạ, bác nhắn trang giúp Hồn Đá, tư vấn viên báo bác dòng đá của công trình này và gửi "
           "ảnh mẫu thật các dòng đá đặt cạnh nhau để bác xem ạ."],
    "B4": ["Dạ, Hồn Đá làm hai họ đá: đá xanh Thanh Hoá (xanh rêu, xanh đen) và đá granite (Bình "
           "Định, đen Ấn Độ). Granite cứng hơn rõ. Không có đá tốt nhất cho mọi nhà; chọn theo màu "
           "nhà mình ưng, chỗ đặt và mức đầu tư ạ."],
    "B5": ["Dạ, chữ \"nguyên khối\" mỗi nơi hiểu một kiểu nên Hồn Đá nói rõ ba loại: liền khối hoàn "
           "toàn, liền khối từng cấu kiện, và cấu kiện lắp ghép. Chỗ ghép nối bằng mộng âm dương. "
           "Bác nhắn trang, Hồn Đá gửi ảnh, video từng cấu kiện của chính công trình này để bác tự xem."],
    "B6": ["Dạ, Hồn Đá tự sản xuất ở xưởng Ninh Bình và Thanh Hoá, đá xanh lấy từ mỏ của Hồn Đá ở "
           "Thanh Hoá ạ. Máy làm phần cắt thô, thợ chạm tay phần hoa văn. Bác về xưởng xem tận mắt "
           "thì nhắn trang báo trước nhé."],
    "B7": ["Dạ, Hồn Đá có nhiều ảnh, video công trình thật, cả khu lớn lẫn khu nhỏ ạ. Bác nhắn trang "
           "khu đất nhà mình rộng chừng nào, Hồn Đá gửi những mẫu cỡ tương tự cho bác dễ hình dung."],
    "B8": ["Dạ, bác nhắn trang giúp Hồn Đá, tư vấn viên báo bác diện tích khu này và gửi thêm ảnh, "
           "video để bác xem ạ."],
    "B9": ["Dạ, Hồn Đá làm theo kích thước đất từng nhà, khu nhỏ cũng bố trí được cho gọn và trang "
           "nghiêm ạ. Bác nhắn trang chiều dài, chiều rộng khu đất và kiểu nhà mình thích, tư vấn "
           "viên trả lời cụ thể cho bác."],
    "B10": ["Dạ, thời gian làm tùy quy mô khu lăng ạ: phải qua thiết kế, chọn và xử lý phôi cho đồng "
            "màu rồi mới chế tác. Nhà mình cần gấp vẫn xử lý được. Bác nhắn trang mốc gia đình muốn "
            "xong, tư vấn viên xem và báo bác."],
    "B11": ["Dạ, đá để ngoài trời lâu năm thì có rêu, bụi, màu dịu đi tự nhiên, lau rửa là sạch ạ; "
            "tránh tẩy bằng hoá chất. Đá có bền hay không nằm ở phôi đá sạch, không nứt ngầm, mộng "
            "ghép khít và cấu kiện đủ dày. Công trình có bảo hành ghi trong hợp đồng ạ."],
    "B12": ["Dạ, phần hướng, ngày giờ và chữ khắc thì nhà mình hỏi thầy của gia đình là chuẩn nhất "
            "ạ. Thầy đưa hướng và kích thước nào, Hồn Đá làm đúng theo đó, có thể trao đổi trực tiếp "
            "với thầy."],
    "B13": ["Dạ, bác nhắn trang nội dung muốn khắc và ảnh nhà mình định gắn, tư vấn viên hướng dẫn "
            "cụ thể cần chuẩn bị gì ạ."],
    "B14": ["Dạ, bác nhắn trang vài tấm ảnh mộ hiện có và kích thước quách, tiểu (nếu có), tư vấn "
            "viên xem rồi trả lời cụ thể cho bác ạ."],
    "B15": ["Dạ, mỗi xã, mỗi nghĩa trang có quy định riêng về diện tích và chiều cao ạ. Nhà mình hỏi "
            "trước xã hoặc ban quản lý nghĩa trang, rồi gửi Hồn Đá con số đó để bố trí cho vừa."],
    "B16": ["Dạ, Hồn Đá chỉ làm lăng mộ, khu lăng dòng họ, nhà thờ họ và các hạng mục trong khu lăng "
            "thôi ạ. Nếu là hạng mục đặt trong khu lăng nhà mình, bác nhắn trang để tư vấn viên xem giúp."],
    "B17": ["Dạ, công trình nào Hồn Đá cũng làm hợp đồng, phần bảo hành ghi trong hợp đồng ạ. Bác "
            "nhắn trang, tư vấn viên gửi bác xem các bước cụ thể."],
    "C1": ["Dạ, Hồn Đá cảm ơn bác ạ.",
           "Dạ, cảm ơn bác đã khen, anh em thợ đọc được chắc vui lắm ạ."],
    "C2": ["Dạ, Hồn Đá cảm ơn bác ạ. 🙏"],
    "C3": ["Dạ, đúng là nhà nào lo được cho ông bà chu đáo thì con cháu cũng yên lòng bác ạ. Hồn Đá "
           "cảm ơn bác."],
    "D1": ["Dạ, Hồn Đá cảm ơn bác đã chia sẻ. Mỗi nhà có cách giữ lòng hiếu riêng; làm khu lớn hay "
           "một ngôi nhỏ gọn đều đáng quý ạ."],
    "D2": ["Dạ, khu lăng có nhiều cỡ ạ, khu nhỏ gọn cũng làm được trang nghiêm. Bác cho Hồn Đá biết "
           "khu đất và mức nhà mình tính, tư vấn viên đưa phương án vừa với nhà mình."],
    "D3": ["Dạ, bác hỏi kỹ là đúng ạ. Giá mỗi nhà tùy khu đất, dòng đá và hạng mục, nên Hồn Đá báo "
           "bằng bảng bóc tách từng phần để bác đối chiếu được. Bác cũng có thể về xưởng ở Ninh Bình, "
           "Thanh Hoá xem đá tận mắt trước khi quyết."],
    # ponytail: bỏ tên pháp nhân - tài liệu ghi "TNHH Văn hóa Mỹ thuật", Personal.md ghi "Cổ phần
    # Mỹ Nghệ", chưa rõ bên nào đúng. Chốt rồi thì thêm lại.
    "D4": ["Dạ, văn phòng Hồn Đá ở Hà Nội, xưởng ở Ninh Bình và Thanh Hoá ạ. Công trình nào cũng có "
           "hợp đồng. Bác cứ nhắn trang hẹn ngày về xưởng, xem phôi đá và thợ đang làm rồi hãy tính."],
    "D5": ["Dạ, bác cứ so ạ. Khi so, bác nhớ so cùng dòng đá, cùng độ dày cấu kiện và cùng cách ghép "
           "thì mới ra đúng giá trị."],
    "D6": ["Dạ, Hồn Đá dùng phôi đã lọc bỏ đá nứt ngầm ạ. Bác nhắn trang, Hồn Đá gửi ảnh, video phôi "
           "và cấu kiện của chính công trình này để bác xem."],
    "D7": ["Dạ, Hồn Đá cảm ơn bác góp ý. Mỗi nhà một ý thích, khu lăng làm theo ý gia đình nên đơn "
           "giản hay cầu kỳ đều làm được ạ."],
    "E1": None,   # ẩn, không trả lời
    "E2": None,   # ẩn
    "E4": None,   # không cần trả lời
    "E5": ["Dạ, bác nhắn trang giúp Hồn Đá để chuyển đúng người ạ."],
}


def cau_tra_loi(ma: str, da_nhan_rieng: bool) -> str | None:
    """Câu trả lời công khai cho mã. None = không trả lời."""
    if ma in _CAN_DA_NHAN and not da_nhan_rieng:
        return _A1_CHUA_NHAN
    mau = MAU.get(ma)
    return random.choice(mau) if mau else None


if __name__ == "__main__":
    assert set(MAU) == set(NHOM), "mã mẫu và mã nhóm lệch nhau"
    assert cau_tra_loi("A5", da_nhan_rieng=False) == _A1_CHUA_NHAN
    assert "nhắn tin riêng" in cau_tra_loi("A1", da_nhan_rieng=True)
    assert cau_tra_loi("E1", True) is None and cau_tra_loi("E4", False) is None
    print("ok")
