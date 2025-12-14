# Copyright (c) 2025 sprowii
"""Антиспам система для модерации чатов.

Requirements:
- 2.1: Mute user for 5 minutes if they send more than 5 messages within 10 seconds
- 2.2: Delete messages with known spam patterns (crypto scams, adult content links)
- 2.3: Hold messages with links from users who joined within 24 hours
- 2.4: Log all spam detection actions

Uses Redis ZSET for storing message timestamps per user.
"""
import asyncio
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Tuple

from app.logging_config import log
from app.moderation.models import ChatModSettings, ModAction
from app.moderation.storage import redis_client, save_mod_action_async

# Redis key prefixes
FLOOD_TIMESTAMPS_PREFIX = "flood_ts:"  # ZSET: message timestamps per user
USER_JOIN_TIME_PREFIX = "user_join:"   # STRING: user join timestamp


class SpamAction(Enum):
    """Действие при обнаружении спама."""
    NONE = "none"           # Нет спама
    MUTE = "mute"           # Замутить пользователя
    DELETE = "delete"       # Удалить сообщение
    WARN = "warn"           # Предупредить пользователя
    HOLD = "hold"           # Задержать для проверки админом


@dataclass
class SpamCheckResult:
    """Результат проверки на спам."""
    action: SpamAction
    reason: str
    should_delete: bool = False
    mute_duration_min: int = 0
    message_ids_to_delete: List[int] = None
    
    def __post_init__(self):
        if self.message_ids_to_delete is None:
            self.message_ids_to_delete = []


# ============================================================================
# SPAM PATTERNS (Requirement 2.2)
# ============================================================================

# Известные скам-домены для крипто
CRYPTO_SCAM_DOMAINS = [
    r"binance-?\w*\.(?:com|org|net|io)",
    r"coinbase-?\w*\.(?:com|org|net|io)",
    r"metamask-?\w*\.(?:com|org|net|io)",
    r"trustwallet-?\w*\.(?:com|org|net|io)",
    r"airdrop-?\w*\.(?:com|org|net|io)",
    r"claim-?\w*\.(?:com|org|net|io)",
    r"free-?crypto\.(?:com|org|net|io)",
    r"earn-?btc\.(?:com|org|net|io)",
]

# Паттерны для adult контента
ADULT_PATTERNS = [
    r"onlyfans\.com",
    r"pornhub\.com",
    r"xvideos\.com",
    r"chaturbate\.com",
    r"livejasmin\.com",
    r"stripchat\.com",
]

# Общие спам-паттерны
SPAM_PATTERNS = [
    r"(?:заработ|зароб)[а-яё]*\s*(?:от|до)?\s*\d+",  # "заработок от 1000"
    r"(?:пассивн|легк)[а-яё]*\s*(?:доход|заработ)",   # "пассивный доход"
    r"(?:работа|вакансия)\s*(?:на\s*дому|удалённ)",   # "работа на дому"
    r"(?:инвест|вложи)[а-яё]*\s*(?:от)?\s*\d+",       # "инвестируй от 100"
    r"(?:казино|casino|slots?|рулетк)",               # казино
    r"(?:ставки|betting|1xbet|fonbet)",               # ставки
]

# Компилируем регулярные выражения
CRYPTO_SCAM_REGEX = re.compile(
    "|".join(CRYPTO_SCAM_DOMAINS),
    re.IGNORECASE
)

ADULT_REGEX = re.compile(
    "|".join(ADULT_PATTERNS),
    re.IGNORECASE
)

SPAM_REGEX = re.compile(
    "|".join(SPAM_PATTERNS),
    re.IGNORECASE
)

# Регулярка для извлечения ссылок
URL_REGEX = re.compile(
    r"https?://[^\s<>\"']+|"
    r"(?:www\.)?[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:/[^\s<>\"']*)?",
    re.IGNORECASE
)


