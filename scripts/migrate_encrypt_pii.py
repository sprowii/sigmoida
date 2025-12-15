#!/usr/bin/env python3
"""Миграция: шифрование старых персональных данных в Redis.

Standalone скрипт — можно запускать откуда угодно.

Запуск:
    python migrate_encrypt_pii.py

Перед запуском задай переменные окружения (или создай .env рядом со скриптом):
    - REDIS_URL
    - DATA_ENCRYPTION_KEY
    - DATA_HASH_SALT (опционально)
"""
import base64
import json
import os
import sys

# Пытаемся загрузить .env если есть
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv не обязателен

import redis
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# ============================================================================
# КОНФИГУРАЦИЯ (из переменных окружения)
# ============================================================================

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    print("❌ REDIS_URL не задан!")
    print("   Задай переменную окружения или создай .env файл")
    sys.exit(1)

DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY")
if not DATA_ENCRYPTION_KEY:
    print("❌ DATA_ENCRYPTION_KEY не задан!")
    print("   Задай переменную окружения или создай .env файл")
    sys.exit(1)

DATA_HASH_SALT = os.getenv("DATA_HASH_SALT", "default_salt_change_me")

USER_KEY_PREFIX = "users:"
HISTORY_KEY_PREFIX = "history:"


# ============================================================================
# ШИФРОВАНИЕ
# ============================================================================

def _create_fernet(key: str, salt: str) -> Fernet:
    """Создаёт Fernet из ключа."""
    try:
        # Если ключ в формате Fernet
        return Fernet(key.encode())
    except Exception:
        # Если обычный пароль — деривируем ключ
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode()[:16],
            iterations=100000,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(key.encode()))
        return Fernet(derived_key)


def encrypt_value(fernet: Fernet, value: str) -> str:
    """Шифрует строку."""
    encrypted = fernet.encrypt(value.encode())
    return f"enc:{encrypted.decode()}"


def encrypt_pii(fernet: Fernet, profile: dict) -> dict:
    """Шифрует PII поля в профиле, включая ID."""
    result = profile.copy()
    # Шифруем ВСЕ персональные данные включая ID!
    pii_fields = ["id", "username", "first_name", "last_name", "full_name", "language_code"]
    
    for field in pii_fields:
        value = result.get(field)
        if value is not None:
            # Проверяем, не зашифровано ли уже
            if isinstance(value, str) and value.startswith("enc:"):
                continue
            result[field] = encrypt_value(fernet, str(value))
    
    return result


def encrypt_history_data(fernet: Fernet, history_json: str) -> str:
    """Шифрует историю диалогов."""
    if history_json.startswith("enc:"):
        return history_json  # Уже зашифровано
    encrypted = fernet.encrypt(history_json.encode())
    return f"enc:{encrypted.decode()}"


# ============================================================================
# МИГРАЦИЯ
# ============================================================================

def migrate():
    print(f"🔐 Создаю шифровальщик...")
    fernet = _create_fernet(DATA_ENCRYPTION_KEY, DATA_HASH_SALT)
    print("   ✅ OK")
    print()
    
    # Подключаемся к Redis
    print(f"📡 Подключаюсь к Redis...")
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
        print("   ✅ Подключено")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        sys.exit(1)
    print()
    
    # Находим все ключи профилей
    keys = list(client.scan_iter(match=f"{USER_KEY_PREFIX}*"))
    print(f"📊 Найдено {len(keys)} чатов с профилями")
    print()
    
    if not keys:
        print("Нечего мигрировать!")
        return
    
    # Статистика
    total_profiles = 0
    encrypted_profiles = 0
    already_encrypted = 0
    errors = 0
    
    for key in keys:
        chat_id = key.split(":", 1)[1]
        
        try:
            raw = client.get(key)
            if not raw:
                continue
            
            profiles = json.loads(raw)
            updated = False
            
            for user_id, profile in profiles.items():
                total_profiles += 1
                
                # Проверяем, есть ли незашифрованные PII (включая id!)
                needs_encryption = False
                pii_fields = ["id", "username", "first_name", "last_name", "full_name"]
                
                for field in pii_fields:
                    value = profile.get(field)
                    if value is not None:
                        if not (isinstance(value, str) and value.startswith("enc:")):
                            needs_encryption = True
                            break
                
                if needs_encryption:
                    profiles[user_id] = encrypt_pii(fernet, profile)
                    encrypted_profiles += 1
                    updated = True
                else:
                    already_encrypted += 1
            
            if updated:
                client.set(key, json.dumps(profiles, ensure_ascii=False))
                print(f"  ✅ Чат {chat_id}: зашифровано")
        
        except Exception as e:
            print(f"  ❌ Ошибка в чате {chat_id}: {e}")
            errors += 1
    
    # Итоги
    print()
    print("=" * 50)
    print("📊 ИТОГИ")
    print("=" * 50)
    print(f"   Всего профилей: {total_profiles}")
    print(f"   Зашифровано: {encrypted_profiles}")
    print(f"   Уже были зашифрованы: {already_encrypted}")
    print(f"   Ошибок: {errors}")
    print()
    
    if errors == 0:
        print("✅ Миграция профилей завершена!")
    else:
        print("⚠️ Миграция профилей завершена с ошибками")
    
    # ========== ШИФРОВАНИЕ ИСТОРИИ ==========
    print()
    print("=" * 50)
    print("📜 ШИФРОВАНИЕ ИСТОРИИ ДИАЛОГОВ")
    print("=" * 50)
    print()
    
    history_keys = list(client.scan_iter(match=f"{HISTORY_KEY_PREFIX}*"))
    print(f"📊 Найдено {len(history_keys)} историй")
    
    history_encrypted = 0
    history_already = 0
    history_errors = 0
    
    for key in history_keys:
        chat_id = key.split(":", 1)[1]
        try:
            raw = client.get(key)
            if not raw:
                continue
            
            if raw.startswith("enc:"):
                history_already += 1
                continue
            
            # Шифруем
            encrypted = encrypt_history_data(fernet, raw)
            client.set(key, encrypted)
            history_encrypted += 1
            print(f"  ✅ Чат {chat_id}: история зашифрована")
        except Exception as e:
            print(f"  ❌ Ошибка в чате {chat_id}: {e}")
            history_errors += 1
    
    print()
    print(f"   Зашифровано историй: {history_encrypted}")
    print(f"   Уже были зашифрованы: {history_already}")
    print(f"   Ошибок: {history_errors}")
    
    # Итоговый статус
    print()
    print("=" * 50)
    total_errors = errors + history_errors
    if total_errors == 0:
        print("✅ ВСЯ МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО!")
    else:
        print(f"⚠️ Миграция завершена с {total_errors} ошибками")


if __name__ == "__main__":
    print("=" * 50)
    print("🔐 МИГРАЦИЯ: Шифрование персональных данных")
    print("   (включая user_id!)")
    print("=" * 50)
    print()
    
    answer = input("Продолжить? (y/n): ").strip().lower()
    if answer != "y":
        print("Отменено")
        sys.exit(0)
    
    print()
    migrate()
