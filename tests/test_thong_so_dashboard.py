"""Số liệu dashboard: giá token, đếm loại sự kiện, và mặc định hiện trên trang Cấu hình.

Ba lỗi từng có thật (07/09/2026): giá cắm cứng tầng Pro trong khi bot chạy flash (đắt gấp 5),
40% loại sự kiện ghi ra rồi không hiện ở đâu, và bảng mặc định của admin lệch khỏi config.
"""
import admin
import config
import stats


def test_gia_token_theo_dung_model_dang_chay():
    for model, (v, r, c) in config._GIA.items():
        assert config.gia_model(model) == (v, r, c)
    assert config.gia_model("model-la-hoac-preview") == config._GIA_LA
    assert config.gia_model("flash") == config.gia_model("gemini-3.5-flash")   # alias


def test_token_da_cache_tinh_gia_re_hon():
    """1 triệu token input mà 900k đã cache -> phải rẻ hơn hẳn tính full giá input."""
    full = stats._usd(1_000_000, 0, 0)
    co_cache = stats._usd(1_000_000, 0, 900_000)
    assert co_cache < full
    assert co_cache == (100_000 / 1e6 * config.PRICE_IN_USD
                        + 900_000 / 1e6 * config.PRICE_CACHED_IN_USD)


def test_gop_ngay_giu_moi_loai_su_kien():
    """Trước đây chỉ gộp ok/error/handoff/rate_limited -> comment, referral... bốc hơi khi dọn."""
    rows = [{"ts": 1_700_000_000, "kind": k, "psid": "A"}
            for k in ("ok", "comment", "referral", "khach_kho_chiu", "unclear")]
    rows.append({"ts": 1_700_000_000, "kind": "usage", "psid": "A",
                 "tin": 1000, "tout": 100, "tcache": 800})
    ngay = next(iter(stats.gop_theo_ngay(rows).values()))
    assert ngay["comment"] == 1 and ngay["referral"] == 1
    assert ngay["khach_kho_chiu"] == 1 and ngay["unclear"] == 1
    assert ngay["tcache"] == 800


def test_khach_kho_chiu_keo_ti_le_thanh_cong_xuong(monkeypatch):
    """Bot phải tự tắt vì khách bực = ca HỎNG, không được vẫn khoe 100%."""
    import time as _t
    bay_gio = _t.time()          # phải là HÔM NAY: summary() lọc sự kiện theo đúng kỳ đang xem
    rows = ([{"ts": bay_gio, "kind": "ok", "psid": "A"}] * 3
            + [{"ts": bay_gio, "kind": "khach_kho_chiu", "psid": "B"}])
    monkeypatch.setattr(stats, "_read_events", lambda days=30: rows)
    monkeypatch.setattr(stats, "_doc_daily", lambda cutoff: {})
    s = stats.summary(7)
    assert s["counts"]["khach_kho_chiu"] == 1
    assert s["failed"] == 1 and s["success_rate"] == 75.0


def test_admin_khong_giu_ban_mac_dinh_chep_tay():
    """Mọi mặc định trên trang Cấu hình phải lấy từ config - nguồn duy nhất, khỏi lệch dần."""
    for k, v in config.MAC_DINH.items():
        assert admin._MAC_DINH[k] == v
    assert admin._MAC_DINH["BOT_FOLLOWUP_AFTER_H"] == str(config.FOLLOWUP_AFTER_H).rstrip("0").rstrip(".")
    assert float(admin._MAC_DINH["GEMINI_PRICE_IN_USD"]) == config.gia_model()[0]


def test_khong_con_o_nhap_nut_chet():
    """Ô nào hiện trên trang Cấu hình cũng phải có mã đọc tới nó (BOT_PER_PSID_RATE_S đã bỏ)."""
    keys = {f["key"] for f in admin._CONFIG_FIELDS}
    assert "BOT_PER_PSID_RATE_S" not in keys
    assert not hasattr(config, "PER_PSID_RATE_S")
    assert {"BOT_STATS_KEEP_DAYS", "BOT_STATS_PRUNE_H", "BOT_MISSED_CHECK_MIN"} <= keys


def test_khong_bao_trong_luong_0_tan():
    """Ô trọng lượng '0.000' = chưa cân. Đọc cho khách thành '0 tấn' là mất mặt (11 mã dính)."""
    import sys
    sys.path.insert(0, "bot_tools")
    import find_by_price as F
    header, _, data = F.load_rows()
    for r in data:
        spec = F._spec(header, r)
        assert "0.000 tấn" not in spec and not spec.startswith("0 tấn"), r[0]


def _stub_states(monkeypatch, states):
    import fb
    monkeypatch.setattr(fb, "fetch_states", lambda force=False: states)
    monkeypatch.setattr(fb, "list_psids", lambda force=False: list(states))


