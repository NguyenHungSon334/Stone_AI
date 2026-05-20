"""
Dev-only chat UI — test the AI without Facebook Messenger.
Only mounted when environment != production.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import orchestrator

router = APIRouter(prefix="/dev", tags=["dev"])

_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hồn Đá AI — Demo</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f0f2f5;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
  }

  /* Name screen */
  #name-screen {
    width: 360px;
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,.12);
    padding: 40px 32px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 20px;
  }
  #name-screen .logo {
    width: 64px; height: 64px;
    background: #6d4c41;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 28px; color: #fff;
  }
  #name-screen h2 { font-size: 20px; color: #333; text-align: center; }
  #name-screen p { font-size: 14px; color: #888; text-align: center; line-height: 1.6; }
  #name-input {
    width: 100%;
    border: 2px solid #ddd;
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 16px;
    outline: none;
    transition: border-color .2s;
    text-align: center;
  }
  #name-input:focus { border-color: #6d4c41; }
  #start-btn {
    width: 100%;
    background: #6d4c41;
    color: #fff;
    border: none;
    border-radius: 12px;
    padding: 14px;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background .2s;
  }
  #start-btn:hover { background: #4e342e; }
  #start-btn:disabled { background: #bbb; cursor: default; }

  /* Chat screen */
  #chat-screen { display: none; }
  .container {
    width: 420px;
    height: 680px;
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,.12);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header {
    background: #6d4c41;
    color: #fff;
    padding: 14px 20px;
    font-size: 15px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .header .dot { width: 9px; height: 9px; background: #a5d6a7; border-radius: 50%; }
  .header .user-name { margin-left: auto; font-size: 12px; opacity: .75; }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .msg {
    max-width: 78%;
    padding: 10px 14px;
    border-radius: 18px;
    font-size: 14px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.bot { background: #f0ebe8; color: #333; align-self: flex-start; border-bottom-left-radius: 4px; }
  .msg.user { background: #6d4c41; color: #fff; align-self: flex-end; border-bottom-right-radius: 4px; }
  .msg.thinking { background: #f0ebe8; color: #999; align-self: flex-start; font-style: italic; }
  .label { font-size: 11px; color: #999; margin-bottom: 2px; padding: 0 4px; }
  .label.right { text-align: right; }
  .input-row {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid #eee;
  }
  #user-input {
    flex: 1;
    border: 1px solid #ddd;
    border-radius: 24px;
    padding: 10px 16px;
    font-size: 14px;
    outline: none;
    transition: border-color .2s;
  }
  #user-input:focus { border-color: #6d4c41; }
  #send-btn {
    background: #6d4c41;
    color: #fff;
    border: none;
    border-radius: 50%;
    width: 40px; height: 40px;
    cursor: pointer;
    font-size: 18px;
    display: flex; align-items: center; justify-content: center;
    transition: background .2s;
    flex-shrink: 0;
  }
  #send-btn:hover { background: #4e342e; }
  #send-btn:disabled { background: #bbb; cursor: default; }
</style>
</head>
<body>

<!-- Step 1: Enter name -->
<div id="name-screen">
  <div class="logo">&#128974;</div>
  <h2>Hồn Đá AI</h2>
  <p>Trợ lý tư vấn đá lăng mộ.<br>Nhập tên để bắt đầu trải nghiệm.</p>
  <input id="name-input" placeholder="Tên của bạn (vd: Anh Nam)" autocomplete="off" maxlength="40">
  <button id="start-btn" disabled>Bắt đầu chat</button>
</div>

<!-- Step 2: Chat -->
<div id="chat-screen">
  <div class="container">
    <div class="header">
      <div class="dot"></div>
      Hồn Đá AI
      <span class="user-name" id="display-name"></span>
    </div>
    <div class="messages" id="messages">
      <div class="label">Hồn Đá AI</div>
      <div class="msg bot">Xin chào! Em là Thảo Vân, trợ lý tư vấn của Hồn Đá. Bác cần tư vấn về đá lăng mộ hay công trình mộ phần ạ?</div>
    </div>
    <div class="input-row">
      <input id="user-input" placeholder="Nhắn tin…" autocomplete="off">
      <button id="send-btn">&#9654;</button>
    </div>
  </div>
</div>

<script>
const nameInput = document.getElementById('name-input');
const startBtn = document.getElementById('start-btn');
const nameScreen = document.getElementById('name-screen');
const chatScreen = document.getElementById('chat-screen');
const displayName = document.getElementById('display-name');
const msgs = document.getElementById('messages');
const input = document.getElementById('user-input');
const btn = document.getElementById('send-btn');
let senderId = '';

nameInput.addEventListener('input', () => {
  startBtn.disabled = nameInput.value.trim().length === 0;
});
nameInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && nameInput.value.trim()) startChat();
});
startBtn.addEventListener('click', startChat);

function startChat() {
  const name = nameInput.value.trim();
  if (!name) return;
  senderId = 'demo_' + name.replace(/\\s+/g, '_').toLowerCase() + '_' + Date.now();
  displayName.textContent = name;
  nameScreen.style.display = 'none';
  chatScreen.style.display = 'block';
  input.focus();
}

function addMsg(text, cls) {
  const label = document.createElement('div');
  label.className = 'label' + (cls === 'user' ? ' right' : '');
  label.textContent = cls === 'user' ? displayName.textContent || 'Bạn' : 'Hồn Đá AI';
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  msgs.appendChild(label);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

async function send() {
  const text = input.value.trim();
  if (!text || !senderId) return;
  input.value = '';
  btn.disabled = true;

  addMsg(text, 'user');
  const thinking = addMsg('Đang xử lý…', 'thinking');

  try {
    const res = await fetch('/dev/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sender_id: senderId, text})
    });
    const data = await res.json();
    thinking.remove();
    addMsg(data.reply || '(không có phản hồi)', 'bot');
  } catch (e) {
    thinking.textContent = 'Lỗi kết nối: ' + e.message;
    thinking.style.color = '#e53935';
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

btn.addEventListener('click', send);
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
</script>
</body>
</html>"""


class ChatRequest(BaseModel):
    sender_id: str = "dev_user_1"
    text: str


@router.get("", response_class=HTMLResponse)
async def chat_ui():
    return HTMLResponse(_HTML)


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Calls the orchestrator directly, intercepts send_text to capture the reply.
    Returns {"reply": "..."} instead of posting to Messenger.
    """
    from unittest.mock import patch, AsyncMock

    captured: list[str] = []

    async def _capture(sender_id: str, text: str) -> None:
        captured.append(text)

    with patch("app.orchestrator.send_text", side_effect=_capture):
        await orchestrator.run(req.sender_id, req.text)

    return {"reply": "\n\n".join(captured) if captured else ""}
