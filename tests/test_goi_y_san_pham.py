"""Tool gợi ý sản phẩm: trần số mẫu/lượt và thứ tự ưu tiên (bán chạy > có ảnh > giá)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
from bot_tools.find_by_price import (_cell, _col, kinds_available, load_rows,
                                     parse_money, render, rows_by_ids, search)


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
    """Mẫu bán chạy phải đứng TRƯỚC - sort thuần theo giá thì mẫu dễ chốt rơi mất.

    Không neo vào mã cụ thể: số mẫu gắn nhãn bán chạy đổi mỗi lần sync bảng hàng từ Base."""
    header, _, _ = load_rows()
    i_bc, i_anh = _col(header, "Bán chạy"), _col(header, "Có ảnh")
    kind = next(k for k in kinds_available()
                if any(_cell(r, i_bc) for _, _, r in search(None, kind=k, limit=500)))
    top = search(None, kind=kind, limit=500)

    so_bc = [i for i, (_, _, r) in enumerate(top) if _cell(r, i_bc)]
    assert so_bc == list(range(len(so_bc))), f"mẫu bán chạy không nằm đầu: {kind} {so_bc}"
    for _, _, r in top:
        assert _cell(r, i_anh).strip().upper() != "FALSE", f"mẫu {r[0]} không có ảnh"


def test_goi_tool_nhieu_vong_trong_1_luot_van_chi_duoc_2_mau():
    """Ca thật đã lọt lên prod: AI gọi tool 2 vòng trong CÙNG một lượt rồi gộp -> khách hỏi
    'các mẫu Long đình' nhận 3 mẫu. Ngân sách đếm theo LƯỢT, không theo từng lần gọi."""
    ngan_sach = {}
    vong1 = brain._run_tool("suggest_products", {"kind": "Long đình"}, ngan_sach)
    vong2 = brain._run_tool("suggest_products", {"q": "long đình"}, ngan_sach)

    assert _so_ma(vong1) == 2
    assert _so_ma(vong2) == 0, "vòng 2 cùng lượt không được thêm mẫu nào"
    assert "ĐỦ RỒI" in vong2, "phải nói rõ là hết ngân sách, không phải hết hàng"


def test_luot_moi_thi_ngan_sach_reset():
    """Khách nói 'xem thêm' ở lượt sau -> lại được 2 mẫu, không bị khoá vĩnh viễn."""
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ"}, {})) == 2
    assert _so_ma(brain._run_tool("suggest_products", {"kind": "Mộ"}, {})) == 2


def test_ma_khach_hoi_dich_danh_khong_bi_tru_ngan_sach():
    """product_ids = mã khách chỉ tên -> trả đủ, và không ăn vào ngân sách mẫu tự đề xuất."""
    ngan_sach = {}
    got = brain._run_tool("suggest_products", {"product_ids": ["M01", "M04", "LĐ03"]}, ngan_sach)

    assert _so_ma(got) == 3
    assert ngan_sach == {}


def test_khach_doi_anh_thi_gui_lai_du_ma_da_nhac_truoc_do():
    """Ca thật: khách gõ 'cho tôi xem ảnh long đình', AI quên chèn <<ANH>>, mã LĐ01/LĐ05 đã
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
    assert not _ma_hong(["M01", "Mộ 2 cấp"]) and not _ma_hong(["LĐ03.2", "Long đình"])
    assert not _ma_hong([]) and not _ma_hong(["", ""]), "dòng trống là bình thường, không phải hỏng"

    kinds = kinds_available()
    assert "ngôi" not in kinds and "Mộ" in kinds, f"thể loại ma lọt vào enum: {kinds}"


def test_khach_hoi_dich_danh_thi_khong_bi_cat():
    """product_ids là mã KHÁCH tự hỏi -> trả đủ, trần 2 chỉ áp cho mẫu bot tự đề xuất."""
    got = brain._run_tool("suggest_products", {"product_ids": ["M01", "M04", "LĐ03"]})
    assert _so_ma(got) == 3


def test_hang_thiet_ke_rieng_van_duoc_chao():
    """Mã chưa gắn giá (bát hương, lư hương, đèn đá...) vẫn phải ra mẫu kèm lời mời xin SĐT.

    Giấu luôn nhóm này là khách hỏi "có bát hương không" nghe bot báo hết hàng rồi bỏ đi."""
    header, stone_cols, data = load_rows()
    rieng = [r for r in data if not any(parse_money(r[i]) > 0 for i in stone_cols if i < len(r))]
    assert rieng, "bảng hàng không còn mã thiết kế riêng nào - test mất ý nghĩa"
    ma = rieng[0][0].strip()

    out = render(rows_by_ids([ma]))
    assert "THIẾT KẾ RIÊNG" in out and "SĐT" in out, out
    assert "0tr" not in out and "0đ" not in out, f"giá trống lọt ra dạng số: {out}"

    kind = _cell(rieng[0], _col(header, "Thể Loại"))
    assert [r[0].strip() for _, _, r in search(None, kind=kind)], "lọc theo thể loại phải ra mẫu"


def test_thiet_ke_rieng_khong_tron_vao_ngan_sach_khach_neu():
    """Khách nêu trần giá -> mẫu chưa có giá KHÔNG được xếp chung với mẫu nằm trong tầm giá
    (không biết giá thì không được hứa nó vừa túi khách). Chỉ khi lọc trắng tay mới đưa ra."""
    header, stone_cols, data = load_rows()
    co_gia = [r for r in data if any(parse_money(r[i]) > 0 for i in stone_cols if i < len(r))]
    kind = _cell(co_gia[0], _col(header, "Thể Loại"))

    got = search(1e9, kind=kind, limit=500)
    assert got and all(p > 0 for p, _, _ in got), f"mẫu chưa có giá lọt vào tầm giá: {kind}"


def test_nhieu_hang_muc_1_luot_moi_hang_muc_deu_co_mau():
    """Ca thật khách Trần Long 28/09: hỏi báo giá cả khu -> 2 long đình ăn hết suất chung, tra
    hàng rào ra rỗng, bot tự bịa HR01 1.8tr/md (thật 4tr). Suất phải tính RIÊNG từng thể loại."""
    ngan_sach = {}
    long_dinh = brain._run_tool("suggest_products", {"kind": "Long đình"}, ngan_sach)
    hang_rao = brain._run_tool("suggest_products", {"kind": "Hàng rào"}, ngan_sach)

    assert _so_ma(long_dinh) == 2
    assert _so_ma(hang_rao) == 2, "hàng rào phải có giá thật, không bị long đình ăn mất suất"
