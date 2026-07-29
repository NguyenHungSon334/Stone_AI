"""Dọn stats: giữ N ngày gần nhất, KHÔNG đụng dữ liệu khách."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain
import fb
import stats


def _ghi_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_prune_giu_dung_cua_so_2_ngay(monkeypatch, tmp_path):
    now = time.time()
    ev = tmp_path / "events.jsonl"
    _ghi_events(ev, [
        {"ts": now - 5 * 86400, "kind": "ok", "psid": "cu_5_ngay"},
        {"ts": now - 3 * 86400, "kind": "ok", "psid": "cu_3_ngay"},
        {"ts": now - 1 * 86400, "kind": "ok", "psid": "moi_1_ngay"},
        {"ts": now - 60, "kind": "ok", "psid": "vua_xong"},
    ])
    monkeypatch.setattr(stats, "_EVENTS", ev)
    monkeypatch.setattr(fb, "prune_events", lambda cutoff: 0)

    kq = stats.prune(keep_days=2)

    assert kq["local"] == 2
    con = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines()]
    assert [r["psid"] for r in con] == ["moi_1_ngay", "vua_xong"]


def test_prune_khong_dung_du_lieu_khach(monkeypatch, tmp_path):
    """Chốt chặn: dọn stats không được xoá/sửa bất cứ gì trong conversations/."""
    hist = tmp_path / "conversations"
    hist.mkdir()
    (hist / "PSID_KHACH.json").write_text('[{"role":"user","content":"xin gia"}]', encoding="utf-8")
    (hist / "PSID_KHACH.crm.json").write_text('{"lead_code":"L1"}', encoding="utf-8")
    (hist / "PSID_KHACH.noise.state").write_text('{"stopped": true}', encoding="utf-8")
    truoc = {p.name: p.read_bytes() for p in hist.iterdir()}

    ev = tmp_path / "events.jsonl"
    _ghi_events(ev, [{"ts": time.time() - 9 * 86400, "kind": "ok", "psid": "PSID_KHACH"}])
    monkeypatch.setattr(brain, "_HIST_DIR", hist)
    monkeypatch.setattr(stats, "_EVENTS", ev)
    monkeypatch.setattr(fb, "prune_events", lambda cutoff: 0)

    stats.prune(keep_days=2)

    assert {p.name: p.read_bytes() for p in hist.iterdir()} == truoc
    assert ev.read_text(encoding="utf-8").strip() == ""


def test_prune_events_chi_dung_node_stats(monkeypatch):
    """fb.prune_events chỉ được mở ref tới stats/events, không nhánh nào khác."""
    da_mo, da_xoa = [], []

    class _Ref:
        def __init__(self, path):
            da_mo.append(path)

        def order_by_child(self, _k):
            return self

        def end_at(self, _v):
            return self

        def get(self):
            now = time.time()
            return {"k_cu": {"ts": now - 9 * 86400}, "k_moi": {"ts": now}}

        def update(self, patch):
            da_xoa.extend(patch)

    monkeypatch.setattr(fb, "_init", lambda: True)
    fake_db = type("db", (), {"reference": staticmethod(_Ref)})
    monkeypatch.setitem(sys.modules, "firebase_admin", type("m", (), {"db": fake_db}))

    n = fb.prune_events(time.time() - 2 * 86400)

    assert n == 1 and da_xoa == ["k_cu"]
    assert da_mo == ["stats/events"]
