"""Self-check phần thuần logic: chữ ký HMAC (security) + bóc sự kiện. Chạy: python tests/test_messenger.py"""
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MSGR_APP_SECRET"] = "s3cret"
os.environ["MSGR_VERIFY_TOKEN"] = "vtok"

import config
config.APP_SECRET = "s3cret"        # config đã load trước khi set env ở trên nên gán lại
config.VERIFY_TOKEN = "vtok"
import messenger


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()


def test_signature():
    body = b'{"object":"page"}'
    assert messenger.verify_signature(body, _sig(body)) is True
    assert messenger.verify_signature(body, _sig(b"tampered")) is False   # sai chữ ký
    assert messenger.verify_signature(body, "") is False                   # thiếu header
    assert messenger.verify_signature(body, "sha256=deadbeef") is False    # rác


def test_verify_webhook():
    assert messenger.verify_webhook("subscribe", "vtok", "42") == "42"
    assert messenger.verify_webhook("subscribe", "wrong", "42") is None


def test_parse_events():
    payload = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": "123"}, "message": {"text": "hi"}},
        {"sender": {"id": "123"}, "message": {"text": "echo", "is_echo": True}},
        {"sender": {"id": "9"}, "message": {}},
    ]}]}
    assert messenger.parse_events(payload) == [("123", "hi")]
    assert messenger.parse_events({"object": "user"}) == []


if __name__ == "__main__":
    test_signature()
    test_verify_webhook()
    test_parse_events()
    print("OK - all messenger self-checks passed")
