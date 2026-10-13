# bot tools

Hàm tra cứu cho bot. API gọi qua function-calling (khai báo trong `brain.py`), chạy ngay
trong process python - KHÔNG mở shell, an toàn với bot khách.

## Tool sẵn có

`sync_bang_hang.py` - kéo bảng "3.4 Sản phẩm" từ Lark Base về `Danh_Muc_San_Pham.csv`.
Chạy lại mỗi khi nhân viên sửa bảng trên Base:

```
python bot_tools/sync_bang_hang.py --dry-run   # xem số mã, chưa ghi
python bot_tools/sync_bang_hang.py             # ghi đè CSV
```

Mã chưa gắn giá đá nào VẪN vào bảng dạng "thiết kế riêng": bot chào mẫu kèm ảnh, nói là hàng
làm riêng theo công trình rồi mời khách để lại SĐT cho chuyên gia báo giá. Khách nêu trần giá
thì nhóm này không trộn vào kết quả, trừ khi lọc xong không còn mẫu nào.
Tên sản phẩm dựng từ thể loại + đặc điểm (dáng mộ, số mái, số cột cổng) vì Base không có
cột tên.

`find_by_price.py` - tra sản phẩm (đọc `Danh_Muc_San_Pham.csv`, lọc theo tầm giá + loại đá +
danh mục + thể loại + từ khoá tên, sort giá tăng dần).

Cột đọc theo TÊN header, không theo số thứ tự - chèn/đổi cột trong CSV không làm lệch.
Thể loại lấy từ cột `Thể Loại` của CSV, thêm loại mới vào bảng là bot dùng được ngay.

Chạy tay để kiểm tra:
```
python bot_tools/find_by_price.py --max 100tr
python bot_tools/find_by_price.py --min 100tr --max 200tr --stone "xanh rêu"
python bot_tools/find_by_price.py --kind "Long đình" --max 150tr
python bot_tools/find_by_price.py --q "mộ tròn"
python bot_tools/find_by_price.py --selftest
```

## Thêm tool mới

1. Viết hàm python ở đây (trả về string cho AI đọc).
2. Khai báo schema + map tên trong `brain.py` (`_TOOLS`, `_run_tool`).
Không cần mở Bash/shell.