class SpamFilter:
    """Фильтр спама для чата.
    
    Requirement 2.1: Detect flood (more than N messages in M seconds)
    Requirement 2.2: Detect spam patterns (crypto scams, adult links)
    Requirement 2.3: Filter links from newbies
    """
    
    def __init__(self, settings: ChatModSettings):
        """
        Args:
            settings: Настройки модерации чата
        """
        self.settings = settings
        self.chat_id = settings.chat_id
    
    # ========================================================================
    # FLOOD DETECTION (Requirement 2.1)
    # ========================================================================
    
    def _get_flood_key(self, user_id: int) -> str:
        """Получить ключ Redis для timestamps сообщений пользователя."""
        return f"{FLOOD_TIMESTAMPS_PREFIX}{self.chat_id}:{user_id}"
    
    def record_message(self, user_id: int, timestamp: Optional[float] = None) -> None:
        """Записать timestamp сообщения пользователя.
        
        Args:
            user_id: ID пользователя
            timestamp: Время сообщения (по умолчанию текущее)
        """
        if timestamp is None:
            timestamp = time.time()
        
        key = self._get_flood_key(user_id)
        try:
            with redis_client.pipeline() as pipe:
                # Добавляем timestamp в ZSET (score = timestamp, member = timestamp)
                pipe.zadd(key, {str(timestamp): timestamp})
                # Удаляем старые записи (старше time_window * 2)
                cutoff = timestamp - (self.settings.spam_time_window_sec * 2)
                pipe.zremrangebyscore(key, "-inf", cutoff)
                # Устанавливаем TTL на ключ
                pipe.expire(key, self.settings.spam_time_window_sec * 3)
                pipe.execute()
        except Exception as exc:
            log.error(f"Ошибка записи timestamp сообщения: {exc}")
    
    def check_flood(self, user_id: int, timestamp: Optional[float] = None) -> bool:
        """Проверить, флудит ли пользователь.
        
        Requirement 2.1: More than spam_message_limit messages within spam_time_window_sec
        
        Args:
            user_id: ID пользователя
            timestamp: Текущее время (по умолчанию time.time())
            
        Returns:
            True если пользователь флудит, False иначе
        """
        if timestamp is None:
            timestamp = time.time()
        
        key = self._get_flood_key(user_id)
        window_start = timestamp - self.settings.spam_time_window_sec
        
        try:
            # Считаем сообщения в окне времени
            count = redis_client.zcount(key, window_start, timestamp)
            return count >= self.settings.spam_message_limit
        except Exception as exc:
            log.error(f"Ошибка проверки флуда: {exc}")
            return False
    
    def get_flood_message_ids(self, user_id: int, timestamp: Optional[float] = None) -> List[str]:
        """Получить timestamps сообщений в окне флуда для удаления.
        
        Args:
            user_id: ID пользователя
            timestamp: Текущее время
            
        Returns:
            Список timestamps сообщений в окне флуда
        """
        if timestamp is None:
            timestamp = time.time()
        
        key = self._get_flood_key(user_id)
        window_start = timestamp - self.settings.spam_time_window_sec
        
        try:
            return redis_client.zrangebyscore(key, window_start, timestamp)
        except Exception as exc:
            log.error(f"Ошибка получения сообщений флуда: {exc}")
            return []
    
    def clear_flood_history(self, user_id: int) -> None:
        """Очистить историю сообщений пользователя."""
        key = self._get_flood_key(user_id)
        try:
            redis_client.delete(key)
        except Exception as exc:
            log.error(f"Ошибка очистки истории флуда: {exc}")
    
    # ========================================================================
    # SPAM PATTERN DETECTION (Requirement 2.2)
    # ========================================================================
    
    def check_spam_patterns(self, text: str) -> Optional[str]:
        """Проверить текст на известные спам-паттерны.
        
        Requirement 2.2: Detect crypto scams, adult content links
        
        Args:
            text: Текст сообщения
            
        Returns:
            Причина если найден спам, None иначе
        """
        if not text:
            return None
        
        # Проверяем крипто-скам домены
        if CRYPTO_SCAM_REGEX.search(text):
            return "crypto_scam"
        
        # Проверяем adult контент
        if ADULT_REGEX.search(text):
            return "adult_content"
        
        # Проверяем общие спам-паттерны
        if SPAM_REGEX.search(text):
            return "spam_pattern"
        
        return None
    
    # ========================================================================
    # LINK FILTER FOR NEWBIES (Requirement 2.3)
    # ========================================================================
    
    def _get_join_time_key(self, user_id: int) -> str:
        """Получить ключ Redis для времени входа пользователя."""
        return f"{USER_JOIN_TIME_PREFIX}{self.chat_id}:{user_id}"
    
    def record_user_join(self, user_id: int, timestamp: Optional[float] = None) -> None:
        """Записать время входа пользователя в чат.
        
        Args:
            user_id: ID пользователя
            timestamp: Время входа (по умолчанию текущее)
        """
        if timestamp is None:
            timestamp = time.time()
        
        key = self._get_join_time_key(user_id)
        try:
            # Храним время входа с TTL = newbie_hours + 1 час
            ttl = (self.settings.link_newbie_hours + 1) * 3600
            redis_client.setex(key, ttl, str(timestamp))
        except Exception as exc:
            log.error(f"Ошибка записи времени входа: {exc}")
    
    def get_user_join_time(self, user_id: int) -> Optional[float]:
        """Получить время входа пользователя в чат.
        
        Returns:
            Timestamp входа или None если не найден
        """
        key = self._get_join_time_key(user_id)
        try:
            value = redis_client.get(key)
            return float(value) if value else None
        except Exception as exc:
            log.error(f"Ошибка получения времени входа: {exc}")
            return None
    
    def is_newbie(self, user_id: int, timestamp: Optional[float] = None) -> bool:
        """Проверить, является ли пользователь новичком.
        
        Args:
            user_id: ID пользователя
            timestamp: Текущее время
            
        Returns:
            True если пользователь в чате меньше link_newbie_hours часов
        """
        if timestamp is None:
            timestamp = time.time()
        
        join_time = self.get_user_join_time(user_id)
        if join_time is None:
            # Если нет записи о входе, считаем новичком
            return True
        
        hours_in_chat = (timestamp - join_time) / 3600
        return hours_in_chat < self.settings.link_newbie_hours
    
    def extract_links(self, text: str) -> List[str]:
        """Извлечь все ссылки из текста.
        
        Args:
            text: Текст сообщения
            
        Returns:
            Список найденных ссылок
        """
        if not text:
            return []
        return URL_REGEX.findall(text)
    
    def is_link_whitelisted(self, link: str) -> bool:
        """Проверить, находится ли ссылка в whitelist.
        
        Args:
            link: URL для проверки
            
        Returns:
            True если домен в whitelist
        """
        link_lower = link.lower()
        for domain in self.settings.link_whitelist:
            if domain.lower() in link_lower:
                return True
        return False
    
    def check_newbie_links(self, user_id: int, text: str, timestamp: Optional[float] = None) -> Optional[str]:
        """Проверить ссылки от новичка.
        
        Requirement 2.3: Hold messages with links from users who joined within 24 hours
        
        Args:
            user_id: ID пользователя
            text: Текст сообщения
            timestamp: Текущее время
            
        Returns:
            Действие (link_action из настроек) если нужно, None иначе
        """
        if not self.settings.link_filter_enabled:
            return None
        
        if not self.is_newbie(user_id, timestamp):
            return None
        
        links = self.extract_links(text)
        if not links:
            return None
        
        # Проверяем, есть ли ссылки не из whitelist
        for link in links:
            if not self.is_link_whitelisted(link):
                return self.settings.link_action  # "delete", "warn", or "hold"
        
        return None

    
    # ========================================================================
    # COMBINED CHECK (All requirements)
    # ========================================================================
    
    async def check_message(
        self,
        user_id: int,
        text: str,
        message_id: int,
        timestamp: Optional[float] = None
    ) -> SpamCheckResult:
        """Полная проверка сообщения на спам.
        
        Выполняет все проверки:
        - Flood detection (Requirement 2.1)
        - Spam patterns (Requirement 2.2)
        - Newbie links (Requirement 2.3)
        
        Args:
            user_id: ID пользователя
            text: Текст сообщения
            message_id: ID сообщения
            timestamp: Время сообщения
            
        Returns:
            SpamCheckResult с действием и причиной
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Проверка 1: Спам-паттерны (Requirement 2.2)
        spam_reason = self.check_spam_patterns(text)
        if spam_reason:
            return SpamCheckResult(
                action=SpamAction.DELETE,
                reason=spam_reason,
                should_delete=True
            )
        
        # Проверка 2: Ссылки от новичков (Requirement 2.3)
        if self.settings.link_filter_enabled:
            link_action = self.check_newbie_links(user_id, text, timestamp)
            if link_action:
                action_map = {
                    "delete": SpamAction.DELETE,
                    "warn": SpamAction.WARN,
                    "hold": SpamAction.HOLD,
                }
                return SpamCheckResult(
                    action=action_map.get(link_action, SpamAction.HOLD),
                    reason="newbie_link",
                    should_delete=(link_action == "delete")
                )
        
        # Проверка 3: Флуд (Requirement 2.1)
        if self.settings.spam_enabled:
            # Записываем сообщение
            self.record_message(user_id, timestamp)
            
            # Проверяем флуд
            if self.check_flood(user_id, timestamp):
                return SpamCheckResult(
                    action=SpamAction.MUTE,
                    reason="flood",
                    should_delete=True,
                    mute_duration_min=self.settings.spam_mute_duration_min
                )
        
        return SpamCheckResult(action=SpamAction.NONE, reason="")


# ============================================================================
# ASYNC HELPER FUNCTIONS
# ============================================================================

async def check_spam_async(
    settings: ChatModSettings,
    user_id: int,
    text: str,
    message_id: int,
    timestamp: Optional[float] = None
) -> SpamCheckResult:
    """Асинхронная проверка сообщения на спам.
    
    Args:
        settings: Настройки модерации чата
        user_id: ID пользователя
        text: Текст сообщения
        message_id: ID сообщения
        timestamp: Время сообщения
        
    Returns:
        SpamCheckResult с действием и причиной
    """
    spam_filter = SpamFilter(settings)
    return await spam_filter.check_message(user_id, text, message_id, timestamp)


def record_user_join_sync(chat_id: int, user_id: int, timestamp: Optional[float] = None) -> None:
    """Синхронная запись времени входа пользователя.
    
    Используется при обработке события new_chat_members.
    """
    if timestamp is None:
        timestamp = time.time()
    
    key = f"{USER_JOIN_TIME_PREFIX}{chat_id}:{user_id}"
    try:
        # Храним 7 дней (максимальный newbie_hours = 168)
        redis_client.setex(key, 7 * 24 * 3600, str(timestamp))
    except Exception as exc:
        log.error(f"Ошибка записи времени входа: {exc}")


async def record_user_join_async(chat_id: int, user_id: int, timestamp: Optional[float] = None) -> None:
    """Асинхронная запись времени входа пользователя."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, record_user_join_sync, chat_id, user_id, timestamp)


def get_user_join_time_sync(chat_id: int, user_id: int) -> Optional[float]:
    """Синхронное получение времени входа пользователя."""
    key = f"{USER_JOIN_TIME_PREFIX}{chat_id}:{user_id}"
    try:
        value = redis_client.get(key)
        return float(value) if value else None
    except Exception as exc:
        log.error(f"Ошибка получения времени входа: {exc}")
        return None


# ============================================================================
# SPAM REASON MESSAGES
# ============================================================================

SPAM_REASON_MESSAGES = {
    "flood": "🚫 Флуд: слишком много сообщений за короткое время",
    "crypto_scam": "🚫 Обнаружена подозрительная крипто-ссылка",
    "adult_content": "🚫 Обнаружена ссылка на запрещённый контент",
    "spam_pattern": "🚫 Обнаружен спам-паттерн",
    "newbie_link": "⏳ Ссылки от новых участников требуют проверки",
}


def get_spam_reason_message(reason: str) -> str:
    """Получить человекочитаемое сообщение о причине спама."""
    return SPAM_REASON_MESSAGES.get(reason, f"🚫 Спам: {reason}")
