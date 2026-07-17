# bot tools

Hàm tra cứu cho bot. API gọi qua function-calling (khai báo trong `brain.py`), chạy ngay
trong process python - KHÔNG mở shell, an toàn với bot khách.

## Tool sẵn có

`find_by_price.py` - tra sản phẩm theo tầm giá (đọc `Danh_Muc_San_Pham.csv`, lọc theo khoảng
giá + loại đá + danh mục, sort tăng dần).

Chạy tay để kiểm tra:
```
python bot_tools/find_by_price.py --max 100tr
python bot_tools/find_by_price.py --min 100tr --max 200tr --stone "xanh rêu"
python bot_tools/find_by_price.py --max 150tr --category "Trường Tồn" --limit 20
python bot_tools/find_by_price.py --selftest
```

## Thêm tool mới

1. Viết hàm python ở đây (trả về string cho AI đọc).
2. Khai báo schema + map tên trong `brain.py` (`_TOOLS`, `_run_tool`).
Không cần mở Bash/shell.
