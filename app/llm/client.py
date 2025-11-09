import base64
import random
import time
import urllib.parse
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types
import requests

from app.config import (
    API_KEYS,
    BOT_PERSONA_PROMPT,
    IMAGE_MODEL_NAME,
    MAX_HISTORY,
    MODELS,
    POLLINATIONS_BASE_URL,
    POLLINATIONS_ENABLED,
    POLLINATIONS_HEIGHT,
    POLLINATIONS_MODEL,
    POLLINATIONS_SEED,
    POLLINATIONS_TIMEOUT,
    POLLINATIONS_WIDTH,
)
from app.logging_config import log
from app.state import history

current_key_idx = 0
current_model_idx = 0
available_models: List[str] = MODELS.copy()
last_model_check_ts: float = 0.0
_clients: Dict[int, genai.Client] = {}


def _get_client(idx: int) -> genai.Client:
    if not API_KEYS:
        raise RuntimeError("Не заданы API ключи для Gemini")
    idx = idx % len(API_KEYS)
    client = _clients.get(idx)
    if client is None:
        client = genai.Client(api_key=API_KEYS[idx])
        _clients[idx] = client
    return client


def _to_base64(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, bytes):
        return base64.b64encode(data).decode("utf-8")
    if isinstance(data, bytearray):
        return base64.b64encode(bytes(data)).decode("utf-8")
    if isinstance(data, memoryview):
        return base64.b64encode(data.tobytes()).decode("utf-8")
    return base64.b64encode(str(data).encode("utf-8")).decode("utf-8")


def _from_base64_maybe(data: Any) -> Any:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:
            return data
    return data


def _normalize_prompt_parts(prompt_parts: List[Any]) -> Dict[str, Any]:
    normalized: List[Dict[str, Any]] = []
    for part in prompt_parts:
        if isinstance(part, dict) and "inline_data" in part:
            inline = part["inline_data"]
            normalized.append(
                {
                    "inline_data": {
                        "mime_type": inline.get("mime_type") or inline.get("mimeType") or "application/octet-stream",
                        "data": inline.get("data"),
                    }
                }
            )
        elif isinstance(part, dict) and "text" in part:
            normalized.append({"text": str(part["text"])})
        elif isinstance(part, (bytes, bytearray, memoryview)):
            normalized.append({"inline_data": {"mime_type": "application/octet-stream", "data": bytes(part)}})
        else:
            normalized.append({"text": str(part)})
    return {"role": "user", "parts": normalized}


def _api_part(part: Dict[str, Any]) -> Dict[str, Any]:
    if "text" in part and part["text"] is not None:
        return {"text": str(part["text"])}
    if "function_call" in part:
        fn = part["function_call"]
        if not fn or not fn.get("name"):
            return {}
        return {
            "functionCall": {
                "name": fn.get("name"),
                "args": fn.get("args", {}),
            }
        }
    if "functionCall" in part:
        if not part["functionCall"] or not part["functionCall"].get("name"):
            return {}
        return part
    if "inline_data" in part:
        inline = part["inline_data"]
        return {
            "inlineData": {
                "mimeType": inline.get("mime_type") or inline.get("mimeType") or "application/octet-stream",
                "data": _to_base64(inline.get("data")),
            }
        }
    if "inlineData" in part:
        return {
            "inlineData": {
                "mimeType": part["inlineData"].get("mimeType"),
                "data": _to_base64(part["inlineData"].get("data")),
            }
        }
    return {"text": str(part)}


def _api_content(message: Dict[str, Any]) -> Dict[str, Any]:
    formatted_parts: List[Dict[str, Any]] = []
    for raw_part in message.get("parts", []):
        mapped = _api_part(raw_part)
        if mapped:
            formatted_parts.append(mapped)
    return {
        "role": message.get("role", "user"),
        "parts": formatted_parts or [{"text": ""}],
    }


def _part_from_any(part: Any) -> Dict[str, Any]:
    if hasattr(part, "function_call"):
        function_call = part.function_call
        args = getattr(function_call, "args", {}) or {}
        if hasattr(args, "items"):
            args = dict(args)
        name = getattr(function_call, "name", "")
        if not name:
            return {}
        return {"function_call": {"name": name, "args": args}}
    if hasattr(part, "text"):
        return {"text": part.text}
    if hasattr(part, "inline_data"):
        inline = part.inline_data
        return {
            "inline_data": {
                "mime_type": getattr(inline, "mime_type", None),
                "data": _from_base64_maybe(getattr(inline, "data", None)),
            }
        }
    if isinstance(part, dict):
        if "functionCall" in part:
            fc = part["functionCall"]
            if not fc or not fc.get("name"):
                return {}
            return {"function_call": {"name": fc.get("name"), "args": fc.get("args", {})}}
        if "function_call" in part:
            if not part["function_call"] or not part["function_call"].get("name"):
                return {}
            return {"function_call": part["function_call"]}
        if "inlineData" in part:
            inline = part["inlineData"]
            return {
                "inline_data": {
                    "mime_type": inline.get("mimeType"),
                    "data": _from_base64_maybe(inline.get("data")),
                }
            }
        if "inline_data" in part:
            inline = part["inline_data"]
            return {
                "inline_data": {
                    "mime_type": inline.get("mime_type"),
                    "data": _from_base64_maybe(inline.get("data")),
                }
            }
        if "text" in part:
            return {"text": part["text"]}
    if isinstance(part, str):
        return {"text": part}
    if isinstance(part, (bytes, bytearray, memoryview)):
        return {"inline_data": {"mime_type": "application/octet-stream", "data": bytes(part)}}
    return {"text": str(part)}


