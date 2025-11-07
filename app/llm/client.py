# Copyright (c) 2025 sprouee
import time
from typing import Any, List, Optional, Tuple

import google.generativeai as genai
from google.generativeai.types import PartType, Tool

from app.config import API_KEYS, BOT_PERSONA_PROMPT, MAX_HISTORY, MODELS
from app.logging_config import log
from app.state import history

current_key_idx = 0
current_model_idx = 0
available_models: List[str] = MODELS.copy()
last_model_check_ts: float = 0.0


def _summarize_history(chat_id: int) -> None:
    chat_history = history.get(chat_id, [])
    if len(chat_history) <= MAX_HISTORY:
        return
    log.info(f"Summarizing history for chat {chat_id}...")
    try:
        summary_model = genai.GenerativeModel(
            "gemini-2.5-flash-preview",
            api_key=API_KEYS[current_key_idx],
            system_instruction=BOT_PERSONA_PROMPT,
        )
        summary_session = summary_model.start_chat(history=chat_history)
        response = summary_session.send_message("Summarize this conversation in a concise paragraph for context.")
        summary = response.text
        new_history = [
            {"role": "user", "parts": [{"text": "Start of conversation."}]},
            {"role": "model", "parts": [{"text": f"Previously discussed: {summary}"}]},
        ]
        history[chat_id] = new_history
    except Exception as exc:
        log.error(f"History summarization failed for chat {chat_id}: {exc}")
        history[chat_id] = chat_history[-MAX_HISTORY:]


def llm_request(chat_id: int, prompt_parts: List[PartType]) -> Tuple[Optional[str], str, Optional[Any]]:
    global current_key_idx, current_model_idx

    _summarize_history(chat_id)
    chat_history = history.get(chat_id, [])

    models_to_try = available_models if available_models else MODELS
    for model_idx_offset in range(len(models_to_try)):
        model_idx = (current_model_idx + model_idx_offset) % len(models_to_try)
        model_name = models_to_try[model_idx]
        for key_try in range(len(API_KEYS)):
            key_idx = (current_key_idx + key_try) % len(API_KEYS)
            try:
                genai.configure(api_key=API_KEYS[key_idx])
                tools = [
                    Tool(
                        function_declarations=[
                            {
                                "name": "generate_image",
                                "description": "Generates an image from a text description. Use for explicit requests to 'draw', 'create an image', etc.",
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
                    )
                ]
                model = genai.GenerativeModel(model_name, tools=tools, system_instruction=BOT_PERSONA_PROMPT)
                chat_session = model.start_chat(history=chat_history)
                response = chat_session.send_message(prompt_parts)

                if response.candidates and response.candidates[0].content.parts[0].function_call:
                    return None, model_name, response.candidates[0].content.parts[0].function_call

                answer = response.text
                history[chat_id] = chat_session.history
                current_key_idx, current_model_idx = key_idx, model_idx
                return answer, model_name, None
            except Exception as exc:
                if "rate limit" in str(exc).lower():
                    log.info(f"Rate limit on key {key_idx + 1}, model {model_name}. Trying next...")
                else:
                    log.warning(f"Request failed: key {key_idx + 1}, model {model_name}: {exc}")
    raise Exception("All API keys/models failed")


def llm_generate_image(prompt: str) -> Tuple[Optional[bytes], str]:
    global current_key_idx
    model_name = "gemini-2.5-flash-preview"
    for key_try in range(len(API_KEYS)):
        key_idx = (current_key_idx + key_try) % len(API_KEYS)
        try:
            genai.configure(api_key=API_KEYS[key_idx])
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                f"Draw: {prompt}",
                generation_config={"response_mime_type": "image/png"},
            )
            if response.parts:
                current_key_idx = key_idx
                return response.parts[0].inline_data.data, model_name
        except Exception as exc:
            log.warning(f"Image generation failed on key {key_idx + 1}: {exc}")
    return None, model_name


def check_available_models() -> List[str]:
    global available_models, last_model_check_ts
    log.info("Checking available models...")
    working_models: List[str] = []
    for model_name in MODELS:
        for api_key in API_KEYS:
            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)
                _ = model.generate_content("hi").text
                working_models.append(model_name)
                log.info(f"Model {model_name} is available")
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


