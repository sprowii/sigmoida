# Copyright (c) 2025 sprouee
"""Саммаризация текста и статей."""

import re
from typing import Optional
import requests

from app.llm.client import llm_request
from app.logging_config import log


def summarize_text(chat_id: int, text: str, max_length: int = 500) -> Optional[str]:
    """
    Создает краткое содержание текста.
    
    Args:
        chat_id: ID чата
        text: Текст для саммаризации
        max_length: Максимальная длина саммари
    
    Returns:
        Краткое содержание или None
    """
    if len(text) < 200:
        return "Текст слишком короткий для саммаризации (минимум 200 символов)"
    
    prompt = (
        f"Сделай краткое содержание следующего текста (максимум {max_length} символов). "
        f"Выдели главные мысли:\n\n{text[:10000]}"  # Лимит на входной текст
    )
    
    try:
        response, _, _ = llm_request(chat_id, [{"text": prompt}], None)
        return response
    except Exception as exc:
        log.error(f"Summarization error: {exc}")
        return None


def extract_text_from_url(url: str) -> Optional[str]:
    """
    Извлекает текст из URL (простая версия).
    
    Args:
        url: URL статьи
    
    Returns:
        Извлеченный текст или None
    """
    try:
        # Проверяем URL
        if not url.startswith(('http://', 'https://')):
            return None
        
        # Получаем контент
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Простое извлечение текста (убираем HTML теги)
        html = response.text
        
        # Убираем скрипты и стили
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        
        # Убираем все HTML теги
        text = re.sub(r'<[^>]+>', ' ', html)
        
        # Убираем лишние пробелы
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Берем первые 15000 символов
        return text[:15000] if text else None
        
    except Exception as exc:
        log.error(f"URL extraction error: {exc}")
        return None


def summarize_url(chat_id: int, url: str) -> Optional[str]:
    """
    Создает саммари статьи по URL.
    
    Args:
        chat_id: ID чата
        url: URL статьи
    
    Returns:
        Краткое содержание или None
    """
    text = extract_text_from_url(url)
    
    if not text:
        return "Не удалось извлечь текст из URL"
    
    if len(text) < 200:
        return "Текст на странице слишком короткий"
    
    return summarize_text(chat_id, text, max_length=800)
