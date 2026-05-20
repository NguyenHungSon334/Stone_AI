"""
Patch missing dimensions in DB without regenerating embeddings.

Reads all products from DB, re-parses dims from kich_thuoc + ten_sp,
updates ONLY dim columns (chieu_dai_mm, chieu_rong_mm, chieu_cao_mm).

Usage:
  cd c:\Project\SpiritStone
  python -m scripts.patch_dims
"""
from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


# ---------------------------------------------------------------------------
# Comprehensive dim parser — covers all formats found in the data
# ---------------------------------------------------------------------------

_MIN_DIM = 50       # mm — nothing smaller than 5cm
_MAX_DIM = 15_000   # mm — nothing larger than 15m (sanity cap for typos)


def _sane(v: int) -> int | None:
    return v if _MIN_DIM <= v <= _MAX_DIM else None


def _3d_from_kt(kt: str) -> dict[str, int]:
    """Parse D{n}xR{n}xC{n} format from kich_thuoc (structured format)."""
    m = re.search(r"[Dd]\s*(\d+)\s*[xX]\s*[Rr]\s*(\d+)\s*[xX]\s*[Cc]\s*(\d+)", kt)
    if not m:
        return {}
    d, r, c = _sane(int(m.group(1))), _sane(int(m.group(2))), _sane(int(m.group(3)))
    result = {}
    if d: result["chieu_dai_mm"]  = d
    if r: result["chieu_rong_mm"] = r
    if c: result["chieu_cao_mm"]  = c
    return result


def _3d_from_name(name: str) -> dict[str, int]:
    """Parse KT: NxNxN format from ten_sp (bare numbers, legacy format)."""
    m = re.search(r"KT[:\s]+(\d+)\s*x\s*(\d+)\s*x\s*(\d+)", name, re.IGNORECASE)
    if not m:
        return {}
    d, r, c = _sane(int(m.group(1))), _sane(int(m.group(2))), _sane(int(m.group(3)))
    result = {}
    if d: result["chieu_dai_mm"]  = d
    if r: result["chieu_rong_mm"] = r
    if c: result["chieu_cao_mm"]  = c
    return result


