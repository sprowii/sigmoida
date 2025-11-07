# Copyright (c) 2025 sprouee
import json
from pathlib import Path
from typing import Any, Dict

from flask import Flask, Response, abort, jsonify, render_template_string, request, send_from_directory

from app import config
from app.logging_config import log
from app.storage.redis_store import load_game_payload, redis_client

BASE_DIR = Path(__file__).resolve().parents[2]
WEBAPP_DIR = BASE_DIR / "webapp"

flask_app = Flask(__name__, static_folder=str(WEBAPP_DIR), static_url_path="/webapp")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://xn--80aqtedp.xn--p1ai/skibidicss">
    <link href="https://unpkg.com/aos@2.3.1/dist/aos.css" rel="stylesheet">
    <title>С‰Р°СЃ РІСЃС‘ Р·Р°СЂР°Р±РѕС‚Р°РµС‚, СЃР»РѕРІРѕ РїР°С†Р°РЅР°</title>
    <link rel="icon" href="https://xn--80aqtedp.xn--p1ai/favicon.ico" type="image/x-icon">
</head>
<body>
    <div class="hero">
        <div class="hero-content">
            <h1>Р‘СЂРѕ, СЏ Р·Р°РїСѓСЃС‚РёР»СЃСЏ</h1>
            <p>Р•СЃР»Рё СЏ РїСЂР°РІРёР»СЊРЅРѕ РїРѕРЅСЏР», С‚Рѕ Р±РѕС‚ РјРѕР¶РµС‚ СЂР°Р±РѕС‚Р°С‚СЊ РµС‰С‘ 15 РјРёРЅСѓС‚, РµСЃР»Рё С‚РµР±Рµ РїСЂРёС€Р»РѕСЃСЊ РїРѕСЃРµС‚РёС‚СЊ СЌС‚РѕС‚ СЃР°Р№С‚, Р° РµСЃР»Рё РѕРЅ Р±СѓРґРµС‚ СЂР°Р±РѕС‚Р°С‚СЊ Рё Р±РѕР»СЊС€Рµ, Р·РЅР°С‡РёС‚ С‚РµР±Рµ РµРіРѕ Рё РЅРµ РЅР°РґРѕ Р±С‹Р»Рѕ РїРѕСЃРµС‰Р°С‚СЊ.\n</p>
            <div class="tenor-gif-embed" data-postid="13327394754582742145" data-share-method="host" data-aspect-ratio="1" data-width="100%">
                <a href="https://tenor.com/view/cologne-wear-i-buddy-home-gif-13327394754582742145">Cologne Wear GIF</a>
                from <a href="https://tenor.com/search/cologne-gifs">Cologne GIFs</a>
            </div>
            <script type="text/javascript" async src="https://tenor.com/embed.js"></script>
            <br>
            <a href="https://xn--80aqtedp.xn--p1ai/" target="_blank" class="button-link">РЎР°Р№С‚ СЃРѕР·РґР°С‚РµР»РµР№</a>
        </div>
    </div>
</body>
</html>
""".strip()


@flask_app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)


@flask_app.route("/admin/download/history")
def download_history():
    provided_key = request.args.get("key")
    if not config.DOWNLOAD_KEY or provided_key != config.DOWNLOAD_KEY:
        abort(403)
    try:
        history_snapshot: Dict[str, Any] = {}
        for key in redis_client.scan_iter(match=f"{config.HISTORY_KEY_PREFIX}*"):
            chat_id = key.split(":", 1)[1]
            raw_value = redis_client.get(key)
            if raw_value:
                history_snapshot[chat_id] = json.loads(raw_value)

        users_snapshot: Dict[str, Any] = {}
        for key in redis_client.scan_iter(match=f"{config.USER_KEY_PREFIX}*"):
            chat_id = key.split(":", 1)[1]
            raw_value = redis_client.get(key)
            if raw_value:
                users_snapshot[chat_id] = json.loads(raw_value)

        response_payload = {
            "history": history_snapshot,
            "users": users_snapshot,
        }
        response = Response(json.dumps(response_payload, ensure_ascii=False, indent=2), mimetype="application/json")
        response.headers["Content-Disposition"] = "attachment; filename=history.json"
        return response
    except Exception as exc:
        log.error(f"РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РіСЂСѓР·РёС‚СЊ РёСЃС‚РѕСЂРёСЋ РёР· Redis: {exc}", exc_info=True)
        abort(500)


@flask_app.route("/webapp/sandbox")
def sandbox_entrypoint():
    if not WEBAPP_DIR.exists():
        abort(404)
    return send_from_directory(flask_app.static_folder, "sandbox.html")


@flask_app.route("/api/games/<string:game_id>")
def fetch_game(game_id: str):
    payload = load_game_payload(game_id)
    if not payload:
        abort(404)

    return jsonify(
        {
            "id": payload.get("id", game_id),
            "title": payload.get("title"),
            "summary": payload.get("summary"),
            "code": payload.get("code"),
        }
    )


