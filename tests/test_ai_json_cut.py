import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain


def _resp(raw: str, finish: str = "MAX_TOKENS"):
    part = SimpleNamespace(text=raw)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason=finish)
    return SimpleNamespace(candidates=[cand])


def _fake_gen(traloi: list, ghi: list):
    def gen(client, **kw):
        ghi.append(kw)
        return _resp(traloi[len(ghi) - 1])
    return gen


def test_json_cut_thu_lai_lan_2_thi_qua(monkeypatch):
    ghi = []
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate",
                        _fake_gen(['{"ten":"Tuan","sdt":"091234', '{"ten":"Tuan","sdt":"0912345678"}'], ghi))

    assert brain._sinh_json("p", {}, 512) == {"ten": "Tuan", "sdt": "0912345678"}
    assert len(ghi) == 2


def test_cut_ca_2_lan_thi_nem_brainerror_kem_finish_reason(monkeypatch):
    ghi = []
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate", _fake_gen(['{"ten":"Tu', '{"ten":"Tu'], ghi))

    with pytest.raises(brain.BrainError) as e:
        brain._sinh_json("p", {}, 512)
    assert "MAX_TOKENS" in str(e.value)
    assert len(ghi) == 2


def test_tom_tat_lead_cut_thi_tra_rong_khong_lam_mat_lead(monkeypatch):
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate", _fake_gen(['{"ten":"Tu'] * 2, []))
    monkeypatch.setattr(brain.alerts, "alert", lambda *a, **k: None)

    assert brain._tom_tat_lead("psid-1", [{"role": "user", "content": "xin chao"}]) == {}
