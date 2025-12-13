# Copyright (c) 2025 sprouee
"""Логирование действий модерации.

Requirement 8.1: Record all moderation actions with timestamp, action type, target user, acting admin, and reason
Requirement 8.4: Forward all moderation actions to log channel in real-time

БЕЗОПАСНОСТЬ:
- В Redis сохраняются псевдонимизированные ID (хэши)
- В лог-канал отправляются реальные ID (для работы модераторов)
- В application logs используются псевдонимы
"""
import asyncio
from datetime import datetime
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.logging_config import log
from app.moderation.models import ModAction
from app.moderation.storage import save_mod_action_async, load_settings_async
from app.security.data_protection import pseudonymize_id, safe_log_action


class ModLogger:
    """Логгер действий модерации с записью в Redis и пересылкой в лог-канал.
    
    Requirement 8.1: Record with timestamp, action type, target user, acting admin, and reason
    Requirement 8.4: Forward all moderation actions to log channel in real-time
    """
    
    def __init__(self, bot: Bot):
        """Инициализация логгера.
        
        Args:
            bot: Telegram Bot instance для отправки сообщений в лог-канал
        """
        self.bot = bot
    
    async def log_action(self, action: ModAction) -> None:
        """Записать действие модерации в лог и отправить в лог-канал.
        
        Requirement 8.1: Record it with timestamp, action type, target user, acting admin, and reason
        Requirement 8.4: Forward all moderation actions to log channel in real-time
        
        Args:
            action: Действие модерации для логирования
        """
        # Сохраняем в Redis
        try:
            await save_mod_action_async(action)
            # Логируем с псевдонимизированными ID для безопасности
            log.info(safe_log_action(
                action.action_type,
                action.target_user_id,
                action.chat_id,
                action.admin_id if not action.auto else None,
                action.reason
            ))
        except Exception as exc:
            log.error(f"Failed to save mod action to Redis: {exc}")
        
        # Отправляем в лог-канал если настроен
        await self._forward_to_log_channel(action)
    
    async def _forward_to_log_channel(self, action: ModAction) -> None:
        """Переслать действие в лог-канал если он настроен.
        
        Requirement 8.4: Forward all moderation actions to log channel in real-time
        """
        try:
            # Загружаем настройки чата
            settings = await load_settings_async(action.chat_id)
            
            if not settings.log_channel_id:
                return  # Лог-канал не настроен
            
            # Форматируем сообщение
            message = self._format_log_message(action)
            
            # Отправляем в канал
            await self.bot.send_message(
                chat_id=settings.log_channel_id,
                text=message,
                parse_mode=ParseMode.HTML
            )
            
        except TelegramError as exc:
            log.warning(f"Failed to forward mod action to log channel: {exc}")
        except Exception as exc:
            log.error(f"Error forwarding to log channel: {exc}")
    
    def _format_log_message(self, action: ModAction) -> str:
        """Форматировать действие модерации для отправки в лог-канал.
        
        Args:
            action: Действие модерации
            
        Returns:
            Отформатированное сообщение
        """
        # Иконки для типов действий
        action_icons = {
            "warn": "⚠️",
            "mute": "🔇",
            "unmute": "🔊",
            "ban": "🚫",
            "kick": "👢",
            "delete": "🗑",
            "filter": "🚫",
            "hold": "⏳",
            "clearwarns": "🧹",
            "spam": "🛡",
        }
        
        # Названия действий
        action_names = {
            "warn": "Предупреждение",
            "mute": "Мут",
            "unmute": "Размут",
            "ban": "Бан",
            "kick": "Кик",
            "delete": "Удаление",
            "filter": "Фильтр",
            "hold": "Задержка",
            "clearwarns": "Очистка варнов",
            "spam": "Антиспам",
        }
        
        icon = action_icons.get(action.action_type, "📋")
        action_name = action_names.get(action.action_type, action.action_type)
        
        # Форматируем время
        dt = datetime.fromtimestamp(action.timestamp)
        time_str = dt.strftime("%d.%m.%Y %H:%M:%S")
        
        # Формируем сообщение
        lines = [
            f"{icon} <b>{action_name}</b>",
            f"",
            f"👤 Пользователь: <code>{action.target_user_id}</code>",
        ]
        
        if action.auto:
            lines.append("🤖 Автоматическое действие")
        elif action.admin_id:
            lines.append(f"👮 Админ: <code>{action.admin_id}</code>")
        
        import html
        # Экранируем причину для защиты от XSS
        safe_reason = html.escape(action.reason) if action.reason else "Не указана"
        
        lines.extend([
            f"📝 Причина: {safe_reason}",
            f"🕐 Время: {time_str}",
            f"💬 Чат: <code>{action.chat_id}</code>",
        ])
        
        return "\n".join(lines)


async def log_mod_action(
    bot: Bot,
    chat_id: int,
    action_type: str,
    target_user_id: int,
    reason: str,
    admin_id: Optional[int] = None,
    auto: bool = False
) -> ModAction:
    """Удобная функция для логирования действия модерации.
    
    Создаёт ModAction, сохраняет в Redis и пересылает в лог-канал.
    
    Args:
        bot: Telegram Bot instance
        chat_id: ID чата
        action_type: Тип действия (warn, mute, ban, etc.)
        target_user_id: ID целевого пользователя
        reason: Причина действия
        admin_id: ID админа (None для автоматических действий)
        auto: True если действие автоматическое
        
    Returns:
        Созданный ModAction
    """
    action = ModAction.create(
        chat_id=chat_id,
        action_type=action_type,
        target_user_id=target_user_id,
        reason=reason,
        admin_id=admin_id,
        auto=auto
    )
    
    logger = ModLogger(bot)
    await logger.log_action(action)
    
    return action


def format_mod_log_entry(action: ModAction, include_chat: bool = False) -> str:
    """Форматировать запись лога модерации для отображения.
    
    Args:
        action: Действие модерации
        include_chat: Включать ли ID чата в вывод
        
    Returns:
        Отформатированная строка
    """
    # Иконки для типов действий
    action_icons = {
        "warn": "⚠️",
        "mute": "🔇",
        "unmute": "🔊",
        "ban": "🚫",
        "kick": "👢",
        "delete": "🗑",
        "filter": "🚫",
        "hold": "⏳",
        "clearwarns": "🧹",
        "spam": "🛡",
    }
    
    icon = action_icons.get(action.action_type, "📋")
    
    # Форматируем время
    dt = datetime.fromtimestamp(action.timestamp)
    time_str = dt.strftime("%d.%m %H:%M")
    
    # Формируем строку
    admin_str = "🤖" if action.auto else f"👮{action.admin_id}"
    
    result = f"{icon} [{time_str}] 👤{action.target_user_id} {admin_str}"
    
    if action.reason:
        # Обрезаем длинные причины
        reason = action.reason[:50] + "..." if len(action.reason) > 50 else action.reason
        result += f"\n   └ {reason}"
    
    if include_chat:
        result += f"\n   └ Чат: {action.chat_id}"
    
    return result
