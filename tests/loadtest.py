"""Load test END-TO-END THẬT: đẩy nhiều đoạn chat ĐỒNG THỜI qua webhook của app đang chạy.

Khác loadtest cũ (gọi thẳng brain.answer): bản này ký payload như FB rồi POST vào
/webhook/messenger -> đi đủ pipeline: verify chữ ký -> parse -> debounce -> brain/forced-handoff
-> gửi FB + báo Lark. Sau đó verify qua dashboard API (khách có mặt, bot đã trả lời).

Chạy: (1) mở app trước: python app.py   (2) rồi: python tests/loadtest.py
psid prefix __loadtest_ -> dễ nhận & dọn sau.
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows console cp1252 -> in được tiếng Việt
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

BASE = f"http://localhost:{config.PORT}"
TOKEN = config.DASH_TOKEN
_DEBOUNCE_S = 4.0            # khớp messenger._DEBOUNCE_S
MSG_GAP = _DEBOUNCE_S + 2.0  # > debounce -> mỗi tin trong 1 chat = 1 lượt xử riêng
POLL_EVERY_S = 5            # nhịp poll dashboard chờ xử xong
POLL_MIN_S = 60            # sàn chờ: 20 chat đồng thời -> Gemini xếp hàng, plateau sớm là GIẢ (chưa giao)
POLL_STABLE_S = 30         # reply đứng yên bao lâu thì coi xong (dài -> không dính pause giữa lúc leo)
POLL_MAX_S = 300           # trần chờ

# --- 6 chat THẬT (mỗi phần tử = 1 tin; nhiều dòng nối \n) ---
REAL = {
    "real1": ["Cho mình xin địa chỉ,\nGửi lại cho mình xin cái clip này nhé Đt 0915253399"],
    "real2": ["mẫu đá đen tính mét vuông hay tính như nào vậy shop xin giá"],
    "real3": ["Mình cần làm 6 ngôi mộ ngai đá xanh rêu 02 ngôi mộ kt69x127 04 ngôi mộ kt 61x107 "
              "Lắp đặt tại Yên Ninh Thanh Hoá. Cho mình xin giá với bạn"],
    "real4": ["Xin giá và địa chỉ, Đá Granite rộng 89,dài :1,47; dày: 17 cm. Giá mộ kép ?, "
              "Gửi cho mình xem hình mẫu mộ đôi mà xưởng đã làm. Sđt: 0989099856 Trọng., "
              "Cho xem một số mẫu khác mầu khác để lựa chọn. Hiện chưa có kích thước cụ thể."],
    "real5": ["Tôi đang quantâm tới mộ . Cho xin tham khảo giá và kt ngôi quảng cáo .",
              "Cho xem mẫu ngôi đá đen ấn độ . Bao kt (d,r,c) độ dầy",
              "Tôi đặt 01 ngôi kt 157× 110× 147 ko mái chất liệu đá xanh rêu loại A .cho xem mẫu và xin giá. Cảm ơn",
              "0386960129",
              "Nam sách, hd cũ"],
    "real6": ["Co lam bia da k",
              "Lam bia da co nhieu tien",
              "Hien tai nha e k co anh tho chi lam bia da ma e k co kha nang lam chi co the giup e dk k",
              "Rong 30cm,dai40co ca chan nua loai da dep ,e co nguoi than liet si chi xem gia ca bao nhieu "
              "e con xem tai chinh cua e co khong e xin chi giup e truoc ngay 27 thang 7",
              "Chi hoi chuyen gia kich thuoc 30x40 e xin gia",
              "Da e xin cam on chi"],
}

# --- Chat THÊM: các góc nhìn khác ---
SYNTH = {
    "greet": ["Alo shop"],
    "price_only": ["Mộ đá tầm 100 triệu có mẫu nào không shop"],
    "bym2": ["Giá tính theo mét vuông hay theo bộ vậy ạ"],
    "out_scope_tuong": ["Shop có làm tượng phật đá không, báo giá giúp"],
    "out_scope_sanvuon": ["Cho hỏi lát sân đá granite ngoài trời giá bao nhiêu 1 mét"],
    "human_req": ["cho tôi gặp nhân viên tư vấn trực tiếp"],
    "angry": ["shop làm ăn kiểu gì hỏi mãi không trả lời, lừa đảo à"],
    "allcaps": ["TAI SAO KHONG AI TRA LOI TOI VAY HA"],
    "refund": ["tôi muốn hoàn tiền cọc đã đặt tuần trước"],
    "img_req": ["cho xem vài mẫu mộ đôi đá xanh rêu đẹp đẹp"],
    "vague": ["ơ", "thế à", "ừ"],
    "multi_item": ["Cần báo giá: 3 mộ đơn đá xanh đen kt 87x167, 1 lăng thờ, cổng đá. Lắp tại Ninh Bình. "
                   "SĐT 0912345678"],
    "phone_first": ["0987654321 tư vấn giúp em mẫu mộ tam sơn đá trắng"],
    "mixed_lang": ["hi shop, can bao gia mo da don, size 81x152, da xanh re, ship di Hai Duong"],
}

_TS = int(time.time())
ALL = {**{f"__loadtest_{_TS}_{k}__": v for k, v in REAL.items()},
       **{f"__loadtest_{_TS}_{k}__": v for k, v in SYNTH.items()}}


def sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(config.APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()


async def send_webhook(client: httpx.AsyncClient, psid: str, text: str) -> int:
    payload = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": psid}, "recipient": {"id": "PAGE"}, "timestamp": int(time.time() * 1000),
         "message": {"mid": f"m_{time.time()}", "text": text}}]}]}
    raw = json.dumps(payload).encode("utf-8")
    r = await client.post(f"{BASE}/webhook/messenger", content=raw,
                          headers={"X-Hub-Signature-256": sign(raw), "Content-Type": "application/json"},
                          timeout=15.0)
    return r.status_code


async def run_chat(client: httpx.AsyncClient, psid: str, msgs: list[str], sent: dict):
    codes = []
    for i, text in enumerate(msgs):
        if i:
            await asyncio.sleep(MSG_GAP)   # > debounce -> mỗi tin xử riêng
        try:
            codes.append(await send_webhook(client, psid, text))
        except Exception as e:
            codes.append(f"{type(e).__name__}")
    sent[psid] = codes


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET có retry: uvicorn 1-worker bận đôi khi drop keep-alive (RemoteProtocolError)."""
    for attempt in range(3):
        try:
            return await client.get(url, params={"token": TOKEN}, timeout=15.0)
        except httpx.HTTPError:
            if attempt == 2:
                raise
            await asyncio.sleep(1.0)


