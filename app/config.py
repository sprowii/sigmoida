# Copyright (c) 2025 sprouee
import os
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _resolve_redis_url(raw_url: str) -> str:
    if ".upstash.io" in raw_url and raw_url.startswith("redis://"):
        return "rediss" + raw_url[len("redis") :]
    return raw_url


REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("РџРµСЂРµРјРµРЅРЅР°СЏ РѕРєСЂСѓР¶РµРЅРёСЏ REDIS_URL РґРѕР»Р¶РЅР° Р±С‹С‚СЊ СѓСЃС‚Р°РЅРѕРІР»РµРЅР°")
REDIS_URL = _resolve_redis_url(REDIS_URL)

TG_TOKEN = os.getenv("TG_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DOWNLOAD_KEY = os.getenv("DOWNLOAD_KEY")


def _load_api_keys() -> List[str]:
    keys: List[str] = []
    for idx in (1, 2):
        key = os.getenv(f"GEMINI_API_KEY_{idx}")
        if key:
            keys.append(key)
    return keys


API_KEYS = _load_api_keys()
if not API_KEYS:
    raise RuntimeError(
        "РќРµРѕР±С…РѕРґРёРјРѕ СѓСЃС‚Р°РЅРѕРІРёС‚СЊ С…РѕС‚СЏ Р±С‹ РѕРґРЅСѓ РїРµСЂРµРјРµРЅРЅСѓСЋ РѕРєСЂСѓР¶РµРЅРёСЏ GEMINI_API_KEY_1 РёР»Рё GEMINI_API_KEY_2"
    )

MODELS: List[str] = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-preview",
]

MAX_HISTORY = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB РїСЂРµРґРµР» РЅР° СЃРѕС…СЂР°РЅРµРЅРёРµ РёР·РѕР±СЂР°Р¶РµРЅРёР№

BOT_PERSONA_PROMPT = """
РўС‹ - СѓРјРЅС‹Р№ Рё РїРѕР»РµР·РЅС‹Р№ Р°СЃСЃРёСЃС‚РµРЅС‚ РїРѕ РёРјРµРЅРё РЎРёРіРјРѕРёРґР°.
РќРµ СѓРїРѕРјРёРЅР°Р№, С‡С‚Рѕ С‚С‹ Google, Gemini РёР»Рё Р±РѕР»СЊС€Р°СЏ СЏР·С‹РєРѕРІР°СЏ РјРѕРґРµР»СЊ.
Р¤РѕСЂРјР°С‚РёСЂСѓР№ СЃРІРѕРё РѕС‚РІРµС‚С‹, РёСЃРїРѕР»СЊР·СѓСЏ HTML-С‚РµРіРё, СЃРѕРІРјРµСЃС‚РёРјС‹Рµ СЃ Telegram.
РСЃРїРѕР»СЊР·СѓР№ <b>РґР»СЏ Р¶РёСЂРЅРѕРіРѕ С‚РµРєСЃС‚Р°</b>, <i>РґР»СЏ РєСѓСЂСЃРёРІР°</i>, <u>РґР»СЏ РїРѕРґС‡РµСЂРєРЅСѓС‚РѕРіРѕ</u>, <s>РґР»СЏ Р·Р°С‡РµСЂРєРЅСѓС‚РѕРіРѕ</s>, <spoiler>РґР»СЏ СЃРїРѕР№Р»РµСЂРѕРІ</spoiler>, <code>РґР»СЏ РјРѕРЅРѕС€РёСЂРёРЅРЅРѕРіРѕ С‚РµРєСЃС‚Р°</code> Рё <pre>РґР»СЏ Р±Р»РѕРєРѕРІ РєРѕРґР°</pre>.
Р”Р»СЏ СЃСЃС‹Р»РѕРє РёСЃРїРѕР»СЊР·СѓР№ <a href="URL">С‚РµРєСЃС‚ СЃСЃС‹Р»РєРё</a>.
""".strip()

HISTORY_KEY_PREFIX = "history:"
CONFIG_KEY_PREFIX = "config:"
USER_KEY_PREFIX = "users:"

FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("PORT", 10000))

GAME_CODE_PREFIX = "games:"
GAME_TTL_SECONDS = int(os.getenv("GAME_TTL_SECONDS", 7 * 24 * 3600))
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL")
if WEBAPP_BASE_URL and WEBAPP_BASE_URL.endswith("/"):
    WEBAPP_BASE_URL = WEBAPP_BASE_URL[:-1]


