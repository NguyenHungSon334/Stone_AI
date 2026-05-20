import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

client = create_client(os.environ["SUPABASE_URL"], os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"])
result = client.table("products").select("ma_sp, ten_sp, kich_thuoc, chieu_dai_mm, chieu_rong_mm, chieu_cao_mm").execute()
products = result.data

total   = len(products)
full3d  = sum(1 for p in products if p.get("chieu_dai_mm") and p.get("chieu_rong_mm") and p.get("chieu_cao_mm"))
partial = sum(1 for p in products if any([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]) and not all([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]))
no_dim  = sum(1 for p in products if not any([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]))

print(f"Total: {total}  |  Full 3D: {full3d}  |  Partial: {partial}  |  No dims: {no_dim}")
print()
print("=== No dims at all ===")
for p in products:
    if not any([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]):
        print(f"  {p['ma_sp']} | {p['ten_sp'][:50]}")
        print(f"    KT: {str(p.get('kich_thuoc',''))[:60]}")

print()
print("=== Partial dims ===")
for p in products:
    if any([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]) and not all([p.get("chieu_dai_mm"), p.get("chieu_rong_mm"), p.get("chieu_cao_mm")]):
        print(f"  {p['ma_sp']} | dai={p.get('chieu_dai_mm')} rong={p.get('chieu_rong_mm')} cao={p.get('chieu_cao_mm')}")
