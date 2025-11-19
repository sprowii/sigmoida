# Copyright (c) 2025 sprouee
"""Rate limiting middleware для защиты от спама."""

import time
from typing import Dict, Tuple

from app.logging_config import log

# Хранилище: {user_id: (timestamp, count)}
_rate_limits: Dict[int, Tuple[float, int]] = {}

# Настройки
MAX_REQUESTS_PER_MINUTE = 10
MAX_REQUESTS_PER_HOUR = 100
CLEANUP_INTERVAL = 300  # Очистка каждые 5 минут
_last_cleanup = time.time()


def _cleanup_old_entries():
    """Удаляет старые записи для экономии памяти."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < CLEANUP_INTERVAL:
        return
    
    cutoff = now - 3600  # Удаляем записи старше часа
    to_remove = [uid for uid, (ts, _) in _rate_limits.items() if ts < cutoff]
    for uid in to_remove:
        del _rate_limits[uid]
    
    _last_cleanup = now
    if to_remove:
        log.debug(f"Cleaned up {len(to_remove)} old rate limit entries")


def check_rate_limit(user_id: int) -> Tuple[bool, str]:
    """
    Проверяет, не превышен ли лимит запросов.
    
    Returns:
        (allowed, message): allowed=True если можно, message - причина отказа
    """
    _cleanup_old_entries()
    
    now = time.time()
    
    if user_id not in _rate_limits:
        _rate_limits[user_id] = (now, 1)
        return True, ""
    
    last_time, count = _rate_limits[user_id]
    time_diff = now - last_time
    
    # Проверка минутного лимита
    if time_diff < 60:
        if count >= MAX_REQUESTS_PER_MINUTE:
            wait_time = int(60 - time_diff)
            return False, f"⏱️ Слишком много запросов. Подожди {wait_time} сек."
        _rate_limits[user_id] = (last_time, count + 1)
        return True, ""
    
    # Проверка часового лимита
    if time_diff < 3600:
        if count >= MAX_REQUESTS_PER_HOUR:
            wait_time = int((3600 - time_diff) / 60)
            return False, f"⏱️ Превышен часовой лимит. Подожди {wait_time} мин."
        _rate_limits[user_id] = (last_time, count + 1)
        return True, ""
    
    # Сброс счетчика после часа
    _rate_limits[user_id] = (now, 1)
    return True, ""


def get_user_stats(user_id: int) -> Dict[str, int]:
    """Возвращает статистику пользователя."""
    if user_id not in _rate_limits:
        return {"requests": 0, "time_window": 0}
    
    last_time, count = _rate_limits[user_id]
    time_diff = int(time.time() - last_time)
    
    return {
        "requests": count,
        "time_window": time_diff,
    }
