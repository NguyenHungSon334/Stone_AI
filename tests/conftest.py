"""Chặn MỌI đường ra ngoài khi chạy test.

test_referral.py từng mock send_text/answer nhưng quên notify_admins -> mỗi lần chạy
pytest là admin nhận 2 tin Lark "👋 KHÁCH MỚI: psid PSID_CU" và Firebase prod nhận rác
stats với psid giả. deploy.sh chạy test ngay trước khi deploy nên mỗi lần deploy cũng bắn.

Vá ở đây thay vì vá từng test: test mới quên mock cũng không rò được nữa.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import fb


@pytest.fixture(autouse=True)
def _chan_ra_ngoai(monkeypatch):
    # Lark: cổng duy nhất mọi cảnh báo/thông báo nghiệp vụ đi ra.
    monkeypatch.setattr(alerts, "post_lark", lambda text: (True, "test: khong gui"))
    # Firebase: _init là CỬA DUY NHẤT - mọi hàm trong fb.py đều gọi nó trước khi đụng mạng,
    # trả False là tất cả thành no-op. Chặn ở đây thay vì liệt kê từng hàm: liệt kê thì thêm
    # hàm ghi mới là quên, và đã quên thật một lần (save_daily ghi đồng bộ, không qua _run,
    # lọt qua bản chặn cũ rồi đẩy 3 bản ghi rác lên stats/daily của prod).
    monkeypatch.setattr(fb, "_init", lambda: False)
    monkeypatch.setattr(fb, "_run", lambda fn: None)
