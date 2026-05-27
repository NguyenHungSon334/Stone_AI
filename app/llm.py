"""
OpenRouter LLM wrapper with model routing, retry, and cost tracking.
"""
import asyncio
import time
from typing import Literal
import httpx
from loguru import logger
from app.config import settings
from app.http_client import get_http_client

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

ModelAlias = Literal["smart", "fast", "guard"]

MODELS: dict[ModelAlias, str] = {
    "smart": "anthropic/claude-sonnet-4-5",       # complex reasoning, slot filling
    "fast": "openai/gpt-4o-mini",                  # quick replies, formatting
    "guard": "openai/gpt-4o-mini",                 # safety classification (same model, different prompt)
}

# Cost per 1M tokens (input, output) in USD — for budget tracking
COST_PER_1M: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4-5": (3.0, 15.0),
    "openai/gpt-4o-mini": (0.15, 0.60),
}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # exponential backoff seconds


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

def calc_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return cost in USD for a single call."""
    in_rate, out_rate = COST_PER_1M.get(model_id, (1.0, 1.0))
    return (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000


# ---------------------------------------------------------------------------
# Core call
# ---------------------------------------------------------------------------

async def llm_call(
    messages: list[dict],
    alias: ModelAlias = "fast",
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> tuple[str, float]:
    """
    Call OpenRouter and return (reply_text, cost_usd).
    Retries up to MAX_RETRIES times on 5xx or network errors.
    Raises RuntimeError if all retries exhausted.
    """
    model_id = MODELS[alias]
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://spiritstone.vn",
        "X-Title": "SpiritStone AI",
    }

    client = get_http_client()
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.monotonic()
            r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            elapsed = time.monotonic() - t0

            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after", RETRY_DELAYS[attempt]))
                logger.warning("OpenRouter rate-limited model={} retry_after={:.1f}s", model_id, retry_after)
                await asyncio.sleep(retry_after)
                continue

            if 400 <= r.status_code < 500:
                # 4xx = client error, fail fast without retry
                logger.error("OpenRouter 4xx model={} status={} body={}", model_id, r.status_code, r.text)
                raise RuntimeError(f"OpenRouter {r.status_code}: {r.text}")

            if r.status_code >= 500:
                logger.warning("OpenRouter 5xx model={} status={} body={}", model_id, r.status_code, r.text)
                raise httpx.HTTPStatusError(r.text, request=r.request, response=r)

            r.raise_for_status()
            data = r.json()

            reply = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            cost = calc_cost(
                model_id,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
            logger.info(
                "llm model={} alias={} tokens={}/{} cost=${:.5f} latency={:.2f}s",
                model_id, alias,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                cost, elapsed,
            )
            return reply, cost

        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning("llm error attempt={} model={} err={} retry in {}s", attempt + 1, model_id, e, delay)
                await asyncio.sleep(delay)

    raise RuntimeError(f"llm_call failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

async def llm_call_with_tools(
    messages: list[dict],
    tools: list[dict],
    alias: ModelAlias = "smart",
    temperature: float = 0.3,
    max_tokens: int = 1000,
) -> tuple[str | None, list[dict], float]:
    """
    Call OpenRouter with optional tools. Returns (text | None, tool_calls, cost_usd).
    text is None when model only emitted tool_calls with no accompanying text.
    When tools is empty, sends a plain completion request (no tool params).
    """
    model_id = MODELS[alias]
    payload: dict = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://spiritstone.vn",
        "X-Title": "SpiritStone AI",
    }

    # Log input: last user message + tool names available
    user_msgs = [m for m in messages if m.get("role") == "user"]
    last_user = user_msgs[-1]["content"][:200] if user_msgs else ""
    tool_names_avail = [t["function"]["name"] for t in tools] if tools else []
    logger.debug(
        "llm_tools INPUT alias={} tools_avail={} last_user={!r} total_msgs={}",
        alias, tool_names_avail, last_user, len(messages),
    )

    client = get_http_client()
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.monotonic()
            r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            elapsed = time.monotonic() - t0

            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after", RETRY_DELAYS[attempt]))
                logger.warning("OpenRouter rate-limited model={} retry_after={:.1f}s", model_id, retry_after)
                await asyncio.sleep(retry_after)
                continue

            if 400 <= r.status_code < 500:
                logger.error("OpenRouter 4xx model={} status={} body={}", model_id, r.status_code, r.text)
                raise RuntimeError(f"OpenRouter {r.status_code}: {r.text}")

            if r.status_code >= 500:
                logger.warning("OpenRouter 5xx model={} status={} body={}", model_id, r.status_code, r.text)
                raise httpx.HTTPStatusError(r.text, request=r.request, response=r)

            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            usage = data.get("usage", {})
            cost = calc_cost(
                model_id,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

            tool_calls: list[dict] = msg.get("tool_calls") or []
            raw_text = (msg.get("content") or "").strip()
            finish_reason = data["choices"][0].get("finish_reason")
            if finish_reason == "length" and raw_text:
                raw_text += "\n(Em xin lỗi, tin nhắn quá dài. Bác vui lòng hỏi thêm để em tiếp tục ạ!)"
            text = raw_text or None

            # Log output: tool calls made + reply preview
            called_tools = [
                f"{tc['function']['name']}({tc['function']['arguments'][:80]})"
                for tc in tool_calls
            ]
            logger.info(
                "llm_tools OUTPUT alias={} tools_called={} reply={!r} cost=${:.5f} latency={:.2f}s",
                alias, called_tools, (text or "")[:150], cost, elapsed,
            )
            return text, tool_calls, cost

        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning("llm_tools error attempt={} err={} retry in {}s", attempt + 1, e, delay)
                await asyncio.sleep(delay)

    raise RuntimeError(f"llm_call_with_tools failed after {MAX_RETRIES} attempts: {last_error}")


async def chat(
    system: str,
    user: str,
    alias: ModelAlias = "fast",
    **kwargs,
) -> tuple[str, float]:
    """Single-turn chat shorthand."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return await llm_call(messages, alias=alias, **kwargs)


