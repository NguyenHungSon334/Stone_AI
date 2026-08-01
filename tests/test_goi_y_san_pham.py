"""Tool gợi ý sản phẩm: trần số mẫu/lượt và thứ tự ưu tiên (bán chạy > có ảnh > giá)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
from bot_tools.find_by_price import _cell, _col, load_rows, search


def _so_ma(ket_qua: str) -> int:
    """Đếm số mẫu trong text render (mỗi mẫu có đúng 1 dòng 'giá:')."""
    return sum(1 for d in ket_qua.splitlines() if d.strip().startswith("giá:"))


def test_toi_da_2_mau_moi_luot():
    """Khách hỏi cả một nhóm hàng -> bot chỉ được đưa 2 mẫu, không dội cả bảng."""
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ"})) == 2


def test_ai_doi_nhieu_hon_van_bi_chan():
    """AI tự truyền limit lớn cũng không vượt trần - trần nằm ở code, không ở persona."""
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ", "limit": 10})) == 2


def test_doi_mau_khac_thi_ra_ma_moi():
    """'Xem thêm' -> exclude_ids loại mã cũ, không đưa lại đúng 2 mẫu vừa gửi."""
    dau = brain._run_tool("suggest_products", {"kind": "Long đình"})
    ma_cu = [d.split(" | ")[0] for d in dau.splitlines() if " | " in d]
    sau = brain._run_tool("suggest_products", {"kind": "Long đình", "exclude_ids": ma_cu})
    ma_moi = [d.split(" | ")[0] for d in sau.splitlines() if " | " in d]

    assert len(ma_cu) == 2 and len(ma_moi) == 2
    assert not set(ma_cu) & set(ma_moi)


def test_uu_tien_ban_chay_va_co_anh():
    """2 mẫu lọt top phải là mẫu bán chạy - sort thuần theo giá thì mẫu dễ chốt rơi mất."""
    header, _, _ = load_rows()
    i_bc, i_anh = _col(header, "Bán chạy"), _col(header, "Có ảnh")
    top = search(None, kind="Mộ", limit=2)

    assert len(top) == 2
    for _, _, r in top:
        assert _cell(r, i_bc), f"mẫu {r[0]} không phải bán chạy mà vẫn lọt top"
        assert _cell(r, i_anh).strip().upper() != "FALSE", f"mẫu {r[0]} không có ảnh"


def test_goi_tool_nhieu_vong_trong_1_luot_van_chi_duoc_2_mau():
    """Ca thật đã lọt lên prod: AI gọi tool 2 vòng trong CÙNG một lượt rồi gộp -> khách hỏi
    'các mẫu Long đình' nhận 3 mẫu. Ngân sách đếm theo LƯỢT, không theo từng lần gọi."""
    ngan_sach = {"con": brain._MAX_GOI_Y}
    vong1 = brain._run_tool("suggest_products", {"kind": "Long đình"}, ngan_sach)
    vong2 = brain._run_tool("suggest_products", {"q": "long đình"}, ngan_sach)

    assert _so_ma(vong1) == 2
    assert _so_ma(vong2) == 0, "vòng 2 cùng lượt không được thêm mẫu nào"
    assert "ĐỦ RỒI" in vong2, "phải nói rõ là hết ngân sách, không phải hết hàng"


def test_luot_moi_thi_ngan_sach_reset():
    """Khách nói 'xem thêm' ở lượt sau -> lại được 2 mẫu, không bị khoá vĩnh viễn."""
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ"}, {"con": 2})) == 2
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ"}, {"con": 2})) == 2


def test_ma_khach_hoi_dich_danh_khong_bi_tru_ngan_sach():
    """product_ids = mã khách chỉ tên -> trả đủ, và không ăn vào ngân sách mẫu tự đề xuất."""
    ngan_sach = {"con": brain._MAX_GOI_Y}
    got = brain._run_tool("suggest_products", {"product_ids": ["M01", "M04", "LD03"]}, ngan_sach)

    assert _so_ma(got) == 3
    assert ngan_sach["con"] == brain._MAX_GOI_Y


def test_khach_doi_anh_thi_gui_lai_du_ma_da_nhac_truoc_do():
    """Ca thật: khách gõ 'cho tôi xem ảnh long đình', AI quên chèn <<ANH>>, mã LD01/LD05 đã
    nhắc hôm trước -> bot trả chữ trơn. Nay luật cứng bắt câu đòi ảnh, gửi lại kể cả mã cũ."""
    for cau in ("cho tôi xem ảnh long đình", "gửi hình đi em", "có ảnh thật không",
                "cho xin thêm ảnh mẫu", "cho xem hình công trình", "gửi em ít ảnh với"):
        assert brain._khach_doi_anh(cau), cau
    # "anh" KHÔNG DẤU là đại từ, dùng liên tục - bắt nhầm là mỗi lượt đều gửi lại ảnh (spam).
    for cau in ("cho anh xin giá mộ đôi", "gửi anh bảng giá", "cho anh hỏi",
                "thêm anh vào zalo", "long đình giá bao nhiêu", "anh cần mộ đôi"):
        assert not brain._khach_doi_anh(cau), cau


def test_dong_thieu_ma_bi_loai_khoi_bang_hang():
    """Dòng dán thiếu mã làm lệch mọi cột -> 'Đơn vị' rơi vào Thể Loại, sinh kind ma 'ngôi'
    mà AI tưởng hợp lệ. Phải bỏ dòng ngay ở cửa đọc file."""
    from bot_tools.find_by_price import _ma_hong, kinds_available

    assert _ma_hong(["Mộ 2 cấp - KT: 870x570x320mm", "", "An Tâm", "Mộ ", "ngôi"])
    assert not _ma_hong(["M01", "Mộ 2 cấp"]) and not _ma_hong(["LD03.2", "Long đình"])
    assert not _ma_hong([]) and not _ma_hong(["", ""]), "dòng trống là bình thường, không phải hỏng"

    kinds = kinds_available()
    assert "ngôi" not in kinds and "Mộ" in kinds, f"thể loại ma lọt vào enum: {kinds}"


def test_khach_hoi_dich_danh_thi_khong_bi_cat():
    """product_ids là mã KHÁCH tự hỏi -> trả đủ, trần 2 chỉ áp cho mẫu bot tự đề xuất."""
    got = brain._run_tool("suggest_products", {"product_ids": ["M01", "M04", "LD03"]})
    assert _so_ma(got) == 3
