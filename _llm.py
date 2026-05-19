"""
skills/_llm.py
Minimal Moonshot (Kimi) chat client used internally by the skills package.

Why this lives here (and not at the top of skills/) :
  Both Summarization/shared/llm.py and ChatbotUI/backend/summarizer.py
  already implement near-identical Moonshot wrappers. The skills/ package
  is consumed by *both* of them, so we can't import either one without
  creating a cycle. This module is the neutral version — small, dependency-
  free beyond `openai` and `os.environ`, and intentionally not re-exported
  from skills/__init__.py (note the leading underscore).

  When we eventually consolidate the three copies (separate refactor), this
  will be the version that survives.

Reads from env:
  MOONSHOT_API_KEY   required
  MOONSHOT_BASE_URL  optional, defaults to https://api.moonshot.ai/v1
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

logger = logging.getLogger("skills._llm")

_DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"

# Lazy singleton — one client per process.
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("MOONSHOT_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MOONSHOT_API_KEY is not set. The skills package needs it for "
                "the router and any LLM-backed skills. Check your .env."
            )
        _client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("MOONSHOT_BASE_URL", _DEFAULT_BASE_URL),
        )
    return _client


def has_api_key() -> bool:
    """Cheap check used by the router to decide whether to even try."""
    return bool(os.environ.get("MOONSHOT_API_KEY"))


def chat(
    messages: list[dict],
    *,
    model: str,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
    timeout: Optional[float] = None,
) -> tuple[str, dict]:
    """Send a chat-completion request with retry on transient errors.

    Returns (content, usage_dict). Usage dict has prompt_tokens /
    completion_tokens / total_tokens; some Moonshot responses may omit
    fields, so callers should treat it as best-effort.

    Mirrors the signature of Summarization/shared/llm.py.chat so a future
    consolidation is mechanical.
    """
    client = _get_client()
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = resp.choices[0].message.content or ""
            usage_obj = getattr(resp, "usage", None)
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
            }
            return content, usage

        except AuthenticationError:
            # No point retrying a bad key.
            raise
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            last_err = e
            backoff = 2 ** attempt
            logger.warning(
                "Moonshot %s on attempt %d/%d: %s (sleeping %ds)",
                type(e).__name__, attempt + 1, max_retries, e, backoff,
            )
            time.sleep(backoff)

    assert last_err is not None  # for the type checker
    raise last_err