def _response_parts(response: Any) -> List[Dict[str, Any]]:
    candidates = getattr(response, "candidates", None)
    if not candidates and isinstance(response, dict):
        candidates = response.get("candidates")
    if candidates:
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        if content is None and isinstance(candidate, dict):
            content = candidate.get("content")
        parts = getattr(content, "parts", None)
        if parts is None and isinstance(content, dict):
            parts = content.get("parts", [])
        if parts:
            cleaned_parts = []
            for part in parts:
                mapped = _part_from_any(part)
                if mapped:
                    cleaned_parts.append(mapped)
            return cleaned_parts
    text = getattr(response, "text", None)
    if text:
        return [{"text": text}]
    return []


def _extract_text_from_parts(parts: List[Dict[str, Any]]) -> str:
    texts = [part["text"] for part in parts if isinstance(part, dict) and part.get("text")]
    return "\n".join(texts).strip()


def _extract_function_call(parts: List[Dict[str, Any]]) -> Optional[SimpleNamespace]:
    for part in parts:
        fn = part.get("function_call")
        if isinstance(fn, dict):
            name = fn.get("name")
            if not name:
                continue
            args = fn.get("args") or {}
            if hasattr(args, "items"):
                args = dict(args)
            return SimpleNamespace(name=name, args=args)
    return None


def _history_to_text(chat_history: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for message in chat_history:
        role = message.get("role", "user")
        texts = [part.get("text") for part in message.get("parts", []) if isinstance(part, dict) and part.get("text")]
        if texts:
            lines.append(f"{role}: {' '.join(texts)}")
    return "\n".join(lines)


def _request_config() -> Dict[str, Any]:
    return {
        "system_instruction": {"parts": [{"text": BOT_PERSONA_PROMPT}]},
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "generate_image",
                        "description": "Generates an image from a text description. Use for explicit image requests.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "prompt": {
                                    "type": "STRING",
                                    "description": "The image description.",
                                }
                            },
                            "required": ["prompt"],
                        },
                    }
                ]
            }
        ],
    }


def _summarize_history(chat_id: int) -> None:
    chat_history = history.get(chat_id, [])
    if len(chat_history) <= MAX_HISTORY:
        return
    log.info(f"Summarizing history for chat {chat_id}...")
    conversation_text = _history_to_text(chat_history)
    if not conversation_text:
        history[chat_id] = chat_history[-MAX_HISTORY:]
        return
    prompt = (
        "Summarize the following conversation in a concise paragraph for future context:\n"
        f"{conversation_text}\nSummary:"
    )
    try:
        client = _get_client(current_key_idx)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config={"system_instruction": {"parts": [{"text": BOT_PERSONA_PROMPT}]}}
        )
        summary_parts = _response_parts(response)
        summary_text = _extract_text_from_parts(summary_parts)
        if not summary_text:
            raise ValueError("Empty summary response")
        history[chat_id] = [
            {"role": "user", "parts": [{"text": "Start of conversation."}]},
            {"role": "model", "parts": [{"text": f"Previously discussed: {summary_text}"}]},
        ]
    except Exception as exc:
        log.error(f"History summarization failed for chat {chat_id}: {exc}")
        history[chat_id] = chat_history[-MAX_HISTORY:]


def llm_request(chat_id: int, prompt_parts: List[Any]) -> Tuple[Optional[str], str, Optional[Any]]:
    global current_key_idx, current_model_idx

    _summarize_history(chat_id)
    stored_history = history.get(chat_id, [])
    user_message = _normalize_prompt_parts(prompt_parts)

    models_to_try = available_models if available_models else MODELS
    if not models_to_try:
        raise RuntimeError("Нет доступных моделей для запроса")

    for model_offset in range(len(models_to_try)):
        model_idx = (current_model_idx + model_offset) % len(models_to_try)
        model_name = models_to_try[model_idx]
        for key_attempt in range(len(API_KEYS)):
            key_idx = (current_key_idx + key_attempt) % len(API_KEYS)
            try:
                client = _get_client(key_idx)
                contents_payload = [_api_content(item) for item in stored_history + [user_message]]
                contents_payload = [item for item in contents_payload if item.get("parts")]
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents_payload,
                    config=_request_config(),
                )
                parts = _response_parts(response)
                reply_text = _extract_text_from_parts(parts)
                fn_call = _extract_function_call(parts)

                # обновляем историю
                new_history = stored_history + [user_message]
                if parts:
                    new_history.append({"role": "model", "parts": parts})
                history[chat_id] = new_history
                current_key_idx, current_model_idx = key_idx, model_idx
                return reply_text if reply_text else None, model_name, fn_call
            except Exception as exc:
                error_text = str(exc).lower()
                if "rate limit" in error_text or "quota" in error_text:
                    log.info(f"Rate limit on key {key_idx + 1}, model {model_name}. Trying next...")
                else:
                    log.warning(f"Request failed: key {key_idx + 1}, model {model_name}: {exc}")
    raise Exception("All API keys/models failed")