def test_tong_the_tach_luot_va_khach(monkeypatch):
    """Số theo LƯỢT lấy từ stats, số theo NGƯỜI lấy từ cờ khách - không được lẫn."""
    import fb
    hom_nay = 1_700_000_000.0
    tho = [{"ts": hom_nay, "kind": "handoff", "psid": "A"},
           {"ts": hom_nay, "kind": "handoff", "psid": "A"},   # cùng 1 khách, 2 lượt
           {"ts": hom_nay, "kind": "ok", "psid": "B"}]
    monkeypatch.setattr(stats, "_read_events", lambda days=30: tho)
    monkeypatch.setattr(stats, "_doc_daily", lambda cutoff: {
        "2026-01-01": {"date": "2026-01-01", "ok": 10, "handoff": 3, "tin": 0, "tout": 0}})
    _stub_states(monkeypatch, {
        "A": {"handoff_lan": 2, "crm_done": True},
        "B": {},
        "C": {"no_contact": True, "stopped": True},
        "D": {"stopped": True},
    })
    stats._TONG_CACHE = None
    t = stats.tong_the()

    assert t["luot_handoff"] == 5 and t["luot_ok"] == 11      # 2 thô + 3 gộp / 1 thô + 10 gộp
    assert t["khach_tung_handoff"] == 1                       # A đi 2 lượt vẫn là 1 người
    assert t["khach_tong"] == 4 and t["khach_da_chot_crm"] == 1
    assert t["khach_kho_chiu"] == 1                           # C
    assert t["khach_bot_ngung_nhieu"] == 1                    # D (C đã tính ở ô khó chịu)
    stats._TONG_CACHE = None


def test_tong_the_khong_dem_doi_ngay_con_su_kien_tho(monkeypatch):
    """Ngày vừa có sự kiện thô vừa có bản gộp -> chỉ tính thô, y như summary()."""
    import fb
    ts = 1_700_000_000.0
    ngay = stats._mocc_ngay(ts)
    monkeypatch.setattr(stats, "_read_events", lambda days=30: [{"ts": ts, "kind": "ok", "psid": "A"}])
    monkeypatch.setattr(stats, "_doc_daily", lambda cutoff: {
        ngay: {"date": ngay, "ok": 99, "handoff": 99, "tin": 0, "tout": 0}})
    _stub_states(monkeypatch, {"A": {}})
    stats._TONG_CACHE = None
    t = stats.tong_the()
    assert t["luot_ok"] == 1 and t["luot_handoff"] == 0
    stats._TONG_CACHE = None


def test_danh_dau_handoff_cong_don_tren_khach(tmp_path, monkeypatch):
    import state
    monkeypatch.setattr(state, "_DIR", tmp_path)
    monkeypatch.setattr(state.fb, "merge_state", lambda psid, patch: None)
    monkeypatch.setattr(state.fb, "fetch_state", lambda psid: None)
    state.danh_dau_handoff("X")
    state.danh_dau_handoff("X", kho_chiu=True)
    st = state.get("X")
    assert st["handoff_lan"] == 2 and st["kho_chiu_lan"] == 1 and st["handoff_luc"] > 0


# --- Lọc theo tháng ---

def test_ngay_trong_thang_dung_o_hom_nay():
    """Tháng đang chạy không được kéo tới cuối tháng: biểu đồ sẽ có cột của ngày CHƯA tới."""
    from datetime import datetime
    hom_nay = datetime.now().date()
    ngay = stats._ngay_trong_thang(hom_nay.strftime("%Y-%m"))
    assert ngay[-1] == hom_nay.isoformat()
    assert len(stats._ngay_trong_thang("2020-02")) == 29        # tháng cũ -> đủ cả tháng (nhuận)
    assert stats._ngay_trong_thang("2999-01") == []             # tháng tương lai -> rỗng


def test_summary_theo_thang_chi_lay_ngay_trong_thang(monkeypatch):
    rollup = {"2026-07-31": {"date": "2026-07-31", "ok": 5, "tin": 100, "tout": 10},
              "2026-08-01": {"date": "2026-08-01", "ok": 7, "tin": 200, "tout": 20},
              "2026-08-31": {"date": "2026-08-31", "ok": 3, "tin": 300, "tout": 30},
              "2026-09-01": {"date": "2026-09-01", "ok": 9, "tin": 400, "tout": 40}}
    monkeypatch.setattr(stats, "_read_events", lambda days=30: [])
    monkeypatch.setattr(stats, "_doc_daily", lambda cutoff: rollup)

    s = stats.summary(thang="2026-08")

    assert s["thang"] == "2026-08" and s["counts"]["ok"] == 10      # 7 + 3, không dính 31/07, 01/09
    assert s["tokens_in"] == 500
    assert len(s["daily"]) == 31 and s["daily"][0]["date"] == "2026-08-01"


def test_thang_co_du_lieu_moi_nhat_truoc(monkeypatch):
    monkeypatch.setattr(stats, "_doc_daily", lambda cutoff: {"2026-07-30": {}, "2026-08-02": {}})
    monkeypatch.setattr(stats, "_read_events", lambda days=30: [{"ts": 1_800_000_000, "kind": "ok"}])
    co = stats.thang_co_du_lieu()
    assert co[:2] == sorted(co, reverse=True)[:2] and "2026-08" in co and "2026-07" in co


def test_thang_den_tat_ca_bo_khach_chua_co_moc(monkeypatch):
    import state
    import time as _t
    moc = _t.mktime((2026, 8, 15, 10, 0, 0, 0, 0, -1))
    monkeypatch.setattr(state.fb, "fetch_states", lambda force=False: {
        "A": {"den_luc": moc}, "B": {}, "C": {"den_luc": 0}})
    m = state.thang_den_tat_ca()
    assert m == {"A": "2026-08"}          # B, C chưa có mốc -> không xếp vào tháng nào


def test_api_chan_thang_sai_dinh_dang():
    import re
    assert admin._THANG_RE.match("2026-08")
    assert not admin._THANG_RE.match("2026-13")
    assert not admin._THANG_RE.match("2026-8")
    assert not admin._THANG_RE.match("' or 1=1")
