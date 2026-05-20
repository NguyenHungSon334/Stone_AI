"""
OpenRouter LLM wrapper with model routing, retry, and cost tracking.
"""
import asyncio
import time
from typing import Literal
import httpx
from loguru import logger
from app.config import settings

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
    client: httpx.AsyncClient | None = None,
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

    owned_client = client is None
    if owned_client:
        client = httpx.AsyncClient(timeout=30)

    last_error: Exception | None = None
    try:
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
    finally:
        if owned_client:
            await client.aclose()

    raise RuntimeError(f"llm_call failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

async def llm_call_with_tools(
    messages: list[dict],
    tools: list[dict],
    alias: ModelAlias = "smart",
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> tuple[str | None, list[dict], float]:
    """
    Call OpenRouter with tools. Returns (text | None, tool_calls, cost_usd).
    text is None when model only emitted tool_calls with no accompanying text.
    """
    model_id = MODELS[alias]
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://spiritstone.vn",
        "X-Title": "SpiritStone AI",
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=30) as client:
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
                text = (msg.get("content") or "").strip() or None

                logger.info(
                    "llm_tools model={} tools={} cost=${:.5f} latency={:.2f}s",
                    model_id,
                    [tc["function"]["name"] for tc in tool_calls],
                    cost, elapsed,
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
    "Bạn là bộ lọc an toàn. Phân loại tin nhắn của người dùng.\n"
    "Trả về 'safe' nếu bình thường.\n"
    "Trả về 'unsafe' nếu có: bạo lực, nội dung tình dục, lừa đảo, hướng dẫn gây hại.\n"
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