async def verify(client: httpx.AsyncClient) -> dict:
    """Sau khi xử xong: đọc dashboard, đếm khách có mặt + có bot trả lời."""
    r = await _get(client, f"{BASE}/admin/api/customers")
    present = {c["psid"] for c in r.json().get("customers", [])}
    out = {}
    for psid in ALL:
        if psid not in present:
            out[psid] = {"present": False, "replied": False, "preview": ""}
            continue
        d = await _get(client, f"{BASE}/admin/api/customers/{psid}")
        msgs = d.json().get("messages", []) if d.status_code == 200 else []
        bot = next((m["text"] for m in msgs if m.get("role") == "assistant"), "")
        out[psid] = {"present": True, "replied": bool(bot), "preview": bot[:90].replace("\n", " ")}
    return out


async def main():
    print(f"=== LOAD TEST E2E vào {BASE} ===")
    # keep-alive=0: mỗi request 1 conn mới -> không dính conn cũ bị uvicorn drop lúc bận.
    async with httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=0)) as client:
        # Preflight: app phải chạy + chữ ký hoạt động
        try:
            rv = await client.get(f"{BASE}/webhook/messenger", params={
                "hub.mode": "subscribe", "hub.verify_token": config.VERIFY_TOKEN,
                "hub.challenge": "PING"}, timeout=10.0)
        except Exception as e:
            print(f"[X] App CHƯA chạy ở {BASE} ({type(e).__name__}). Mở trước: python app.py")
            return
        if rv.text.strip('"') != "PING":
            print(f"[X] Verify webhook lỗi -> {rv.status_code} {rv.text!r}. Check MSGR_VERIFY_TOKEN.")
            return
        print(f"[1] Verify webhook OK ({rv.status_code})")

        n_msgs = sum(len(m) for m in ALL.values())
        print(f"[2] Bắn {len(ALL)} chat song song, {n_msgs} tin (gap {MSG_GAP}s/tin trong 1 chat)...")
        t0 = time.monotonic()
        sent: dict = {}
        await asyncio.gather(*(run_chat(client, p, m, sent) for p, m in ALL.items()))
        n_bad = sum(1 for codes in sent.values() for c in codes if c != 200)
        print(f"    gửi xong {round(time.monotonic()-t0,1)}s, {n_msgs-n_bad}/{n_msgs} webhook 200"
              + (f", {n_bad} KHÔNG-200" if n_bad else ""))

        # Đợi debounce + brain: poll tới khi số khách trả lời NGỪNG tăng (ổn định 2 nhịp) hoặc hết trần.
        # Chờ cứng không hợp vì 20 chat đồng thời làm Gemini xếp hàng, thời gian xử biến thiên mạnh.
        print(f"[3] Poll dashboard mỗi {POLL_EVERY_S}s tới khi ổn định (trần {POLL_MAX_S}s)...")
        n_chats = len(ALL)
        prev, stable, waited = -1, 0, 0
        res = {}
        while waited < POLL_MAX_S:
            await asyncio.sleep(POLL_EVERY_S)
            waited += POLL_EVERY_S
            try:
                res = await verify(client)
            except httpx.HTTPError as e:
                print(f"    +{waited}s: (poll lỗi tạm {type(e).__name__}, bỏ qua)")
                continue
            replied = sum(1 for r in res.values() if r["replied"])
            print(f"    +{waited}s: {replied}/{n_chats} có bot trả lời")
            stable = stable + POLL_EVERY_S if replied == prev else 0
            prev = replied
            if waited >= POLL_MIN_S and stable >= POLL_STABLE_S:   # qua sàn + đứng yên đủ lâu -> xong
                break

        print("[4] Verify xong.")

    present = sum(1 for r in res.values() if r["present"])
    replied = sum(1 for r in res.values() if r["replied"])
    print(f"\n{'='*72}")
    print(f"KẾT QUẢ: {present}/{len(ALL)} khách có trên dashboard, {replied} có bot trả lời")
    print(f"{'='*72}")
    for psid, r in res.items():
        short = psid.replace(f"__loadtest_{_TS}_", "").rstrip("_")
        flag = "OK " if r["replied"] else ("HANDOFF/no-history" if not r["present"] else "NO-REPLY")
        print(f"   {short:20} [{flag:18}] {r['preview']}")
    print("\nGhi chú: forced-handoff (human_req) KHÔNG tạo history -> 'no-history' là ĐÚNG.")
    print("FB send tới psid giả -> log server 400 là BÌNH THƯỜNG. Kiểm group Lark có cảnh báo KHÁCH MỚI/HANDOFF.")


if __name__ == "__main__":
    asyncio.run(main())