_GUARD_SYSTEM = (
    "Bạn là bộ lọc an toàn cho chatbot tư vấn đá lăng mộ (Hồn Đá). "
    "Khách hàng hỏi về: mộ, lăng, lăng tộc, kích thước, giá đá, thi công. Những chủ đề này LUÔN an toàn.\n"
    "Trả về 'safe' nếu tin nhắn bình thường hoặc liên quan đến đá/lăng mộ/xây dựng.\n"
    "Trả về 'unsafe' CHỈ khi có: bạo lực rõ ràng, nội dung tình dục, lừa đảo tài chính, hướng dẫn gây hại.\n"
    "Chỉ trả về đúng một từ: safe hoặc unsafe. Không giải thích."
)


async def classify(prompt: str) -> str:
    """
    Safety classification using fast model with a safety system prompt.
    Returns 'safe' or 'unsafe'.
    """
    reply, _ = await chat(
        system=_GUARD_SYSTEM,
        user=prompt,
        alias="fast",
        temperature=0.0,
        max_tokens=10,
    )
    return reply.lower().strip()


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
_EMBED_MODEL = "openai/text-embedding-3-small"


async def embed(text: str) -> list[float]:
    """Generate 1536-dim embedding via OpenRouter. Retries on transient errors."""
    client = get_http_client()
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://spiritstone.vn",
    }
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = await client.post(
                _EMBED_URL,
                headers=headers,
                json={"model": _EMBED_MODEL, "input": text[:8000]},
            )
            if r.status_code == 200:
                return r.json()["data"][0]["embedding"]
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"embed failed status={r.status_code} body={r.text[:200]}")
            # 5xx — retry
            last_error = RuntimeError(f"embed 5xx status={r.status_code}")
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_error = e
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])
            logger.warning("embed retry attempt={} err={}", attempt + 1, last_error)
    raise RuntimeError(f"embed failed after {MAX_RETRIES} attempts: {last_error}")
