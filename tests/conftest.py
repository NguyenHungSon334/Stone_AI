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
    # Firebase: _run bọc mọi thao tác ghi (conversations + stats) trong thread nền.
    monkeypatch.setattr(fb, "_run", lambda fn: None)
    # Đọc cũng chặn: test không được phụ thuộc dữ liệu khách thật trên prod.
    monkeypatch.setattr(fb, "fetch_conversation", lambda psid: None)
    monkeypatch.setattr(fb, "list_psids", lambda: None)