def parse_dims(ten_sp: str, kich_thuoc: str) -> dict[str, int]:
    """
    Returns a dict with any of: chieu_dai_mm, chieu_rong_mm, chieu_cao_mm.
    Strategy: parse kich_thuoc AND ten_sp independently, then merge.
    Per-field: prefer kich_thuoc value if sane, else use ten_sp value.
    Sanity cap: 50mm ≤ value ≤ 15000mm (rejects clear typos like D27000).
    """
    dims: dict[str, int] = {}
    kt   = (kich_thuoc or "").lower()
    name = (ten_sp or "")

    # ── Try structured D×R×C from both sources, merge per-field ──
    from_kt   = _3d_from_kt(kt)
    from_name = _3d_from_name(name)

    if from_kt or from_name:
        for key in ("chieu_dai_mm", "chieu_rong_mm", "chieu_cao_mm"):
            # kich_thuoc preferred when sane; fall back to ten_sp
            v = from_kt.get(key) or from_name.get(key)
            if v:
                dims[key] = v
        if len(dims) == 3:
            return dims   # full 3D match → done

    # ── Priority 3: DK{n}xC{n} — mộ tròn circular (đường kính × cao) ──
    m = re.search(r"[Dd][Kk]\s*(\d+)\s*[xX]\s*[Cc]\s*(\d+)", kt)
    if m:
        dims["chieu_rong_mm"] = int(m.group(1))   # diameter → rong
        dims["chieu_cao_mm"]  = int(m.group(2))
        # fall through to also pick up chiều dài tổng if available

    # ── Priority 4: {n}x{n}x{n}mm bare (existing fallback from kich_thuoc) ──
    if len(dims) < 3:
        for m in re.finditer(r":\s*(\d+)\s*[xX]\s*(\d+)\s*[xX]\s*(\d+)\s*mm", kt):
            if "chieu_dai_mm" not in dims:
                dims["chieu_dai_mm"]  = int(m.group(1))
                dims["chieu_rong_mm"] = int(m.group(2))
                dims["chieu_cao_mm"]  = int(m.group(3))
            break

    # ── Chiều dài tổng / dài ──
    if "chieu_dai_mm" not in dims:
        for pat in [
            r"chiều\s+dài\s+tổng[:\s]+(\d+)\s*mm",
            r"chiều\s+dài[:\s]+(\d+)\s*mm",
        ]:
            m = re.search(pat, kt)
            if m:
                dims["chieu_dai_mm"] = int(m.group(1))
                break

    # ── Chiều cao tổng / cao / cột chính ──
    if "chieu_cao_mm" not in dims:
        for pat in [
            r"chiều\s+cao\s+tổng[:\s]+(\d+)\s*mm",
            r"chiều\s+cao\s+cột\s+chính[:\s]+(\d+)\s*mm",
            r"chiều\s+cao[:\s]+(\d+)\s*mm",
        ]:
            m = re.search(pat, kt)
            if m:
                dims["chieu_cao_mm"] = int(m.group(1))
                break

    # ── Chiều rộng ──
    if "chieu_rong_mm" not in dims:
        m = re.search(r"chiều\s+rộng[:\s]+(\d+)\s*mm", kt)
        if m:
            dims["chieu_rong_mm"] = int(m.group(1))

    # ── Hộp thờ → chieu_rong for Long đình ──
    if "chieu_rong_mm" not in dims:
        m = re.search(r"hộp\s+thờ[:\s]+(\d+)\s*mm", kt)
        if m:
            dims["chieu_rong_mm"] = int(m.group(1))

    # ── D{n}xC{n} 2D (Tam Sơn etc.) — only if nothing else matched yet ──
    if "chieu_dai_mm" not in dims and "chieu_cao_mm" not in dims:
        m = re.search(r"(?<![Kk])[Dd]\s*(\d+)\s*[xX]\s*[Cc]\s*(\d+)", kt)
        if m:
            dims["chieu_dai_mm"] = int(m.group(1))
            dims["chieu_cao_mm"] = int(m.group(2))

    # ── Bare "NxN mm" 2D (TS08: KT: 3400x2020mm) ──
    if "chieu_dai_mm" not in dims:
        m = re.search(r"\bkt[:\s]+(\d+)\s*[xX]\s*(\d+)\s*mm", kt)
        if m:
            dims["chieu_dai_mm"] = int(m.group(1))
            dims["chieu_cao_mm"] = int(m.group(2))

    # ── Single bare diameter "KT: 1070mm" (MT08, MT09) ──
    if not dims:
        m = re.search(r"\bkt[:\s]+(\d{3,4})\s*mm", kt)
        if m:
            dims["chieu_rong_mm"] = int(m.group(1))

    # ── Cuốn thư: extract width from name "Cuốn thư 1370" / "CUỐN THƯ 1370" ──
    if not dims:
        m = re.search(r"cuốn\s+thư\D+(\d{3,4})", name, re.IGNORECASE)
        if m:
            dims["chieu_rong_mm"] = int(m.group(1))

    return dims


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    client = create_client(url, key)

    print("Fetching all products from DB...")
    result = client.table("products").select(
        "id, ma_sp, ten_sp, kich_thuoc, chieu_dai_mm, chieu_rong_mm, chieu_cao_mm"
    ).execute()
    products = result.data
    print(f"  {len(products)} products found")

    updated = skipped = errors = 0
    no_dims = 0

    for p in products:
        ma_sp      = p["ma_sp"]
        ten_sp     = p.get("ten_sp") or ""
        kich_thuoc = p.get("kich_thuoc") or ""
        old = {
            "chieu_dai_mm":  p.get("chieu_dai_mm"),
            "chieu_rong_mm": p.get("chieu_rong_mm"),
            "chieu_cao_mm":  p.get("chieu_cao_mm"),
        }

        dims = parse_dims(ten_sp, kich_thuoc)

        if not dims:
            no_dims += 1
            continue

        # Only update if something changed
        patch = {k: v for k, v in dims.items() if old.get(k) != v}
        if not patch:
            skipped += 1
            continue

        try:
            client.table("products").update(patch).eq("ma_sp", ma_sp).execute()
            updated += 1
            old_vals = {k: old[k] for k in patch}
            print(f"  Updated {ma_sp}: {old_vals} → {patch}")
        except Exception as e:
            print(f"  [ERR] {ma_sp}: {e}")
            errors += 1

    print(f"\nDone: {updated} updated, {skipped} already correct, {no_dims} no parseable dims, {errors} errors")


if __name__ == "__main__":
    main()
