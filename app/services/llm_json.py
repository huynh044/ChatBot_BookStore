# app/services/llm_json.py
from __future__ import annotations
import json, time
import httpx
from pydantic import BaseModel, ValidationError

# ---------- utils ----------
def _messages_to_prompt(messages: list[dict]) -> str:
    parts, sys = [], []
    for m in messages:
        role = (m.get("role") or "").lower()
        content = m.get("content") or ""
        if role == "system":
            sys.append(content)
        else:
            parts.append(f"{role.upper()}: {content}")
    if sys:
        sys_text = "\n".join(sys)
        header = f"<<SYS>>\n{sys_text}\n<</SYS>>\n"
    else:
        header = ""
    return header + "\n".join(parts) + "\nASSISTANT:"

def _nice_404(e: httpx.HTTPStatusError):
    txt = ""
    try:
        txt = e.response.text or ""
    except Exception:
        pass
    lower = (txt or "").lower()
    if e.response.status_code == 404 and (
        "not found" in lower or "no such model" in lower or "model" in lower and "found" in lower
    ):
        raise RuntimeError(
            "Ollama báo 404: model planner không tồn tại trên server. "
            "Hãy `ollama pull <model>` hoặc đổi PLANNER_MODEL sang model đã có.\n"
            f"Chi tiết server: {txt[:300]}"
        ) from e
    raise

def _ok(r: httpx.Response) -> bool:
    return 200 <= r.status_code < 300

# ---------- backend detection ----------
def _detect_backend(base_url: str) -> str:
    """
    Trả về: 'ollama_api' | 'ollama_root' | 'openai'
    """
    base = base_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=5)
        if _ok(r): return "ollama_api"
    except Exception:
        pass
    try:
        r = httpx.get(f"{base}/tags", timeout=5)
        if _ok(r): return "ollama_root"
    except Exception:
        pass
    return "openai"

# ---------- low-level calls ----------
def _post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    r = httpx.post(url, json=payload, timeout=timeout)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        _nice_404(e)
    return r.json()

def _chat_ollama(base: str, model: str, messages: list[dict], root_style: bool) -> str:
    """
    Gọi Ollama theo 2 style: /api/* (root_style=False) hoặc /* (root_style=True)
    """
    prefix = "" if root_style else "/api"

    # 1) /chat
    try:
        data = _post_json(
            f"{base}{prefix}/chat",
            {"model": model, "messages": messages, "format": "json", "stream": False, "options": {"temperature": 0}},
        )
        # Ollama chuẩn
        if "message" in data and isinstance(data["message"], dict) and "content" in data["message"]:
            return data["message"]["content"]
        # Một số proxy dùng cấu trúc choices
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        # Nếu là 404/405 có thể server không support /chat → thử /generate
        if e.response is None or e.response.status_code not in (404, 405):
            _nice_404(e)

    # 2) /generate
    prompt = _messages_to_prompt(messages)
    data2 = _post_json(
        f"{base}{prefix}/generate",
        {"model": model, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0}},
    )
    return data2.get("response", "")

def _chat_openai(base: str, model: str, messages: list[dict]) -> str:
    data = _post_json(
        f"{base}/v1/chat/completions",
        {"model": model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}},
    )
    return data["choices"][0]["message"]["content"]

def _chat_any(base_url: str, model: str, messages: list[dict]) -> str:
    base = base_url.rstrip("/")
    style = _detect_backend(base)
    if style == "ollama_api":
        return _chat_ollama(base, model, messages, root_style=False)
    if style == "ollama_root":
        return _chat_ollama(base, model, messages, root_style=True)
    # openai style (proxy)
    return _chat_openai(base, model, messages)

# ---------- public: complete_json ----------
def complete_json(
    *, base_url: str, model: str, system: str, user: str,
    context: dict, schema_hint: dict, schema_model: type[BaseModel], retries: int = 2,
) -> dict:
    """
    Gọi LLM và ÉP trả JSON đúng schema (validate bằng Pydantic).
    Hỗ trợ tự phát hiện backend: Ollama (/api hoặc root) hoặc OpenAI-style proxy.
    """
    envelope = {
        "instruction": "Chỉ trả về DUY NHẤT JSON hợp lệ đúng schema. Không markdown, không lời văn thừa.",
        "schema": schema_hint, "context": context, "user": user,
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(envelope, ensure_ascii=False)},
    ]

    last_err = None
    for _ in range(retries + 1):
        try:
            raw = _chat_any(base_url, model, messages)
            obj = json.loads(raw)
            return schema_model.model_validate(obj).model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            messages.append({"role": "user", "content": "JSON không hợp lệ. Trả lại đúng JSON theo schema, KHÔNG thêm chữ nào khác."})
            time.sleep(0.2)
    raise last_err
