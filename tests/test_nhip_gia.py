# -*- coding: utf-8 -*-
"""Luồng giá 2 nhịp: lần 1 xin SĐT (không số), lần 2 mới báo giá + ảnh báo giá."""
import brain


def _hist(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


def test_dem_hoi_gia():
    dem = brain._dem_hoi_gia
    assert dem([], "chào shop") == 0
    assert dem([], "đánh giá dịch vụ tốt") == 0, "'đánh giá' không phải hỏi giá"
    assert dem([], "cho xin giá mộ đơn") == 1
    assert dem([], "mộ đôi bn tiền") == 1
    assert dem([], "lăng mộ chi phí thế nào") == 1
    assert dem(_hist(("user", "giá bao nhiêu")), "ok bác") == 1, "lượt này không hỏi giá"
    assert dem(_hist(("user", "giá bao nhiêu"),
                     ("assistant", "dạ tuỳ công trình ạ")), "báo giá đi em") == 2


def test_nhip_gia_prompt():
    lan1 = brain._gia_prompt([], "cho xin giá", {})
    assert "LẦN ĐẦU" in lan1 and "KHÔNG nêu con số" in lan1

    # đã có SĐT -> bỏ nhịp 1, báo giá luôn
    assert "PHẢI báo giá thật" in brain._gia_prompt([], "cho xin giá", {"sdt": "0912345678"})

    lan2 = brain._gia_prompt(_hist(("user", "giá bao nhiêu")), "báo giá đi em", {})
    assert "PHẢI báo giá thật" in lan2

    assert brain._gia_prompt([], "chào shop", {}) == "", "không hỏi giá -> không chèn khối"
