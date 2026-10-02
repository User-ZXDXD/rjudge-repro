"""Minimal OpenAI-compatible chat client used by the R-Judge reproduction.

Reads the API key from the local Codex config so the key never lives in this file.
Only uses the standard library, so no pip install is required.
"""
import json
import os
import time
import urllib.error
import urllib.request

CODEX_AUTH = os.path.join(os.path.expanduser("~"), ".codex", "auth.json")

BASE_URL = os.environ.get("RJ_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("RJ_MODEL", "deepseek-flash")


def load_api_key():
    key = os.environ.get("RJ_API_KEY")
    if key:
        return key
    with open(CODEX_AUTH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for name in ("OPENAI_API_KEY", "api_key", "API_KEY"):
        if data.get(name):
            return data[name]
    raise RuntimeError("no API key found in " + CODEX_AUTH)


def chat(messages, model=None, max_tokens=2048, temperature=0, retries=5, timeout=300):
    """Single call to the chat completions endpoint.

    Returns a dict with `content`, `reasoning` and `usage`. Empty `content` is
    returned as-is; the caller decides how to handle a starved budget.
    """
    model = model or MODEL
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "n": 1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = BASE_URL.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + load_api_key(),
        },
        method="POST",
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            return {
                "content": msg.get("content") or "",
                "reasoning": msg.get("reasoning_content") or "",
                "usage": data.get("usage") or {},
                "finish_reason": data["choices"][0].get("finish_reason"),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last_err = "HTTP %s %s" % (exc.code, detail)
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last_err)
        except Exception as exc:
            last_err = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(2 ** attempt)
    raise RuntimeError("chat failed after %d tries: %s" % (retries, last_err))


def chat_text(messages, model=None, max_tokens=2048, temperature=0, min_chars=1,
              grow=4, attempts=3, **kw):
    """Like `chat` but guarantees non-empty text.

    Reasoning models spend part of `max_tokens` on hidden thinking, so the
    visible answer can come back empty. Each retry multiplies the budget.
    """
    budget = max_tokens
    last = None
    for _ in range(attempts):
        last = chat(messages, model=model, max_tokens=budget,
                    temperature=temperature, **kw)
        if len(last["content"].strip()) >= min_chars:
            return last
        budget *= grow
    return last


if __name__ == "__main__":
    print("base_url =", BASE_URL)
    print("model    =", MODEL)
    print("key      =", load_api_key()[:6] + "..." + " (loaded)")
    out = chat_text([{"role": "user", "content": "Reply with exactly: OK"}],
                    max_tokens=256)
    print("reply    =", repr(out["content"]))
    print("usage    =", out["usage"])
