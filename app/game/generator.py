# Copyright (c) 2025 sprouee
"""Utilities that orchestrate AI-driven generation of sandbox games (2D/3D)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app import config
from app.llm.client import llm_request
from app.logging_config import log
from app.storage.redis_store import store_game_payload
from app.state import ChatConfig, configs

# === НОВЫЙ ПРОМПТ ДЛЯ ДВИЖКА 2D/3D В ПЕСОЧНИЦЕ ===

PROMPT_TEMPLATE = (
    """
You are a JavaScript game generator for the Sigmoida Game Sandbox.

The game code you produce will be:
- inserted into a wrapper and executed inside a sandboxed iframe,
- not allowed to access window, document, localStorage, sessionStorage, indexedDB, eval, new Function, importScripts, Worker, SharedWorker, or <script> tags.

🔥 IMPORTANT:
Your entire answer MUST be a single JSON object, without Markdown, comments, or extra text.
Use this structure:

{{
  "title": "Short game title",
  "summary": "Short description of the mechanics (1–3 sentences, in Russian)",
  "code": "JavaScript code as a single string"
}}

The "code" value must be a string with escaped newlines (use \\n), valid JavaScript.

### GAME ENGINE API

Your code will be inserted into a wrapper and executed.
You MUST end your code with exactly one `return` statement that returns a function.

You have three allowed signatures:

1) 2D-only game:

```js
return function run2D(create2D) {{
    // your 2D game code here
}};
```

2) 3D-only game:

```js
return function run3D(create3D) {{
    // your 3D game code here
}};
```

3) Flexible game (decides 2D/3D itself):

```js
return function run(create2D, create3D) {{
    // choose whether to call create2D() or create3D()
}};
```

The engine will call this returned function automatically.

#### 2D API: create2D()

```js
const {{ canvas, ctx, utils }} = create2D();
```

- `canvas`: HTMLCanvasElement
- `ctx`: 2D rendering context (CanvasRenderingContext2D)
- `utils` has:

```ts
utils.onFrame(cb: (timeMs: number) => void): () => void;
utils.onResize(cb: (width: number, height: number) => void): () => void;
utils.clear(): void;
utils.random(): number;
utils.now(): number;
```

Use `utils.onFrame` to update the game each frame, and draw via `ctx`.

Example skeleton:

```js
return function run2D(create2D) {{
    const {{ canvas, ctx, utils }} = create2D();

    utils.onFrame((timeMs) => {{
        const t = timeMs / 1000;
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = "#111827";
        ctx.fillRect(0, 0, w, h);

        const radius = Math.min(w, h) * 0.08;
        const cx = (Math.sin(t) * 0.8 + 0.5) * w;
        const cy = h * 0.5;

        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = "#8b5cf6";
        ctx.fill();
    }});
}};
```

#### 3D API: create3D()

```js
const {{ THREE, scene, camera, renderer, utils }} = create3D();
```

- `THREE`: Three.js namespace.
- `scene`: THREE.Scene.
- `camera`: THREE.PerspectiveCamera.
- `renderer`: THREE.WebGLRenderer.
- `utils` has:

```ts
utils.onFrame(cb: (timeSeconds: number) => void): () => void;
utils.addAmbientLight(intensity?: number): THREE.AmbientLight;
utils.addDirectionalLight(
  intensity?: number,
  position?: {{ x: number; y: number; z: number }}
): THREE.DirectionalLight;
utils.loadTexture(url: string): Promise<THREE.Texture>;
utils.loadModel(url: string): Promise<THREE.Object3D>;
utils.random(): number;
utils.now(): number;
```

Example skeleton (textured cube):

```js
return function run3D(create3D) {{
    const {{ THREE, scene, camera, renderer, utils }} = create3D();

    utils.addAmbientLight(0.4);
    utils.addDirectionalLight(1.2, {{ x: 3, y: 5, z: 2 }});

    (async () => {{
        const texture = await utils.loadTexture("https://threejs.org/examples/textures/crate.gif");

        const geometry = new THREE.BoxGeometry(1.5, 1.5, 1.5);
        const material = new THREE.MeshStandardMaterial({{ map: texture }});
        const cube = new THREE.Mesh(geometry, material);
        scene.add(cube);

        camera.position.set(0, 1.5, 4);
        camera.lookAt(cube.position);

        utils.onFrame((time) => {{
            cube.rotation.x = time * 0.6;
            cube.rotation.y = time * 0.9;
        }});
    }})();
}};
```

