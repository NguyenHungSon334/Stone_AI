"""Điền mốc ĐẾN LẦN ĐẦU (`den_luc`) cho khách cũ - chạy MỘT LẦN sau khi cập nhật bot.

Khách mới thì messenger tự ghi (state.ghi_den_luc). Khách có từ trước thì mốc phải dò lại từ
tin ĐẦU TIÊN trong lịch sử chat; không có mốc thì bộ lọc "khách theo tháng" trên dashboard xếp
họ vào nhóm "chưa rõ".

    python bot_tools/backfill_den_luc.py            # chạy thật
    python bot_tools/backfill_den_luc.py --thu      # chỉ xem, không ghi

Chạy lại bao nhiêu lần cũng được: khách đã có mốc thì bỏ qua.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain      # noqa: E402
import fb         # noqa: E402
import state      # noqa: E402


def _moc_dau(msgs: list) -> float:
    """Thời điểm tin đầu tiên có ghi giờ. 0 = cả log không tin nào có trường 'at'."""
    for m in msgs:
        at = str(m.get("at") or "").strip()
        if not at:
            continue
        try:
            return datetime.fromisoformat(at).timestamp()
        except ValueError:
            continue
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thu", action="store_true", help="chỉ in ra, không ghi")
    args = ap.parse_args()

    psids = fb.list_psids()
    if psids is None:
        print("Firebase tắt/lỗi -> chỉ chạy được trên khách có cache local.", file=sys.stderr)
        psids = sorted({p.stem for p in (brain._HIST_DIR).glob("*.json")
                        if not p.name.endswith(brain.SIDECAR_SUFFIXES)})
    xong = bo_qua = khong_ro = 0
    for i, psid in enumerate(psids, 1):
        if state.get(psid).get("den_luc"):
            bo_qua += 1
            continue
        ts = _moc_dau(brain.load_history(psid) or [])
        if not ts:
            khong_ro += 1
            continue
        if not args.thu:
            state.ghi_den_luc(psid, ts)
        xong += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(psids)}", file=sys.stderr)
    print(f"Đã ghi {xong} khách, bỏ qua {bo_qua} (đã có mốc), {khong_ro} không dò được "
          f"(log cũ không ghi giờ). Tổng {len(psids)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
