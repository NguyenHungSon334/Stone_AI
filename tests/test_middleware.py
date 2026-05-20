"""
Unit tests for the sliding-window rate limiter.
"""
import time
import pytest
from app.middleware import _UserRateLimiter


def test_allows_calls_up_to_max():
    rl = _UserRateLimiter(max_calls=3, window_seconds=60)
    assert rl.allow("u1") is True
    assert rl.allow("u1") is True
    assert rl.allow("u1") is True


def test_blocks_after_max():
    rl = _UserRateLimiter(max_calls=3, window_seconds=60)
    rl.allow("u1")
    rl.allow("u1")
    rl.allow("u1")
    assert rl.allow("u1") is False


def test_different_users_are_independent():
    rl = _UserRateLimiter(max_calls=1, window_seconds=60)
    assert rl.allow("u1") is True
    assert rl.allow("u1") is False
    assert rl.allow("u2") is True   # u2 unaffected by u1


def test_window_expiry_allows_new_calls():
    rl = _UserRateLimiter(max_calls=1, window_seconds=1)
    assert rl.allow("u1") is True
    assert rl.allow("u1") is False
    time.sleep(1.05)
    assert rl.allow("u1") is True   # window expired


def test_max_calls_one():
    rl = _UserRateLimiter(max_calls=1, window_seconds=60)
    assert rl.allow("x") is True
    assert rl.allow("x") is False


def test_empty_key_allowed():
    rl = _UserRateLimiter(max_calls=2, window_seconds=60)
    assert rl.allow("") is True
    assert rl.allow("") is True
    assert rl.allow("") is False