### HARD RESTRICTIONS

Your code MUST NOT:
- reference `window`, `document`, `parent`, `top`, `opener`,
- use `localStorage`, `sessionStorage`, `indexedDB`,
- call `eval`, `Function`, `new Function`,
- use `importScripts`, `Worker`, `SharedWorker`,
- insert `<script>` tags.

If you try to use any of the above, the code will be rejected.

### TASK

User's game idea (in Russian):

«{idea}»

Generate a JSON object with fields: "title", "summary", "code".

- "title": short Russian title of the game.
- "summary": short Russian description of the game mechanic.
- "code": JavaScript code as a single string (with \\n), following the API and restrictions above.

Do NOT output anything except this JSON object.
    """
)

TWEAK_PROMPT_TEMPLATE = (
    """
You are a JavaScript game generator and editor for the Sigmoida Game Sandbox.

The game engine is the same as described below, and runs inside a sandboxed iframe.
You are given the current game code, its idea/summary, and a user request to modify it.

You must return ONLY a JSON object with fields: "title", "summary", "code".

### ENGINE API (REMINDER)

- Your code is inserted into a wrapper and executed.
- You MUST end your code with exactly one `return` statement that returns a function:

Allowed forms:

1) 2D-only:

```js
return function run2D(create2D) {{
    // ...
}};
```

2) 3D-only:

```js
return function run3D(create3D) {{
    // ...
}};
```

3) Flexible:

```js
return function run(create2D, create3D) {{
    // ...
}};
```

`create2D()` returns:

```ts
{{
    canvas: HTMLCanvasElement;
    ctx: CanvasRenderingContext2D;
    utils: {{
        onFrame(cb: (timeMs: number) => void): () => void;
        onResize(cb: (width: number, height: number) => void): () => void;
        clear(): void;
        random(): number;
        now(): number;
    }};
}}
```

`create3D()` returns:

```ts
{{
    THREE: typeof import("three");
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    utils: {{
        onFrame(cb: (timeSeconds: number) => void): () => void;
        addAmbientLight(intensity?: number): THREE.AmbientLight;
        addDirectionalLight(
            intensity?: number,
            position?: {{ x: number; y: number; z: number }}
        ): THREE.DirectionalLight;
        loadTexture(url: string): Promise<THREE.Texture>;
        loadModel(url: string): Promise<THREE.Object3D>;
        random(): number;
        now(): number;
    }};
}}
```

HARD RESTRICTIONS:
- Do NOT use window, document, parent, top, opener.
- Do NOT use localStorage, sessionStorage, indexedDB.
- Do NOT call eval, Function, new Function.
- Do NOT use importScripts, Worker, SharedWorker.
- Do NOT insert <script> tags.
- Do NOT use ES module syntax (no import/export).

### BASE INFO

Original idea:
«{idea}»

Original summary:
«{summary}»

Current code:
```javascript
{code}
```

User modification request:
«{instructions}»

### REQUIREMENTS

1. Return updated, fully working JavaScript game code compatible with the API above.
2. Preserve a playable game; do NOT introduce syntax errors.
3. Keep the same engine concept (2D or 3D) unless the user explicitly wants a different one.
4. Respect the security restrictions.

### OUTPUT FORMAT

Return ONLY a JSON object:

{{
  "title": "Game title (can be the same or updated, in Russian)",
  "summary": "Short Russian description of changes/mechanics",
  "code": "Updated JavaScript code as a single string with \\n"
}}
    """
)

JSON_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
CODE_BLOCK_PATTERN = re.compile(r"```[a-zA-Z0-9]*\n|```")

_NODE_CHECK_SUPPORTED: Optional[bool] = None


@dataclass
class GeneratedGame:
    game_id: str
    title: str
    summary: str
    code: str
    idea: str
    model: str
    share_url: Optional[str]
    author_id: Optional[int]
    author_username: Optional[str]
    author_name: Optional[str]
    created_at: float
    parent_id: Optional[str] = None
    revision: int = 1


def _extract_json(payload: str) -> Dict[str, Any]:
    payload = payload.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        match = JSON_PATTERN.search(payload)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                log.debug("JSON parse failed on extracted block: %s", exc)
    raise ValueError("Модель вернула ответ, который невозможно преобразовать в JSON.")


def _cleanup_code(code: str) -> str:
    cleaned = CODE_BLOCK_PATTERN.sub("", code or "").strip()
    return cleaned


def _escape_braces(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}").strip()


def _is_node_available() -> bool:
    global _NODE_CHECK_SUPPORTED
    if _NODE_CHECK_SUPPORTED is not None:
        return _NODE_CHECK_SUPPORTED
    try:
        subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5, check=False)
    except FileNotFoundError:
        log.warning("Node.js не найден, синтаксическая проверка игр отключена.")
        _NODE_CHECK_SUPPORTED = False
        return False
    except Exception as exc:
        log.warning("Не удалось выполнить проверку наличия Node.js: %s", exc)
        _NODE_CHECK_SUPPORTED = False
        return False
    _NODE_CHECK_SUPPORTED = True
    return True


def _sanitize_js_error(raw_error: str) -> str:
    lines = [line.strip() for line in (raw_error or "").splitlines() if line.strip()]
    for line in lines:
        if any(keyword in line for keyword in ("SyntaxError", "ReferenceError", "TypeError")):
            return line
    if lines:
        return lines[-1]
    return "Код игры содержит синтаксическую ошибку."


def _validate_js_code(code: str) -> Optional[str]:
    if not code or not code.strip():
        return "Код игры пустой."
    if not _is_node_available():
        return None
    tmp_path = None
    result = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp_file:
            tmp_file.write(code)
            tmp_path = tmp_file.name
        result = subprocess.run(
            ["node", "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        log.warning("Синтаксическая проверка игры превысила допустимое время.")
        return "Проверка синтаксиса превысила лимит времени."
    except Exception as exc:
        log.error("Не удалось выполнить синтаксическую проверку игры: %s", exc, exc_info=True)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    if result and result.returncode != 0:
        error_output = (result.stderr or result.stdout or "").strip()
        sanitized = _sanitize_js_error(error_output)
        log.warning("Синтаксическая проверка игры не пройдена: %s", sanitized or error_output)
        return sanitized or "Код игры содержит синтаксическую ошибку."
    return None


def _ensure_code_is_valid(code: str) -> None:
    validation_error = _validate_js_code(code)
    if validation_error:
        raise ValueError(
            f"Код игры не прошёл синтаксическую проверку: {validation_error}. "
            "Попробуйте уточнить запрос и повторить генерацию."
        )


def _build_prompt(idea: str) -> str:
    safe_idea = _escape_braces(idea)
    return PROMPT_TEMPLATE.format(idea=safe_idea)


def _build_tweak_prompt(idea: str, summary: str, code: str, instructions: str) -> str:
    return TWEAK_PROMPT_TEMPLATE.format(
        idea=_escape_braces(idea or "нет описания"),
        summary=_escape_braces(summary or "нет описания"),
        code=code,
        instructions=_escape_braces(instructions),
    )


def _build_share_url(game_id: str) -> Optional[str]:
    base = config.WEBAPP_BASE_URL
    if not base:
        return None
    return f"{base}/webapp/sandbox.html?game_id={game_id}"


def _normalize_chat_id(chat_id: Optional[int]) -> int:
    if chat_id is None:
        return 0
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return 0


def _resolve_provider(
    chat_id: int,
    provider: Optional[str],
    pollinations_model: Optional[str] = None,
) -> Optional[str]:
    cfg = configs.setdefault(chat_id, ChatConfig())
    normalized = (provider or "").strip().lower() if provider else ""

    if normalized in {"", "auto", "default"}:
        cfg.llm_provider = ""
        if pollinations_model and pollinations_model in config.POLLINATIONS_TEXT_MODELS:
            cfg.pollinations_text_model = pollinations_model
        return None

    if normalized in {"gemini", "openrouter", "pollinations"}:
        cfg.llm_provider = normalized
        if normalized == "pollinations" and pollinations_model in config.POLLINATIONS_TEXT_MODELS:
            cfg.pollinations_text_model = pollinations_model
        return normalized

    if pollinations_model and pollinations_model in config.POLLINATIONS_TEXT_MODELS:
        cfg.pollinations_text_model = pollinations_model

    return cfg.llm_provider or None


def generate_game(
    chat_id: Optional[int],
    idea: str,
    author_id: Optional[int] = None,
    author_username: Optional[str] = None,
    author_name: Optional[str] = None,
    provider: Optional[str] = None,
    pollinations_model: Optional[str] = None,
) -> GeneratedGame:
    if not idea or not idea.strip():
        raise ValueError("Описание игры не должно быть пустым.")

    prompt = _build_prompt(idea)
    normalized_chat_id = _normalize_chat_id(chat_id)
    provider_override = _resolve_provider(normalized_chat_id, provider, pollinations_model)
    response, model_name, _ = llm_request(normalized_chat_id, [{"text": prompt}], provider_override)
    if not response:
        raise RuntimeError("Модель не вернула код игры.")

    parsed = _extract_json(response)
    code = _cleanup_code(parsed.get("code", ""))
    if not code:
        raise ValueError("Модель не вернула JavaScript-код игры.")

    _ensure_code_is_valid(code)

    title = (parsed.get("title") or "Игра от Сигмоиды").strip()
    summary = (parsed.get("summary") or "").strip()

    game_id = uuid.uuid4().hex
    created_at = time.time()
    payload = {
        "id": game_id,
        "title": title,
        "summary": summary,
        "code": code,
        "idea": idea.strip(),
        "model": model_name,
        "created_at": created_at,
        "ttl": config.GAME_TTL_SECONDS,
        "chat_id": chat_id,
        "author_id": author_id,
        "author_username": author_username,
        "author_name": author_name,
        "parent_id": None,
        "revision": 1,
    }
    store_game_payload(game_id, payload)

    share_url = _build_share_url(game_id)

    log.info("Сгенерирована игра %s моделью %s", game_id, model_name)
    return GeneratedGame(
        game_id=game_id,
        title=title,
        summary=summary,
        code=code,
        idea=idea.strip(),
        model=model_name,
        share_url=share_url,
        author_id=author_id,
        author_username=author_username,
        author_name=author_name,
        created_at=created_at,
        parent_id=None,
        revision=1,
    )


def tweak_game(
    payload: Dict[str, Any],
    instructions: str,
    chat_id: Optional[int],
    author_id: Optional[int] = None,
    author_username: Optional[str] = None,
    author_name: Optional[str] = None,
    provider: Optional[str] = None,
    pollinations_model: Optional[str] = None,
) -> GeneratedGame:
    if not instructions or not instructions.strip():
        raise ValueError("Описание изменений не должно быть пустым.")

    base_code = payload.get("code", "")
    if not isinstance(base_code, str) or not base_code.strip():
        raise ValueError("Нельзя обновить игру без исходного кода.")

    base_idea = payload.get("idea", "")
    base_summary = payload.get("summary", "")

    prompt = _build_tweak_prompt(base_idea, base_summary, base_code, instructions)
    normalized_chat_id = _normalize_chat_id(chat_id)
    provider_override = _resolve_provider(normalized_chat_id, provider, pollinations_model)
    response, model_name, _ = llm_request(normalized_chat_id, [{"text": prompt}], provider_override)
    if not response:
        raise RuntimeError("Модель не вернула обновлённый код игры.")

    parsed = _extract_json(response)
    code = _cleanup_code(parsed.get("code", ""))
    if not code:
        raise ValueError("Модель не вернула JavaScript-код игры.")

    _ensure_code_is_valid(code)

    title = (parsed.get("title") or payload.get("title") or "Игра от Сигмоиды").strip()
    summary = (parsed.get("summary") or "").strip()

    created_at = time.time()
    game_id = uuid.uuid4().hex
    revision = int(payload.get("revision") or 1) + 1

    new_payload = {
        "id": game_id,
        "title": title,
        "summary": summary,
        "code": code,
        "idea": base_idea,
        "model": model_name,
        "created_at": created_at,
        "ttl": config.GAME_TTL_SECONDS,
        "chat_id": chat_id,
        "author_id": author_id,
        "author_username": author_username,
        "author_name": author_name,
        "parent_id": payload.get("id"),
        "revision": revision,
        "instructions": instructions.strip(),
    }
    store_game_payload(game_id, new_payload)

    share_url = _build_share_url(game_id)
    log.info("Создана новая версия игры %s -> %s", payload.get("id"), game_id)
    return GeneratedGame(
        game_id=game_id,
        title=title,
        summary=summary,
        code=code,
        idea=base_idea,
        model=model_name,
        share_url=share_url,
        author_id=author_id,
        author_username=author_username,
        author_name=author_name,
        created_at=created_at,
        parent_id=payload.get("id"),
        revision=revision,
    )
