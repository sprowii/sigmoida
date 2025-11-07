# Copyright (c) 2025 sprouee
import re
from typing import List


def strip_html_tags(text: str) -> str:
    clean = re.compile(r"<.*?>")
    return re.sub(clean, "", text)


def answer_size_prompt(size: str) -> str:
    return {
        "small": "РљСЂР°С‚РєРѕ:",
        "medium": "РћС‚РІРµС‚СЊ СЂР°Р·РІРµСЂРЅСѓС‚Рѕ:",
        "large": "РћС‚РІРµС‚СЊ РјР°РєСЃРёРјР°Р»СЊРЅРѕ РїРѕРґСЂРѕР±РЅРѕ:",
    }.get(size, "")


def split_long_message(text: str, max_length: int = 4096) -> List[str]:
    if len(text) <= max_length:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 <= max_length:
            current += line + "\n"
        else:
            if current:
                parts.append(current.strip())
            current = line
    if current:
        parts.append(current.strip())
    return parts