def llm_generate_image(prompt: str) -> Tuple[Optional[bytes], str]:
    global current_key_idx
    if POLLINATIONS_ENABLED:
        image_bytes, provider = _generate_image_via_pollinations(prompt)
        if image_bytes:
            return image_bytes, provider

    model_name = IMAGE_MODEL_NAME
    for attempt in range(len(API_KEYS)):
        key_idx = (current_key_idx + attempt) % len(API_KEYS)
        try:
            client = _get_client(key_idx)
            image_bytes = _generate_image_via_gemini(client, model_name, prompt)
            if image_bytes:
                current_key_idx = key_idx
                return image_bytes, model_name
        except Exception as exc:
            log.warning(f"Image generation failed on key {key_idx + 1}: {exc}")
    return None, model_name


def _generate_image_via_pollinations(prompt: str) -> Tuple[Optional[bytes], str]:
    seed_value = POLLINATIONS_SEED or str(random.randint(0, 1_000_000_000))
    encoded_prompt = urllib.parse.quote_plus(prompt)
    params = {
        "width": str(POLLINATIONS_WIDTH),
        "height": str(POLLINATIONS_HEIGHT),
        "seed": seed_value,
        "model": POLLINATIONS_MODEL,
    }
    query = "&".join(f"{key}={urllib.parse.quote_plus(value)}" for key, value in params.items())
    url = f"{POLLINATIONS_BASE_URL.rstrip('/')}/p/{encoded_prompt}?{query}"
    try:
        response = requests.get(url, timeout=POLLINATIONS_TIMEOUT)
        response.raise_for_status()
        if response.content:
            provider_name = f"pollinations:{POLLINATIONS_MODEL}"
            log.info("Generated image via Pollinations (%s)", provider_name)
            return response.content, provider_name
        log.warning("Pollinations returned empty image content for prompt: %s", prompt)
    except Exception as exc:
        log.warning("Pollinations image generation failed: %s", exc)
    return None, f"pollinations:{POLLINATIONS_MODEL}"


def _generate_image_via_gemini(client: genai.Client, model_name: str, prompt: str) -> Optional[bytes]:
    images_api = getattr(client, "images", None)
    if images_api and hasattr(images_api, "generate"):
        try:
            response = images_api.generate(model=model_name, prompt=prompt)
            generated = getattr(response, "generated_images", None) or getattr(response, "images", None)
            if generated:
                first = generated[0]
                data = getattr(first, "data", None) or getattr(first, "image", None) or getattr(first, "bytes", None)
                if isinstance(data, str):
                    return base64.b64decode(data)
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
        except Exception as exc:
            log.warning(f"Gemini image generation via images.generate failed: {exc}")
    try:
        generate_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1"),
        )
        response = client.models.generate_content(
            model=model_name,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=generate_config,
        )
        parts = _response_parts(response)
        for part in parts:
            inline = part.get("inline_data")
            if inline:
                data = inline.get("data")
                if isinstance(data, bytes):
                    return data
                if isinstance(data, str):
                    try:
                        return base64.b64decode(data)
                    except Exception:
                        continue
    except Exception as exc:
        log.warning(f"Gemini image generation fallback failed: {exc}")
    return None


def check_available_models() -> List[str]:
    global available_models, last_model_check_ts
    log.info("Checking available models...")
    working_models: List[str] = []
    for model_name in MODELS:
        for key_idx in range(len(API_KEYS)):
            try:
                client = _get_client(key_idx)
                response = client.models.generate_content(
                    model=model_name,
                    contents=[{"role": "user", "parts": [{"text": "hi"}]}],
                    config={"system_instruction": {"parts": [{"text": "You are a helper."}]}}
                )
                text = _extract_text_from_parts(_response_parts(response))
                if text:
                    working_models.append(model_name)
                    log.info(f"Model {model_name} is available with key #{key_idx + 1}")
                    break
            except Exception:
                continue
    if working_models:
        available_models = working_models
        last_model_check_ts = time.time()
        log.info(f"Available models updated: {working_models}")
    else:
        available_models = MODELS.copy()
    return available_models

