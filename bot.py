import logging
import os
import sqlite3
import random
import asyncio
import html
import re
import functools
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, CallbackQueryHandler
from telegram.ext import filters
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from dotenv import load_dotenv

async def safe_reply(update: Update, context: CallbackContext, text: str, *, reply_markup=None, parse_mode: Optional[str] = "HTML"):
    try:
        if getattr(update, "callback_query", None):
            q = update.callback_query
            if getattr(q, "message", None):
                try:
                    await q.edit_message_text(
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Ошибка редактирования сообщения: {e}")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup
                    )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
        else:
            # Простая отправка сообщения
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
    except Exception as e:
        # Логируем ошибку с деталями
        logger.error(f"Ошибка отправки сообщения пользователю {update.effective_chat.id}: {e}")
        
        # Пытаемся отправить без HTML если была ошибка парсинга
        if "Can't parse entities" in str(e):
            try:
                # Убираем HTML теги для простого текста
                plain_text = re.sub(r'<[^>]+>', '', text)  # Удаляем HTML теги
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=plain_text,
                    reply_markup=reply_markup
                )
                logger.info("Сообщение отправлено без HTML")
            except Exception as e2:
                logger.error(f"Не удалось отправить даже без HTML: {e2}")

async def safe_send_video_or_text(update, context, *, video_id, caption_text, reply_markup=None, parse_mode: Optional[str] = "HTML"):
    try:
        if video_id:
            await context.bot.send_video(chat_id=update.effective_chat.id, video=video_id, caption=caption_text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await safe_reply(update, context, caption_text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        await safe_reply(update, context, caption_text, reply_markup=reply_markup, parse_mode=parse_mode)

# Настройки
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
if not TOKEN:
    raise ValueError("❌ Ошибка: BOT_TOKEN не найден в .env файле!")

# Константы для callback_data
CONFIRM_CALLBACK = "confirm_callback"
CANCEL_CALLBACK = "cancel_callback"
HELP_BACK_CALLBACK = "help_back_callback"
APPROVE_SCREENSHOT = "approve_screenshot"
REJECT_SCREENSHOT = "reject_screenshot"
USER_LIST_PAGE = "user_list_page"
CALLED_LIST_PAGE = "called_list_page"
SCREENSHOT_LIST_PAGE = "screenshot_list_page"
EDIT_INFO = "edit_info"
EDIT_MORNING = "edit_morning"
EDIT_EVENING = "edit_evening"
BACK_TO_EDITOR = "back_to_editor"
SEND_MORNING_NOW = "send_morning_now"
SEND_EVENING_NOW = "send_evening_now"

# Константы для статусов заданий
TASK_STATUS = {
    "GET_TASK": "get_task",           # Пользователь только начал
    "CONFIRM_CALL": "confirm_call",   # Получил задание, должен подтвердить звонок
    "WAITING_REVIEW_DAY": "waiting_review_day",  # Подтвердил звонок, ждет утра
    "WAITING_REVIEW_EVENING": "waiting_review_evening",  # Получил утреннее сообщение, ждет вечера
    "SEND_SCREENSHOT": "send_screenshot",  # Должен отправить скриншот
    "WAITING_ADMIN_REVIEW": "waiting_admin_review",  # Скриншот отправлен на проверку
    "COMPLETED": "completed",         # Задание завершено успешно
    "SCREENSHOT_REJECTED": "screenshot_rejected",  # Скриншот отклонен
    "CANCELLED": "cancelled"          # Задание отменено
}

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Простой кэш для частых запросов
class SimpleCache:
    def __init__(self, ttl_seconds=300):
        self.cache: Dict[str, tuple] = {}
        self.ttl = timedelta(seconds=ttl_seconds)
    
    def get(self, key):
        if key in self.cache:
            value, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now())

# Глобальный кэш
cache = SimpleCache()

# Декоратор для кэширования
def cached(ttl_seconds=300):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Создаем ключ кэша на основе аргументов
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # Пытаемся получить из кэша
            cached_result = cache.get(key)
            if cached_result is not None:
                return cached_result
            
            # Если нет в кэше, выполняем функцию
            result = func(*args, **kwargs)
            
            # Сохраняем в кэш
            cache.set(key, result)
            
            return result
        return wrapper
    return decorator

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Таблица для категорий заданий (ДОБАВЬТЕ ЭТО ПЕРЕД create table для photos)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS task_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Добавляем столбец category_id в таблицу photos
    try:
        cursor.execute("SELECT category_id FROM photos LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE photos ADD COLUMN category_id INTEGER DEFAULT 1')
        # Устанавливаем категорию по умолчанию для существующих фото
        cursor.execute('UPDATE photos SET category_id = 1 WHERE category_id IS NULL')
        logger.info("Добавлен столбец category_id в photos")
    
    # Добавляем категорию по умолчанию
    cursor.execute('''
    INSERT OR IGNORE INTO task_categories (id, name, description) 
    VALUES (1, 'По умолчанию', 'Категория по умолчанию')
    ''')
    
    # Таблица для фото
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для инструкции
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS instruction (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        text TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для статуса пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_progress (
        user_id INTEGER PRIMARY KEY,
        photo_id INTEGER,
        called BOOLEAN DEFAULT FALSE,
        called_confirmed BOOLEAN DEFAULT FALSE,
        called_confirmed_at TIMESTAMP,
        review_sent BOOLEAN DEFAULT FALSE,
        review_sent_at TIMESTAMP,
        morning_message_sent BOOLEAN DEFAULT FALSE,
        evening_reminder_sent BOOLEAN DEFAULT FALSE,
        screenshot_sent BOOLEAN DEFAULT FALSE,
        screenshot_id TEXT,
        screenshot_sent_at TIMESTAMP,
        screenshot_status TEXT DEFAULT 'not_sent',
        admin_review_comment TEXT,
        assigned_at TIMESTAMP,
        completed_at TIMESTAMP,
        current_step TEXT DEFAULT 'get_task',
        multi_accounts BOOLEAN DEFAULT FALSE,
        accounts_requested INTEGER DEFAULT 0,
        photos_sent TEXT,
        balance INTEGER DEFAULT 0,
        total_earned INTEGER DEFAULT 0,
        tasks_completed INTEGER DEFAULT 0
        successful_refs INTEGER DEFAULT 0,      
        is_ambassador BOOLEAN DEFAULT FALSE    
    )
    ''')
    
    # Таблица для информации о пользователях
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone_number TEXT,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для утренних сообщений
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS morning_messages (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        text TEXT NOT NULL,
        video_id TEXT,
        send_time TEXT DEFAULT '09:00',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для вечерних напоминаний
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS evening_reminders (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        text TEXT NOT NULL,
        video_id TEXT,
        send_time TEXT DEFAULT '20:00',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для кнопок помощи в заданиях
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS task_help_buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        order_index INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица для реферальной системы
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL UNIQUE,  
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        bonus_paid BOOLEAN DEFAULT FALSE,
        FOREIGN KEY (referrer_id) REFERENCES users (user_id),
        FOREIGN KEY (referred_id) REFERENCES users (user_id)
    )
    ''')
    
    # Таблица для уведомлений
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        is_read BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    try:
        # Проверяем существование столбца notification_type
        cursor.execute("SELECT notification_type FROM notifications LIMIT 1")
    except sqlite3.OperationalError:
        # Если столбца нет - добавляем
        cursor.execute('ALTER TABLE notifications ADD COLUMN notification_type TEXT DEFAULT "info"')
        # Устанавливаем значение по умолчанию для существующих записей
        cursor.execute('UPDATE notifications SET notification_type = "info" WHERE notification_type IS NULL')
        logger.info("Добавлен столбец notification_type в notifications")
    
    # Добавьте в существующий блок CREATE TABLE
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_completed_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id),
    FOREIGN KEY (photo_id) REFERENCES photos (id),
    UNIQUE(user_id, photo_id)
    )
    ''')

    try:
        # Проверяем существование столбца replacement_count
        cursor.execute("SELECT replacement_count FROM user_progress LIMIT 1")
    except sqlite3.OperationalError:
        # Если столбца нет - добавляем
        cursor.execute('ALTER TABLE user_progress ADD COLUMN replacement_count INTEGER')
        # Устанавливаем значение по умолчанию для существующих записей
        cursor.execute('UPDATE user_progress SET replacement_count = 0 WHERE replacement_count IS NULL')
        logger.info("Добавлен столбец replacement_count в user_progress")
        
    try:
        # Проверяем существование столбца last_replacement_reset
        cursor.execute("SELECT last_replacement_reset FROM user_progress LIMIT 1")
    except sqlite3.OperationalError:
        # Если столбца нет - добавляем
        cursor.execute('ALTER TABLE user_progress ADD COLUMN last_replacement_reset TIMESTAMP')
        # Устанавливаем значение по умолчанию для существующих записей
        cursor.execute('UPDATE user_progress SET last_replacement_reset = datetime("now") WHERE last_replacement_reset IS NULL')
        logger.info("Добавлен столбец last_replacement_reset в user_progress")
        
    # Вставляем начальные данные, если их нет
    cursor.execute('''
    INSERT OR IGNORE INTO instruction (id, text) 
    VALUES (1, '📝 <b>Инструкция по выполнению задания:</b>\n\n1. Позвоните по указанному номеру\n2. Подтвердите выполнение задания\n3. На следующий день оставьте отзыв\n4. Пришлите скриншот раздела "Мои отзывы"')
    ''')
    
    cursor.execute('''
    INSERT OR IGNORE INTO morning_messages (id, text) 
    VALUES (1, 'Доброе утро! Напоминаем, что сегодня нужно оставить отзыв на Авито. Вечером пришлите скриншот раздела "Мои отзывы".')
    ''')
    
    cursor.execute('''
    INSERT OR IGNORE INTO evening_reminders (id, text) 
    VALUES (1, '🌙 Добрый вечер! Напоминаем, что сегодня нужно оставить отзыв на Авито. После 21:00 по МСК пришлите скриншот раздел "Мои отзывы".')
    ''')
    
    # Добавляем начальные кнопки помощи в заданиях
    cursor.execute('''
    INSERT OR IGNORE INTO task_help_buttons (id, question, answer, order_index) 
    VALUES 
    (1, '❓ Какие вопросы задавать когда звоню?', '📞 <b>Вопросы для звонка:</b>\n\n1. Уточните информацию об услуге/товаре\n2. Спросите о наличии\n3. Узнайте о скидках или акциях\n4. Уточните условия доставки\n5. Поинтересуйтесь отзывы других клиентов', 1),
    (2, '❓ Что делать если не берут трубку?', '📞 <b>Если не берут трубку:</b>\n\n1. Попробуйте позвонить в другое время\n2. Проверьте правильность номера\n3. Если после 3-х попыток не берут - напишите админу @denvr11', 2),
    (3, '❓ Можно я не буду звонить?', '📞 <b>Обязательно нужно звонить!</b>\n\nЗвонок - обязательная часть задания. Без звонка задание не считается выполненным и оплата не производится.', 3),
    (4, '❓ Не могу найти объявление', '📞 <b>Как найти объявление?</b>\n\n1. Что бы найти объявление нужно указать в поиске Авито название объявления (например: Аренда авто с выкупом Kia K5 GT-Line) как на скриншоте и поставить город при поиске как в объявлении (например Москва или Краснодар и т.п)\n2. Важно! Сверяйте название автосалона! Оно должно быть как на скриншоте!', 4)
    ''')
   
    # Таблица для информационных кнопок
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS info_buttons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Добавляем начальные информационные кнопки
    cursor.execute('''
    INSERT OR IGNORE INTO info_buttons (id, title, content, order_index) 
    VALUES 
    (1, '🏢 О нас', '🏢 <b>О нас:</b>\n\nМы работаем с 2023 года, сотрудничая с маркетинговыми компаниями и брендами. Наша цель — помогать бизнесу улучшать онлайн-репутацию, предоставляя нашим исполнителям стабильный заработок за выполнение простых заданий.\n\nЗа это время мы помогли 1000+ исполнителей начать зарабатывать, а десяткам компаний — улучшить свои отзывы и репутацию.', 1),
    (2, '💼 Как это работает', '💼 <b>Как это работает:</b>\n\n🤝 <b>Принципы доверия и прозрачности:</b>\n\nМы находим компании, которые хотят повысить свою репутацию и готовы платить реальные деньги реальным людям за честные отзывы. Это взаимовыгодное сотрудничество:\n\n• <b>Для компаний:</b> улучшение репутации и доверия клиентов\n• <b>Для вас:</b> стабильный заработок за выполнение простых заданий\n\n📋 <b>Процесс работы:</b>\n\n1. <b>Получаете задание</b> - находим компанию, которой нужны отзывы\n2. <b>Выполняете по инструкции</b> - звоните, уточняете информацию\n3. <b>Оставляете честный отзыв</b> - делитесь реальным опытом общения\n4. <b>Получаете оплату</b> - компания платит за улучшение репутации.', 2),
    (3, '💰 Тарифы и выплаты', '💰 <b>Тарифы и выплаты:</b>\n\n• <b>Основное задание:</b> 200 рублей за отзыв\n• <b>Реферальная программа:</b> 50 рублей за друга\n• <b>Партнерский статус:</b> +10% от дохода рефералов (подробнее по кнопке в профиле)\n\n💸 <b>Условия выплат:</b>\n• Минимальная сумма вывода: 50 рублей\n• Выплаты: ежедневно после 22:00 МСК\n• Способы вывода: банковская карта, Qiwi, ЮMoney\n\n👥 <b>Пример заработка:</b>\n1 задание в день = 200 руб.\n5 заданий в неделю = 1 150 руб.\n+ 2 реферала = +100 руб.\n<b>Итого: 1 250 руб./неделю</b>', 3),
    (4, '🛡️ Гарантии и безопасность', '🛡️ <b>Почему это безопасно и легально:</b>\n\n• Отзывы основаны на реальном общении с компанией\n• Вы получаете деньги за потраченное время и честное мнение\n• Компании платят за возможность улучшить сервис через обратную связь\n• Вся процедура соответствует правилам платформ\n\n💡 <b>Суть в том, что все участники остаются в плюсе:</b> компании улучшают репутацию, а вы получаете деньги за свое время и честное мнение!\n\n• <i>Где гарантия оплаты?</i>\n— Обращайтесь к администратору за доказательствами\n\n📞 <b>Поддержка:</b> @denvr11 (круглосуточно)', 4)
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS withdrawal_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        payment_method TEXT NOT NULL,  -- 'card', 'qiwi', 'yoomoney', 'phone', 'sber'
        details TEXT NOT NULL,  -- Номер карты/телефона/кошелька
        status TEXT DEFAULT 'pending',  -- pending, approved, rejected, completed
        admin_comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP,
        completed_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')

    # Таблица для хранения способов выплат пользователя
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_payment_methods (
        user_id INTEGER PRIMARY KEY,
        default_method TEXT DEFAULT 'card',
        card_number TEXT,
        qiwi_wallet TEXT,
        yoomoney_wallet TEXT,
        phone_number TEXT,
        sber_account TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Проверяем и добавляем недостающие колонки
    try:
        cursor.execute("SELECT sber_account FROM user_payment_methods LIMIT 1")
    except sqlite3.OperationalError:
        # Добавляем недостающие колонки
        cursor.execute('ALTER TABLE user_payment_methods ADD COLUMN sber_account TEXT')
        logger.info("Добавлена колонка sber_account в user_payment_methods")

    #ИНДЕКСЫ ДЛЯ БАЗЫ ДАННЫХ
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_progress_user_id ON user_progress(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_progress_step ON user_progress(current_step)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_progress_screenshot ON user_progress(screenshot_status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_activity ON users(last_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")
    
def get_db_connection():
    """Оптимизированное соединение с базой данных"""
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    
    # Оптимизируем настройки SQLite для быстрой работы
    conn.execute("PRAGMA journal_mode = WAL")  # Режим журналирования WAL
    conn.execute("PRAGMA synchronous = NORMAL")  # Баланс производительности и надежности
    conn.execute("PRAGMA cache_size = 10000")  # Увеличиваем кэш
    conn.execute("PRAGMA temp_store = MEMORY")  # Временные таблицы в памяти
    conn.execute("PRAGMA mmap_size = 268435456")  # Используем memory-mapped I/O (256MB)
    conn.execute("PRAGMA busy_timeout = 10000")  # Увеличиваем timeout
    
    return conn
    
# Функции для работы с выплатами

def create_withdrawal_request(user_id, amount, payment_method, details):
    """Создать запрос на вывод средств с немедленным резервированием"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем текущий баланс пользователя
    cursor.execute('SELECT balance FROM user_progress WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return None, "Пользователь не найден"
    
    current_balance = result[0] or 0
    
    if current_balance < amount:
        conn.close()
        return None, f"Недостаточно средств. Баланс: {current_balance}, требуется: {amount}"
    
    # Проверяем, есть ли уже ожидающие выплаты
    cursor.execute('''
        SELECT SUM(amount) FROM withdrawal_requests 
        WHERE user_id = ? AND status = 'pending'
    ''', (user_id,))
    
    pending_sum_result = cursor.fetchone()
    pending_sum = pending_sum_result[0] or 0
    
    # Общая сумма (новая + ожидающие) не должна превышать баланс
    if amount + pending_sum > current_balance:
        conn.close()
        return None, f"Уже есть ожидающие выплаты на {pending_sum}₽. Общая сумма превышает баланс"
    
    # Немедленно списываем средства с баланса
    cursor.execute('''
        UPDATE user_progress 
        SET balance = balance - ?
        WHERE user_id = ?
    ''', (amount, user_id))
    
    # Создаем запрос на вывод
    cursor.execute('''
        INSERT INTO withdrawal_requests (user_id, amount, payment_method, details, status)
        VALUES (?, ?, ?, ?, 'pending')
    ''', (user_id, amount, payment_method, details))
    
    conn.commit()
    request_id = cursor.lastrowid
    conn.close()
    
    return request_id, None

def get_withdrawal_requests(status=None, page=0, limit=10):
    """Получить запросы на вывод с фильтрацией по статусу"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if status:
        cursor.execute('''
        SELECT wr.*, u.username, u.first_name, u.last_name, up.balance
        FROM withdrawal_requests wr
        JOIN users u ON wr.user_id = u.user_id
        LEFT JOIN user_progress up ON wr.user_id = up.user_id
        WHERE wr.status = ?
        ORDER BY wr.created_at DESC
        LIMIT ? OFFSET ?
        ''', (status, limit, page * limit))
    else:
        cursor.execute('''
        SELECT wr.*, u.username, u.first_name, u.last_name, up.balance
        FROM withdrawal_requests wr
        JOIN users u ON wr.user_id = u.user_id
        LEFT JOIN user_progress up ON wr.user_id = up.user_id
        ORDER BY wr.created_at DESC
        LIMIT ? OFFSET ?
        ''', (limit, page * limit))
    
    requests = cursor.fetchall()
    conn.close()
    return requests

def get_withdrawal_requests_count(status=None):
    """Получить количество запросов по статусу"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if status:
        cursor.execute('SELECT COUNT(*) FROM withdrawal_requests WHERE status = ?', (status,))
    else:
        cursor.execute('SELECT COUNT(*) FROM withdrawal_requests')
    
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_withdrawal_request(request_id):
    """Получить информацию о запросе на вывод"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT wr.*, u.username, u.first_name, u.last_name, up.balance
    FROM withdrawal_requests wr
    JOIN users u ON wr.user_id = u.user_id
    LEFT JOIN user_progress up ON wr.user_id = up.user_id
    WHERE wr.id = ?
    ''', (request_id,))
    request = cursor.fetchone()
    conn.close()
    return request

def update_withdrawal_status(request_id, status, admin_comment=None):
    """Обновить статус запроса на вывод"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем информацию о запросе перед обновлением
    cursor.execute('''
    SELECT user_id, amount, status FROM withdrawal_requests 
    WHERE id = ?
    ''', (request_id,))
    request_info = cursor.fetchone()
    
    if not request_info:
        conn.close()
        return False, "Запрос не найден"
    
    user_id = request_info[0]
    amount = request_info[1]
    current_status = request_info[2]
    
    # Если запрос уже обработан, не позволяем повторно
    if current_status in ['approved', 'rejected', 'completed']:
        conn.close()
        return False, "Запрос уже обработан"
    
    if status in ['approved', 'completed']:
        # Для одобренных запросов - просто обновляем статус
        if status == 'completed':
            cursor.execute('''
            UPDATE withdrawal_requests 
            SET status = ?, admin_comment = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''', (status, admin_comment, request_id))
        else:
            cursor.execute('''
            UPDATE withdrawal_requests 
            SET status = ?, admin_comment = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''', (status, admin_comment, request_id))
        
        conn.commit()
        conn.close()
        return True, "Статус обновлен"
        
    elif status == 'rejected':
        # При отклонении - возвращаем средства на баланс
        cursor.execute('''
        UPDATE user_progress 
        SET balance = balance + ?
        WHERE user_id = ?
        ''', (amount, user_id))
        
        cursor.execute('''
        UPDATE withdrawal_requests 
        SET status = ?, admin_comment = ?, processed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''', (status, admin_comment, request_id))
        
        conn.commit()
        conn.close()
        return True, "Средства возвращены на баланс"
        
    else:
        # Для других статусов просто обновляем
        cursor.execute('''
        UPDATE withdrawal_requests 
        SET status = ?, admin_comment = ?
        WHERE id = ?
        ''', (status, admin_comment, request_id))
        
        conn.commit()
        conn.close()
        return True, "Статус обновлен"

def get_user_withdrawal_history(user_id, page=0, limit=10):
    """Получить историю выводов пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT * FROM withdrawal_requests 
    WHERE user_id = ?
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
    ''', (user_id, limit, page * limit))
    history = cursor.fetchall()
    conn.close()
    return history

def get_user_payment_methods(user_id):
    """Получить способы оплаты пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM user_payment_methods WHERE user_id = ?', (user_id,))
    methods = cursor.fetchone()
    conn.close()
    return methods

def save_user_payment_method(user_id, method_type, details):
    """Сохранить способ оплаты пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Сопоставление типов методов с именами колонок
    column_mapping = {
        'card': 'card_number',
        'qiwi': 'qiwi_wallet',
        'yoomoney': 'yoomoney_wallet',
        'phone': 'phone_number',
        'sber': 'sber_account'
    }
    
    # Получаем имя колонки
    if method_type not in column_mapping:
        conn.close()
        raise ValueError(f"Неизвестный тип метода оплаты: {method_type}")
    
    column_name = column_mapping[method_type]
    
    # Проверяем, есть ли уже запись
    cursor.execute('SELECT * FROM user_payment_methods WHERE user_id = ?', (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        # Обновляем существующую запись
        cursor.execute(f'''
        UPDATE user_payment_methods 
        SET {column_name} = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        ''', (details, user_id))
    else:
        # Создаем новую запись
        cursor.execute('''
        INSERT INTO user_payment_methods (user_id, {})
        VALUES (?, ?)
        '''.format(column_name), (user_id, details))
    
    conn.commit()
    conn.close()
    return True

def get_pending_withdrawals_count():
    """Получить количество ожидающих выплат"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_user_pending_withdrawals(user_id):
    """Получить ожидающие выплаты пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT * FROM withdrawal_requests 
    WHERE user_id = ? AND status = 'pending'
    ORDER BY created_at DESC
    ''', (user_id,))
    withdrawals = cursor.fetchall()
    conn.close()
    return withdrawals

def can_user_withdraw(user_id, amount):
    """Проверить, может ли пользователь запросить вывод"""
    balance = get_user_balance(user_id)
    
    # Минимальная сумма вывода
    if amount < 50:
        return False, "Минимальная сумма вывода - 50 рублей"
    
    # Проверяем баланс
    if amount > balance:
        return False, f"Недостаточно средств. Ваш баланс: {balance} рублей"
    
    # Проверяем, нет ли уже ожидающих выплат
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT COUNT(*) FROM withdrawal_requests 
    WHERE user_id = ? AND status = 'pending'
    ''', (user_id,))
    pending_count = cursor.fetchone()[0] or 0
    conn.close()
    
    if pending_count > 0:
        return False, "У вас уже есть ожидающая выплата. Дождитесь ее обработки."
    
    return True, None

def get_user_total_reserved(user_id):
    """Получить общую сумму зарезервированных средств"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT COALESCE(SUM(amount), 0) 
    FROM withdrawal_requests 
    WHERE user_id = ? AND status = 'pending'
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] else 0

def get_info_buttons():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM info_buttons ORDER BY order_index")
    buttons = cursor.fetchall()
    conn.close()
    return buttons
    
@cached(ttl_seconds=600)  # Кэшируем на 60 секунд
def get_info_buttons_cached():
    return get_info_buttons()

@cached(ttl_seconds=600)
def get_task_help_buttons_cached():
    return get_task_help_buttons()

@cached(ttl_seconds=300)  # Кэшируем на 5 минут
def get_all_categories_cached():
    return get_all_categories()

def get_info_content(button_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM info_buttons WHERE id = ?", (button_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Информация не найдена."    

def add_user(user_id, username, first_name, last_name, phone_number=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, phone_number, last_active)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id, username, first_name, last_name, phone_number))
    conn.commit()
    conn.close()

def update_user_activity(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()

def add_photo(photo_id, category_id=1):
    """Добавить фото с указанием категории"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO photos (photo_id, category_id) VALUES (?, ?)",
        (photo_id, category_id)
    )
    conn.commit()
    conn.close()

def get_all_photos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM photos")
    photos = cursor.fetchall()
    conn.close()
    return photos
    
def add_completed_task(user_id, photo_id):
    """Добавляет выполненное задание в историю"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO user_completed_tasks (user_id, photo_id) VALUES (?, ?)",
            (user_id, photo_id)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при добавлении выполненного задания: {e}")
    finally:
        conn.close()

def get_completed_tasks(user_id):
    """Возвращает список ID фото, которые пользователь уже выполнял"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT photo_id FROM user_completed_tasks WHERE user_id = ?",
        (user_id,)
    )
    completed_tasks = [row[0] for row in cursor.fetchall()]
    conn.close()
    return completed_tasks

def get_available_photos(user_id, count=1):
    """Возвращает доступные фото, которые пользователь еще не выполнял"""
    completed_tasks = get_completed_tasks(user_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if completed_tasks:
        placeholders = ','.join('?' * len(completed_tasks))
        cursor.execute(
            f"SELECT * FROM photos WHERE id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT ?",
            completed_tasks + [count]
        )
    else:
        cursor.execute("SELECT * FROM photos ORDER BY RANDOM() LIMIT ?", (count,))
    
    photos = cursor.fetchall()
    conn.close()
    return photos

def get_random_photo():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM photos")
    photos = cursor.fetchall()
    conn.close()
    return random.choice(photos) if photos else None

def get_available_photos_from_other_categories(user_id, exclude_category_id, count=1):
    """Возвращает доступные фото из других категорий, исключая указанную"""
    completed_tasks = get_completed_tasks(user_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Строим запрос для исключения выполненных заданий и указанной категории
    if completed_tasks:
        placeholders = ','.join('?' * len(completed_tasks))
        query = f'''
            SELECT * FROM photos 
            WHERE id NOT IN ({placeholders}) 
            AND category_id != ?
            ORDER BY RANDOM() 
            LIMIT ?
        '''
        params = completed_tasks + [exclude_category_id, count]
    else:
        query = '''
            SELECT * FROM photos 
            WHERE category_id != ?
            ORDER BY RANDOM() 
            LIMIT ?
        '''
        params = [exclude_category_id, count]
    
    cursor.execute(query, params)
    photos = cursor.fetchall()
    conn.close()
    
    return photos
def get_all_categories():
    """Получить все категории"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_categories ORDER BY id")
    categories = cursor.fetchall()
    conn.close()
    return categories

def get_category(category_id):
    """Получить информацию о категории"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_categories WHERE id = ?", (category_id,))
    category = cursor.fetchone()
    conn.close()
    return category

def add_category(name, description=""):
    """Добавить новую категорию"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO task_categories (name, description) VALUES (?, ?)",
        (name, description)
    )
    conn.commit()
    category_id = cursor.lastrowid
    conn.close()
    return category_id

def update_category(category_id, name, description=""):
    """Обновить категорию"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE task_categories SET name = ?, description = ? WHERE id = ?",
        (name, description, category_id)
    )
    conn.commit()
    conn.close()

def delete_category(category_id):
    """Удалить категорию (только если нет фото в этой категории)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем, есть ли фото в этой категории
    cursor.execute("SELECT COUNT(*) FROM photos WHERE category_id = ?", (category_id,))
    count = cursor.fetchone()[0]
    
    if count > 0:
        conn.close()
        return False, "Нельзя удалить категорию, в которой есть фото"
    
    # Удаляем категорию
    cursor.execute("DELETE FROM task_categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    return True, "Категория удалена"

def get_photos_by_category(category_id):
    """Получить все фото в категории"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM photos WHERE category_id = ? ORDER BY id",
        (category_id,)
    )
    photos = cursor.fetchall()
    conn.close()
    return photos

def update_photo_category(photo_id, category_id):
    """Обновить категорию для фото"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE photos SET category_id = ? WHERE id = ?",
        (category_id, photo_id)
    )
    conn.commit()
    conn.close()

def get_user_completed_categories(user_id):
    """Получить категории, которые пользователь уже выполнял"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT p.category_id
        FROM user_completed_tasks uct
        JOIN photos p ON uct.photo_id = p.id
        WHERE uct.user_id = ?
    ''', (user_id,))
    completed_categories = [row[0] for row in cursor.fetchall()]
    conn.close()
    return completed_categories

def get_available_photos(user_id, count=1, exclude_category_id=None):
    """Возвращает доступные фото, которые пользователь еще не выполнял"""
    completed_tasks = get_completed_tasks(user_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем категории, которые пользователь уже выполнял
    completed_categories = get_user_completed_categories(user_id)
    
    # Если все категории выполнены, показываем любые доступные
    cursor.execute("SELECT DISTINCT category_id FROM photos")
    all_categories = [row[0] for row in cursor.fetchall()]
    
    # Определяем категории для исключения
    exclude_categories = []
    if exclude_category_id:
        exclude_categories.append(exclude_category_id)
    
    # Если есть невыполненные категории, показываем из них
    if len(completed_categories) < len(all_categories):
        # Исключаем выполненные категории и указанную для исключения
        available_categories = [cat for cat in all_categories 
                              if cat not in completed_categories and cat not in exclude_categories]
        
        if available_categories:
            # Выбираем случайную категорию из доступных
            selected_category = random.choice(available_categories)
            if completed_tasks:
                placeholders = ','.join('?' * len(completed_tasks))
                cursor.execute(
                    f"SELECT * FROM photos WHERE id NOT IN ({placeholders}) AND category_id = ? ORDER BY RANDOM() LIMIT ?",
                    completed_tasks + [selected_category, count]
                )
            else:
                cursor.execute(
                    "SELECT * FROM photos WHERE category_id = ? ORDER BY RANDOM() LIMIT ?",
                    (selected_category, count)
                )
            photos = cursor.fetchall()
            conn.close()
            return photos
    
    # Если все категории выполнены или нет доступных, показываем любые, кроме исключенных
    if exclude_categories:
        cat_placeholders = ','.join('?' * len(exclude_categories))
        if completed_tasks:
            task_placeholders = ','.join('?' * len(completed_tasks))
            cursor.execute(
                f"SELECT * FROM photos WHERE id NOT IN ({task_placeholders}) AND category_id NOT IN ({cat_placeholders}) ORDER BY RANDOM() LIMIT ?",
                completed_tasks + exclude_categories + [count]
            )
        else:
            cursor.execute(
                f"SELECT * FROM photos WHERE category_id NOT IN ({cat_placeholders}) ORDER BY RANDOM() LIMIT ?",
                exclude_categories + [count]
            )
    else:
        if completed_tasks:
            placeholders = ','.join('?' * len(completed_tasks))
            cursor.execute(
                f"SELECT * FROM photos WHERE id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT ?",
                completed_tasks + [count]
            )
        else:
            cursor.execute("SELECT * FROM photos ORDER BY RANDOM() LIMIT ?", (count,))
    
    photos = cursor.fetchall()
    conn.close()
    return photos
    
def delete_photo(photo_id): # удаление фото функция из списка фото заданий
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()

def get_instruction():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT text FROM instruction WHERE id = 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Инструкция еще не установлена. Обратитесь к администратору."

def get_morning_message():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT text, send_time, video_id FROM morning_messages WHERE id = 1")
    result = cursor.fetchone()
    conn.close()
    return result if result else ("Доброе утро! Не забудьте оставить отзыв.", "09:00", None)

def set_morning_message(text, send_time="09:00", video_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO morning_messages (id, text, send_time, video_id, updated_at)
    VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (text, send_time, video_id))
    conn.commit()
    conn.close()

def get_evening_reminder():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT text, send_time, video_id FROM evening_reminders WHERE id = 1")
    result = cursor.fetchone()
    conn.close()
    return result if result else ("Добрый вечер! Не забудьте оставить отзыв.", "20:00", None)

def set_evening_reminder(text, send_time="20:00", video_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO evening_reminders (id, text, send_time, video_id, updated_at)
    VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (text, send_time, video_id))
    conn.commit()
    conn.close()

def update_user_step(user_id, step):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET current_step = ?
    WHERE user_id = ?
    ''', (step, user_id))
    conn.commit()
    conn.close()
    
def delete_user_completely(user_id):
    """Полное удаление пользователя из всех таблиц"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Удаляем из всех связанных таблиц
        cursor.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM referrals WHERE referrer_id = ? OR referred_id = ?", (user_id, user_id))
        cursor.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при удалении пользователя {user_id}: {e}")
        return False
    finally:
        conn.close()
    
def format_user_link(user_id, username, first_name, last_name):
    """Форматирует ссылку на пользователя для HTML-сообщений (исправленная версия)"""
    if username:
        # Возвращаем username без HTML тегов
        return f"@{username}"
    else:
        name = f"{first_name or ''} {last_name or ''}".strip()
        if not name:
            name = f"Пользователь {user_id}"
        # Экранируем HTML-символы
        escaped_name = html.escape(name)
        # Используем правильный формат для Telegram
        return f'<a href="tg://user?id={user_id}">{escaped_name}</a>'
    
def get_user_step(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "get_task"

def can_assign_task(user_id):
    """Проверяет, может ли пользователь получить новое задание"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return True, None
    
    current_step = result[0]
    
    # Разрешаем новое задание если предыдущее в финальных статусах
    final_steps = [TASK_STATUS["COMPLETED"], TASK_STATUS["SCREENSHOT_REJECTED"], TASK_STATUS["CANCELLED"]]
    if current_step in final_steps:
        return True, None
    
    # Если задание активно и не в финальном статусе - запрещаем
    return False, None

def assign_task_to_user(user_id, photo_id):
    """Назначает задание пользователю (упрощенная версия без мультиаккаунтов)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Сначала сбрасываем старое задание если оно в финальном статусе
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    final_steps = [TASK_STATUS["COMPLETED"], TASK_STATUS["SCREENSHOT_REJECTED"], TASK_STATUS["CANCELLED"]]
    if result and result[0] in final_steps:
        # Очищаем старое задание
        cursor.execute('DELETE FROM user_progress WHERE user_id = ?', (user_id,))
    
    cursor.execute('''
    INSERT OR REPLACE INTO user_progress 
    (user_id, photo_id, called, assigned_at, current_step, replacement_count, last_replacement_reset)
    VALUES (?, ?, FALSE, CURRENT_TIMESTAMP, ?, 0, CURRENT_TIMESTAMP)
    ''', (user_id, photo_id, TASK_STATUS["CONFIRM_CALL"]))
    
    conn.commit()
    conn.close()

def fix_database():
    """Исправляет структуру базы данных при ошибках миграции"""
    try:
        conn = sqlite3.connect('bot.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Создаем временную таблицу с правильной структурой
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress_new (
            user_id INTEGER PRIMARY KEY,
            photo_id INTEGER,
            called BOOLEAN DEFAULT FALSE,
            called_confirmed BOOLEAN DEFAULT FALSE,
            called_confirmed_at TIMESTAMP,
            review_sent BOOLEAN DEFAULT FALSE,
            review_sent_at TIMESTAMP,
            morning_message_sent BOOLEAN DEFAULT FALSE,
            evening_reminder_sent BOOLEAN DEFAULT FALSE,
            screenshot_sent BOOLEAN DEFAULT FALSE,
            screenshot_id TEXT,
            screenshot_sent_at TIMESTAMP,
            screenshot_status TEXT DEFAULT 'not_sent',
            admin_review_comment TEXT,
            assigned_at TIMESTAMP,
            completed_at TIMESTAMP,
            current_step TEXT DEFAULT 'get_task',
            multi_accounts BOOLEAN DEFAULT FALSE,
            accounts_requested INTEGER DEFAULT 0,
            photos_sent TEXT,
            balance INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0,
            replacement_count INTEGER DEFAULT 0,
            last_replacement_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Копируем данные из старой таблицы
        try:
            cursor.execute('''
            INSERT INTO user_progress_new 
            SELECT * FROM user_progress
            ''')
        except sqlite3.OperationalError as e:
            # Если структура отличается, копируем явно указанные колонки
            logger.warning(f"Не удалось скопировать данные напрямую: {e}")
            
            # Получаем список колонок в старой таблице
            cursor.execute("PRAGMA table_info(user_progress)")
            old_columns = [col[1] for col in cursor.fetchall()]
            
            # Определяем, какие колонки есть в обоих таблицах
            cursor.execute("PRAGMA table_info(user_progress_new)")
            new_columns = [col[1] for col in cursor.fetchall()]
            
            common_columns = set(old_columns) & set(new_columns)
            
            if common_columns:
                columns_str = ', '.join(common_columns)
                cursor.execute(f'''
                INSERT INTO user_progress_new ({columns_str})
                SELECT {columns_str} FROM user_progress
                ''')
        
        # Заменяем старую таблицу новой
        cursor.execute('DROP TABLE IF EXISTS user_progress_old')
        cursor.execute('ALTER TABLE user_progress RENAME TO user_progress_old')
        cursor.execute('ALTER TABLE user_progress_new RENAME TO user_progress')
        
        conn.commit()
        conn.close()
        logger.info("База данных успешно исправлена")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при исправлении базы данных: {e}")
        return False

def update_info_button(button_id, title, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE info_buttons 
    SET title = ?, content = ?
    WHERE id = ?
    ''', (title, content, button_id))
    conn.commit()
    conn.close()

def get_info_button(button_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM info_buttons WHERE id = ?", (button_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def confirm_user_call(user_id):
    """Подтверждение звонка - переход в статус ожидания утра"""
    update_user_status(user_id, TASK_STATUS["WAITING_REVIEW_DAY"])
    
    # Дополнительные обновления
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET called = TRUE, called_confirmed = TRUE, called_confirmed_at = CURRENT_TIMESTAMP
    WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()

def get_user_info(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def reset_user_task(user_id, new_status=TASK_STATUS["CANCELLED"]):
    """Сброс задания с указанием статуса"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
    UPDATE user_progress 
    SET current_step = ?, completed_at = CURRENT_TIMESTAMP
    WHERE user_id = ?
    ''', (new_status, user_id))
    
    conn.commit()
    conn.close()

def get_user_task(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT up.photo_id, p.photo_id, up.assigned_at, up.called, up.called_confirmed, up.screenshot_sent, up.current_step, up.accounts_requested, up.photos_sent
    FROM user_progress up
    LEFT JOIN photos p ON up.photo_id = p.id
    WHERE up.user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM photos")
    photo_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE called_confirmed = TRUE")
    called_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE screenshot_sent = TRUE")
    screenshot_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE tasks_completed > 0")
    active_users_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(total_earned) FROM user_progress")
    total_earned = cursor.fetchone()[0] or 0
    
    conn.close()
    return user_count, photo_count, called_count, screenshot_count, active_users_count, total_earned

def get_called_users(page=0, limit=10):
    """Получить список подтвердивших звонок с улучшенной информацией"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT 
        u.user_id, 
        u.username, 
        u.first_name, 
        u.last_name, 
        up.called_confirmed_at,
        up.photo_id,
        p.category_id,
        c.name as category_name,
        up.current_step,
        up.screenshot_status
    FROM user_progress up
    LEFT JOIN users u ON u.user_id = up.user_id
    LEFT JOIN photos p ON up.photo_id = p.id
    LEFT JOIN task_categories c ON p.category_id = c.id
    WHERE up.called_confirmed = TRUE
    ORDER BY up.called_confirmed_at DESC
    LIMIT ? OFFSET ?
    ''', (limit, page * limit))
    users = cursor.fetchall()
    conn.close()
    return users
 
def reset_all_tasks():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
    UPDATE user_progress 
    SET current_step = 'cancelled', 
        completed_at = CURRENT_TIMESTAMP,
        replacement_count = 0,
        called = FALSE,
        called_confirmed = FALSE,
        morning_message_sent = FALSE,
        evening_reminder_sent = FALSE,
        screenshot_sent = FALSE,
        screenshot_status = 'not_sent'
    WHERE current_step NOT IN ('completed', 'cancelled')
    ''')
    
    affected_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return affected_count
    
    return affected_count

def get_screenshot_users(page=0, limit=10):
    """Получить список приславших скриншот с улучшенной информацией"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT 
        u.user_id, 
        u.username, 
        u.first_name, 
        u.last_name, 
        up.screenshot_sent_at,
        up.photo_id,
        p.category_id,
        c.name as category_name,
        up.current_step,
        up.screenshot_status,
        up.admin_review_comment
    FROM user_progress up
    LEFT JOIN users u ON u.user_id = up.user_id
    LEFT JOIN photos p ON up.photo_id = p.id
    LEFT JOIN task_categories c ON p.category_id = c.id
    WHERE up.screenshot_sent = TRUE
    ORDER BY up.screenshot_sent_at DESC
    LIMIT ? OFFSET ?
    ''', (limit, page * limit))
    users = cursor.fetchall()
    conn.close()
    return users

def get_pending_screenshots():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT u.user_id, u.username, u.first_name, u.last_name, up.screenshot_id, up.screenshot_sent_at
    FROM user_progress up
    LEFT JOIN users u ON u.user_id = up.user_id
    WHERE up.screenshot_status = 'pending'
    ORDER BY up.screenshot_sent_at DESC
    ''')
    screenshots = cursor.fetchall()
    conn.close()
    return screenshots

def save_screenshot(user_id, screenshot_id):
    """Сохранение скриншота и переход в статус проверки"""
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем текущий статус
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    current_step = result[0] if result else TASK_STATUS["GET_TASK"]
    
    # Определяем целевой статус в зависимости от текущего
    if current_step in [TASK_STATUS["SEND_SCREENSHOT"], TASK_STATUS["SCREENSHOT_REJECTED"], 
                        TASK_STATUS["WAITING_REVIEW_EVENING"], TASK_STATUS["WAITING_REVIEW_DAY"]]:
        target_status = TASK_STATUS["WAITING_ADMIN_REVIEW"]
    else:
        # Если статус неподходящий, используем текущий
        target_status = current_step
    
    conn.close()
    
    # Обновляем статус
    update_user_status(user_id, target_status)
    
    # Сохраняем скриншот
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET screenshot_sent = TRUE, screenshot_id = ?, screenshot_sent_at = CURRENT_TIMESTAMP,
        screenshot_status = 'pending'
    WHERE user_id = ?
    ''', (screenshot_id, user_id))
    conn.commit()
    conn.close()
    
def get_user_current_status(user_id):
    """Получение текущего статуса пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else TASK_STATUS["GET_TASK"]

def update_screenshot_status(user_id, status, comment=None, context: CallbackContext=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем информацию о задании перед обновлением статуса
    cursor.execute("SELECT photo_id FROM user_progress WHERE user_id = ?", (user_id,))
    task_result = cursor.fetchone()
    photo_id = task_result[0] if task_result else None

    if status == 'approved':
        earned_amount = 200
        
        # 1. Начисляем деньги пользователю за выполнение задания
        cursor.execute('''
        UPDATE user_progress 
        SET screenshot_status = ?, admin_review_comment = ?, current_step = 'completed',
            balance = balance + ?, total_earned = total_earned + ?, tasks_completed = tasks_completed + 1,
            completed_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        ''', (status, comment, earned_amount, earned_amount, user_id))
        
        # 2. Добавляем в историю выполненных заданий
        if photo_id:
            add_completed_task(user_id, photo_id)
        
        # 3. РЕФЕРАЛЬНЫЕ БОНУСЫ (ИСПРАВЛЕННАЯ ЛОГИКА)
        # Ищем реферера ДЛЯ ЭТОГО пользователя
        cursor.execute('''
        SELECT referrer_id 
        FROM referrals 
        WHERE referred_id = ?
        ''', (user_id,))
        
        row = cursor.fetchone()
        
        if row:
            referrer_id = row[0]
            
            # 4. ПРОВЕРЯЕМ, не выплачивали ли уже бонус за этого реферала
            cursor.execute('''
            SELECT bonus_paid 
            FROM referrals 
            WHERE referred_id = ? AND referrer_id = ?
            ''', (user_id, referrer_id))
            
            bonus_check = cursor.fetchone()
            
            # Если бонус уже выплачен - пропускаем
            if bonus_check and bonus_check[0]:
                logger.info(f"Бонус за реферала {user_id} уже выплачен рефереру {referrer_id}")
            else:
                # 5. Получаем статистику реферера
                cursor.execute('''
                SELECT 
                    COALESCE(successful_refs, 0), 
                    COALESCE(is_ambassador, FALSE)
                FROM user_progress 
                WHERE user_id = ?
                ''', (referrer_id,))
                
                ref_stats = cursor.fetchone()
                
                if ref_stats:
                    successful_refs, is_ambassador = ref_stats
                    
                    # 6. Рассчитываем бонус по ВАШЕЙ ЛОГИКЕ:
                    base_bonus = 50  # Всегда 50 руб
                    
                    # Проверяем, стал ли реферер амбассадором на ЭТОМ шаге
                    # ВАЖНО: проверяем ДО увеличения счетчика!
                    will_become_ambassador = (successful_refs + 1 >= 5) and not is_ambassador
                    
                    # Если реферер УЖЕ амбассадор или СТАНЕТ им после этой выплаты
                    if is_ambassador or will_become_ambassador:
                        ambassador_bonus = int(earned_amount * 0.10)  # 10% от 200 = 20 руб
                        total_bonus = base_bonus + ambassador_bonus
                    else:
                        total_bonus = base_bonus  # Только 50 руб
                    
                    # 7. Начисляем бонус рефереру
                    cursor.execute('''
                    UPDATE user_progress 
                    SET balance = balance + ?, 
                        total_earned = total_earned + ?,
                        successful_refs = successful_refs + 1
                    WHERE user_id = ?
                    ''', (total_bonus, total_bonus, referrer_id))
                    
                    # 8. Если реферер стал амбассадором на этом шаге - обновляем статус
                    if will_become_ambassador:
                        cursor.execute('''
                        UPDATE user_progress 
                        SET is_ambassador = TRUE 
                        WHERE user_id = ?
                        ''', (referrer_id,))
                        logger.info(f"🎉 Пользователь {referrer_id} стал амбассадором!")
                    
                    # 9. КРИТИЧЕСКИ ВАЖНО: помечаем бонус как ВЫПЛАЧЕННЫЙ
                    cursor.execute('''
                    UPDATE referrals 
                    SET bonus_paid = TRUE 
                    WHERE referred_id = ? AND referrer_id = ?
                    ''', (user_id, referrer_id))
                    
                    # 10. Отправляем уведомление рефереру
                    if context:
                        if is_ambassador:
                            message = f"🎉 Ваш реферал выполнил задание!\nПолучено: {total_bonus}₽\n(50₽ базовый + {ambassador_bonus}₽ бонус амбассадора)"
                        elif will_become_ambassador:
                            message = f"🏆 ВЫ СТАЛИ АМБАССАДОРОМ!\nВаш реферал выполнил задание!\nПолучено: {total_bonus}₽\n(50₽ базовый + {ambassador_bonus}₽ бонус амбассадора)"
                        else:
                            message = f"✅ Ваш реферал выполнил задание!\nПолучено: {total_bonus}₽\nДо амбассадора осталось: {5 - (successful_refs + 1)} успешных рефералов"
                        
                        asyncio.create_task(send_notification(referrer_id, message, context))
    
    else:
        # Логика для отклонения скриншота
        cursor.execute('''
        UPDATE user_progress 
        SET screenshot_status = 'rejected', 
            admin_review_comment = ?,
            current_step = ?
        WHERE user_id = ?
        ''', (comment, TASK_STATUS["SCREENSHOT_REJECTED"], user_id))

    conn.commit()
    conn.close()

def get_task_help_buttons():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_help_buttons ORDER BY order_index")
    buttons = cursor.fetchall()
    conn.close()
    return buttons

def get_task_help_answer(button_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT answer FROM task_help_buttons WHERE id = ?", (button_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Ответ не найден."

def add_referral(referrer_id, referred_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR IGNORE INTO referrals (referrer_id, referred_id)
    VALUES (?, ?)
    ''', (referrer_id, referred_id))
    conn.commit()
    conn.close()

def get_referral_stats(user_id: int):
    """
    Возвращает кортеж (registered_count, completed_count) для рефералов пользователя.
    completed_count — число рефералов, у которых скриншот одобрен (screenshot_status = 'approved').
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    # Сколько зарегистрировалось по ссылке
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
    registered = cursor.fetchone()[0] or 0
    # Сколько из них завершили задание (одобрен скрин)
    cursor.execute('''
        SELECT COUNT(*)
        FROM referrals r
        JOIN user_progress up ON up.user_id = r.referred_id
        WHERE r.referrer_id = ? AND up.screenshot_status = 'approved'
    ''', (user_id,))
    completed = cursor.fetchone()[0] or 0
    conn.close()
    return registered, completed

def get_user_balance(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0
    
def get_called_stats():
    """Получить детальную статистику по подтвердившим звонок"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Общее количество подтвердивших
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE called_confirmed = TRUE")
    total_called = cursor.fetchone()[0] or 0
    
    # Количество по дням (последние 7 дней)
    cursor.execute('''
    SELECT DATE(called_confirmed_at) as date, COUNT(*) as count
    FROM user_progress 
    WHERE called_confirmed = TRUE 
    AND called_confirmed_at >= DATE('now', '-7 days')
    GROUP BY DATE(called_confirmed_at)
    ORDER BY date DESC
    ''')
    last_7_days = cursor.fetchall()
    
    # Статусы скриншотов у подтвердивших
    cursor.execute('''
    SELECT 
        COUNT(CASE WHEN screenshot_status = 'approved' THEN 1 END) as approved,
        COUNT(CASE WHEN screenshot_status = 'rejected' THEN 1 END) as rejected,
        COUNT(CASE WHEN screenshot_status = 'pending' THEN 1 END) as pending,
        COUNT(CASE WHEN screenshot_status = 'not_sent' THEN 1 END) as not_sent
    FROM user_progress 
    WHERE called_confirmed = TRUE
    ''')
    screenshot_stats = cursor.fetchone()
    
    conn.close()
    
    return {
        'total_called': total_called,
        'last_7_days': last_7_days,
        'screenshot_stats': screenshot_stats
    }

def get_screenshot_stats():
    """Получить детальную статистику по скриншотам"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Общее количество скриншотов
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE screenshot_sent = TRUE")
    total_screenshots = cursor.fetchone()[0] or 0
    
    # Количество по статусам
    cursor.execute('''
    SELECT screenshot_status, COUNT(*) as count
    FROM user_progress 
    WHERE screenshot_sent = TRUE
    GROUP BY screenshot_status
    ''')
    status_counts = cursor.fetchall()
    
    # Количество по дням (последние 7 дней)
    cursor.execute('''
    SELECT DATE(screenshot_sent_at) as date, COUNT(*) as count
    FROM user_progress 
    WHERE screenshot_sent = TRUE 
    AND screenshot_sent_at >= DATE('now', '-7 days')
    GROUP BY DATE(screenshot_sent_at)
    ORDER BY date DESC
    ''')
    last_7_days = cursor.fetchall()
    
    # Среднее время между подтверждением звонка и отправкой скриншота
    cursor.execute('''
    SELECT AVG(
        (julianday(screenshot_sent_at) - julianday(called_confirmed_at)) * 24
    ) as avg_hours
    FROM user_progress 
    WHERE called_confirmed = TRUE 
    AND screenshot_sent = TRUE
    AND screenshot_sent_at > called_confirmed_at
    ''')
    avg_hours = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        'total_screenshots': total_screenshots,
        'status_counts': status_counts,
        'last_7_days': last_7_days,
        'avg_hours': avg_hours
    }

def add_notification(user_id, message, notification_type="info"):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем, есть ли столбец notification_type
    try:
        cursor.execute("PRAGMA table_info(notifications)")
        columns = [col[1] for col in cursor.fetchall()]
        has_notification_type = 'notification_type' in columns
    except:
        has_notification_type = False
    
    if has_notification_type:
        cursor.execute('''
        INSERT INTO notifications (user_id, message, notification_type)
        VALUES (?, ?, ?)
        ''', (user_id, message, notification_type))
    else:
        # Для обратной совместимости
        cursor.execute('''
        INSERT INTO notifications (user_id, message)
        VALUES (?, ?)
        ''', (user_id, message))
    
    conn.commit()
    conn.close()

def get_unread_notifications(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем структуру таблицы
    cursor.execute("PRAGMA table_info(notifications)")
    columns = [col[1] for col in cursor.fetchall()]
    has_notification_type = 'notification_type' in columns
    
    if has_notification_type:
        cursor.execute('''
        SELECT id, user_id, message, notification_type, is_read, created_at
        FROM notifications 
        WHERE user_id = ? AND is_read = FALSE
        ORDER BY created_at DESC
        ''', (user_id,))
    else:
        cursor.execute('''
        SELECT id, user_id, message, is_read, created_at
        FROM notifications 
        WHERE user_id = ? AND is_read = FALSE
        ORDER BY created_at DESC
        ''', (user_id,))
    
    notifications = cursor.fetchall()
    conn.close()
    return notifications

def mark_notification_read(notification_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE notifications SET is_read = TRUE
    WHERE id = ?
    ''', (notification_id,))
    conn.commit()
    conn.close()
    
def get_photo_category_name(photo_id):
    """Получить название категории по ID фото"""
    if not photo_id:
        return "Без категории"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.name 
        FROM photos p 
        LEFT JOIN task_categories c ON p.category_id = c.id 
        WHERE p.id = ?
    ''', (photo_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0]
    return "Без категории"

def get_users_for_payout():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT u.user_id, u.username, u.first_name, u.last_name, up.balance
    FROM users u
    JOIN user_progress up ON u.user_id = up.user_id
    WHERE up.balance > 0
    ORDER BY up.balance DESC
    ''')
    users = cursor.fetchall()
    conn.close()
    return users

def process_payout(context: CallbackContext, user_id, amount):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET balance = balance - ?
    WHERE user_id = ?
    ''', (amount, user_id))
    conn.commit()
    conn.close()
    
    # Отправляем уведомление пользователю в фоне
    asyncio.create_task(
        send_notification(
            user_id,
            f"💸 Вам выплачено {amount} рублей! Проверьте ваш кошелек.",
            context
        )
    )

def get_last_replacement_reset(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT last_replacement_reset FROM user_progress WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except sqlite3.OperationalError as e:
        # Если столбец еще не создан
        logger.warning(f"Столбец last_replacement_reset не найден: {e}")
        return None
    finally:
        conn.close()

def get_users_waiting_for_morning():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id FROM user_progress
        WHERE called_confirmed = TRUE
          AND current_step = 'waiting_review_day'
          AND morning_message_sent = FALSE
    ''')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def get_users_waiting_for_evening():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id FROM user_progress
        WHERE called_confirmed = TRUE
          AND current_step = 'waiting_review_evening'
          AND evening_reminder_sent = FALSE
    ''')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def mark_morning_message_sent(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET morning_message_sent = TRUE, current_step = ?
    WHERE user_id = ?
    ''', (TASK_STATUS["WAITING_REVIEW_EVENING"], user_id))
    conn.commit()
    conn.close()


def mark_evening_reminder_sent(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE user_progress 
    SET evening_reminder_sent = TRUE, current_step = ?
    WHERE user_id = ?
    ''', (TASK_STATUS["SEND_SCREENSHOT"], user_id))
    conn.commit()
    conn.close()
    
def update_user_status(user_id, new_status, additional_data=None):
    """Безопасное обновление статуса пользователя с валидацией переходов"""
    
    # РАСШИРЕННЫЕ ДОПУСТИМЫЕ ПЕРЕХОДЫ
    allowed_transitions = {
        TASK_STATUS["GET_TASK"]: [TASK_STATUS["CONFIRM_CALL"]],
        TASK_STATUS["CONFIRM_CALL"]: [TASK_STATUS["WAITING_REVIEW_DAY"]],
        TASK_STATUS["WAITING_REVIEW_DAY"]: [TASK_STATUS["WAITING_REVIEW_EVENING"]],
        TASK_STATUS["WAITING_REVIEW_EVENING"]: [TASK_STATUS["SEND_SCREENSHOT"]],
        TASK_STATUS["SEND_SCREENSHOT"]: [TASK_STATUS["WAITING_ADMIN_REVIEW"], TASK_STATUS["SCREENSHOT_REJECTED"]],
        TASK_STATUS["WAITING_ADMIN_REVIEW"]: [TASK_STATUS["COMPLETED"], TASK_STATUS["SCREENSHOT_REJECTED"]],
        TASK_STATUS["SCREENSHOT_REJECTED"]: [TASK_STATUS["SEND_SCREENSHOT"], TASK_STATUS["CANCELLED"]],
        TASK_STATUS["COMPLETED"]: [TASK_STATUS["GET_TASK"]],
        TASK_STATUS["CANCELLED"]: [TASK_STATUS["GET_TASK"]],
        
        # ДОПОЛНИТЕЛЬНЫЕ ПЕРЕХОДЫ ДЛЯ ГИБКОСТИ
        TASK_STATUS["WAITING_REVIEW_DAY"]: [TASK_STATUS["WAITING_REVIEW_EVENING"], TASK_STATUS["SEND_SCREENSHOT"]],
        TASK_STATUS["WAITING_REVIEW_EVENING"]: [TASK_STATUS["SEND_SCREENSHOT"], TASK_STATUS["WAITING_ADMIN_REVIEW"]],
        TASK_STATUS["SEND_SCREENSHOT"]: [TASK_STATUS["WAITING_ADMIN_REVIEW"], TASK_STATUS["SCREENSHOT_REJECTED"]],
    }
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем текущий статус
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    if not result:
        # Если записи нет, создаем новую
        cursor.execute('''
        INSERT INTO user_progress (user_id, current_step, assigned_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, new_status))
    else:
        current_step = result[0]
        
        # Проверяем допустимость перехода
        if current_step in allowed_transitions and new_status in allowed_transitions[current_step]:
            cursor.execute('''
            UPDATE user_progress SET current_step = ? WHERE user_id = ?
            ''', (new_status, user_id))
        else:
            # Логируем проблему, но не блокируем операцию
            logger.warning(f"Попытка нестандартного перехода из {current_step} в {new_status} для пользователя {user_id}")
            cursor.execute('''
            UPDATE user_progress SET current_step = ? WHERE user_id = ?
            ''', (new_status, user_id))
    
    conn.commit()
    conn.close()
    
def get_replacement_count(user_id):
    """Возвращает количество использованных замен"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT replacement_count FROM user_progress WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.OperationalError as e:
        # Если столбец еще не создан
        logger.warning(f"Столбец replacement_count не найден: {e}")
        return 0
    finally:
        conn.close()
    
def optimize_database():
    """Создание индексов для ускорения работы базы данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Удаляем неиспользуемые столбцы (если они есть)
    try:
        # Проверяем наличие столбцов, которые не используются в коде
        cursor.execute("PRAGMA table_info(user_progress)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Эти столбцы создаются, но не используются активно в логике
        unused_columns = ['multi_accounts', 'accounts_requested', 'photos_sent']
        
        for column in unused_columns:
            if column in columns:
                logger.info(f"Столбец {column} существует, но возможно не используется")
                # ВАЖНО: Не удаляем автоматически, только если вы уверены!
    except Exception as e:
        logger.error(f"Ошибка при проверке столбцов: {e}")
    
    # Создаем недостающие индексы для ускорения запросов
    indexes = [
        # Для поиска пользователей по активности
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
        
        # Для быстрого получения заданий по статусу
        "CREATE INDEX IF NOT EXISTS idx_user_progress_status ON user_progress(screenshot_status, current_step)",
        
        # Для ускорения работы с выплатами
        "CREATE INDEX IF NOT EXISTS idx_withdrawal_status ON withdrawal_requests(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_withdrawal_user ON withdrawal_requests(user_id, status)",
        
        # Для ускорения работы с рефералами
        "CREATE INDEX IF NOT EXISTS idx_referrals_completed ON referrals(referred_id, bonus_paid)",
        
        # Для ускорения поиска выполненных заданий
        "CREATE INDEX IF NOT EXISTS idx_completed_tasks_user ON user_completed_tasks(user_id, photo_id)",
        
        # Для ускорения работы с категориями
        "CREATE INDEX IF NOT EXISTS idx_photos_category ON photos(category_id, id)",
        
        # Для ускорения поиска по датам
        "CREATE INDEX IF NOT EXISTS idx_notifications_date ON notifications(user_id, created_at DESC)",
    ]
    
    for index_sql in indexes:
        try:
            cursor.execute(index_sql)
        except Exception as e:
            logger.error(f"Ошибка создания индекса: {e}")
    
    # Оптимизируем базу данных
    cursor.execute("PRAGMA optimize")
    cursor.execute("VACUUM")  # Дефрагментация базы данных
    
    conn.commit()
    conn.close()
    logger.info("База данных оптимизирована")        

async def delete_user_command(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    if not context.args:
        await safe_reply(update, context, 
            "❌ <b>Использование:</b>\n/dell <user_id> - удалить пользователя\n/dell @username - удалить по юзернейму",
            parse_mode="HTML"
        )
        return
    
    target = context.args[0].strip()
    
    try:
        # Определяем тип ввода (ID или username)
        if target.startswith('@'):
            # Поиск по username
            username = target[1:]
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                await safe_reply(update, context, f"❌ Пользователь @{username} не найден.")
                return
            
            user_id = result[0]
        else:
            # Поиск по ID
            user_id = int(target)
        
        # Получаем информацию о пользователе перед удалением
        user_info = get_user_info(user_id)
        if not user_info:
            await safe_reply(update, context, f"❌ Пользователь с ID {user_id} не найден.")
            return
        
        # Удаляем пользователя
        success = delete_user_completely(user_id)
        
        if success:
            user_id_db, username, first_name, last_name, phone_number, joined_at, last_active = user_info
            user_link = format_user_link(user_id_db, username, first_name, last_name)
            
            await safe_reply(update, context,
                f"✅ <b>Пользователь полностью удален!</b>\n\n"
                f"👤 {user_link}\n"
                f"🆔 ID: {user_id_db}\n"
                f"📛 Username: @{username if username else 'нет'}\n"
                f"👨‍💼 Имя: {first_name} {last_name}\n"
                f"📅 Был зарегистрирован: {joined_at}",
                parse_mode="HTML"
            )
            logger.info(f"Админ {update.effective_user.id} удалил пользователя {user_id}")
        else:
            await safe_reply(update, context, "❌ Произошла ошибка при удалении пользователя.")
            
    except ValueError:
        await safe_reply(update, context, "❌ Неверный формат ID пользователя.")
    except Exception as e:
        logger.error(f"Ошибка в команде /dell: {e}")
        await safe_reply(update, context, "❌ Произошла ошибка при выполнении команды.")

# Функция для отправки утренних сообщений
async def send_morning_messages(context: CallbackContext):
    users = get_users_waiting_for_morning()
    morning_message, send_time, video_id = get_morning_message()
    
    for user_id in users:
        try:
            if video_id:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=video_id,
                    caption=f"🌅 <b>Доброе утро!</b>\n\n{morning_message}",
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🌅 <b>Доброе утро!</b>\n\n{morning_message}",
                    parse_mode="HTML"
                )
            
            mark_morning_message_sent(user_id)
            
            # Обновляем интерфейс пользователя
            keyboard = [
                [KeyboardButton("📸 Прислать скриншот")],
                [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
                [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await context.bot.send_message(
                chat_id=user_id,
                text="📸 <b>После того как оставите отзыв, пришлите скриншот раздела 'Мои отзывы' на Авито.</b>",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке утреннего сообщения пользователю {user_id}: {e}")


# Функция для отправки вечерних напоминаний
async def send_evening_reminders(context: CallbackContext):
    users = get_users_waiting_for_evening()
    evening_reminder, send_time, video_id = get_evening_reminder()
    
    for user_id in users:
        try:
            if video_id:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=video_id,
                    caption=f"🌙 <b>Добрый вечер!</b>\n\n{evening_reminder}",
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🌙 <b>Добрый вечер!</b>\n\n{evening_reminder}",
                    parse_mode="HTML"
                )
            
            mark_evening_reminder_sent(user_id)
        except Exception as e:
            logger.error(f"Ошибка при отправке вечернего напоминания пользователю {user_id}: {e}")
            
# Добавьте эту функцию после других функций админ-панели
async def set_balance_command(update: Update, context: CallbackContext):
    """Команда для изменения баланса пользователя - РАБОЧАЯ ВЕРСИЯ"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    if len(context.args) < 3:
        await safe_reply(update, context,
            "🔧 <b>Использование:</b>\n"
            "/setbalance <user_id> <действие> <сумма>\n\n"
            "📌 <b>Действия:</b>\n"
            "• add - добавить сумму к балансу\n"
            "• set - установить точную сумму\n"
            "• sub - вычесть сумму\n\n"
            "<b>Примеры:</b>\n"
            "/setbalance 123456 add 200 - добавить 200 рублей\n"
            "/setbalance 123456 set 500 - установить баланс 500\n"
            "/setbalance 123456 sub 100 - вычесть 100 рублей",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(context.args[0])
        action = context.args[1].lower()
        amount = int(context.args[2])
        
        logger.info(f"Команда setbalance: user_id={user_id}, action={action}, amount={amount}")
        
        if action not in ['add', 'set', 'sub']:
            await safe_reply(update, context, "❌ Неверное действие. Используйте: add, set или sub")
            return
        
        if amount <= 0:
            await safe_reply(update, context, "❌ Сумма должна быть положительной")
            return
        
        # Проверяем существование пользователя
        user_info = get_user_info(user_id)
        if not user_info:
            await safe_reply(update, context, f"❌ Пользователь с ID {user_id} не найден")
            return
        
        # Получаем или создаем запись в user_progress
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем существование записи
        cursor.execute("SELECT balance FROM user_progress WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result is None:
            # Создаем запись если её нет
            logger.info(f"Создаем новую запись для пользователя {user_id}")
            cursor.execute('''
                INSERT INTO user_progress (user_id, balance, total_earned, tasks_completed)
                VALUES (?, 0, 0, 0)
            ''', (user_id,))
            conn.commit()
            current_balance = 0
        else:
            current_balance = result[0] if result[0] is not None else 0
        
        logger.info(f"Текущий баланс пользователя {user_id}: {current_balance}")
        
        # Вычисляем новый баланс
        if action == 'add':
            new_balance = current_balance + amount
            # Обновляем баланс и total_earned
            cursor.execute('''
                UPDATE user_progress 
                SET balance = ?, total_earned = COALESCE(total_earned, 0) + ?
                WHERE user_id = ?
            ''', (new_balance, amount, user_id))
        elif action == 'set':
            new_balance = amount
            # Только устанавливаем баланс
            cursor.execute('''
                UPDATE user_progress 
                SET balance = ?
                WHERE user_id = ?
            ''', (new_balance, user_id))
        elif action == 'sub':
            new_balance = max(0, current_balance - amount)
            # Вычитаем из баланса
            cursor.execute('''
                UPDATE user_progress 
                SET balance = ?
                WHERE user_id = ?
            ''', (new_balance, user_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Новый баланс пользователя {user_id}: {new_balance}")
        
        # Формируем сообщение без использования format_user_link (чтобы избежать ошибок)
        user_id_db, username, first_name, last_name, phone_number, joined_at, last_active = user_info
        
        # Используем простой формат без сложного HTML
        if username:
            user_display = f"@{username}"
        else:
            name = f"{first_name or ''} {last_name or ''}".strip()
            user_display = name if name else f"Пользователь {user_id}"
        
        action_text = {
            'add': 'добавлено',
            'set': 'установлено',
            'sub': 'вычтено'
        }
        
        message = (
            f"✅ <b>Баланс обновлен!</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_display}\n"
            f"🆔 <b>ID:</b> {user_id}\n"
            f"💰 <b>Старый баланс:</b> {current_balance}₽\n"
            f"💰 <b>Новый баланс:</b> {new_balance}₽\n"
            f"📊 <b>Действие:</b> {action_text[action]} {amount}₽"
        )
        
        await safe_reply(update, context, message, parse_mode="HTML")
        
        # Отправляем уведомление пользователю (простой текст без HTML)
        try:
            notify_text = {
                'add': f"💰 Вам начислено {amount}₽. Новый баланс: {new_balance}₽",
                'set': f"💰 Ваш баланс установлен на {amount}₽",
                'sub': f"💰 С вашего баланса списано {amount}₽. Новый баланс: {new_balance}₽"
            }
            await context.bot.send_message(
                chat_id=user_id,
                text=notify_text[action]
            )
            logger.info(f"Уведомление отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        
    except ValueError as e:
        logger.error(f"ValueError в setbalance: {e}")
        await safe_reply(update, context, 
            "❌ Неверный формат чисел. Проверьте ID и сумму.\n"
            "ID должен быть числом, сумма - целым числом."
        )
    except Exception as e:
        logger.error(f"Ошибка в команде /setbalance: {e}", exc_info=True)
        await safe_reply(update, context, 
            f"❌ Произошла ошибка при выполнении команды.\n"
            f"Детали: {str(e)[:100]}..."
        )
        
async def admin_help_command(update: Update, context: CallbackContext):
    """Справка по командам администратора (исправленная версия)"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    help_text = (
        "🔧 <b>КОМАНДЫ АДМИНИСТРАТОРА</b>\n\n"
        
        "👤 <b>Управление пользователями:</b>\n"
        "• /find @username - найти пользователя\n"
        "• /dell ID - удалить пользователя\n"
        "• /reset_task - сбросить задание (через админ-панель)\n"
        "• /setbalance ID действие сумма - изменить баланс\n"
        "   (действия: add, set, sub)\n\n"
        
        "💰 <b>Управление выплатами:</b>\n"
        "• /pay ID сумма - выплатить средства\n"
        "• /status ID_запроса - статус выплаты\n\n"
        
        "🖼️ <b>Управление заданиями:</b>\n"
        "• /deleteallphotos - удалить ВСЕ фото\n\n"
        
        "⚙️ <b>Настройки и обслуживание:</b>\n"
        "• /reset_all - сбросить все задания\n"
        "• /clean_db - очистка базы данных\n"
        "• /skip - пропустить редактирование сообщения\n"
        "• /cancel - отменить текущую операцию\n\n"
        
        "📝 <b>Быстрые команды:</b>\n"
        "• /vs(viewscreenshot) ID - посмотреть скриншот\n\n"
        
        "<i>ℹ️ Большинство функций доступно через админ-панель (кнопка 'Админ-панель')</i>\n"
        "<i>📞 Для добавления фото используйте кнопку 'Добавить фото' в редакторе</i>"
    )
    
    await safe_reply(update, context, help_text, parse_mode="HTML")

async def delete_all_photos_command(update: Update, context: CallbackContext):
    """Удаление всех фото заданий"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    # Запрашиваем подтверждение
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить ВСЕ", callback_data="confirm_delete_all_photos"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_delete_all_photos")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Получаем количество фото
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM photos")
    count = cursor.fetchone()[0]
    conn.close()
    
    await safe_reply(update, context,
        f"⚠️ <b>ВНИМАНИЕ! ВЫ УДАЛЯЕТЕ ВСЕ ФОТО ЗАДАНИЙ!</b>\n\n"
        f"📸 Всего фото в базе: {count}\n\n"
        f"❌ Это действие НЕОБРАТИМО!\n"
        f"📋 Будут удалены все задания пользователей\n\n"
        f"Вы уверены, что хотите удалить ВСЕ фото?",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def clean_database_command(update: Update, context: CallbackContext):
    """Очистка базы данных от неиспользуемых данных"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    # Проверяем структуру базы данных
    conn = get_db_connection()
    cursor = conn.cursor()
    
    message = "🧹 <b>Очистка базы данных:</b>\n\n"
    
    # 1. Удаляем старые уведомления (старше 30 дней)
    cursor.execute("DELETE FROM notifications WHERE created_at < datetime('now', '-30 days')")
    old_notifications = cursor.rowcount
    
    # 2. Удаляем пользователей, которые не активны более 90 дней
    cursor.execute("DELETE FROM users WHERE last_active < datetime('now', '-90 days')")
    old_users = cursor.rowcount
    
    # 3. Очищаем старые записи из user_progress для завершенных заданий (старше 60 дней)
    cursor.execute('''
        DELETE FROM user_progress 
        WHERE completed_at IS NOT NULL 
        AND completed_at < datetime('now', '-60 days')
    ''')
    old_progress = cursor.rowcount
    
    # 4. Проверяем неиспользуемые столбцы
    # В вашем коде есть столбцы, которые могут быть не нужны:
    # - multi_accounts, accounts_requested, photos_sent в user_progress
    # Они создаются, но в коде не используются активно
    
    conn.commit()
    
    # Получаем статистику базы
    cursor.execute("SELECT COUNT(*) FROM photos")
    photo_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_progress WHERE current_step != 'cancelled'")
    active_tasks = cursor.fetchone()[0]
    
    conn.close()
    
    message += (
        f"✅ <b>Очистка выполнена:</b>\n\n"
        f"🗑️ Удалено старых уведомлений: {old_notifications}\n"
        f"👤 Удалено неактивных пользователей: {old_users}\n"
        f"📋 Очищено старых заданий: {old_progress}\n\n"
        f"📊 <b>Текущее состояние:</b>\n"
        f"🖼️ Фото заданий: {photo_count}\n"
        f"👥 Пользователей: {user_count}\n"
        f"📝 Активных заданий: {active_tasks}\n\n"
        f"💡 <i>Для удаления неиспользуемых столбцов нужен прямой доступ к БД</i>"
    )
    
    await safe_reply(update, context, message, parse_mode="HTML")    
            
async def show_main_menu(update: Update, context: CallbackContext, user_id=None):
    if not user_id:
        user_id = update.effective_user.id
    
    update_user_activity(user_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_step FROM user_progress WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    user_step = row[0] if row else TASK_STATUS["GET_TASK"]

    # Определяем кнопку в зависимости от статуса
    if user_step == TASK_STATUS["SEND_SCREENSHOT"]:
        task_btn = KeyboardButton("📸 Прислать скриншот")
    elif user_step in [TASK_STATUS["COMPLETED"], TASK_STATUS["SCREENSHOT_REJECTED"], TASK_STATUS["CANCELLED"], TASK_STATUS["GET_TASK"]]:
        task_btn = KeyboardButton("Получить задание")
    else:
        task_btn = KeyboardButton("Мое задание")  # Для просмотра текущего задания

    if user_id == ADMIN_ID:
        keyboard = [
            [task_btn, KeyboardButton("Мой профиль")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("📊 Статистика")],
            [KeyboardButton("🔧 Админ-панель"), KeyboardButton("ℹ️ Информация")]
        ]
    else:
        keyboard = [
            [task_btn, KeyboardButton("Мой профиль")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("💎 Реферальная система"), KeyboardButton("📞 Поддержка")]
        ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.callback_query:
        await update.callback_query.message.reply_text("Главное меню:", reply_markup=reply_markup)
    else:
        await safe_reply(update, context, "Главное меню:", reply_markup=reply_markup)
     
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    
    # Проверяем, существует ли пользователь уже в базе
    existing_user = get_user_info(user.id)
    
    if existing_user:
        # Пользователь уже существует - просто обновляем данные
        add_user(user.id, user.username, user.first_name, user.last_name)
        user_step = get_user_step(user.id)
        
        welcome_text = (
            f"👋 <b>С возвращением, {user.first_name}!</b>\n\n"
            "🤖 <b>Я бот для выполнения заданий с реальной оплатой на следующий день!</b>\n\n"
            "💵 <b>Мы платим за выполнение заданий:</b>\n"
            "• За оставленный отзыв на Авито/2-гис/Яндекс-Карты: от 200 рублей\n"
            "• За приглашенного друга: 50 рублей\n\n"
            "<i>Мы дорожим своей репутацией и всегда выполняем обязательства!</i>\n"
            "📞 <b>Поддержка 24/7:</b> @denvr11\n\n"
        )
        
        # Сразу показываем главное меню с кнопками
        await show_main_menu(update, context, user.id)
        
        # И отправляем приветственное сообщение
        await safe_reply(update, context, welcome_text, parse_mode="HTML")
        return
    
    # НОВЫЙ ПОЛЬЗОВАТЕЛЬ - добавляем с реферальными проверками
    add_user(user.id, user.username, user.first_name, user.last_name)
    
    # ОБРАБОТКА РЕФЕРАЛЬНОЙ ССЫЛКИ С УЛУЧШЕННОЙ ЗАЩИТОЙ
    if context.args:
        try:
            referrer_id = int(context.args[0])
            
            logger.info(f"Попытка реферальной регистрации: новый пользователь {user.id}, реферер {referrer_id}")
            
            # 1. ЗАПРЕТ САМОПРИГЛАШЕНИЯ
            if referrer_id == user.id:
                logger.warning(f"Попытка самоприглашения пользователем {user.id}")
                # Просто продолжаем без реферала
                pass
            else:
                # 2. ПРОВЕРКА СУЩЕСТВОВАНИЯ РЕФЕРЕРА
                referrer_info = get_user_info(referrer_id)
                if not referrer_info:
                    logger.warning(f"Реферер {referrer_id} не существует (пользователь {user.id})")
                    # Просто продолжаем без реферала
                    pass
                else:
                    # 3. ПРОВЕРКА ЧТО ПОЛЬЗОВАТЕЛЬ ЕЩЕ НЕ ЯВЛЯЕТСЯ ЧЬИМ-ТО РЕФЕРАЛОМ
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM referrals WHERE referred_id = ?", (user.id,))
                    existing_referral = cursor.fetchone()
                    
                    if existing_referral:
                        logger.warning(f"Пользователь {user.id} уже является чьим-то рефералом")
                        conn.close()
                    else:
                        # 4. ПРОВЕРКА ЧТО РЕФЕРЕР НЕ ЯВЛЯЕТСЯ РЕФЕРАЛОМ ЭТОГО ПОЛЬЗОВАТЕЛЯ (защита от циклов)
                        cursor.execute("SELECT id FROM referrals WHERE referrer_id = ? AND referred_id = ?", 
                                     (user.id, referrer_id))
                        reverse_referral = cursor.fetchone()
                        
                        if reverse_referral:
                            logger.warning(f"Обнаружена попытка циклической реферальной связи: {user.id} -> {referrer_id}")
                            conn.close()
                        else:
                            # 5. ДОБАВЛЯЕМ РЕФЕРАЛА С ПРОВЕРКОЙ UNIQUE
                            try:
                                cursor.execute('''
                                INSERT OR IGNORE INTO referrals (referrer_id, referred_id)
                                VALUES (?, ?)
                                ''', (referrer_id, user.id))
                                
                                if cursor.rowcount > 0:
                                    logger.info(f"Успешно добавлен реферал: {user.id} приглашен пользователем {referrer_id}")
                                    
                                    # Отправляем уведомление рефереру
                                    try:
                                        await context.bot.send_message(
                                            chat_id=referrer_id,
                                            text=f"🎉 У вас новый реферал! Пользователь {user.first_name} зарегистрировался по вашей ссылке.\n"
                                                 f"Вы получите 50₽ после того как он выполнит свое первое задание."
                                        )
                                    except Exception as e:
                                        logger.error(f"Не удалось отправить уведомление рефереру {referrer_id}: {e}")
                                else:
                                    logger.info(f"Реферальная запись уже существует для пользователя {user.id}")
                                    
                            except sqlite3.IntegrityError as e:
                                logger.error(f"Ошибка UNIQUE constraint при добавлении реферала: {e}")
                            
                            conn.commit()
                            conn.close()
        
        except (ValueError, TypeError) as e:
            logger.error(f"Ошибка обработки реферальной ссылки: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при обработке реферальной ссылки: {e}")
    
    user_step = get_user_step(user.id)
    
    welcome_text = (
        f"👋 <b>Добро пожаловать, {user.first_name}!</b>\n\n"
        "🤖 <b>Я бот для выполнения заданий с реальной оплатой на следующий день!</b>\n\n"
        "💵 <b>Мы платим за выполнение заданий:</b>\n"
        "• За оставленный отзыв на Авито/2-гис/Яндекс-Карты: от 200 рублей\n"
        "• За приглашенного друга: 50 рублей\n\n"
        "🎯 <b>Как начать:</b>\n"
        "1. Нажмите кнопку <b>Получить задание</b>\n"
        "2. Выполнить задание\n"
        "3. Получите оплату по СБП/номер карты/баланс телефона и прочее (только в РУБ)!\n\n"
        "<i>Мы дорожим своей репутацией и всегда выполняем обязательства!</i>\n"
        "📞 <b>Поддержка 24/7:</b> @denvr11\n\n"
    )
    
    # Сразу показываем главное меню с кнопками
    await show_main_menu(update, context, user.id)
    
    # И отправляем приветственное сообщение
    await safe_reply(update, context, welcome_text, parse_mode="HTML")


async def edit_info_buttons(update: Update, context: CallbackContext):#ИНФОРМАЦИЯ РЕДАКТОР
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    buttons = get_info_buttons()
    
    message = "📝 <b>Редактирование информационных кнопок:</b>\n\n"
    
    for button in buttons:
        button_id, title, content, order_index, created_at = button
        message += f"🆔 {button_id}: {title}\n"
    
    message += "\nВыберите кнопку для редактирования:"
    
    # Создаем клавиатуру с кнопками для редактирования
    keyboard = []
    for button in buttons:
        button_id, title, content, order_index, created_at = button
        keyboard.append([InlineKeyboardButton(f"✏️ {title}", callback_data=f"edit_info_button_{button_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=BACK_TO_EDITOR)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

# Показать интерфейс пользователя в зависимости от шага
async def show_user_interface(update: Update, context: CallbackContext, user_id, user_step):
    if user_step == TASK_STATUS["CONFIRM_CALL"]:
        keyboard = [
            [KeyboardButton("✅ Готово"), KeyboardButton("🆘 Помощь в задании")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "После выполнения нажмите <b>✅ Готово</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["WAITING_REVIEW_DAY"]:
        keyboard = [
            [KeyboardButton("📞 Связаться с админом")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "✅ <b>Вы подтвердили выполнение задания! Отлично!</b>\n\n"
            "🕘 <b>Завтра утром я пришлю вам инструкцию по оставлению отзыва.</b>\n"
            "📝 <b>Пока ничего делать не нужно.</b>\n\n"
            f"📞 <b>Если возникнут вопросы:</b> {ADMIN_USERNAME}",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["WAITING_REVIEW_EVENING"]:
        keyboard = [
            [KeyboardButton("📸 Прислать скриншот")],
            [KeyboardButton("📋 Показать задание"), KeyboardButton("🆘 Помощь в задании")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "🌅 <b>Утром присылал видео-инструкцию,там показан принцип оставления отзыва.</b>\n\n"
            "📝 <b>Отзыв рекомендуем оставлять вечером, так вероятность что Ваш отзыв пройдет модерацию Авито - больше. (Но если неудобно, можете вечером по вашему времени и прислать скриншот)</b>\n\n"
            "🌙 <b>В 19:00 по МСК я пришлю примерный текст отзыва. Если Ваш часовой пояс разнится с Московским, вы можете оставить отзыв в любое время сегодня НО вероятность отклонения такого отзыва выше, чем вечером.</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["SEND_SCREENSHOT"]:
        keyboard = [
            [KeyboardButton("📸 Прислать скриншот")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "📸 <b>Пришлите скриншот раздела 'Мои отзывы' в профиле на Авито.</b>\n\n"
            "💵 <b>После проверки скриншота вы получите 200 рублей!</b>\n\n"
            "📞 <b>Если возникли проблемы:</b> @denvr11",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["WAITING_ADMIN_REVIEW"]:
        keyboard = [
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "✅ <b>Скриншот отправлен на проверку администратору!</b>\n\n"
            "⏳ <b>Обычно проверка занимает до 24 часов.</b>\n\n"
            "📞 <b>Если возникли вопросы:</b> @denvr11",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["COMPLETED"]:
        keyboard = [
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("💎 Реферальная система"), KeyboardButton("📞 Поддержка")],
            [KeyboardButton("Получить задание"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            "🎉 <b>Поздравляем! Задание успешно проверено администрацией!</b>\n\n"
            "💰 <b>Ваш баланс пополнен на 200 рублей. Его можно проверить и вывести во вкладке профиль.</b>\n\n"
            "📅 <b>Вы можете получить новое задание!</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    elif user_step == TASK_STATUS["SCREENSHOT_REJECTED"]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT admin_review_comment FROM user_progress WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        comment = result[0] if result else "Не указана"
        conn.close()
        
        keyboard = [
            [KeyboardButton("📸 Прислать скриншот")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            user_id,
            f"❌ <b>Скриншот отклонен администратором.</b>\n\n"
            f"📝 <b>Комментарий:</b> {comment}\n\n"
            f"📸 <b>Не переживайте, в этот раз модерация не пропустила отзыв, можете попробовать взять новое задание.</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

# Показать профиль пользователя
async def show_profile(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    user_info = get_user_info(user_id)
    if user_info:
        user_id, username, first_name, last_name, phone_number, joined_at, last_active = user_info
        
        balance = get_user_balance(user_id)
        task_info = get_user_task(user_id)
        
        profile_text = (
            f"👤 <b>Ваш профиль:</b>\n\n"
            f"🆔 <b>ID:</b> {user_id}\n"
            f"📛 <b>Имя:</b> {first_name} {last_name}\n"
            f"📞 <b>Телефон:</b> {phone_number or 'не указан'}\n"
            f"💰 <b>Баланс:</b> {balance} рублей\n"
            f"📅 <b>Зарегистрирован:</b> {joined_at}\n"
        )
        
        if task_info:
            _, _, assigned_at_str, called, called_confirmed, screenshot_sent, current_step, accounts_requested, _ = task_info
            if called:
                profile_text += f"✅ <b>Статус:</b> Задание выполнено\n"
                profile_text += f"📅 <b>Выполнено:</b> {assigned_at_str}\n"
            else:
                profile_text += f"🟡 <b>Статус:</b> Задание в процессе\n"
        

        # Реферальная статистика
        reg_count, comp_count = get_referral_stats(user_id)
        profile_text += f"\n💎 <b>Рефералы:</b> зарегистрировалось — {reg_count}, завершили задание — {comp_count}\n"
        # Клавиатура для профиля
        keyboard = [
            [KeyboardButton("🔔 Уведомления"), KeyboardButton("💰 Баланс")],
            [KeyboardButton("💎 Реферальная система"), KeyboardButton("📞 Поддержка")],
            [KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context, profile_text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await safe_reply(update, context, "❌ Информация о профиле не найдена.")

# Показать баланс
async def show_balance(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    balance = get_user_balance(user_id)
    
    # Получаем историю выводов
    withdrawal_history = get_user_withdrawal_history(user_id, limit=5)
    
    # Создаем клавиатуру
    keyboard = [
        [KeyboardButton("💸 Вывести средства"), KeyboardButton("📋 История выводов")],
        [KeyboardButton("💳 Мои реквизиты")],
        [KeyboardButton("ℹ️ Информация"), KeyboardButton("📞 Поддержка")],
        [KeyboardButton("Меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Формируем сообщение с балансом
    message = f"💰 <b>Ваш баланс:</b> {balance} рублей\n\n"
    
    if balance >= 50:
        message += "✅ <b>Вы можете вывести средства!</b>\n"
        message += f"💸 <b>Минимальная сумма:</b> 50 рублей\n"
        message += f"⏰ <b>Время обработки:</b> до 24 часов\n\n"
    else:
        message += "❌ <b>Недостаточно средств для вывода</b>\n"
        message += f"💸 <b>Минимальная сумма:</b> 50 рублей\n\n"
    
    # Добавляем информацию о выводе
    pending_withdrawals = get_user_pending_withdrawals(user_id)
    if pending_withdrawals:
        total_pending = sum([w[2] for w in pending_withdrawals])
        message += f"⏳ <b>Ожидает выплаты:</b> {total_pending} рублей\n"
        message += f"📋 <b>Количество запросов:</b> {len(pending_withdrawals)}\n\n"
    
    # Добавляем последние выводы
    if withdrawal_history:
        message += "📋 <b>Последние операции:</b>\n"
        for withdrawal in withdrawal_history[:3]:  # Показываем только 3 последние
            w_id, w_user_id, amount, method, details, status, comment, created_at, processed_at, completed_at = withdrawal[:10]
            status_icons = {
                'pending': '⏳',
                'approved': '✅',
                'rejected': '❌',
                'completed': '💸'
            }
            icon = status_icons.get(status, '❓')
            date_str = created_at.split()[0] if created_at else ''
            
            # Сокращаем реквизиты для отображения
            short_details = details[:10] + "..." if len(details) > 10 else details
            
            method_names = {
                'card': '💳',
                'qiwi': '📱',
                'yoomoney': '🧾',
                'phone': '☎️',
                'sber': '🏦'
            }
            method_icon = method_names.get(method, '💳')
            
            message += f"{icon}{method_icon} {amount} руб. ({status}) {date_str}\n"
    
    message += "\n💡 <i>Для вывода средств нажмите '💸 Вывести средства'</i>"
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

async def show_withdrawal_menu(update: Update, context: CallbackContext):
    """Меню вывода средств"""
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    # Очищаем все предыдущие данные о выводе
    for key in ['waiting_for_withdrawal_details', 'waiting_for_withdrawal_amount', 
                'withdrawal_method', 'withdrawal_method_name', 'withdrawal_details', 'withdrawal_amount']:
        context.user_data.pop(key, None)
    
    balance = get_user_balance(user_id)
    
    if balance < 50:
        keyboard = [[KeyboardButton("💰 Баланс"), KeyboardButton("Меню")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context, 
            f"❌ <b>Недостаточно средств для вывода</b>\n\n"
            f"💰 Ваш баланс: {balance} рублей\n"
            f"💸 Минимальная сумма вывода: 50 рублей\n\n"
            f"✅ Выполните задание, чтобы заработать больше!",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
    
    # Получаем сохраненные реквизиты пользователя
    payment_methods = get_user_payment_methods(user_id)
    
    # Создаем клавиатуру с методами выплат
    keyboard = [
        [KeyboardButton("💳 Банковская карта"), KeyboardButton("📱 Qiwi")],
        [KeyboardButton("🧾 ЮMoney"), KeyboardButton("☎️ Баланс телефона")],
        [KeyboardButton("🏦 Сбербанк Онлайн")],
        [KeyboardButton("🔙 Назад к балансу")]  # Измененная кнопка
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    message = (
        f"💸 <b>Вывод средств</b>\n\n"
        f"💰 <b>Ваш баланс:</b> {balance} рублей\n"
        f"💸 <b>Минимальная сумма:</b> 50 рублей\n"
        f"⏰ <b>Время обработки:</b> до 24 часов\n\n"
        f"<b>Выберите способ получения средств:</b>\n\n"
    )
    
    # Добавляем информацию о сохраненных реквизитах
    if payment_methods:
        message += "💡 <b>Ваши сохраненные реквизиты:</b>\n"
        if payment_methods[2]:  # card_number
            message += f"💳 Карта: {payment_methods[2][:8]}...{payment_methods[2][-4:]}\n"
        if payment_methods[3]:  # qiwi_wallet
            message += f"📱 Qiwi: {payment_methods[3]}\n"
        if payment_methods[4]:  # yoomoney_wallet
            message += f"🧾 ЮMoney: {payment_methods[4]}\n"
        if payment_methods[5]:  # phone_number
            message += f"☎️ Телефон: {payment_methods[5]}\n"
        if payment_methods[6]:  # sber_account
            message += f"🏦 Сбербанк: {payment_methods[6]}\n"
        message += "\n"
    
    message += "💡 <i>Реквизиты будут сохранены для будущих выплат</i>"
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

async def handle_withdrawal_method(update: Update, context: CallbackContext):
    """Обработчик выбора способа выплаты"""
    user_id = update.effective_user.id
    text = update.message.text
    
    method_map = {
        "💳 Банковская карта": "card",
        "📱 Qiwi": "qiwi", 
        "🧾 ЮMoney": "yoomoney",
        "☎️ Баланс телефона": "phone",
        "🏦 Сбербанк Онлайн": "sber"
    }
    
    if text in method_map:
        context.user_data['withdrawal_method'] = method_map[text]
        context.user_data['withdrawal_method_name'] = text
        
        # Получаем сохраненные реквизиты для этого метода
        payment_methods = get_user_payment_methods(user_id)
        saved_details = None
        
        if payment_methods:
            if method_map[text] == "card" and payment_methods[2]:
                saved_details = payment_methods[2]
            elif method_map[text] == "qiwi" and payment_methods[3]:
                saved_details = payment_methods[3]
            elif method_map[text] == "yoomoney" and payment_methods[4]:
                saved_details = payment_methods[4]
            elif method_map[text] == "phone" and payment_methods[5]:
                saved_details = payment_methods[5]
            elif method_map[text] == "sber" and payment_methods[6]:
                saved_details = payment_methods[6]
        
        # Если есть сохраненные реквизиты, предлагаем использовать их
        if saved_details:
            keyboard = [
                [KeyboardButton(f"✅ Использовать: {saved_details}")],
                [KeyboardButton("📝 Ввести новые реквизиты")],
                [KeyboardButton("🔙 Назад к выбору метода")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await safe_reply(update, context,
                f"💸 <b>Вы выбрали:</b> {text}\n\n"
                f"📝 <b>Сохраненные реквизиты:</b> {saved_details}\n\n"
                f"Вы хотите использовать сохраненные реквизиты или ввести новые?",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            context.user_data['saved_details'] = saved_details
            context.user_data['waiting_for_details_choice'] = True
        else:
            # Запрашиваем реквизиты
            method_instructions = {
                "card": "Введите номер банковской карты (16-19 цифр):",
                "qiwi": "Введите номер QIWI кошелька (в формате +79001234567):",
                "yoomoney": "Введите номер ЮMoney кошелька:",
                "phone": "Введите номер телефона для пополнения баланса:",
                "sber": "Введите номер телефона или карты Сбербанка:"
            }
            
            keyboard = [[KeyboardButton("🔙 Назад к выбору метода")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await safe_reply(update, context,
                f"💸 <b>Вы выбрали:</b> {text}\n\n"
                f"📝 {method_instructions[method_map[text]]}\n\n"
                f"💡 <i>Реквизиты будут сохранены для будущих выплат</i>",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            context.user_data['waiting_for_withdrawal_details'] = True
    
    elif text == "🔙 Назад к балансу":
        await show_balance(update, context)
    elif text == "🔙 Назад к выбору метода":
        await show_withdrawal_menu(update, context)
        
async def handle_details_choice(update: Update, context: CallbackContext):
    """Обработчик выбора: использовать сохраненные реквизиты или ввести новые"""
    if not context.user_data.get('waiting_for_details_choice'):
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
    if text.startswith("✅ Использовать:"):
        # Используем сохраненные реквизиты
        details = context.user_data['saved_details']
        context.user_data['withdrawal_details'] = details
        
        # Переходим к вводу суммы
        balance = get_user_balance(user_id)
        await safe_reply(update, context,
            f"✅ <b>Используем сохраненные реквизиты!</b>\n\n"
            f"📝 <b>Реквизиты:</b> {details}\n\n"
            f"💰 <b>Ваш баланс:</b> {balance} рублей\n\n"
            f"<b>Введите сумму для вывода (мин. 50 рублей):</b>",
            parse_mode="HTML"
        )
        
        context.user_data['waiting_for_details_choice'] = False
        context.user_data['waiting_for_withdrawal_amount'] = True
        
    elif text == "📝 Ввести новые реквизиты":
        # Запрашиваем новые реквизиты
        method = context.user_data['withdrawal_method']
        method_name = context.user_data['withdrawal_method_name']
        
        method_instructions = {
            "card": "Введите номер банковской карты (16-19 цифр):",
            "qiwi": "Введите номер QIWI кошелька (в формате +79001234567):",
            "yoomoney": "Введите номер ЮMoney кошелька:",
            "phone": "Введите номер телефона для пополнения баланса:",
            "sber": "Введите номер телефона или карты Сбербанка:"
        }
        
        await safe_reply(update, context,
            f"💸 <b>Вы выбрали:</b> {method_name}\n\n"
            f"📝 {method_instructions[method]}",
            parse_mode="HTML"
        )
        
        context.user_data['waiting_for_details_choice'] = False
        context.user_data['waiting_for_withdrawal_details'] = True
    
    elif text == "🔙 Назад к выбору метода":
        await show_withdrawal_menu(update, context)
        context.user_data['waiting_for_details_choice'] = False

# В функции handle_withdrawal_details добавьте кнопку "Назад" при запросе суммы:

async def handle_withdrawal_details(update: Update, context: CallbackContext):
    """Обработчик ввода реквизитов"""
    if not context.user_data.get('waiting_for_withdrawal_details'):
        return
    
    user_id = update.effective_user.id
    details = update.message.text.strip()
    method = context.user_data.get('withdrawal_method')
    method_name = context.user_data.get('withdrawal_method_name')
    
    # Проверяем, не нажата ли кнопка "Назад"
    if details == "🔙 Назад к выбору метода":
        await show_withdrawal_menu(update, context)
        context.user_data.pop('waiting_for_withdrawal_details', None)
        return
    
    # Валидация реквизитов
    if method == "card":
        # Проверяем номер карты (16-19 цифр)
        card_clean = re.sub(r'\D', '', details)
        if not (16 <= len(card_clean) <= 19):
            keyboard = [[KeyboardButton("🔙 Назад к выбору метода")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await safe_reply(update, context,
                "❌ <b>Неверный номер карты!</b>\n\n"
                "Номер карты должен содержать 16-19 цифр.\n"
                "Пример: 1234567812345678\n\n"
                "Введите номер карты еще раз:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return
        details = card_clean
    
    elif method in ["qiwi", "phone", "sber"]:
        # Проверяем номер телефона
        phone_clean = re.sub(r'\D', '', details)
        if not (10 <= len(phone_clean) <= 15):
            keyboard = [[KeyboardButton("🔙 Назад к выбору метода")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await safe_reply(update, context,
                "❌ <b>Неверный номер телефона!</b>\n\n"
                "Введите номер телефона в формате +79001234567\n"
                "Или введите 10-15 цифр без пробелов\n\n"
                "Введите номер еще раз:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return
        details = phone_clean
    
    # Сохраняем реквизиты
    context.user_data['withdrawal_details'] = details
    
    # Запрашиваем сумму с кнопкой "Назад"
    balance = get_user_balance(user_id)
    keyboard = [[KeyboardButton("🔙 Назад к балансу")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await safe_reply(update, context,
        f"✅ <b>Реквизиты сохранены!</b>\n\n"
        f"💸 <b>Способ выплаты:</b> {method_name}\n"
        f"📝 <b>Реквизиты:</b> {details}\n\n"
        f"💰 <b>Ваш баланс:</b> {balance} рублей\n"
        f"💸 <b>Минимальная сумма:</b> 50 рублей\n\n"
        f"<b>Введите сумму для вывода:</b>\n\n"
        f"💡 <i>Или нажмите 'Назад', чтобы изменить реквизиты</i>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    
    context.user_data['waiting_for_withdrawal_details'] = False
    context.user_data['waiting_for_withdrawal_amount'] = True

async def handle_withdrawal_amount(update: Update, context: CallbackContext):
    """Обработчик ввода суммы"""
    if not context.user_data.get('waiting_for_withdrawal_amount'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # Проверяем, не нажата ли кнопка "Назад"
    if text == "🔙 Назад к балансу":
        # Очищаем состояние и возвращаемся к вводу реквизитов
        context.user_data.pop('waiting_for_withdrawal_amount', None)
        
        # Показываем инструкцию по вводу реквизитов
        method = context.user_data.get('withdrawal_method')
        method_name = context.user_data.get('withdrawal_method_name')
        
        method_instructions = {
            "card": "Введите номер банковской карты (16-19 цифр):",
            "qiwi": "Введите номер QIWI кошелька (в формате +79001234567):",
            "yoomoney": "Введите номер ЮMoney кошелька:",
            "phone": "Введите номер телефона для пополнения баланса:",
            "sber": "Введите номер телефона или карты Сбербанка:"
        }
        
        keyboard = [[KeyboardButton("🔙 Назад к выбору метода")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context,
            f"💸 <b>Вы выбрали:</b> {method_name}\n\n"
            f"📝 {method_instructions[method]}",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        context.user_data['waiting_for_withdrawal_details'] = True
        return
    
    try:
        amount = int(text)
        
        # Проверяем возможность вывода
        can_withdraw, error_message = can_user_withdraw(user_id, amount)
        
        if not can_withdraw:
            keyboard = [[KeyboardButton("🔙 Назад к балансу")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await safe_reply(update, context,
                f"❌ <b>{error_message}</b>\n\n"
                f"💰 Ваш баланс: {get_user_balance(user_id)} рублей\n\n"
                f"Введите другую сумму или нажмите 'Назад':",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return
        
        # Получаем сохраненные данные
        method = context.user_data.get('withdrawal_method')
        method_name = context.user_data.get('withdrawal_method_name')
        details = context.user_data.get('withdrawal_details')
        
        # Создаем клавиатуру подтверждения
        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить вывод", callback_data=f"confirm_withdrawal_{amount}"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_withdrawal")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Показываем подтверждение
        await safe_reply(update, context,
            f"💸 <b>Подтверждение вывода средств</b>\n\n"
            f"💰 <b>Сумма:</b> {amount} рублей\n"
            f"💳 <b>Способ:</b> {method_name}\n"
            f"📝 <b>Реквизиты:</b> {details}\n\n"
            f"💡 <b>Информация:</b>\n"
            f"• Баланс после вывода: {get_user_balance(user_id) - amount} рублей\n"
            f"• Время обработки: до 24 часов\n"
            f"• Комиссия: нет\n\n"
            f"<i>После подтверждения средства будут зарезервированы, "
            f"а запрос отправлен администратору на обработку.</i>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        # Сохраняем данные для подтверждения
        context.user_data['withdrawal_amount'] = amount
        
        # Убираем состояние ожидания суммы
        context.user_data.pop('waiting_for_withdrawal_amount', None)
        
    except ValueError:
        keyboard = [[KeyboardButton("🔙 Назад к балансу")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context,
            "❌ <b>Неверная сумма!</b>\n\n"
            "Введите сумму цифрами (например: 200):\n\n"
            "💡 Минимальная сумма: 50 рублей",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
async def withdrawal_status_command(update: Update, context: CallbackContext):
    """Команда для проверки статуса выплаты"""
    user_id = update.effective_user.id
    
    if context.args:
        try:
            request_id = int(context.args[0])
            request = get_withdrawal_request(request_id)
            
            if not request or request[1] != user_id:  # Проверяем, что запрос принадлежит пользователю
                await safe_reply(update, context,
                    "❌ Запрос не найден или у вас нет доступа к нему.",
                    parse_mode="HTML"
                )
                return
            
            w_id, user_id, amount, method, details, status, comment, created_at, processed_at, completed_at = request[:10]
            
            status_icons = {
                'pending': '⏳ Ожидает проверки',
                'approved': '✅ Одобрено',
                'rejected': '❌ Отклонено',
                'completed': '💸 Выплачено'
            }
            
            method_names = {
                'card': '💳 Карта',
                'qiwi': '📱 Qiwi', 
                'yoomoney': '🧾 ЮMoney',
                'phone': '☎️ Телефон',
                'sber': '🏦 Сбербанк'
            }
            
            message = f"📋 <b>Статус выплаты #{w_id}</b>\n\n"
            message += f"💰 <b>Сумма:</b> {amount} рублей\n"
            message += f"💳 <b>Способ:</b> {method_names.get(method, method)}\n"
            message += f"📝 <b>Реквизиты:</b> {details}\n"
            message += f"📊 <b>Статус:</b> {status_icons.get(status, status)}\n"
            message += f"📅 <b>Дата создания:</b> {created_at}\n"
            
            if comment:
                message += f"💬 <b>Комментарий:</b> {comment}\n"
            
            if status == 'completed' and completed_at:
                message += f"✅ <b>Выплачено:</b> {completed_at}\n"
            
            await safe_reply(update, context, message, parse_mode="HTML")
            
        except ValueError:
            await safe_reply(update, context,
                "❌ Неверный формат ID. Используйте: /status ID_запроса",
                parse_mode="HTML"
            )
    else:
        # Показываем последние запросы
        history = get_user_withdrawal_history(user_id, limit=5)
        
        if not history:
            await safe_reply(update, context,
                "📋 <b>У вас еще не было выводов средств</b>",
                parse_mode="HTML"
            )
            return
        
        message = "📋 <b>Ваши последние запросы:</b>\n\n"
        
        for withdrawal in history:
            w_id, w_user_id, amount, method, details, status, comment, created_at, processed_at, completed_at = withdrawal[:10]
            
            status_icons = {
                'pending': '⏳',
                'approved': '✅',
                'rejected': '❌',
                'completed': '💸'
            }
            
            icon = status_icons.get(status, '❓')
            date_str = created_at.split()[0] if created_at else ''
            
            message += f"{icon} <b>#{w_id}</b> | {date_str}\n"
            message += f"💰 {amount} руб. | Статус: {status}\n\n"
        
        message += "📝 <b>Для проверки статуса конкретной выплаты:</b>\n"
        message += "<code>/status ID_запроса</code>\n"
        message += "Например: <code>/status 123</code>"
        
        await safe_reply(update, context, message, parse_mode="HTML")

async def check_new_withdrawals(context: CallbackContext):
    """Периодическая проверка новых выплат"""
    try:
        # Получаем количество новых выплат
        pending_count = get_pending_withdrawals_count()
        
        if pending_count > 0:
            # Отправляем напоминание админу
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
                     f"У вас {pending_count} ожидающих выплат.\n"
                     f"Проверьте раздел '💸 Выплаты' в админ-панели.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Ошибка при проверке новых выплат: {e}")

# Показать уведомления
async def show_notifications(update: Update, context: CallbackContext):
    """Показывает уведомления с группировкой по типу"""
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    notifications = get_unread_notifications(user_id)
    
    if not notifications:
        await safe_reply(update, context, "📭 У вас нет новых уведомлений.")
        return
    
    # Группируем уведомления по типу
    grouped = {}
    for notification in notifications:
        # Получаем данные уведомления
        # В зависимости от структуры базы, может быть 5 или 6 колонок
        if len(notification) == 6:
            notif_id, user_id_db, message, notification_type, is_read, created_at = notification
        else:
            # Обратная совместимость: если 5 колонок
            notif_id, user_id_db, message, is_read, created_at = notification
            notification_type = "info"  # Значение по умолчанию
        
        if notification_type not in grouped:
            grouped[notification_type] = []
        grouped[notification_type].append((notif_id, message, created_at))
    
    # Отправляем группированные уведомления
    for ntype, notifs in grouped.items():
        message = f"<b>Уведомления ({ntype}):</b>\n\n"
        for notif_id, msg, created_at in notifs[:10]:  # Ограничиваем 10 на тип
            message += f"• {msg}\n🕒 {created_at}\n\n"
            mark_notification_read(notif_id)
        
        if len(notifs) > 10:
            message += f"📋 И еще {len(notifs) - 10} уведомлений..."
        
        await safe_reply(update, context, message, parse_mode="HTML")
        
async def send_enhanced_notification(user_id: int, text: str, context: CallbackContext, notification_type="info"):
    """Отправляет улучшенное уведомление"""
    # Сохраняем уведомление в базу
    add_notification(user_id, text, notification_type)
    
    try:
        # Добавляем эмодзи в зависимости от типа
        if notification_type == "success":
            prefix = "✅ "
        elif notification_type == "warning":
            prefix = "⚠️ "
        elif notification_type == "error":
            prefix = "❌ "
        elif notification_type == "payment":
            prefix = "💰 "
        else:
            prefix = "ℹ️ "
        
        # Отправляем пользователю
        await context.bot.send_message(
            chat_id=user_id,
            text=f"{prefix}{text}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

# Показать информационное сообщение с кнопками
async def show_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    buttons = get_info_buttons()
    
    # Создаем клавиатуру с кнопками
    keyboard = []
    for button in buttons:
        button_id, title, content, order_index, created_at = button
        keyboard.append([InlineKeyboardButton(title, callback_data=f"info_{button_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_message = (
        "ℹ️ <b>Информация о нашем сервисе</b>\n\n"
        "Выберите интересующий вас раздел:"
    )
    
    await safe_reply(update, context, 
        welcome_message,
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    
async def reset_all_tasks_command(update: Update, context: CallbackContext):
    """Команда для сброса всех заданий - /reset_all"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    try:
        # Выполняем сброс всех заданий
        affected_count = reset_all_tasks()
        
        await safe_reply(update, context,
            f"✅ <b>Массовый сброс выполнен!</b>\n\n"
            f"📊 Сброшено заданий: {affected_count}\n\n"
            f"Все пользователи теперь могут получить новые задания.",
            parse_mode="HTML"
        )
        
        # Логируем действие
        logger.info(f"Админ {update.effective_user.id} сбросил все задания. Затронуто: {affected_count}")
        
    except Exception as e:
        logger.error(f"Ошибка при массовом сбросе заданий: {e}")
        await safe_reply(update, context, 
            "❌ <b>Произошла ошибка при сбросе заданий.</b>",
            parse_mode="HTML"
        )
        
# Реферальная система
async def show_referral_system(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)

    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    registered, completed = get_referral_stats(user_id)

    msg = (
        f"💎 <b>Реферальная система</b>\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n{referral_link}\n\n"
        f"📌 <b>Правила начислений:</b>\n"
        f"• По 50₽ за каждого, кто успешно завершил хотя бы одно задание.\n"
        f"• Если пригласили более 5 → вы  получаете статус партнёра и 10% от дохода каждого пригласившего (с 200₽ = 20₽).\n\n"
        f"👥 Зарегистрировалось: {registered}\n"
        f"✅ Завершили задание: {completed}\n\n"
        f"📊 Совет: пригласите 5 друзей, чтобы получать 10% пассивых начислений.\n"
        f"📞 Вопросы: {ADMIN_USERNAME}"
    )

    await safe_reply(update, context, msg, parse_mode="HTML")


# Админ-панель
async def admin_panel(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к админ-панели.")
        return
    
    update_user_activity(update.effective_user.id)
    
    # Получаем количество ожидающих выплат
    pending_withdrawals = get_pending_withdrawals_count()
    
    keyboard = [
        [KeyboardButton("📊 Статистика"), KeyboardButton("👥 Список пользователей")],
        [KeyboardButton(f"💸 Выплаты"), KeyboardButton("✅ Подтвердившие")],
        [KeyboardButton("📸 Приславшие скриншот"), KeyboardButton("📋 Скриншоты на проверке")],
        [KeyboardButton("📝 Редактор"), KeyboardButton("🔄 Сбросить задание")],
        [KeyboardButton("📢 Рассылка"), KeyboardButton("🔙 Главное меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    message = "🔧 <b>Админ-панель:</b>\nВыберите действие:\n\n"
    if pending_withdrawals > 0:
        message += f"⚠️ <b>Ожидают выплаты:</b> {pending_withdrawals} запросов"
    
    await safe_reply(update, context, 
        message,
        parse_mode="HTML",
        reply_markup=reply_markup
    )

# Редактор контента
async def editor_panel(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к редактору.")
        return
    
    update_user_activity(update.effective_user.id)
    
    keyboard = [
        [KeyboardButton("🌅 Утреннее сообщение"), KeyboardButton("🌙 Вечернее напоминание")],
        [KeyboardButton("📝 Информационные кнопки"), KeyboardButton("🖼️ Добавить фото")],
        [KeyboardButton("📁 Управление категориями"), KeyboardButton("🖼️ Список фото")],  # Новая кнопка
        [KeyboardButton("🔙 Назад в админ-панель")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await safe_reply(update, context, 
        "📝 <b>Редактор контента:</b>\nВыберите что хотите отредактировать:",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
async def manage_categories(update: Update, context: CallbackContext):
    """Главное меню управления категориями"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    categories = get_all_categories()
    
    message = "📁 <b>Управление категориями заданий:</b>\n\n"
    
    if not categories:
        message += "Категории не найдены. Создайте первую категорию."
    else:
        for category in categories:
            category_id, name, description, created_at = category
            # Считаем количество фото в категории
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM photos WHERE category_id = ?", (category_id,))
            photo_count = cursor.fetchone()[0]
            conn.close()
            
            message += f"🆔 <b>{category_id}: {name}</b>\n"
            message += f"📝 Описание: {description or 'нет'}\n"
            message += f"🖼️ Фото: {photo_count} шт.\n"
            message += f"📅 Создана: {created_at}\n\n"
    
    keyboard = [
        [KeyboardButton("➕ Добавить категорию"), KeyboardButton("✏️ Редактировать категорию")],
        [KeyboardButton("🗑️ Удалить категорию"), KeyboardButton("📊 Статистика по категориям")],
        [KeyboardButton("🖼️ Назначить категорию фото"), KeyboardButton("🔙 Назад в редактор")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

async def add_category_handler(update: Update, context: CallbackContext):
    """Обработчик добавления категории"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    await safe_reply(update, context,
        "📝 <b>Добавление новой категории:</b>\n\n"
        "Введите название и описание категории в формате:\n"
        "<code>Название | Описание</code>\n\n"
        "Пример:\n"
        "<code>Автосалоны | Отзывы на автомобильные салоны</code>\n\n"
        "Или отправьте /cancel для отмены",
        parse_mode="HTML"
    )
    context.user_data['waiting_for_new_category'] = True

async def handle_category_input(update: Update, context: CallbackContext):
    """Обработчик ввода данных категории"""
    if not context.user_data.get('waiting_for_new_category'):
        return
    
    text = update.message.text
    
    if '|' not in text:
        await safe_reply(update, context,
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>Название | Описание</code>\n\n"
            "Пример: <code>Автосалоны | Отзывы на автомобильные салоны</code>\n\n"
            "Или отправьте /cancel для отмены",
            parse_mode="HTML"
        )
        return
    
    try:
        name, description = [part.strip() for part in text.split('|', 1)]
        
        if not name:
            await safe_reply(update, context,
                "❌ <b>Название категории не может быть пустым!</b>",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, существует ли уже категория с таким названием
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM task_categories WHERE name = ?", (name,))
        existing = cursor.fetchone()
        conn.close()
        
        if existing:
            await safe_reply(update, context,
                f"❌ <b>Категория с названием '{name}' уже существует!</b>",
                parse_mode="HTML"
            )
            return
        
        # Добавляем категорию
        category_id = add_category(name, description)
        
        await safe_reply(update, context,
            f"✅ <b>Категория успешно добавлена!</b>\n\n"
            f"🆔 ID: {category_id}\n"
            f"📝 Название: {name}\n"
            f"📋 Описание: {description}\n\n"
            f"Теперь вы можете добавлять фото в эту категорию.",
            parse_mode="HTML"
        )
        
        # Сбрасываем состояние
        context.user_data['waiting_for_new_category'] = False
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении категории: {e}")
        await safe_reply(update, context,
            "❌ <b>Произошла ошибка при добавлении категории.</b>",
            parse_mode="HTML"
        )

async def edit_category_handler(update: Update, context: CallbackContext):
    """Обработчик редактирования категории"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    await safe_reply(update, context,
        "✏️ <b>Редактирование категории:</b>\n\n"
        "Введите ID категории, которую хотите отредактировать.\n\n"
        "Или отправьте /cancel для отмены",
        parse_mode="HTML"
    )
    context.user_data['waiting_for_edit_category_id'] = True

async def handle_edit_category_id_input(update: Update, context: CallbackContext):
    """Обработчик ввода ID категории для редактирования"""
    if not context.user_data.get('waiting_for_edit_category_id'):
        return
    
    text = update.message.text
    
    try:
        category_id = int(text)
        category = get_category(category_id)
        
        if not category:
            await safe_reply(update, context,
                f"❌ Категория с ID {category_id} не найдена.",
                parse_mode="HTML"
            )
            return
        
        cat_id, name, description, created_at = category
        
        # Сохраняем данные для следующего шага
        context.user_data['editing_category_id'] = category_id
        context.user_data['editing_category_name'] = name
        context.user_data['waiting_for_edit_category_id'] = False
        context.user_data['waiting_for_edit_category_data'] = True
        
        await safe_reply(update, context,
            f"✏️ <b>Редактирование категории:</b>\n\n"
            f"Текущие данные:\n"
            f"Название: {name}\n"
            f"Описание: {description or 'нет'}\n\n"
            f"Введите новые данные в формате:\n"
            f"<code>Новое название | Новое описание</code>\n\n"
            f"Или отправьте /cancel для отмены",
            parse_mode="HTML"
        )
        
    except ValueError:
        await safe_reply(update, context,
            "❌ Неверный формат ID! Введите числовой ID категории.",
            parse_mode="HTML"
        )

async def handle_edit_category_data_input(update: Update, context: CallbackContext):
    """Обработчик ввода новых данных категории"""
    if not context.user_data.get('waiting_for_edit_category_data'):
        return
    
    text = update.message.text
    
    if '|' not in text:
        await safe_reply(update, context,
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>Новое название | Новое описание</code>\n\n"
            "Или отправьте /cancel для отмены",
            parse_mode="HTML"
        )
        return
    
    try:
        new_name, new_description = [part.strip() for part in text.split('|', 1)]
        category_id = context.user_data['editing_category_id']
        
        if not new_name:
            await safe_reply(update, context,
                "❌ <b>Название категории не может быть пустым!</b>",
                parse_mode="HTML"
            )
            return
        
        # Обновляем категорию
        update_category(category_id, new_name, new_description)
        
        await safe_reply(update, context,
            f"✅ <b>Категория успешно обновлена!</b>\n\n"
            f"🆔 ID: {category_id}\n"
            f"📝 Новое название: {new_name}\n"
            f"📋 Новое описание: {new_description}",
            parse_mode="HTML"
        )
        
        # Сбрасываем состояния
        for key in ['editing_category_id', 'editing_category_name', 'waiting_for_edit_category_data']:
            if key in context.user_data:
                del context.user_data[key]
                
    except Exception as e:
        logger.error(f"Ошибка при обновлении категории: {e}")
        await safe_reply(update, context,
            "❌ <b>Произошла ошибка при обновлении категории.</b>",
            parse_mode="HTML"
        )

async def delete_category_handler(update: Update, context: CallbackContext):
    """Обработчик удаления категории"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    await safe_reply(update, context,
        "🗑️ <b>Удаление категории:</b>\n\n"
        "Введите ID категории, которую хотите удалить.\n\n"
        "⚠️ <b>Внимание:</b> Удалить можно только пустые категории (без фото).\n\n"
        "Или отправьте /cancel для отмены",
        parse_mode="HTML"
    )
    context.user_data['waiting_for_delete_category'] = True

async def handle_delete_category_input(update: Update, context: CallbackContext):
    """Обработчик ввода ID категории для удаления"""
    if not context.user_data.get('waiting_for_delete_category'):
        return
    
    text = update.message.text
    
    try:
        category_id = int(text)
        
        # Пытаемся удалить категорию
        success, message = delete_category(category_id)
        
        if success:
            await safe_reply(update, context,
                f"✅ <b>Категория успешно удалена!</b>\n\n"
                f"ID: {category_id}\n"
                f"{message}",
                parse_mode="HTML"
            )
        else:
            await safe_reply(update, context,
                f"❌ <b>Не удалось удалить категорию!</b>\n\n"
                f"ID: {category_id}\n"
                f"Причина: {message}",
                parse_mode="HTML"
            )
        
        # Сбрасываем состояние
        context.user_data['waiting_for_delete_category'] = False
        
    except ValueError:
        await safe_reply(update, context,
            "❌ Неверный формат ID! Введите числовой ID категории.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении категории: {e}")
        await safe_reply(update, context,
            f"❌ Произошла ошибка при удалении категории: {str(e)}",
            parse_mode="HTML"
        )
        context.user_data['waiting_for_delete_category'] = False

async def category_stats_handler(update: Update, context: CallbackContext):
    """Показать статистику по категориям"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    categories = get_all_categories()
    
    if not categories:
        await safe_reply(update, context, "📊 Категории не найдены.")
        return
    
    message = "📊 <b>Статистика по категориям:</b>\n\n"
    
    for category in categories:
        category_id, name, description, created_at = category
        
        # Получаем статистику
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Количество фото в категории
        cursor.execute("SELECT COUNT(*) FROM photos WHERE category_id = ?", (category_id,))
        photo_count = cursor.fetchone()[0]
        
        conn.close()
        
        message += f"📁 <b>{name}</b> (ID: {category_id})\n"
        message += f"🖼️ Фото: {photo_count} шт.\n\n"
    
    await safe_reply(update, context, message, parse_mode="HTML")

async def assign_category_to_photo_handler(update: Update, context: CallbackContext):
    """Обработчик назначения категории фото"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    await safe_reply(update, context,
        "🖼️ <b>Назначение категории фото:</b>\n\n"
        "Введите ID фото и ID категории в формате:\n"
        "<code>ID_фото | ID_категории</code>\n\n"
        "Пример: <code>15 | 2</code>\n\n"
        "Или отправьте /cancel для отмены",
        parse_mode="HTML"
    )
    context.user_data['waiting_for_assign_category'] = True

async def handle_assign_category_input(update: Update, context: CallbackContext):
    """Обработчик ввода данных для назначения категории фото"""
    if not context.user_data.get('waiting_for_assign_category'):
        return
    
    text = update.message.text
    
    if '|' not in text:
        await safe_reply(update, context,
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>ID_фото | ID_категории</code>\n\n"
            "Пример: <code>15 | 2</code>\n\n"
            "Или отправьте /cancel для отмены",
            parse_mode="HTML"
        )
        return
    
    try:
        photo_id_str, category_id_str = [part.strip() for part in text.split('|', 1)]
        photo_id = int(photo_id_str)
        category_id = int(category_id_str)
        
        # Проверяем существование фото
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, photo_id FROM photos WHERE id = ?", (photo_id,))
        photo = cursor.fetchone()
        
        if not photo:
            conn.close()
            await safe_reply(update, context,
                f"❌ Фото с ID {photo_id} не найдено.",
                parse_mode="HTML"
            )
            return
        
        # Проверяем существование категории
        cursor.execute("SELECT id, name FROM task_categories WHERE id = ?", (category_id,))
        category = cursor.fetchone()
        
        if not category:
            conn.close()
            await safe_reply(update, context,
                f"❌ Категория с ID {category_id} не найдена.",
                parse_mode="HTML"
            )
            return
        
        # Обновляем категорию фото
        cursor.execute("UPDATE photos SET category_id = ? WHERE id = ?", (category_id, photo_id))
        conn.commit()
        conn.close()
        
        await safe_reply(update, context,
            f"✅ <b>Категория назначена успешно!</b>\n\n"
            f"🖼️ Фото ID: {photo_id}\n"
            f"📁 Категория: {category[1]} (ID: {category_id})",
            parse_mode="HTML"
        )
        
        # Сбрасываем состояние
        context.user_data['waiting_for_assign_category'] = False
        
    except ValueError:
        await safe_reply(update, context,
            "❌ Неверный формат данных! Используйте числовые ID.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при назначении категории фото: {e}")
        await safe_reply(update, context,
            f"❌ Произошла ошибка при назначении категории: {str(e)}",
            parse_mode="HTML"
        )
        context.user_data['waiting_for_assign_category'] = False

# Показать статистику
async def show_stats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    user_count, photo_count, called_count, screenshot_count, active_users_count, total_earned = get_stats()
    
    await safe_reply(update, context, 
        f"📊 <b>Статистика:</b>\n\n"
        f"👥 <b>Всего пользователей:</b> {user_count}\n"
        f"🚀 <b>Активных пользователей:</b> {active_users_count}\n"
        f"🖼️ <b>Фото в базе:</b> {photo_count}\n"
        f"✅ <b>Подтвердивших звонок:</b> {called_count}\n"
        f"📸 <b>Приславших скриншот:</b> {screenshot_count}\n"
        f"💰 <b>Общая сумма выплат:</b> {total_earned} рублей",
        parse_mode="HTML"
    )

# Показать список всех пользователей с пагинацией
async def show_all_users(update: Update, context: CallbackContext, page=0):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT u.user_id, u.username, u.first_name, u.last_name, u.joined_at, u.last_active,
           up.tasks_completed, up.total_earned, up.current_step
    FROM users u
    LEFT JOIN user_progress up ON u.user_id = up.user_id
    ORDER BY u.joined_at DESC
    LIMIT 10 OFFSET ?
    ''', (page * 10,))
    users = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    conn.close()
    
    if not users:
        await safe_reply(update, context, "Пользователи отсутствуют.")
        return
    
    total_pages = (total_users + 9) // 10  # Округление вверх
    
    message = f"👥 <b>Список пользователей (стр. {page+1}/{total_pages}):</b>\n\n"
    for user in users:
        user_id, username, first_name, last_name, joined_at, last_active, tasks_completed, total_earned, current_step = user
        user_link = format_user_link(user_id, username, first_name, last_name)
        
        message += f"👤 {user_link}\n🆔 ID: {user_id}\n📅 Регистрация: {joined_at}\n"
        message += f"✅ Заданий: {tasks_completed or 0}\n💰 Заработано: {total_earned or 0} руб.\n"
        message += f"📊 Статус: {current_step or 'нет задания'}\n\n"
    
    # Создаем клавиатуру пагинации
    keyboard = []
    if page > 0:
        keyboard.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{USER_LIST_PAGE}_{page-1}"))
    if page < total_pages - 1:
        keyboard.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"{USER_LIST_PAGE}_{page+1}"))
    
    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)


# Показать список подтвердивших звонок с пагинацией
# Показать список подтвердивших звонок с пагинацией (УЛУЧШЕННАЯ ВЕРСИЯ)
async def show_called_users(update: Update, context: CallbackContext, page=0):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT 
        u.user_id, 
        u.username, 
        u.first_name, 
        u.last_name, 
        up.called_confirmed_at,
        up.photo_id,
        p.category_id,
        c.name as category_name,
        up.current_step,
        up.screenshot_status
    FROM user_progress up
    LEFT JOIN users u ON u.user_id = up.user_id
    LEFT JOIN photos p ON up.photo_id = p.id
    LEFT JOIN task_categories c ON p.category_id = c.id
    WHERE up.called_confirmed = TRUE
      AND up.current_step IN ('waiting_review_day', 'waiting_review_evening')
    ORDER BY up.called_confirmed_at DESC
    LIMIT ? OFFSET ?
    ''', (10, page * 10))
    users = cursor.fetchall()
    
    cursor.execute('''
    SELECT COUNT(*)
    FROM user_progress up
    WHERE up.called_confirmed = TRUE
      AND up.current_step IN ('waiting_review_day', 'waiting_review_evening')
    ''')
    count = cursor.fetchone()[0] or 0
    conn.close()
    
    if not users:
        await safe_reply(update, context, "✅ <b>Нет пользователей, ожидающих утреннее/вечернее сообщение.</b>", parse_mode="HTML")
        return
    
    total_pages = (count + 9) // 10
    
    message = f"✅ <b>Пользователи, ожидающие сообщение (стр. {page+1}/{total_pages}):</b>\n\n"
    
    for user in users:
        (user_id, username, first_name, last_name, called_at, 
         photo_id, category_id, category_name, current_step, screenshot_status) = user
        
        user_link = format_user_link(user_id, username, first_name, last_name)
        category_name = category_name or "Без категории"
        
        screenshot_info = ""
        if screenshot_status == 'approved':
            screenshot_info = "✅ Одобрен"
        elif screenshot_status == 'rejected':
            screenshot_info = "❌ Отклонен"
        elif screenshot_status == 'pending':
            screenshot_info = "⏳ На проверке"
        elif screenshot_status == 'not_sent':
            screenshot_info = "📭 Не отправлен"
        else:
            screenshot_info = "❓ Неизвестно"
        
        message += f"👤 {user_link}\n"
        message += f"🆔 ID: {user_id}\n"
        message += f"📅 Время подтверждения: {called_at}\n"
        message += f"📁 Категория задания: {category_name}\n"
        message += f"📊 Текущий статус: {current_step}\n"
        message += f"📸 Статус скриншота: {screenshot_info}\n"
        
        if photo_id:
            message += f"🖼️ ID фото задания: {photo_id}\n"
        
        message += f"\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    keyboard = []
    if page > 0:
        keyboard.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{CALLED_LIST_PAGE}_{page-1}"))
    if page < total_pages - 1:
        keyboard.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"{CALLED_LIST_PAGE}_{page+1}"))
    
    additional_buttons = [
        InlineKeyboardButton("📊 Статистика подтверждений", callback_data="called_stats")
    ]
    
    if keyboard:
        reply_markup = InlineKeyboardMarkup([keyboard, additional_buttons])
    else:
        reply_markup = InlineKeyboardMarkup([additional_buttons])
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

# Показать список приславших скриншот с пагинацией (УЛУЧШЕННАЯ ВЕРСИЯ)
async def show_screenshot_users(update: Update, context: CallbackContext, page=0):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT 
        u.user_id, 
        u.username, 
        u.first_name, 
        u.last_name, 
        up.screenshot_sent_at,
        up.photo_id,
        p.category_id,
        c.name as category_name,
        up.current_step,
        up.screenshot_status,
        up.admin_review_comment
    FROM user_progress up
    LEFT JOIN users u ON u.user_id = up.user_id
    LEFT JOIN photos p ON up.photo_id = p.id
    LEFT JOIN task_categories c ON p.category_id = c.id
    WHERE up.screenshot_status = 'pending'
    ORDER BY up.screenshot_sent_at DESC
    LIMIT ? OFFSET ?
    ''', (10, page * 10))
    users = cursor.fetchall()
    
    cursor.execute('''
    SELECT COUNT(*)
    FROM user_progress up
    WHERE up.screenshot_status = 'pending'
    ''')
    count = cursor.fetchone()[0] or 0
    conn.close()
    
    if not users:
        await safe_reply(update, context, "📸 <b>Нет скриншотов на проверке.</b>", parse_mode="HTML")
        return
    
    total_pages = (count + 9) // 10
    
    message = f"📸 <b>Скриншоты на проверке (стр. {page+1}/{total_pages}):</b>\n\n"
    
    for user in users:
        (user_id, username, first_name, last_name, screenshot_at, 
         photo_id, category_id, category_name, current_step, screenshot_status, admin_comment) = user
        
        user_link = format_user_link(user_id, username, first_name, last_name)
        category_name = category_name or "Без категории"
        
        if screenshot_status == 'pending':
            status_icon = "⏳"
        else:
            status_icon = "❓"
        
        message += f"{status_icon} 👤 {user_link}\n"
        message += f"🆔 ID: {user_id}\n"
        message += f"📅 Время отправки: {screenshot_at}\n"
        message += f"📁 Категория задания: {category_name}\n"
        message += f"📊 Текущий статус: {current_step}\n"
        
        if admin_comment:
            message += f"💬 Комментарий админа: {admin_comment}\n"
        
        if photo_id:
            message += f"🖼️ ID фото задания: {photo_id}\n"
        
        message += f"\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    keyboard = []
    if page > 0:
        keyboard.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{SCREENSHOT_LIST_PAGE}_{page-1}"))
    if page < total_pages - 1:
        keyboard.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"{SCREENSHOT_LIST_PAGE}_{page+1}"))
    
    additional_buttons = [
        InlineKeyboardButton("📊 Статистика скриншотов", callback_data="screenshot_stats"),
        InlineKeyboardButton("📋 Показать ожидающие", callback_data="show_pending_screenshots_admin")
    ]
    
    if keyboard:
        reply_markup = InlineKeyboardMarkup([keyboard, additional_buttons])
    else:
        reply_markup = InlineKeyboardMarkup([additional_buttons])
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)
# Показать скриншоты на проверке
async def show_pending_screenshots(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    screenshots = get_pending_screenshots()
    
    if not screenshots:
        await safe_reply(update, context, "Нет скриншотов на проверке.")
        return
    
    message = "📋 <b>Скриншоты на проверке:</b>\n\n"
    
    for i, screenshot in enumerate(screenshots, 1):
        user_id, username, first_name, last_name, screenshot_id, screenshot_sent_at = screenshot
        user_link = format_user_link(user_id, username, first_name, last_name)
        message += f"{i}. 👤 {user_link}\n🆔 ID: {user_id}\n📅 Время: {screenshot_sent_at}\n\n"
    
    message += "📸 <b>Для просмотра скриншота отправьте команду:</b> /viewscreenshot ID пользователя"
    
    await safe_reply(update, context, message, parse_mode="HTML")

# Показать скриншот пользователя
async def view_screenshot(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    if not context.args:
        await safe_reply(update, context, 
            "Использование: /viewscreenshot ID_пользователя\nКороткая версия: /vs ID_пользователя")
        return
    
    try:
        user_id = int(context.args[0])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT up.screenshot_id, u.first_name, u.last_name, up.photo_id, p.photo_id as photo_file_id,
               c.name as category_name
        FROM user_progress up
        LEFT JOIN users u ON u.user_id = up.user_id
        LEFT JOIN photos p ON up.photo_id = p.id
        LEFT JOIN task_categories c ON p.category_id = c.id
        WHERE up.user_id = ? AND up.screenshot_status = 'pending'
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await safe_reply(update, context, "Скриншот не найден или уже проверен.")
            return
        
        screenshot_id, first_name, last_name, photo_id, photo_file_id, category_name = result
        user_link = f"<a href='tg://user?id={user_id}'>{first_name or ''} {last_name or ''}</a>".strip() or f"Пользователь {user_id}"
        
        # Отправляем скриншот
        await update.message.reply_photo(
            photo=screenshot_id,
            caption=(
                f"📸 <b>Скриншот от пользователя:</b> {user_link}\n"
                f"🆔 <b>ID пользователя:</b> {user_id}\n"
                f"🖼️ <b>ID фото задания:</b> {photo_id}\n"
                f"📁 <b>Категория задания:</b> {category_name or 'Без категории'}\n"
                f"📁 <b>File ID фото:</b> {photo_file_id}"
            ),
            parse_mode="HTML"
        )
        
        # Создаем кнопки для одобрения/отклонения
        keyboard = [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"{APPROVE_SCREENSHOT}_{user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"{REJECT_SCREENSHOT}_{user_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_reply(update, context, 
            "Выберите действие:",
            reply_markup=reply_markup
        )
        
    except ValueError:
        await safe_reply(update, context, "Пожалуйста, введите числовой ID пользователя.")

async def show_all_photos(update: Update, context: CallbackContext, page=0):
    """Показывает фото с пагинацией (исправленная версия)"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    limit = 5  # Показывать по 5 фото на странице (меньше для быстродействия)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем фото для текущей страницы с информацией о категориях
    cursor.execute('''
        SELECT p.id, p.photo_id, p.category_id, c.name as category_name
        FROM photos p
        LEFT JOIN task_categories c ON p.category_id = c.id
        ORDER BY p.id
        LIMIT ? OFFSET ?
    ''', (limit, page * limit))
    
    photos = cursor.fetchall()
    
    # Получаем общее количество фото
    cursor.execute("SELECT COUNT(*) FROM photos")
    total_photos = cursor.fetchone()[0] or 0
    conn.close()
    
    if not photos:
        await safe_reply(update, context, "🖼️ <b>Фото отсутствуют.</b>", parse_mode="HTML")
        return
    
    # Отправляем информацию о странице
    total_pages = max(1, (total_photos + limit - 1) // limit)  # Округление вверх
    message = f"🖼️ <b>Страница {page+1} из {total_pages}</b>\n📊 Всего фото: {total_photos}"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(message, parse_mode="HTML")
    else:
        await safe_reply(update, context, message, parse_mode="HTML")
    
    # Отправляем фото по одному
    for photo in photos:
        photo_id, photo_file_id, category_id, category_name = photo
        
        if not category_name:
            category_name = "Без категории"
        
        # Получаем все категории для кнопок
        all_categories = get_all_categories()
        
        # Создаем клавиатуру
        keyboard = []
        keyboard.append([InlineKeyboardButton("❌ Удалить", callback_data=f"delete_photo_{photo_id}")])
        
        # Добавляем кнопки для смены категории
        for cat in all_categories:
            cat_id, name, description, created_at = cat
            if cat_id != category_id:  # Не показываем текущую категорию
                keyboard.append([InlineKeyboardButton(f"📁 В категорию: {name}", 
                                                     callback_data=f"change_category_{photo_id}_{cat_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if update.callback_query:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo_file_id,
                    caption=f"🖼️ <b>ID:</b> {photo_id}\n📁 <b>Категория:</b> {category_name}",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_photo(
                    photo=photo_file_id,
                    caption=f"🖼️ <b>ID:</b> {photo_id}\n📁 <b>Категория:</b> {category_name}",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото ID {photo_id}: {e}")
            error_msg = f"❌ Не удалось отправить фото ID: {photo_id}\nКатегория: {category_name}"
            await safe_reply(update, context, error_msg, parse_mode="HTML")
    
    # Добавляем пагинацию только если есть больше одной страницы
    if total_pages > 1:
        keyboard = []
        if page > 0:
            keyboard.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"photos_page_{page-1}"))
        
        # Добавляем номер текущей страницы
        keyboard.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        
        if page < total_pages - 1:
            keyboard.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"photos_page_{page+1}"))
        
        if keyboard:
            reply_markup = InlineKeyboardMarkup([keyboard])
            
            # Отправляем кнопки пагинации отдельным сообщением
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📄 Навигация по страницам:",
                reply_markup=reply_markup
            )
            
async def handle_change_category(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("change_category_"):
        parts = data.replace("change_category_", "").split("_")
        photo_id = int(parts[0])
        category_id = int(parts[1])
        
        # Обновляем категорию фото
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE photos SET category_id = ? WHERE id = ?", (category_id, photo_id))
        conn.commit()
        
        # Получаем информацию о категории
        cursor.execute("SELECT name FROM task_categories WHERE id = ?", (category_id,))
        category_result = cursor.fetchone()
        category_name = category_result[0] if category_result else "Неизвестно"
        
        conn.close()
        
        await query.edit_message_caption(
            caption=f"✅ <b>Категория изменена!</b>\n\n"
                   f"🖼️ Фото ID: {photo_id}\n"
                   f"📁 Новая категория: {category_name}",
            parse_mode="HTML"
        )

# Добавление фото
async def add_photo_handler(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет прав для добавления фото.")
        return

    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        
        # Получаем список категорий
        categories = get_all_categories()
        
        if not categories:
            await safe_reply(update, context,
                "❌ <b>Нет доступных категорий!</b>\n\n"
                "Сначала создайте категорию через раздел управления категориями.",
                parse_mode="HTML"
            )
            return
        
        # Сохраняем фото во временные данные
        context.user_data['temp_photo_id'] = photo_id
        
        # Создаем клавиатуру с категориями
        keyboard = []
        for category in categories:
            cat_id, name, description, created_at = category
            keyboard.append([InlineKeyboardButton(f"{name}", callback_data=f"select_category_{cat_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add_photo")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_photo(
            photo=photo_id,
            caption="📸 <b>Выберите категорию для этого фото:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    else:
        await safe_reply(update, context,
            "❌ <b>Пожалуйста, прикрепите фото к сообщению.</b>",
            parse_mode="HTML"
        )
async def handle_category_selection(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "cancel_add_photo":
        await query.edit_message_caption(caption="❌ Добавление фото отменено.")
        return
    
    if data.startswith("select_category_"):
        category_id = int(data.replace("select_category_", ""))
        photo_id = context.user_data.get('temp_photo_id')
        
        if photo_id:
            # Сохраняем фото с категорией
            add_photo(photo_id, category_id)
            
            # Получаем информацию о категории
            category = get_category(category_id)
            cat_id, name, description, created_at = category
            
            await query.edit_message_caption(
                caption=f"✅ <b>Фото добавлено в категорию!</b>\n\n"
                       f"📁 Категория: {name}\n"
                       f"📝 Описание: {description or 'нет'}",
                parse_mode="HTML"
            )
            
            # Очищаем временные данные
            if 'temp_photo_id' in context.user_data:
                del context.user_data['temp_photo_id']
        else:
            await query.answer("❌ Фото не найдено", show_alert=True)
        
async def find_user_command(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    if not context.args:
        await safe_reply(update, context, 
            "🔍 <b>Использование:</b>\n"
            "/find @username - найти по юзернейму\n"
            "/find 123456789 - найти по ID\n"
            "/find имя - найти по имени",
            parse_mode="HTML"
        )
        return
    
    search_term = context.args[0].strip()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Пробуем найти по разным критериям
        if search_term.startswith('@'):
            # Поиск по username
            username = search_term[1:]
            cursor.execute('''
                SELECT u.user_id, u.username, u.first_name, u.last_name, u.phone_number, u.joined_at, u.last_active,
                       up.balance, up.tasks_completed, up.total_earned, up.current_step
                FROM users u
                LEFT JOIN user_progress up ON u.user_id = up.user_id
                WHERE u.username = ?
            ''', (username,))
        else:
            try:
                # Пробуем поиск по ID
                user_id = int(search_term)
                cursor.execute('''
                    SELECT u.user_id, u.username, u.first_name, u.last_name, u.phone_number, u.joined_at, u.last_active,
                           up.balance, up.tasks_completed, up.total_earned, up.current_step
                    FROM users u
                    LEFT JOIN user_progress up ON u.user_id = up.user_id
                    WHERE u.user_id = ?
                ''', (user_id,))
            except ValueError:
                # Поиск по имени
                cursor.execute('''
                    SELECT u.user_id, u.username, u.first_name, u.last_name, u.phone_number, u.joined_at, u.last_active,
                           up.balance, up.tasks_completed, up.total_earned, up.current_step
                    FROM users u
                    LEFT JOIN user_progress up ON u.user_id = up.user_id
                    WHERE u.first_name LIKE ? OR u.last_name LIKE ?
                ''', (f'%{search_term}%', f'%{search_term}%'))
        
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            await safe_reply(update, context, 
                f"❌ Пользователь '{search_term}' не найден.",
                parse_mode="HTML"
            )
            return
        
        for user in users:
            user_id, username, first_name, last_name, phone_number, joined_at, last_active, balance, tasks_completed, total_earned, current_step = user
            
            user_link = format_user_link(user_id, username, first_name, last_name)
            
            # Реферальная статистика
            reg_count, comp_count = get_referral_stats(user_id)
            
            # Информация о текущем задании
            task_info = get_user_task(user_id)
            photo_info = ""
            if task_info:
                photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
                if photo_id:
                    # Получаем информацию о категории задания
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT c.name 
                        FROM photos p 
                        LEFT JOIN task_categories c ON p.category_id = c.id 
                        WHERE p.id = ?
                    ''', (photo_id,))
                    category_result = cursor.fetchone()
                    category_name = category_result[0] if category_result else "Без категории"
                    conn.close()
                    
                    photo_info = f"🖼️ <b>ID фото задания:</b> {photo_id}\n"
                    photo_info += f"📁 <b>Категория задания:</b> {category_name}\n"
            
            message = (
                f"🔍 <b>Найден пользователь:</b>\n\n"
                f"👤 {user_link}\n"
                f"🆔 <b>ID:</b> {user_id}\n"
                f"📛 <b>Username:</b> @{username if username else 'нет'}\n"
                f"👨‍💼 <b>Имя:</b> {first_name} {last_name}\n"
                f"📞 <b>Телефон:</b> {phone_number or 'не указан'}\n\n"
                f"💰 <b>Баланс:</b> {balance or 0} руб.\n"
                f"✅ <b>Выполнено заданий:</b> {tasks_completed or 0}\n"
                f"💵 <b>Всего заработано:</b> {total_earned or 0} руб.\n"
                f"📊 <b>Текущий статус:</b> {current_step or 'нет задания'}\n"
                f"👥 <b>Рефералы:</b> зарегистрировалось — {reg_count}, завершили — {comp_count}\n"
                f"{photo_info}"
                f"📅 <b>Зарегистрирован:</b> {joined_at}\n"
                f"🕒 <b>Последняя активность:</b> {last_active}"
            )
            
            await safe_reply(update, context, message, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Ошибка при поиске пользователя: {e}")
        await safe_reply(update, context, 
            "❌ Произошла ошибка при поиске пользователя.",
            parse_mode="HTML"
        )
        
# Сброс задания пользователя
async def reset_user_task_handler(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    await safe_reply(update, context, 
        "Введите ID пользователя, для которого нужно сбросить задание:"
    )
    context.user_data['waiting_for_reset_user_id'] = True

# Обработчик ввода ID пользователя для сброса
async def handle_reset_user_id(update: Update, context: CallbackContext):
    if not context.user_data.get('waiting_for_reset_user_id'):
        return
    
    try:
        user_id = int(update.message.text)
        user_info = get_user_info(user_id)
        
        if user_info:
            user_id_db, username, first_name, last_name, phone_number, joined_at, last_active = user_info
            
            # Полное сброс задания с обнулением счетчика замен
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
            UPDATE user_progress 
            SET current_step = 'cancelled', 
                completed_at = CURRENT_TIMESTAMP,
                replacement_count = 0,
                called = FALSE,
                called_confirmed = FALSE,
                morning_message_sent = FALSE,
                evening_reminder_sent = FALSE,
                screenshot_sent = FALSE,
                screenshot_status = 'not_sent'
            WHERE user_id = ?
            ''', (user_id_db,))
            conn.commit()
            conn.close()
            
            user_link = format_user_link(user_id_db, username, first_name, last_name)
            
            await safe_reply(update, context, 
                f"✅ <b>Задание полностью сброшено!</b>\n\n"
                f"👤 <b>Пользователь:</b> {user_link}\n"
                f"🆔 <b>ID:</b> {user_id_db}\n"
                f"📛 <b>Username:</b> @{username if username else 'нет'}\n"
                f"👨‍💼 <b>Имя:</b> {first_name} {last_name}\n\n"
                f"🔄 <b>Счетчик замен обнулен</b>\n"
                f"Теперь пользователь может получить новое задание.",
                parse_mode="HTML"
            )
        else:
            await safe_reply(update, context, 
                f"❌ Пользователь с ID {user_id} не найден."
            )
            
        context.user_data['waiting_for_reset_user_id'] = False
    except ValueError:
        await safe_reply(update, context, "Пожалуйста, введите числовой ID пользователя:")
        
async def force_reset_all_tasks_command(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой команде.")
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Сбрасываем ВСЕ задания, включая завершенные
        cursor.execute('''
        UPDATE user_progress 
        SET current_step = 'cancelled', 
            completed_at = CURRENT_TIMESTAMP,
            replacement_count = 0,
            called = FALSE,
            called_confirmed = FALSE,
            morning_message_sent = FALSE,
            evening_reminder_sent = FALSE,
            screenshot_sent = FALSE,
            screenshot_status = 'not_sent'
        ''')
        
        affected_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        await safe_reply(update, context,
            f"✅ <b>Полный сброс всех заданий выполнен!</b>\n\n"
            f"📊 Сброшено заданий: {affected_count}\n"
            f"🔄 Все счетчики замен обнулены\n\n"
            f"Все пользователи теперь могут получить новые задания.",
            parse_mode="HTML"
        )
        
        logger.info(f"Админ {update.effective_user.id} выполнил полный сброс всех заданий. Затронуто: {affected_count}")
        
    except Exception as e:
        logger.error(f"Ошибка при полном сбросе заданий: {e}")
        await safe_reply(update, context, 
            "❌ <b>Произошла ошибка при сбросе заданий.</b>",
            parse_mode="HTML"
        )

# Настройка утреннего сообщения
async def morning_message_settings(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return

    current_text, send_time, video_id = get_morning_message()
    recipients = len(get_users_waiting_for_morning())

    title = "🌅 <b>Настройки утреннего сообщения</b>"
    message_text = (
        f"{title}\n\n"
        f"<b>Текущий текст:</b>\n{current_text}\n\n"
        f"⏰ <b>Время отправки:</b> {send_time}\n"
        f"👥 <b>Получателей:</b> {recipients}"
    )

    # Показываем текущее сообщение с медиа если есть
    if video_id:
        try:
            await safe_send_video_or_text(update, context, 
                video_id=video_id, 
                caption_text=message_text, 
                parse_mode="HTML"
            )
        except Exception:
            await safe_reply(update, context, 
                message_text + "\n\n📹 <b>Видео прикреплено (не удалось отобразить превью)</b>", 
                parse_mode="HTML"
            )
    else:
        await safe_reply(update, context, message_text, parse_mode="HTML")

    # Кнопки действий
    keyboard = [
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data=SEND_MORNING_NOW)],
        [InlineKeyboardButton("🔙 Назад", callback_data=BACK_TO_EDITOR)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # ★★★ ИСПРАВЛЕННАЯ ИНСТРУКЦИЯ ★★★
    instruction_text = (
        "✏️ <b>Отправьте новый текст утреннего сообщения в формате:</b>\n\n"
        "<code>Текст сообщения | Время</code>\n\n"
        "<b>Пример:</b>\n"
        "<code>Доброе утро! Не забудьте оставить отзыв сегодня | 09:00</code>\n\n"
        "🔹 <b>Или отправьте</b> <code>/skip</code> <b>чтобы оставить текущий текст</b>\n"
        "🔹 <b>Или отправьте видео чтобы обновить медиа</b>"
    )
    
    await safe_reply(update, context, instruction_text, parse_mode="HTML", reply_markup=reply_markup)
    
    # Устанавливаем флаг ожидания
    context.user_data['waiting_for_morning_message'] = True

async def handle_morning_message_input(update: Update, context: CallbackContext):
    if not context.user_data.get('waiting_for_morning_message'):
        return
    
    text = update.message.text
    
    # ★★★ ОБРАБОТКА КОМАНДЫ /skip ★★★
    if text == '/skip':
        context.user_data['waiting_for_morning_message'] = False
        current_text, current_time, current_video = get_morning_message()
        
        await safe_reply(update, context, 
            f"✅ <b>Текст утреннего сообщения оставлен без изменений!</b>\n\n"
            f"📝 <b>Текст:</b> {current_text}\n"
            f"⏰ <b>Время:</b> {current_time}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_morning_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для утреннего сообщения или введите 'Пропустить' чтобы оставить без видео."
        )
        return
    
    # ★★★ ОБРАБОТКА ОБЫЧНОГО ВВОДА ★★★
    if '|' not in text:
        await safe_reply(update, context, 
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>Текст сообщения | Время (например: 09:00)</code>\n\n"
            "Или отправьте <code>/skip</code> чтобы оставить текущий текст",
            parse_mode="HTML"
        )
        return
    
    try:
        message_text, time_str = [part.strip() for part in text.split('|', 1)]
        
        # Проверяем формат времени
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
            await safe_reply(update, context, 
                "❌ <b>Неверный формат времени!</b>\n\n"
                "Используйте формат: <code>ЧЧ:MM</code> (например: 09:00)",
                parse_mode="HTML"
            )
            return
        
        # Получаем текущее видео чтобы не потерять его
        _, _, current_video = get_morning_message()
        set_morning_message(message_text, time_str, current_video)
        
        context.user_data['waiting_for_morning_message'] = False
        
        await safe_reply(update, context, 
            f"✅ <b>Утреннее сообщение обновлено!</b>\n\n"
            f"📝 <b>Текст:</b> {message_text}\n"
            f"⏰ <b>Время отправки:</b> {time_str}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_morning_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для утреннего сообщения или введите 'Пропустить' чтобы оставить без видео."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении утреннего сообщения: {e}")
        await safe_reply(update, context, 
            "❌ <b>Произошла ошибка при обновлении утреннего сообщения.</b>",
            parse_mode="HTML"
        )

# Настройка вечернего напоминания
async def evening_reminder_settings(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return

    current_text, send_time, video_id = get_evening_reminder()
    recipients = len(get_users_waiting_for_evening())

    title = "🌙 <b>Настройки вечернего напоминания</b>"
    message_text = (
        f"{title}\n\n"
        f"<b>Текущий текст:</b>\n{current_text}\n\n"
        f"⏰ <b>Время отправки:</b> {send_time}\n"
        f"👥 <b>Получателей:</b> {recipients}"
    )

    # Показываем текущее сообщение с медиа если есть
    if video_id:
        try:
            await safe_send_video_or_text(update, context, 
                video_id=video_id, 
                caption_text=message_text, 
                parse_mode="HTML"
            )
        except Exception:
            await safe_reply(update, context, 
                message_text + "\n\n📹 <b>Видео прикреплено (не удалось отобразить превью)</b>", 
                parse_mode="HTML"
            )
    else:
        await safe_reply(update, context, message_text, parse_mode="HTML")

    # Кнопки действий
    keyboard = [
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data=SEND_EVENING_NOW)],
        [InlineKeyboardButton("🔙 Назад", callback_data=BACK_TO_EDITOR)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # ★★★ ИСПРАВЛЕННАЯ ИНСТРУКЦИЯ ★★★
    instruction_text = (
        "✏️ <b>Отправьте новый текст вечернего напоминания в формате:</b>\n\n"
        "<code>Текст сообщения | Время</code>\n\n"
        "<b>Пример:</b>\n"
        "<code>Добрый вечер! Напоминаем отправить скриншот | 20:00</code>\n\n"
        "🔹 <b>Или отправьте</b> <code>/skip</code> <b>чтобы оставить текущий текст</b>\n"
        "🔹 <b>Или отправьте видео чтобы обновить медиа</b>"
    )
    
    await safe_reply(update, context, instruction_text, parse_mode="HTML", reply_markup=reply_markup)
    
    # Устанавливаем флаг ожидания
    context.user_data['waiting_for_evening_reminder'] = True

# ★★★ ИСПРАВЛЕННЫЕ ОБРАБОТЧИКИ ВВОДА ★★★
async def handle_morning_message_input(update: Update, context: CallbackContext):
    if not context.user_data.get('waiting_for_morning_message'):
        return
    
    text = update.message.text
    
    # ★★★ ОБРАБОТКА КОМАНДЫ /skip ★★★
    if text == '/skip':
        context.user_data['waiting_for_morning_message'] = False
        current_text, current_time, current_video = get_morning_message()
        
        await safe_reply(update, context, 
            f"✅ <b>Текст утреннего сообщения оставлен без изменений!</b>\n\n"
            f"📝 <b>Текст:</b> {current_text}\n"
            f"⏰ <b>Время:</b> {current_time}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_morning_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для утреннего сообщения или введите 'Пропустить' чтобы оставить без видео."
        )
        return
    
    # ★★★ ОБРАБОТКА ОБЫЧНОГО ВВОДА ★★★
    if '|' not in text:
        await safe_reply(update, context, 
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>Текст сообщения | Время (например: 09:00)</code>\n\n"
            "Или отправьте <code>/skip</code> чтобы оставить текущий текст",
            parse_mode="HTML"
        )
        return
    
    try:
        message_text, time_str = [part.strip() for part in text.split('|', 1)]
        
        # Проверяем формат времени
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
            await safe_reply(update, context, 
                "❌ <b>Неверный формат времени!</b>\n\n"
                "Используйте формат: <code>ЧЧ:MM</code> (например: 09:00)",
                parse_mode="HTML"
            )
            return
        
        # Получаем текущее видео чтобы не потерять его
        _, _, current_video = get_morning_message()
        set_morning_message(message_text, time_str, current_video)
        
        context.user_data['waiting_for_morning_message'] = False
        
        await safe_reply(update, context, 
            f"✅ <b>Утреннее сообщение обновлено!</b>\n\n"
            f"📝 <b>Текст:</b> {message_text}\n"
            f"⏰ <b>Время отправки:</b> {time_str}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_morning_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для утреннего сообщения или введите 'Пропустить' чтобы оставить без видео."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении утреннего сообщения: {e}")
        await safe_reply(update, context, 
            "❌ <b>Произошла ошибка при обновлении утреннего сообщения.</b>",
            parse_mode="HTML"
        )
        
async def handle_evening_reminder_input(update: Update, context: CallbackContext):
    if not context.user_data.get('waiting_for_evening_reminder'):
        return
    
    text = update.message.text
    
    # ★★★ ОБРАБОТКА КОМАНДЫ /skip ★★★
    if text == '/skip':
        context.user_data['waiting_for_evening_reminder'] = False
        current_text, current_time, current_video = get_evening_reminder()
        
        await safe_reply(update, context, 
            f"✅ <b>Текст вечернего напоминания оставлен без изменений!</b>\n\n"
            f"📝 <b>Текст:</b> {current_text}\n"
            f"⏰ <b>Время:</b> {current_time}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_evening_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для вечернего напоминания или введите 'Пропустить' чтобы оставить без видео."
        )
        return
    
    # ★★★ ОБРАБОТКА ОБЫЧНОГО ВВОДА ★★★
    if '|' not in text:
        await safe_reply(update, context, 
            "❌ <b>Неверный формат!</b>\n\n"
            "Используйте формат: <code>Текст сообщения | Время (например: 20:00)</code>\n\n"
            "Или отправьте <code>/skip</code> чтобы оставить текущий текст",
            parse_mode="HTML"
        )
        return
    
    try:
        message_text, time_str = [part.strip() for part in text.split('|', 1)]
        
        # Проверяем формат времени
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
            await safe_reply(update, context, 
                "❌ <b>Неверный формат времени!</b>\n\n"
                "Используйте формат: <code>ЧЧ:MM</code> (например: 20:00)",
                parse_mode="HTML"
            )
            return
        
        # Получаем текущее видео чтобы не потерять его
        _, _, current_video = get_evening_reminder()
        set_evening_reminder(message_text, time_str, current_video)
        
        context.user_data['waiting_for_evening_reminder'] = False
        
        await safe_reply(update, context, 
            f"✅ <b>Вечернее напоминание обновлено!</b>\n\n"
            f"📝 <b>Текст:</b> {message_text}\n"
            f"⏰ <b>Время отправки:</b> {time_str}",
            parse_mode="HTML"
        )
        
        # Предлагаем обновить видео
        context.user_data['waiting_for_evening_video'] = True
        await safe_reply(update, context,
            "📹 Теперь отправьте видео для вечернего напоминания или введите 'Пропустить' чтобы оставить без видео."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении вечернего напоминания: {e}")
        await safe_reply(update, context, 
            "❌ <b>Произошла ошибка при обновлении вечернего напоминания.</b>",
            parse_mode="HTML"
        )

# Новый универсальный обработчик видео
async def handle_video_input(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    
    # Проверяем для какого сообщения предназначено видео
    if context.user_data.get('waiting_for_morning_video'):
        if update.message.video:
            video_id = update.message.video.file_id
            current_message, current_time, _ = get_morning_message()
            set_morning_message(current_message, current_time, video_id)
            context.user_data['waiting_for_morning_video'] = False
            await safe_reply(update, context, "✅ Видео добавлено к утреннему сообщению!")
        # Если это текст "Пропустить" - обрабатывается в handle_message
    
    elif context.user_data.get('waiting_for_evening_video'):
        if update.message.video:
            video_id = update.message.video.file_id
            current_message, current_time, _ = get_evening_reminder()
            set_evening_reminder(current_message, current_time, video_id)
            context.user_data['waiting_for_evening_video'] = False
            await safe_reply(update, context, "✅ Видео добавлено к вечернему напоминанию!")
            
async def handle_skip_command(update: Update, context: CallbackContext):
    """Обработчик команды /skip"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет прав для использования этой команды.")
        return
    
    # Проверяем контекст и перенаправляем в соответствующий обработчик
    if context.user_data.get('waiting_for_morning_message'):
        await handle_morning_message_input(update, context)
    elif context.user_data.get('waiting_for_evening_reminder'):
        await handle_evening_reminder_input(update, context)
    else:
        await safe_reply(update, context, 
            "ℹ️ Команда /skip доступна только при редактировании утренних/вечерних сообщений."
        )
 
# Показать список выплат
async def show_payouts_list(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    users = get_users_for_payout()
    
    if not users:
        await safe_reply(update, context, "💸 <b>Нет пользователей для выплат.</b>", parse_mode="HTML")
        return
    
    message = "💸 <b>Пользователи для выплат:</b>\n\n"
    
    for user in users:
        user_id, username, first_name, last_name, balance = user
        user_link = f"<a href='tg://user?id={user_id}'>{first_name or ''} {last_name or ''}</a>".strip() or f"Пользователь {user_id}"
        message += f"👤 {user_link}\n🆔 ID: {user_id}\n💰 Баланс: {balance} руб.\n\n"
    
    message += "📞 <b>Свяжитесь с пользователями для осуществления выплат.</b>"
    
    await safe_reply(update, context, message, parse_mode="HTML")

# Обработчик выплат
async def handle_payout(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.", parse_mode=None)
        return
    
    if not context.args or len(context.args) < 2:
        await safe_reply(update, context, 
            "❌ Неверный формат команды!\n\n"
            "Используйте: /pay <ID пользователя> <сумма>",
            parse_mode=None
        )
        return
    
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        
        logger.info(f"Попытка выплаты: user_id={user_id}, amount={amount}")
        
        current_balance = get_user_balance(user_id)
        if current_balance < amount:
            await safe_reply(update, context, 
                f"❌ Недостаточно средств!\n\n"
                f"У пользователя {user_id} только {current_balance} руб.",
                parse_mode=None
            )
            return
        
        # Обрабатываем выплату
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        UPDATE user_progress 
        SET balance = balance - ?
        WHERE user_id = ?
        ''', (amount, user_id))
        conn.commit()
        conn.close()
        
        # Уведомление пользователю
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"💸 Вам выплачено {amount} рублей! Проверьте ваш кошелек."
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}")
        
        await safe_reply(update, context, 
            f"✅ Выплата осуществлена!\n\n"
            f"👤 Пользователь: {user_id}\n"
            f"💰 Сумма: {amount} руб.\n"
            f"📊 Новый баланс: {current_balance - amount} руб.",
            parse_mode=None
        )
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await safe_reply(update, context, f"Ошибка: {e}", parse_mode=None)

# Рассылка сообщений
async def start_broadcast(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    await safe_reply(update, context, 
        "📢 <b>Введите сообщение для рассылки:</b>\n\n"
        "Можно использовать HTML-разметку.",
        parse_mode="HTML"
    )
    context.user_data['waiting_for_broadcast'] = True

# Обработчик рассылки
async def handle_broadcast(update: Update, context: CallbackContext):
    if not context.user_data.get('waiting_for_broadcast'):
        return
    
    message = update.message.text
    context.user_data['waiting_for_broadcast'] = False
    
    await safe_reply(update, context, "🔄 <b>Начинаю рассылку...</b>", parse_mode="HTML")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    success = 0
    failed = 0
    
    for user in users:
        user_id = user[0]
        try:
            await context.bot.send_message(user_id, message, parse_mode="HTML")
            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
    
    await safe_reply(update, context, 
        f"📊 <b>Рассылка завершена:</b>\n\n"
        f"✅ Успешно: {success}\n"
        f"❌ Не удалось: {failed}",
        parse_mode="HTML"
    )

# Обработчик кнопки помощи в задании
async def handle_task_help(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    buttons = get_task_help_buttons()
    
    if not buttons:
        await safe_reply(update, context, "❌ Раздел помощи временно недоступен.")
        return
    
    keyboard = []
    for button in buttons:
        button_id, question, answer, order_index, created_at = button
        keyboard.append([InlineKeyboardButton(question, callback_data=f"help_{button_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_reply(update, context, 
        "🆘 <b>Выберите нужный вопрос:</b>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

# Обработчик инлайн-кнопок помощи
async def handle_help_button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == HELP_BACK_CALLBACK:
        buttons = get_task_help_buttons()
        
        keyboard = []
        for button in buttons:
            button_id, question, answer, order_index, created_at = button
            keyboard.append([InlineKeyboardButton(question, callback_data=f"help_{button_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🆘 <b>Выберите нужный вопрос:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    elif data.startswith("help_"):
        button_id = int(data.replace("help_", ""))
        answer = get_task_help_answer(button_id)
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=HELP_BACK_CALLBACK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            answer,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
async def handle_cancel_command(update: Update, context: CallbackContext):
    """Обработчик команды /cancel"""   
    # Сбрасываем все состояния ожидания
    states_to_clear = [
        'waiting_for_morning_video', 'waiting_for_evening_video',
        'waiting_for_new_category', 'waiting_for_edit_category_id',
        'waiting_for_edit_category_data', 'waiting_for_delete_category',
        'waiting_for_assign_category', 'editing_button_id',
        'editing_button_title', 'waiting_for_morning_message',
        'waiting_for_evening_reminder', 'waiting_for_reset_user_id',
        'waiting_for_broadcast', 'waiting_for_reject_comment',
        'temp_photo_id', 'waiting_for_withdrawal_details',
        'waiting_for_reject_withdrawal_comment', 'waiting_for_withdrawal_amount'
    ]
    
    for state in states_to_clear:
        if state in context.user_data:
            del context.user_data[state]
    
    await safe_reply(update, context,
        "✅ <b>Все операции отменены.</b>\n\n"
        "Состояния сброшены.",
        parse_mode="HTML"
    )
# Универсальное уведомление
async def send_notification(user_id: int, text: str, context: CallbackContext):
    # Сохраняем уведомление в базу
    add_notification(user_id, text)
    
    try:
        # Отправляем пользователю
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
        
# Обработчик инлайн-кнопок для скриншотов
async def handle_screenshot_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith(APPROVE_SCREENSHOT):
        user_id = int(data.replace(f"{APPROVE_SCREENSHOT}_", ""))
        
        # Получаем информацию о задании для отображения ID фото
        task_info = get_user_task(user_id)
        photo_id_info = ""
        if task_info:
            photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
            photo_id_info = f"🖼️ <b>ID фото задания:</b> {photo_id}\n"
        
        # Обновляем статус скриншота
        update_screenshot_status(user_id, 'approved', context=context)
        
        # Уведомляем пользователя
        user_info = get_user_info(user_id)
        if user_info:
            user_id_db, username, first_name, last_name, phone_number, joined_at, last_active = user_info
            user_link = f"<a href='tg://user?id={user_id_db}'>{first_name or ''} {last_name or ''}</a>".strip() or f"Пользователь {user_id_db}"
            
            await send_notification(
                user_id_db,
                "✅ Ваш скриншот одобрен! Ваш баланс пополнен на 200 рублей.",
                context
            )
            
            text = (
                f"✅ <b>Скриншот одобрен!</b>\n\n"
                f"👤 Пользователь: {user_link}\n"
                f"🆔 ID: {user_id_db}\n"
                f"{photo_id_info}"
                f"💰 Начислено: 200 рублей"
            )
            
            if query.message.text:
                # Если было текстовое сообщение — редактируем
                await query.edit_message_text(text, parse_mode="HTML")
            else:
                # Если кнопка была под фото — шлём новое сообщение
                await query.message.reply_text(text, parse_mode="HTML")
    
    elif data.startswith(REJECT_SCREENSHOT):
        user_id = int(data.replace(f"{REJECT_SCREENSHOT}_", ""))
        
        # Получаем информацию о задании для отображения ID фото
        task_info = get_user_task(user_id)
        photo_id_info = ""
        if task_info:
            photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
            photo_id_info = f"🖼️ <b>ID фото задания:</b> {photo_id}\n"
        
        # Сохраняем информацию о фото для комментария
        context.user_data['reject_photo_info'] = photo_id_info
        context.user_data['reject_user_id'] = user_id
        
        # Запрашиваем комментарий для отклонения
        context.user_data['waiting_for_reject_comment'] = user_id
        await query.message.reply_text(
            f"📝 <b>Введите комментарий для отклонения скриншота:</b>\n\n"
            f"{photo_id_info}",
            parse_mode="HTML"
        )

# Обработчик комментария для отклонения скриншота
async def handle_reject_comment(update: Update, context: CallbackContext):
    if 'waiting_for_reject_comment' not in context.user_data:
        return
    
    user_id = context.user_data['waiting_for_reject_comment']
    comment = update.message.text
    
    # Обновляем статус скриншота
    update_screenshot_status(user_id, 'rejected', comment, context=context)
    
    # Уведомляем пользователя
    user_info = get_user_info(user_id)
    if user_info:
        user_id_db, username, first_name, last_name, phone_number, joined_at, last_active = user_info
        user_link = f"<a href='tg://user?id={user_id_db}'>{first_name or ''} {last_name or ''}</a>".strip() or f"Пользователь {user_id_db}"
        
        await send_notification(user_id_db, f"❌ Ваш скриншот отклонен. Комментарий: {comment}", context)
        
        # Сообщение администратору или модеру
        await safe_reply(update, context, 
            f"❌ <b>Скриншот отклонен!</b>\n\n"
            f"👤 Пользователь: {user_link}\n"
            f"🆔 ID: {user_id_db}\n"
            f"📝 Комментарий: {comment}",
            parse_mode="HTML"
        )

    # Сбрасываем текущее задание пользователя
    reset_user_task(user_id)

    # Сообщаем пользователю, что он может взять новое задание
    kb = [[KeyboardButton("Меню")]]
    await context.bot.send_message(
        chat_id=user_id,
        text="❌ Ваш скриншот отклонен. Вы можете взять новое задание.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    
    # Убираем флаг ожидания комментария
    del context.user_data['waiting_for_reject_comment']
# Обработчик кнопки "Получить задание"
async def handle_get_task(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)

    can, _ = can_assign_task(user_id)
    
    if not can:
        # Получаем текущее задание пользователя
        task_info = get_user_task(user_id)
        if task_info:
            # Показываем текущее задание через улучшенный интерфейс
            await show_enhanced_task_interface(update, context, user_id, task_info)
        return

    # Проверяем, есть ли у пользователя текущее задание
    current_task = get_user_task(user_id)
    exclude_category_id = None
    
    if current_task:
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = current_task
        
        # Получаем категорию текущего задания
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT category_id FROM photos WHERE id = ?", (photo_id,))
        result = cursor.fetchone()
        if result:
            exclude_category_id = result[0]
        conn.close()
    
    # Для новых пользователей (без текущего задания) получаем любое доступное задание
    if not current_task:
        # Используем стандартную функцию без исключения категорий
        available_photos = get_available_photos(user_id)
    else:
        # Для пользователей с текущим заданием - получаем из других категорий
        available_photos = get_available_photos_from_other_categories(user_id, exclude_category_id)
    
    if not available_photos:
        # Если нет доступных заданий с учетом исключения, пробуем найти любое доступное
        available_photos = get_available_photos(user_id, exclude_category_id=exclude_category_id)
        
        if not available_photos:
            await safe_reply(update, context, 
                "❌ <b>На данный момент нет доступных заданий.</b>\n\n"
                "Попробуйте позже или обратитесь к администратору.",
                parse_mode="HTML"
            )
            return

    photo = available_photos[0]
    assign_task_to_user(user_id, photo[0])
    
    # Получаем информацию о категории нового задания
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.id, p.photo_id, c.name 
        FROM photos p 
        LEFT JOIN task_categories c ON p.category_id = c.id 
        WHERE p.id = ?
    ''', (photo[0],))
    new_photo_info = cursor.fetchone()
    conn.close()
    
    # Получаем полную информацию о задании
    task_info = get_user_task(user_id)
    if task_info and new_photo_info:
        # Добавляем информацию о категории к task_info
        photo_id, photo_file_id, category_name = new_photo_info
        extended_task_info = task_info + (category_name or "Без категории",)
        await show_enhanced_task_interface(update, context, user_id, extended_task_info)
    else:
        await show_enhanced_task_interface(update, context, user_id, task_info)

# Новая функция для получения фото с исключением категории
def get_available_photos(user_id, count=1, exclude_category_id=None):
    """Возвращает доступные фото, которые пользователь еще не выполнял"""
    completed_tasks = get_completed_tasks(user_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем список всех категорий с фото
    cursor.execute("SELECT DISTINCT category_id FROM photos WHERE category_id IS NOT NULL")
    all_categories = [row[0] for row in cursor.fetchall()]
    
    if not all_categories:
        # Если нет категорий, возвращаем пустой список
        conn.close()
        return []
    
    # Для новых пользователей или если исключена определенная категория
    if not completed_tasks or len(completed_tasks) == 0:
        # Новый пользователь - показываем любую категорию
        if exclude_category_id:
            # Исключаем указанную категорию
            cursor.execute(
                "SELECT * FROM photos WHERE category_id != ? ORDER BY RANDOM() LIMIT ?",
                (exclude_category_id, count)
            )
        else:
            # Без исключений
            cursor.execute("SELECT * FROM photos ORDER BY RANDOM() LIMIT ?", (count,))
    else:
        # Пользователь уже выполнял задания
        if exclude_category_id:
            # Исключаем выполненные задания и указанную категорию
            placeholders = ','.join('?' * len(completed_tasks))
            cursor.execute(
                f"SELECT * FROM photos WHERE id NOT IN ({placeholders}) AND category_id != ? ORDER BY RANDOM() LIMIT ?",
                completed_tasks + [exclude_category_id, count]
            )
        else:
            # Исключаем только выполненные задания
            placeholders = ','.join('?' * len(completed_tasks))
            cursor.execute(
                f"SELECT * FROM photos WHERE id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT ?",
                completed_tasks + [count]
            )
    
    photos = cursor.fetchall()
    conn.close()
    return photos

async def show_enhanced_task_interface(update: Update, context: CallbackContext, user_id, task_info):
    """Показывает улучшенный интерфейс управления заданием"""
    
    # Распаковываем task_info
    if len(task_info) >= 9:
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, replacement_count, last_replacement_reset = task_info[:9]
    else:
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step = task_info[:7]
        replacement_count = get_replacement_count(user_id)
        last_replacement_reset = get_last_replacement_reset(user_id)
    
    # Получаем информацию о категории задания
    category_name = "Без категории"
    if photo_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.name 
            FROM photos p 
            LEFT JOIN task_categories c ON p.category_id = c.id 
            WHERE p.id = ?
        ''', (photo_id,))
        category_result = cursor.fetchone()
        category_name = category_result[0] if category_result else "Без категории"
        conn.close()
    
    # ★★★ ИСПРАВЛЕННАЯ ЛОГИКА ОТОБРАЖЕНИЯ ★★★
    if current_step == TASK_STATUS["CONFIRM_CALL"]:
        # Пользователь еще не подтвердил звонок - показываем задание и кнопки управления
        instruction = get_instruction()
        
        # Проверяем, не прошло ли 3 дня с последнего сброса счетчика замен
        available_replacements = 2
        if last_replacement_reset:
            try:
                # Преобразуем строку в datetime
                last_reset = datetime.strptime(last_replacement_reset, "%Y-%m-%d %H:%M:%S")
                # Если прошло 3 дня, обнуляем счетчик
                if (datetime.now() - last_reset).days >= 3:
                    # Обновляем в базе
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                    UPDATE user_progress 
                    SET replacement_count = 0, last_replacement_reset = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    ''', (user_id,))
                    conn.commit()
                    conn.close()
                    replacement_count = 0
                    available_replacements = 2
                else:
                    available_replacements = 2 - replacement_count
            except Exception as e:
                logger.error(f"Ошибка при обработке даты сброса счетчика замен: {e}")
                available_replacements = 2 - replacement_count
        else:
            available_replacements = 2 - replacement_count
        
        # Создаем клавиатуру с учетом доступных замен
        keyboard = []
        if available_replacements > 0:
            keyboard.append([KeyboardButton("🔄 Заменить задание")])
            
        keyboard.extend([
            [KeyboardButton("✅ Готово"), KeyboardButton("🆘 Помощь в задании")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # Сначала отправляем фото с инструкцией
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=photo_file_id,
                caption=f"📝 <b>Ваше задание:</b>\n\n{instruction}\n\n📁 <b>Категория:</b> {category_name}",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
            await safe_reply(update, context, 
                f"📝 <b>Ваше задание:</b>\n\n{instruction}\n\n📁 <b>Категория:</b> {category_name}",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        
        # Сообщение о статусе замен
        status_message = "✅ <b>После выполнения нажмите 'Готово'</b>"
        
        if available_replacements > 0:
            status_message += f"\n🔄 <b>Доступно замен:</b> {available_replacements}"
        else:
            if last_replacement_reset:
                try:
                    last_reset = datetime.strptime(last_replacement_reset, "%Y-%m-%d %H:%M:%S")
                    days_passed = (datetime.now() - last_reset).days
                    days_remaining = 3 - days_passed
                    if days_remaining > 0:
                        status_message += f"\n🔄 <b>Следующая замена через:</b> {days_remaining} дней"
                except:
                    pass
        
    else:
        # Пользователь уже подтвердил звонок - показываем только статус и кнопки
        status_messages = {
            TASK_STATUS["WAITING_REVIEW_DAY"]: "⏳ <b>Ожидание утреннего сообщения</b>\n\nЗавтра утром получите инструкцию по отзыву",
            TASK_STATUS["WAITING_REVIEW_EVENING"]: "🌙 <b>В 19:00 по МСК я пришлю примерный текст отзыва. Если Ваш часовой пояс разнится с Московским, вы можете оставить отзыв вечером по вашему времени.</b>\n\n 🌅 <b>Утром присылал видео-инструкцию, там показан принцип оставления отзыва.</b>\n\n📝 <b>Отзыв рекомендуем оставлять вечером, так вероятность что Ваш отзыв пройдет модерацию Авито - больше.</b>",
            TASK_STATUS["SEND_SCREENSHOT"]: "📸 <b>Ожидание скриншота</b>\n\nПришлите скриншот раздела 'Мои отзывы' в профиле на Авито.",
            TASK_STATUS["WAITING_ADMIN_REVIEW"]: "⏳ <b>Скриншот на проверке</b>\n\nОжидайте решения администратора",
            TASK_STATUS["COMPLETED"]: "✅ <b>Задание завершено</b>\n\nМожете получить новое задание",
            TASK_STATUS["SCREENSHOT_REJECTED"]: "❌ <b>Скриншот отклонен</b>\n\nМожете получить новое задание"
        }
        
        status_message = status_messages.get(current_step, f"📊 <b>Статус:</b> {current_step}")
        status_message += f"\n📁 <b>Категория:</b> {category_name}"
        
        # Определяем кнопки в зависимости от статуса
        keyboard = []
        if current_step in [TASK_STATUS["SEND_SCREENSHOT"], TASK_STATUS["WAITING_REVIEW_EVENING"], TASK_STATUS["SCREENSHOT_REJECTED"]]:
            main_buttons = [KeyboardButton("📸 Прислать скриншот")]
        elif current_step in [TASK_STATUS["COMPLETED"], TASK_STATUS["CANCELLED"]]:
            main_buttons = [KeyboardButton("Получить задание")]
        else:
            main_buttons = [KeyboardButton("📋 Показать задание")]
        
        # Разбиваем на ряды по 2 кнопки
        for i in range(0, len(main_buttons), 2):
            keyboard.append(main_buttons[i:i+2])
        
        keyboard.append([KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")])
        keyboard.append([KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
        await safe_reply(update, context, 
            status_message,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

# Обработчик кнопки "Один аккаунт"
async def handle_single_account(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    photo = get_random_photo()
    instruction = get_instruction()
    
    if photo:
        photo_id_db = photo[0]
        photo_file_id = photo[1]
        
        assign_task_to_user(user_id, photo_id_db)
        
        await safe_reply(update, context, 
            "✅ <b>Задание получено!</b>\n\n"
            "🚀 <b>Как выполните звонок и добавите в избранное нажмите готово!</b>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        
        await update.message.reply_photo(
            photo=photo_file_id,
            caption=instruction,
            parse_mode="HTML"
        )
        
        keyboard = [
            [KeyboardButton("✅ Готово"), KeyboardButton("🆘 Помощь в задании")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context, 
            "✅ <b>После выполнения задания нажмите кнопку ниже:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    else:
        await safe_reply(update, context, "❌ <b>Фото временно отсутствуют!</b>\n\nОбратитесь к администратору.", parse_mode="HTML")

# Обработчик кнопки "Готово"
async def handle_ready(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, мы поговорили", callback_data=CONFIRM_CALLBACK)],
        [InlineKeyboardButton("❌ Нет, не удалось", callback_data=CANCEL_CALLBACK)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await safe_reply(
        update, context,
        "❗ <b>Обратите внимание!</b> Вы подтверждаете, что Вам удалось поговорить с менеджером 1 минуту и более?",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

    if message:  # проверяем, что не None
        context.user_data['confirmation_message_id'] = message.message_id
    else:
        context.user_data['confirmation_message_id'] = None

# Обработчик скриншотов
# Пользователь присылает скриншот
async def handle_screenshot(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    update_user_activity(user_id)

    user_step = get_user_step(user_id)

    # Разрешённые шаги
    allowed_steps = ["send_screenshot", "waiting_review_evening", "screenshot_rejected", "waiting_review_day"]
    if user_step not in allowed_steps:
        await safe_reply(update, context,
            "❌ <b>Сначала получите задание и подтвердите его выполнение!</b>",
            parse_mode="HTML"
        )
        return

    if not update.message.photo:
        await safe_reply(update, context,
            "❌ <b>Пожалуйста, прикрепите скриншот к сообщению.</b>",
            parse_mode="HTML"
        )
        return

    # Берём последнее фото (лучшее качество)
    screenshot_id = update.message.photo[-1].file_id
    save_screenshot(user_id, screenshot_id)

    # Обновляем шаг пользователя
    update_user_step(user_id, "waiting_admin_review")

    # Уведомляем админа
    user_info = get_user_info(user_id)
    if user_info:
        # ИСПРАВЛЕНО: используем разные имена переменных
        info_user_id, username, first_name, last_name, phone_number, joined_at, last_active = user_info

        user_link = f"<a href='tg://user?id={info_user_id}'>{first_name or ''} {last_name or ''}</a>".strip() \
                    or f"Пользователь {info_user_id}"
        # Получаем информацию о задании пользователя
        task_info = get_user_task(user_id)
        photo_id_info = ""
        category_info = ""
        if task_info:
            photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
            photo_id_info = f"🖼️ <b>ID фото задания:</b> {photo_id}\n"
            
            # Получаем информацию о категории
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT c.name 
                FROM photos p 
                LEFT JOIN task_categories c ON p.category_id = c.id 
                WHERE p.id = ?
            ''', (photo_id,))
            category_result = cursor.fetchone()
            category_name = category_result[0] if category_result else "Без категории"
            conn.close()
            
            category_info = f"📁 <b>Категория задания:</b> {category_name}\n"

        # Отправляем скриншот админу
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=screenshot_id,
            caption=(
                f"📸 <b>Новый скриншот от пользователя:</b>\n\n"
                f"👤 {user_link}\n"
                f"🆔 <b>ID:</b> {info_user_id}\n"
                f"{photo_id_info}"
                f"{category_info}"
                f"📅 <b>Время отправки:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"{APPROVE_SCREENSHOT}_{info_user_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"{REJECT_SCREENSHOT}_{info_user_id}")
                ]
            ])
        )

    # Сообщение пользователю
    await safe_reply(update, context,
        "✅ <b>Скриншот отправлен на проверку администратору!</b>\n\n"
        "⏳ <b>Обычно проверка занимает до 24 часов. Вам придет уведомление в профиль💡</b>\n\n"
        "📞 <b>Если возникли вопросы:</b> @denvr11",
        parse_mode="HTML"
    )

    # Меняем клавиатуру
    keyboard = [
        [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
        [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await safe_reply(update, context,
        "🔄 <b>Ожидайте проверки администратора.</b>",
        reply_markup=reply_markup
    )
# Обработчик инлайн-кнопок
async def handle_button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == CONFIRM_CALLBACK:
        confirm_user_call(user_id)

        # ПРАВИЛЬНАЯ КЛАВИАТУРА после подтверждения звонка
        kb = [[KeyboardButton("📞 Связаться с админом")],
              [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
              [KeyboardButton("Меню")]]
        reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
        
        await query.message.reply_text(
            "✅ <b>Звонок подтверждён! Отлично!</b>\n\n"
            "🌅 <b>Завтра утром я пришлю вам инструкцию по оставлению отзыва.</b>\n"
            "📝 <b>Пока ничего делать не нужно - просто ждите утреннего сообщения.</b>",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

        # Отправляем уведомление админу
        user_info = get_user_info(user_id)
        if user_info:
            user_id, username, first_name, last_name, phone_number, joined_at, last_active = user_info
            user_link = f"<a href='tg://user?id={user_id}'>{first_name or ''} {last_name or ''}</a>".strip() or f"Пользователь {user_id}"
            message = f"✅ <b>Пользователь подтвердил выполнение задания!</b>\n\n👤 {user_link}\n🆔 ID: {user_id}"
            await context.bot.send_message(ADMIN_ID, message, parse_mode="HTML")

    elif data == CANCEL_CALLBACK:
        await query.edit_message_text("❌ <b>Звонок не подтвержден.</b>", parse_mode="HTML")
        
        keyboard = [
            [KeyboardButton("✅ Готово"), KeyboardButton("🆘 Помощь в задании")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
            [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ <b>Вы можете подтвердить выполнение позже:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    elif data.startswith(APPROVE_SCREENSHOT) or data.startswith(REJECT_SCREENSHOT):
        await handle_screenshot_callback(update, context)
    
    elif data.startswith(USER_LIST_PAGE):
        page = int(data.split('_')[-1])
        await show_all_users(update, context, page)
    
    elif data.startswith(CALLED_LIST_PAGE):
        page = int(data.split('_')[-1])
        await show_called_users(update, context, page)
    
    elif data.startswith(SCREENSHOT_LIST_PAGE):
        page = int(data.split('_')[-1])
        await show_screenshot_users(update, context, page)
    
    elif data.startswith("delete_photo_"):
        photo_id = int(data.replace("delete_photo_", ""))
        delete_photo(photo_id)
        await query.edit_message_caption(caption=f"❌ Фото {photo_id} удалено.")
    
    elif data == EDIT_INFO:
        await info_message_settings(update, context)
    
    elif data == EDIT_MORNING:
        await morning_message_settings(update, context)
    
    elif data == EDIT_EVENING:
        await evening_reminder_settings(update, context)
    
    elif data == SEND_MORNING_NOW:
        await send_morning_messages(context)
        await update.callback_query.answer('Утреннее отправлено')
        await morning_message_settings(update, context)
    
    elif data == SEND_EVENING_NOW:
        await send_evening_reminders(context)
        await update.callback_query.answer('Вечернее отправлено')
        await evening_reminder_settings(update, context)
        
    elif data.startswith("photos_page_"):
        page = int(data.replace("photos_page_", ""))
        await show_all_photos(update, context, page)
    elif data == "noop":
        # Пустое действие - ничего не делаем
        await query.answer()
        
    elif data.startswith("select_category_"):
        await handle_category_selection(update, context)
    elif data == "cancel_add_photo":
        await update.callback_query.edit_message_caption(caption="❌ Добавление фото отменено.")
    elif data.startswith("change_category_"):
        await handle_change_category(update, context)
    elif data.startswith("show_categories_"):
        photo_id = int(data.replace("show_categories_", ""))
        
        # Получаем все категории
        categories = get_all_categories()
        
        # Получаем текущую категорию фото
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT category_id FROM photos WHERE id = ?", (photo_id,))
        result = cursor.fetchone()
        current_category_id = result[0] if result else 1
        conn.close()
        
        # Создаем клавиатуру с категориями
        keyboard = []
        for cat in categories:
            cat_id, name, description, created_at = cat
            if cat_id != current_category_id:  # Не показываем текущую категорию
                keyboard.append([InlineKeyboardButton(f"📁 {name}", callback_data=f"change_category_{photo_id}_{cat_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_category_change_{photo_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_caption(
            caption=f"🖼️ <b>Фото ID:</b> {photo_id}\n\n📁 <b>Выберите новую категорию:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    elif data.startswith("cancel_category_change_"):
        photo_id = int(data.replace("cancel_category_change_", ""))
        
        # Получаем информацию о фото
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.id, p.photo_id, p.category_id, c.name 
            FROM photos p 
            LEFT JOIN task_categories c ON p.category_id = c.id 
            WHERE p.id = ?
        ''', (photo_id,))
        photo = cursor.fetchone()
        conn.close()
        
        if photo:
            photo_id, photo_file_id, category_id, category_name = photo
            category_name = category_name or "Без категории"
            
            # Создаем оригинальную клавиатуру
            categories = get_all_categories()
            
            def should_show_category_buttons(cat_id):
                return cat_id in [None, 0, 1]
            
            keyboard = []
            keyboard.append([InlineKeyboardButton("❌ Удалить", callback_data=f"delete_photo_{photo_id}")])
            
            if should_show_category_buttons(category_id):
                for cat in categories:
                    cat_id, name, description, created_at = cat
                    if cat_id != category_id:
                        keyboard.append([InlineKeyboardButton(f"📁 В категорию: {name}", callback_data=f"change_category_{photo_id}_{cat_id}")])
            else:
                keyboard.append([InlineKeyboardButton(f"📁 Текущая: {category_name}", callback_data=f"show_categories_{photo_id}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_caption(
                caption=f"🖼️ <b>Фото ID:</b> {photo_id}\n📁 <b>Категория:</b> {category_name}",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    
    elif data == BACK_TO_EDITOR:
        await editor_panel(update, context)
        
    elif data == "called_stats":
        stats = get_called_stats()
        
        message = "📊 <b>Статистика по подтвердившим звонок:</b>\n\n"
        message += f"✅ <b>Всего подтвердивших:</b> {stats['total_called']}\n\n"
        
        # Статистика по скриншотам
        if stats['screenshot_stats']:
            approved, rejected, pending, not_sent = stats['screenshot_stats']
            message += "<b>Статусы скриншотов:</b>\n"
            message += f"✅ Одобрено: {approved or 0}\n"
            message += f"❌ Отклонено: {rejected or 0}\n"
            message += f"⏳ На проверке: {pending or 0}\n"
            message += f"📭 Не отправлено: {not_sent or 0}\n\n"
        
        # Последние 7 дней
        if stats['last_7_days']:
            message += "<b>Активность за 7 дней:</b>\n"
            for date_str, count in stats['last_7_days']:
                message += f"📅 {date_str}: {count} подтверждений\n"
        
        await query.edit_message_text(message, parse_mode="HTML")
        
    elif data == "screenshot_stats":
        stats = get_screenshot_stats()
        
        message = "📊 <b>Статистика по скриншотам:</b>\n\n"
        message += f"📸 <b>Всего скриншотов:</b> {stats['total_screenshots']}\n\n"
        
        # Статусы
        if stats['status_counts']:
            message += "<b>Распределение по статусам:</b>\n"
            for status, count in stats['status_counts']:
                if status == 'approved':
                    icon = "✅"
                elif status == 'rejected':
                    icon = "❌"
                elif status == 'pending':
                    icon = "⏳"
                else:
                    icon = "❓"
                message += f"{icon} {status}: {count}\n"
            message += "\n"
        
        # Среднее время
        message += f"⏱️ <b>Среднее время выполнения:</b> {stats['avg_hours']:.1f} часов\n\n"
        
        # Последние 7 дней
        if stats['last_7_days']:
            message += "<b>Отправки за 7 дней:</b>\n"
            for date_str, count in stats['last_7_days']:
                message += f"📅 {date_str}: {count} скриншотов\n"
        
        await query.edit_message_text(message, parse_mode="HTML")

    elif data == "confirm_delete_all_photos":
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Удаляем все фото
        cursor.execute("DELETE FROM photos")
        
        # Сбрасываем все задания пользователей
        cursor.execute('''
        UPDATE user_progress 
        SET current_step = 'cancelled', 
            photo_id = NULL,
            called = FALSE,
            called_confirmed = FALSE,
            morning_message_sent = FALSE,
            evening_reminder_sent = FALSE,
            screenshot_sent = FALSE,
            screenshot_status = 'not_sent'
        ''')
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        await query.edit_message_text(
            f"✅ <b>Все фото удалены!</b>\n\n"
            f"🗑️ Удалено фото: все\n"
            f"🔄 Сброшено заданий: {deleted_count}\n\n"
            f"📝 База данных очищена. Не забудьте добавить новые фото заданий.",
            parse_mode="HTML"
        )

    elif data == "cancel_delete_all_photos":
        await query.edit_message_text("❌ Удаление фото отменено.")

    # ★★★ ДОБАВЬТЕ ЭТОТ БЛОК ДЛЯ РЕДАКТИРОВАНИЯ ИНФОРМАЦИОННЫХ КНОПОК ★★★
    elif data.startswith("edit_info_button_"):
        button_id = int(data.replace("edit_info_button_", ""))
        button_info = get_info_button(button_id)
        
        if button_info:
            button_id, title, content, order_index, created_at = button_info
            
            # Сохраняем ID кнопки для редактирования
            context.user_data['editing_button_id'] = button_id
            context.user_data['editing_button_title'] = title
            
            await query.edit_message_text(
                f"📝 <b>Редактирование кнопки:</b> {title}\n\n"
                f"<b>Текущее содержание:</b>\n{content}\n\n"
                f"Отправьте новый текст для этой кнопки:",
                parse_mode="HTML"
            )
    
    # ★★★ ИСПРАВЛЕННЫЙ ПОРЯДОК ОБРАБОТКИ ИНФОРМАЦИОННЫХ КНОПОК ★★★
    # Сначала обрабатываем "info_back", потом "info_"
    elif data == "info_back":
        # Возврат к списку информационных кнопок
        buttons = get_info_buttons()
        
        keyboard = []
        for button in buttons:
            button_id, title, content, order_index, created_at = button
            keyboard.append([InlineKeyboardButton(title, callback_data=f"info_{button_id}")])
        
        # Добавляем кнопку редактирования для админа
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("✏️ Редактировать информацию", callback_data="edit_info_admin")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            "ℹ️ <b>Информация о нашем сервисе</b>\n\n"
            "Выберите интересующий вас раздел:"
        )
        
        await query.edit_message_text(
            welcome_message,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    elif data == "edit_info_admin":
        # Админ нажал "Редактировать информацию" - переходим в редактор
        if user_id == ADMIN_ID:
            await editor_panel(update, context)
        else:
            await query.answer("У вас нет прав для редактирования", show_alert=True)
    
    elif data.startswith("info_"):
        # Пользователь нажал на одну из информационных кнопок
        # Проверяем, что это не "info_back" (должен быть уже обработан выше)
        if data == "info_back":
            return  # На всякий случай
            
        button_id = int(data.replace("info_", ""))
        content = get_info_content(button_id)
        
        # Кнопка "Назад к информации"
        keyboard = [[InlineKeyboardButton("⬅️ Назад к информации", callback_data="info_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            content,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    # ★★★ ОБРАБОТКА КНОПОК ПОМОЩИ В ЗАДАНИЯХ ★★★
    elif data == HELP_BACK_CALLBACK:
        buttons = get_task_help_buttons()
        
        keyboard = []
        for button in buttons:
            button_id, question, answer, order_index, created_at = button
            keyboard.append([InlineKeyboardButton(question, callback_data=f"help_{button_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🆘 <b>Выберите нужный вопрос:</b>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    elif data.startswith("help_"):
        button_id = int(data.replace("help_", ""))
        answer = get_task_help_answer(button_id)
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=HELP_BACK_CALLBACK)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            answer,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    # ★★★ НОВЫЕ ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ЗАДАНИЯМИ ★★★
    elif data.startswith("show_task_"):
        target_user_id = int(data.replace("show_task_", ""))
        
        # Проверяем, что пользователь запрашивает свое задание
        if query.from_user.id != target_user_id:
            await query.answer("❌ Вы не можете просматривать чужие задания.", show_alert=True)
            return
        
        task_info = get_user_task(target_user_id)
        if not task_info:
            await query.answer("❌ Задание не найдено.", show_alert=True)
            return
        
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
        instruction = get_instruction()
        
        try:
            # Отправляем фото и инструкцию
            await query.message.reply_photo(
                photo=photo_file_id,
                caption=f"📝 <b>Ваше задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )
            await query.answer("✅ Задание показано")
        except Exception as e:
            await query.answer("❌ Ошибка при показе задания", show_alert=True)
            logger.error(f"Ошибка при показе задания: {e}")

    elif data.startswith("replace_task_"):
        target_user_id = int(data.replace("replace_task_", ""))
        
        # Проверяем, что пользователь заменяет свое задание
        if query.from_user.id != target_user_id:
            await query.answer("❌ Вы не можете заменять чужие задания.", show_alert=True)
            return
        
        # Проверяем текущий статус
        current_step = get_user_step(target_user_id)
        if current_step != TASK_STATUS["CONFIRM_CALL"]:
            await query.answer("❌ Задание можно заменять только на этапе подтверждения звонка.", show_alert=True)
            return
        
        # Проверяем лимит замен
        replacement_count = get_replacement_count(target_user_id)
        if replacement_count >= 2:
            await query.answer("❌ Вы исчерпали лимит замен. Обратитесь к администратору.", show_alert=True)
            return
        
        # Получаем новое доступное фото
        available_photos = get_available_photos(target_user_id)
        if not available_photos:
            await query.answer("❌ Нет доступных заданий для замены.", show_alert=True)
            return
        
        new_photo = available_photos[0]
        new_photo_id = new_photo[0]
        new_photo_file_id = new_photo[1]
        
        # Обновляем задание
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE user_progress SET photo_id = ?, replacement_count = replacement_count + 1 WHERE user_id = ?",
            (new_photo_id, target_user_id)
        )
        conn.commit()
        conn.close()
        
        instruction = get_instruction()
        
        try:
            # Отправляем новое задание
            await query.message.reply_photo(
                photo=new_photo_file_id,
                caption=f"🔄 <b>Задание заменено!</b>\n\n📝 <b>Новое задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )
            await query.answer(f"✅ Задание заменено! Осталось замен: {1 - replacement_count}")
        except Exception as e:
            await query.answer("❌ Ошибка при замене задания", show_alert=True)
            logger.error(f"Ошибка при замене задания: {e}")

    elif data == "replace_limit":
        await query.answer("❌ Вы исчерпали лимит замен (2 раза). Обратитесь к администратору.", show_alert=True)

    elif data.startswith("confirm_withdrawal_"):
        user_id = query.from_user.id
        amount = int(data.replace("confirm_withdrawal_", ""))
        
        # Получаем данные из context.user_data
        method = context.user_data.get('withdrawal_method')
        method_name = context.user_data.get('withdrawal_method_name')
        details = context.user_data.get('withdrawal_details')
        
        if not all([method, details]):
            await query.answer("❌ Ошибка данных. Начните заново.", show_alert=True)
            return
        
        # Проверяем еще раз возможность вывода
        can_withdraw, error_message = can_user_withdraw(user_id, amount)
        if not can_withdraw:
            await query.answer(f"❌ {error_message}", show_alert=True)
            return
        
        # Сохраняем реквизиты пользователя
        save_user_payment_method(user_id, method, details)
        
        # Создаем запрос на вывод
        request_id, error = create_withdrawal_request(user_id, amount, method, details)
        if error:
            await query.answer(f"❌ {error}", show_alert=True)
            return
        
        # Получаем информацию о пользователе
        user_info = get_user_info(user_id)
        user_link = format_user_link(user_id, user_info[1], user_info[2], user_info[3])
        
        # Уведомляем администратора
        admin_message = (
            f"💸 <b>НОВЫЙ ЗАПРОС НА ВЫВОД СРЕДСТВ</b>\n\n"
            f"🆔 <b>ID запроса:</b> {request_id}\n"
            f"👤 <b>Пользователь:</b> {user_link}\n"
            f"💰 <b>Сумма:</b> {amount} рублей\n"
            f"💳 <b>Способ:</b> {method_name}\n"
            f"📝 <b>Реквизиты:</b> {details}\n"
            f"⏰ <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"💰 <b>Баланс пользователя:</b> {get_user_balance(user_id)} руб."
        )
        
        # Кнопки для админа
        keyboard = [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"admin_approve_withdrawal_{request_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject_withdrawal_{request_id}")
            ],
            [
                InlineKeyboardButton("👤 Профиль пользователя", callback_data=f"admin_user_profile_{user_id}")
            ]
        ]
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")
        
        # Сообщение пользователю
        await query.edit_message_text(
            f"✅ <b>Запрос на вывод создан!</b>\n\n"
            f"💰 <b>Сумма:</b> {amount} рублей\n"
            f"💳 <b>Способ:</b> {method_name}\n"
            f"📝 <b>Реквизиты:</b> {details}\n\n"
            f"🆔 <b>ID запроса:</b> {request_id}\n"
            f"⏰ <b>Статус:</b> ⏳ Ожидает проверки\n\n"
            f"<i>Администратор получил уведомление. "
            f"Обычно проверка занимает до 24 часов.</i>",
            parse_mode="HTML"
        )
        
        await show_main_menu(update, context, user_id)
        
        # Очищаем данные
        for key in ['withdrawal_method', 'withdrawal_method_name', 'withdrawal_details', 'withdrawal_amount']:
            context.user_data.pop(key, None)

    elif data == "cancel_withdrawal":
        await query.edit_message_text(
            "❌ <b>Вывод средств отменен</b>\n\n"
            "Вы можете повторить операцию в любое время.",
            parse_mode="HTML"
        )

    elif data.startswith("admin_approve_withdrawal_"):
        request_id = int(data.replace("admin_approve_withdrawal_", ""))
        request = get_withdrawal_request(request_id)
        
        if not request:
            await query.answer("Запрос не найден", show_alert=True)
            return
        
        # Получаем данные запроса
        w_id, user_id, amount, method, details, status, comment, created_at, *rest = request
        user_info = get_user_info(user_id)
        user_link = format_user_link(user_id, user_info[1], user_info[2], user_info[3])
        
        # Обновляем статус на "approved"
        update_withdrawal_status(request_id, "approved", "Одобрено администратором")
        
        # Кнопки для завершения выплаты
        keyboard = [
            [
                InlineKeyboardButton("💸 Выплачено", callback_data=f"admin_complete_withdrawal_{request_id}"),
                InlineKeyboardButton("👤 Профиль", callback_data=f"admin_user_profile_{user_id}")
            ],
            [
                InlineKeyboardButton("📋 Все выплаты", callback_data="admin_withdrawals_list_0")
            ]
        ]
        
        await query.edit_message_text(
            f"✅ <b>ЗАПРОС ОДОБРЕН</b>\n\n"
            f"🆔 <b>ID запроса:</b> {request_id}\n"
            f"👤 <b>Пользователь:</b> {user_link}\n"
            f"💰 <b>Сумма:</b> {amount} рублей\n"
            f"💳 <b>Способ:</b> {method}\n"
            f"📝 <b>Реквизиты:</b> {details}\n\n"
            f"<b>После выполнения выплаты нажмите кнопку '💸 Выплачено'</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Уведомляем пользователя
        await send_notification(
            user_id,
            f"✅ Ваш запрос на вывод {amount} рублей одобрен!\n\n"
            f"💰 Сумма: {amount} рублей\n"
            f"💳 Способ: {method}\n"
            f"📝 Реквизиты: {details}\n\n"
            f"⏰ Средства будут переведены в ближайшее время.",
            context
        )

    elif data.startswith("admin_reject_withdrawal_"):
        request_id = int(data.replace("admin_reject_withdrawal_", ""))
        request = get_withdrawal_request(request_id)
        
        if not request:
            await query.answer("Запрос не найден", show_alert=True)
            return
        
        # Сохраняем ID запроса для ввода комментария
        context.user_data['reject_withdrawal_id'] = request_id
        context.user_data['waiting_for_reject_withdrawal_comment'] = True
        
        await query.message.reply_text(
            "📝 <b>Введите причину отклонения запроса на вывод:</b>",
            parse_mode="HTML"
        )
        await query.answer()

    elif data.startswith("admin_complete_withdrawal_"):
        request_id = int(data.replace("admin_complete_withdrawal_", ""))
        request = get_withdrawal_request(request_id)
        
        if not request:
            await query.answer("Запрос не найден", show_alert=True)
            return
        
        # Обновляем статус на "completed" (средства списываются автоматически в функции)
        update_withdrawal_status(request_id, "completed", "Выплата выполнена")
        
        w_id, user_id, amount, method, details, status, comment, created_at, *rest = request
        user_info = get_user_info(user_id)
        user_link = format_user_link(user_id, user_info[1], user_info[2], user_info[3])
        
        await query.edit_message_text(
            f"💸 <b>ВЫПЛАТА ВЫПОЛНЕНА</b>\n\n"
            f"🆔 <b>ID запроса:</b> {request_id}\n"
            f"👤 <b>Пользователь:</b> {user_link}\n"
            f"💰 <b>Сумма:</b> {amount} рублей\n"
            f"💳 <b>Способ:</b> {method}\n"
            f"📝 <b>Реквизиты:</b> {details}\n\n"
            f"✅ <b>Средства были списаны с баланса пользователя.</b>",
            parse_mode="HTML"
        )
        
        # Уведомляем пользователя
        await send_notification(
            user_id,
            f"💸 Вам выплачено {amount} рублей!\n\n"
            f"✅ Перевод выполнен по реквизитам: {details}\n"
            f"💰 Ваш текущий баланс: {get_user_balance(user_id)} рублей",
            context
        )

    elif data.startswith("admin_withdrawals_list_"):
        page = int(data.replace("admin_withdrawals_list_", ""))
        await admin_show_withdrawals(update, context, page)
    elif data.startswith("admin_withdrawals_page_"):
        page = int(data.replace("admin_withdrawals_page_", ""))
        await admin_show_withdrawals(update, context, page)
    
    else:
        # Если callback не распознан, пробуем обработчик помощи
        await handle_help_button_callback(update, context)
        
async def admin_show_withdrawals(update: Update, context: CallbackContext, page=0):
    """Показать список выплат для администратора"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой функции.")
        return
    
    update_user_activity(update.effective_user.id)
    
    # Получаем запросы на вывод
    requests = get_withdrawal_requests(status='pending', page=page)
    total_count = get_withdrawal_requests_count('pending')
    
    if not requests:
        message = (
            "💸 <b>Управление выплатами</b>\n\n"
            "⏳ <b>Нет ожидающих выплат.</b>\n\n"
            "Все запросы на вывод обработаны."
        )
        await safe_reply(update, context, message, parse_mode="HTML")
        return
    
    total_pages = (total_count + 9) // 10  # 10 на страницу
    
    message = f"💸 <b>Ожидающие выплаты (стр. {page+1}/{total_pages}):</b>\n\n"
    
    for req in requests:
        # Распаковываем данные запроса
        # Структура: wr.*, u.username, u.first_name, u.last_name, up.balance
        if len(req) >= 10:  # Проверяем, что есть все данные
            wr_id = req[0]  # id
            user_id = req[1]  # user_id
            amount = req[2]  # amount
            method = req[3]  # payment_method
            details = req[4]  # details
            status = req[5]  # status
            comment = req[6] if len(req) > 6 else None  # admin_comment
            created_at = req[7] if len(req) > 7 else None  # created_at
            
            # Информация о пользователе (если есть)
            username = req[8] if len(req) > 8 else None
            first_name = req[9] if len(req) > 9 else None
            last_name = req[10] if len(req) > 10 else None
            balance = req[11] if len(req) > 11 else 0
            
            # Форматируем информацию о пользователе
            user_link = format_user_link(user_id, username, first_name, last_name)
            
            # Сокращаем реквизиты для отображения
            short_details = details[:15] + "..." if len(details) > 15 else details
            
            # Методы оплаты
            method_names = {
                'card': '💳 Карта',
                'qiwi': '📱 Qiwi', 
                'yoomoney': '🧾 ЮMoney',
                'phone': '☎️ Телефон',
                'sber': '🏦 Сбербанк'
            }
            method_display = method_names.get(method, method)
            
            message += (
                f"🆔 <b>#{wr_id}</b>\n"
                f"👤 <b>Пользователь:</b> {user_link}\n"
                f"💰 <b>Сумма:</b> {amount} руб.\n"
                f"💳 <b>Способ:</b> {method_display}\n"
                f"📝 <b>Реквизиты:</b> {short_details}\n"
                f"📅 <b>Дата:</b> {created_at or 'не указана'}\n"
                f"💰 <b>Баланс:</b> {balance} руб.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )
        else:
            # Если данных меньше, выводим базовую информацию
            wr_id = req[0]
            user_id = req[1]
            amount = req[2]
            method = req[3]
            details = req[4]
            
            short_details = details[:15] + "..." if len(details) > 15 else details
            
            message += (
                f"🆔 #{wr_id} | 👤 ID: {user_id} | 💰 {amount} руб.\n"
                f"💳 {method}: {short_details}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )
    
    # Создаем клавиатуру пагинации
    keyboard = []
    if page > 0:
        keyboard.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_withdrawals_page_{page-1}"))
    if page < total_pages - 1:
        keyboard.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"admin_withdrawals_page_{page+1}"))
    
    # Кнопки для каждого запроса
    for req in requests:
        wr_id = req[0]
        user_id = req[1]
        amount = req[2]
        
        action_buttons = [
            InlineKeyboardButton(f"✅ Одобрить #{wr_id}", callback_data=f"admin_approve_withdrawal_{wr_id}"),
            InlineKeyboardButton(f"❌ Отклонить #{wr_id}", callback_data=f"admin_reject_withdrawal_{wr_id}")
        ]
        keyboard.append(action_buttons)
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

async def admin_withdrawals_stats(update: Update, context: CallbackContext):
    """Статистика выплат для администратора"""
    if update.effective_user.id != ADMIN_ID:
        await safe_reply(update, context, "❌ У вас нет доступа к этой функции.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM withdrawal_requests WHERE status = 'completed'")
    completed_data = cursor.fetchone()
    completed_count = completed_data[0] or 0
    completed_total = completed_data[1] or 0
    
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM withdrawal_requests WHERE status = 'pending'")
    pending_data = cursor.fetchone()
    pending_count = pending_data[0] or 0
    pending_total = pending_data[1] or 0
    
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM withdrawal_requests WHERE status = 'approved'")
    approved_data = cursor.fetchone()
    approved_count = approved_data[0] or 0
    approved_total = approved_data[1] or 0
    
    # Статистика по методам выплат
    cursor.execute('''
        SELECT payment_method, COUNT(*), SUM(amount) 
        FROM withdrawal_requests 
        WHERE status = 'completed'
        GROUP BY payment_method
    ''')
    method_stats = cursor.fetchall()
    
    conn.close()
    
    method_names = {
        'card': '💳 Карта',
        'qiwi': '📱 Qiwi', 
        'yoomoney': '🧾 ЮMoney',
        'phone': '☎️ Телефон',
        'sber': '🏦 Сбербанк'
    }
    
    message = "📊 <b>Статистика выплат</b>\n\n"
    message += f"✅ <b>Выполнено:</b> {completed_count} выплат на {completed_total} руб.\n"
    message += f"⏳ <b>Ожидает:</b> {pending_count} выплат на {pending_total} руб.\n"
    message += f"🔄 <b>Одобрено:</b> {approved_count} выплат на {approved_total} руб.\n\n"
    
    if method_stats:
        message += "<b>По способам выплат:</b>\n"
        for method, count, total in method_stats:
            method_name = method_names.get(method, method)
            message += f"  {method_name}: {count} выплат на {total} руб.\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад к выплатам", callback_data="admin_withdrawals_list_0")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)
        
async def show_withdrawal_history(update: Update, context: CallbackContext):
    """Показать историю выводов пользователя"""
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    history = get_user_withdrawal_history(user_id, limit=20)
    
    if not history:
        keyboard = [[KeyboardButton("💸 Вывести средства"), KeyboardButton("💰 Баланс")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await safe_reply(update, context,
            "📋 <b>У вас еще не было выводов средств</b>\n\n"
            "Для первого вывода нажмите '💸 Вывести средства'",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
    
    message = "📋 <b>История ваших выводов:</b>\n\n"
    
    for withdrawal in history:
        w_id, w_user_id, amount, method, details, status, comment, created_at, processed_at, completed_at = withdrawal[:10]
        
        status_icons = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌',
            'completed': '💸'
        }
        method_names = {
            'card': '💳 Карта',
            'qiwi': '📱 Qiwi', 
            'yoomoney': '🧾 ЮMoney',
            'phone': '☎️ Телефон',
            'sber': '🏦 Сбербанк'
        }
        
        icon = status_icons.get(status, '❓')
        method_name = method_names.get(method, method)
        date_str = created_at.split()[0] if created_at else ''
        
        message += f"{icon} <b>#{w_id}</b> | {date_str}\n"
        message += f"💰 {amount} руб. | {method_name}\n"
        
        # Сокращаем длинные реквизиты
        if len(details) > 15:
            short_details = details[:8] + "..." + details[-4:]
        else:
            short_details = details
            
        message += f"📝 {short_details}\n"
        message += f"📊 Статус: {status}\n"
        
        if comment:
            message += f"💬 Комментарий: {comment}\n"
        
        message += "\n"
    
    keyboard = [
        [KeyboardButton("💸 Вывести средства"), KeyboardButton("💰 Баланс")],
        [KeyboardButton("Меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

async def show_my_payment_methods(update: Update, context: CallbackContext):
    """Показать сохраненные реквизиты пользователя"""
    user_id = update.effective_user.id
    update_user_activity(user_id)
    
    methods = get_user_payment_methods(user_id)
    
    if not methods:
        keyboard = [
            [KeyboardButton("💸 Вывести средства")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("📋 История выводов")],
            [KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        message = (
            "💳 <b>У вас нет сохраненных реквизитов</b>\n\n"
            "Реквизиты будут автоматически сохраняться "
            "при первом выводе средств.\n\n"
            "💡 <i>Нажмите '💸 Вывести средства', чтобы добавить реквизиты</i>"
        )
    else:
        keyboard = [
            [KeyboardButton("💸 Вывести средства"), KeyboardButton("📋 История выводов")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("Меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        message = "💳 <b>Ваши сохраненные реквизиты:</b>\n\n"
        
        if methods[2]:  # card_number
            # Форматируем номер карты: 1234 **** **** 5678
            card = methods[2]
            if len(card) >= 16:
                formatted_card = f"{card[:4]} **** **** {card[-4:]}"
            else:
                formatted_card = card
            message += f"💳 <b>Банковская карта:</b> {formatted_card}\n"
            
        if methods[3]:  # qiwi_wallet
            message += f"📱 <b>QIWI кошелек:</b> {methods[3]}\n"
            
        if methods[4]:  # yoomoney_wallet
            message += f"🧾 <b>ЮMoney кошелек:</b> {methods[4]}\n"
            
        if methods[5]:  # phone_number
            message += f"☎️ <b>Телефон:</b> {methods[5]}\n"
            
        if methods[6]:  # sber_account
            message += f"🏦 <b>Сбербанк:</b> {methods[6]}\n"
        
        message += "\n💡 <i>Реквизиты обновляются автоматически при каждом выводе</i>\n"
        message += "📝 <i>Для изменения реквизитов просто введите новые при следующем выводе</i>"
    
    await safe_reply(update, context, message, parse_mode="HTML", reply_markup=reply_markup)

# Показ текущего утреннего сообщения
async def show_current_morning(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return

    message_text_value, send_time_value, video_id = get_morning_message()
    recipients = len(get_users_waiting_for_morning())  # <-- считаем именно тех, кто ждёт утренку

    title = "🌅 <b>Текущее утреннее сообщение:</b>"
    message_text = (
        f"{title}\n\n"
        f"{message_text_value}\n\n"
        f"⏰ <b>Время отправки:</b> {send_time_value}\n"
        f"👥 <b>Получателей:</b> {recipients}\n"
    )

    if video_id:
        try:
            await safe_send_video_or_text(update, context, video_id=video_id, caption_text=message_text, parse_mode="HTML")
        except Exception:
            message_text += "\n📹 <b>Видео прикреплено (не удалось отобразить превью)</b>"
            await safe_reply(update, context, message_text, parse_mode="HTML")
    else:
        await safe_reply(update, context, message_text, parse_mode="HTML")

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить", callback_data=EDIT_MORNING)],
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data=SEND_MORNING_NOW)],
        [InlineKeyboardButton("🔙 Назад", callback_data=BACK_TO_EDITOR)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_reply(update, context, "Выберите действие:", reply_markup=reply_markup)


# Показ текущего вечернего сообщения
async def show_current_evening(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return

    message_text_value, send_time_value, video_id = get_evening_reminder()
    recipients = len(get_users_waiting_for_evening())  # <-- считаем именно тех, кто ждёт вечерку

    title = "🌙 <b>Текущее вечернее напоминание:</b>"
    message_text = (
        f"{title}\n\n"
        f"{message_text_value}\n\n"
        f"⏰ <b>Время отправки:</b> {send_time_value}\n"
        f"👥 <b>Получателей:</b> {recipients}\n"
    )

    if video_id:
        try:
            await safe_send_video_or_text(update, context, video_id=video_id, caption_text=message_text, parse_mode="HTML")
        except Exception:
            message_text += "\n📹 <b>Видео прикреплено (не удалось отобразить превью)</b>"
            await safe_reply(update, context, message_text, parse_mode="HTML")
    else:
        await safe_reply(update, context, message_text, parse_mode="HTML")

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить", callback_data=EDIT_EVENING)],
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data=SEND_EVENING_NOW)],
        [InlineKeyboardButton("🔙 Назад", callback_data=BACK_TO_EDITOR)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_reply(update, context, "Выберите действие:", reply_markup=reply_markup)

async def handle_message(update: Update, context: CallbackContext):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    update_user_activity(user.id)
    
    text = update.message.text

    # Добавляем обработку команды "Пропустить" для видео
    if context.user_data.get('waiting_for_morning_video') and text.lower() == 'пропустить':
        context.user_data['waiting_for_morning_video'] = False
        await safe_reply(update, context, "✅ Видео для утреннего сообщения не будет прикреплено.")
        return

    if context.user_data.get('waiting_for_evening_video') and text.lower() == 'пропустить':
        context.user_data['waiting_for_evening_video'] = False
        await safe_reply(update, context, "✅ Видео для вечернего напоминания не будет прикреплено.")
        return
    # 1. Обработка команды /cancel в самом начале
    if text == '/cancel':
        # Сбрасываем все состояния ожидания
        states_to_clear = [
            'waiting_for_morning_video', 'waiting_for_evening_video',
            'waiting_for_new_category', 'waiting_for_edit_category_id',
            'waiting_for_edit_category_data', 'waiting_for_delete_category',
            'waiting_for_assign_category', 'editing_button_id',
            'editing_button_title', 'waiting_for_morning_message',
            'waiting_for_evening_reminder', 'waiting_for_reset_user_id',
            'waiting_for_broadcast', 'waiting_for_reject_comment',
            'temp_photo_id', 'waiting_for_withdrawal_details',
            'waiting_for_reject_withdrawal_comment', 'waiting_for_withdrawal_amount'
        ]
        
        for state in states_to_clear:
            if state in context.user_data:
                del context.user_data[state]
        
        await safe_reply(update, context,
            "✅ <b>Операция отменена.</b>",
            parse_mode="HTML"
        )
        return
        
    if text == "🔙 Назад":
        # Проверяем, из какого меню мы пришли
        if context.user_data.get('waiting_for_withdrawal_details') or context.user_data.get('waiting_for_withdrawal_amount'):
            # Если мы в процессе ввода реквизитов/суммы - возвращаемся к выбору метода выплаты
            for key in ['waiting_for_withdrawal_details', 'waiting_for_withdrawal_amount', 
                       'withdrawal_method', 'withdrawal_method_name', 'withdrawal_details', 'withdrawal_amount']:
                context.user_data.pop(key, None)
            
            await show_withdrawal_menu(update, context)
            return
        else:
            # Иначе возвращаемся к меню баланса
            await show_balance(update, context)
            return

    if context.user_data.get('waiting_for_withdrawal_details'):
        await handle_withdrawal_details(update, context)
        return

    if context.user_data.get('waiting_for_withdrawal_amount'):
        await handle_withdrawal_amount(update, context)
        return

    if context.user_data.get('waiting_for_reject_withdrawal_comment'):
        request_id = context.user_data['reject_withdrawal_id']
        comment = update.message.text
        
        # Обновляем статус запроса
        update_withdrawal_status(request_id, 'rejected', comment)
        
        # Получаем информацию о запросе
        request = get_withdrawal_request(request_id)
        if request:
            w_id, user_id, amount, method, details, status, old_comment, created_at, *rest = request
            
            # Уведомляем пользователя
            await send_notification(
                user_id,
                f"❌ Ваш запрос на вывод {amount} рублей отклонен\n\n"
                f"💰 Сумма: {amount} рублей\n"
                f"📝 Причина: {comment}\n\n"
                f"Средства остались на вашем балансе.",
                context
            )
        
        # Уведомляем администратора
        await safe_reply(update, context,
            f"✅ <b>Запрос #{request_id} отклонен</b>\n\n"
            f"📝 Причина: {comment}",
            parse_mode="HTML"
        )
        
        # Очищаем данные
        context.user_data.pop('reject_withdrawal_id', None)
        context.user_data.pop('waiting_for_reject_withdrawal_comment', None)
        return
    # ★★★ ОБРАБОТКА ВЫБОРА РЕКВИЗИТОВ ★★★
    if context.user_data.get('waiting_for_details_choice'):
        await handle_details_choice(update, context)
        return

    # Затем обработка других состояний ожидания
    if context.user_data.get('waiting_for_withdrawal_details'):
        await handle_withdrawal_details(update, context)
        return

    if context.user_data.get('waiting_for_withdrawal_amount'):
        await handle_withdrawal_amount(update, context)
        return
    
    # ★★★ ОБРАБОТКА РЕДАКТИРОВАНИЯ ИНФОРМАЦИОННЫХ КНОПОК ★★★
    if 'editing_button_id' in context.user_data:
        button_id = context.user_data['editing_button_id']
        old_title = context.user_data['editing_button_title']
        
        # Обновляем содержание кнопки
        update_info_button(button_id, old_title, text)
        
        # Очищаем данные
        del context.user_data['editing_button_id']
        del context.user_data['editing_button_title']
        
        await safe_reply(update, context, 
            f"✅ <b>Кнопка '{old_title}' успешно обновлена!</b>",
            parse_mode="HTML"
        )
        return
    
    if context.user_data.get('waiting_for_reset_user_id'):
        await handle_reset_user_id(update, context)
        return
    
    if context.user_data.get('waiting_for_accounts_count'):
        await handle_accounts_count(update, context)
        return
    
    if context.user_data.get('waiting_for_broadcast'):
        await handle_broadcast(update, context)
        return
    
    if context.user_data.get('waiting_for_morning_message'):
        await handle_morning_message_input(update, context)
        return
    
    if context.user_data.get('waiting_for_evening_reminder'):
        await handle_evening_reminder_input(update, context)
        return
    
    if context.user_data.get('waiting_for_reject_comment'):
        await handle_reject_comment(update, context)
        return
    if context.user_data.get('waiting_for_new_category'):
        await handle_category_input(update, context)
        return
        
    if context.user_data.get('waiting_for_edit_category_id'):
        await handle_edit_category_id_input(update, context)
        return
        
    if context.user_data.get('waiting_for_edit_category_data'):
        await handle_edit_category_data_input(update, context)
        return
        
    if context.user_data.get('waiting_for_delete_category'):
        await handle_delete_category_input(update, context)
        return
        
    if context.user_data.get('waiting_for_assign_category'):
        await handle_assign_category_input(update, context)
        return
    
    if text == "Меню" or text == "🔙 Главное меню":
        await show_main_menu(update, context)
    elif text == "🔧 Админ-панель" and update.effective_user.id == ADMIN_ID:
        await admin_panel(update, context)
    elif text == "📝 Редактор" and update.effective_user.id == ADMIN_ID:
        await editor_panel(update, context)
    elif text == "🔙 Назад в админ-панель" and update.effective_user.id == ADMIN_ID:
        await admin_panel(update, context)
    elif text == "📊 Статистика":
        if update.effective_user.id == ADMIN_ID:
            await show_stats(update, context)
        else:
            await safe_reply(update, context, "У вас нет доступа к этой функции.")
    elif text == "👥 Список пользователей" and update.effective_user.id == ADMIN_ID:
        await show_all_users(update, context)
    elif text == "✅ Подтвердившие" and update.effective_user.id == ADMIN_ID:
        await show_called_users(update, context)
    elif text == "📸 Приславшие скриншот" and update.effective_user.id == ADMIN_ID:
        await show_screenshot_users(update, context)
    elif text == "📋 Скриншоты на проверке" and update.effective_user.id == ADMIN_ID:
        await show_pending_screenshots(update, context)
    elif text == "🖼️ Список фото" and update.effective_user.id == ADMIN_ID:
        await show_all_photos(update, context)
    elif text == "🖼️ Добавить фото" and update.effective_user.id == ADMIN_ID:
        await safe_reply(update, context, 
            "📸 <b>Отправьте фото, которое нужно добавить в базу заданий.</b>",
            parse_mode="HTML"
        )
    elif text == "🌅 Утреннее сообщение" and update.effective_user.id == ADMIN_ID:
        await show_current_morning(update, context)
    elif text == "🌙 Вечернее напоминание" and update.effective_user.id == ADMIN_ID:
        await show_current_evening(update, context)
    elif text == "📁 Управление категориями" and update.effective_user.id == ADMIN_ID:
        await manage_categories(update, context)        
    elif text == "➕ Добавить категорию" and update.effective_user.id == ADMIN_ID:
        await add_category_handler(update, context)       
    elif text == "✏️ Редактировать категорию" and update.effective_user.id == ADMIN_ID:
        await edit_category_handler(update, context)       
    elif text == "🗑️ Удалить категорию" and update.effective_user.id == ADMIN_ID:
        await delete_category_handler(update, context)       
    elif text == "📊 Статистика по категориям" and update.effective_user.id == ADMIN_ID:
        await category_stats_handler(update, context)      
    elif text == "🖼️ Назначить категорию фото" and update.effective_user.id == ADMIN_ID:
        await assign_category_to_photo_handler(update, context)
     
    elif text == "🔙 Назад в редактор" and update.effective_user.id == ADMIN_ID:
        await editor_panel(update, context)
    elif text == "📝 Информационные кнопки" and update.effective_user.id == ADMIN_ID:
        await edit_info_buttons(update, context)
    
    elif text == "ℹ️ Информация":
        await show_info(update, context)
    elif text == "🔄 Сбросить задание" and update.effective_user.id == ADMIN_ID:
        await reset_user_task_handler(update, context)
    elif text == "📢 Рассылка" and update.effective_user.id == ADMIN_ID:
        await start_broadcast(update, context)
    elif text == "💸 Выплаты" and update.effective_user.id == ADMIN_ID:
        await admin_show_withdrawals(update, context)
    elif text == "Мой профиль":
        await show_profile(update, context)
    elif text == "💰 Баланс":
        await show_balance(update, context)
    elif text == "🔙 Назад к балансу":
        await show_balance(update, context)
    elif text == "🔔 Уведомления":
        await show_notifications(update, context)
    elif text == "💎 Реферальная система":
        await show_referral_system(update, context)
    elif text == "📞 Поддержка":
        await safe_reply(update, context, 
            f"🆘 <b>Помощь и поддержка</b>\n\n"
            f"📞 <b>Администратор:</b> @denvr11\n\n"
            f"⏰ <b>Время работы поддержки:</b> круглосуточно\n\n"
            f"<blockquote>Часто задаваемые вопросы:\n\n"
            f"1. Могут ли мой аккаунт заблокировать?\n"
            f"- нет, процедура написания отзывов не противоречит правилам Авито, даже если вы их пишите по заданию - максимум могут отклонить отзыв в публикации\n\n"
            f"2. Когда получить выплату?\n"
            f"- выплаты производятся на следующий день, как вы написали отзыв (расчет и проверка после 22:00 МСК) либо через администратора.\n\n"
            f"3. Могу ли я не звонить или молчать в трубку?\n"
            f"- инструкцией предусмотрен звонок с составлением диалога на 1:30 минуты и более для того, что бы отзыв прошел модерацию, и Вам оплатили Ваше задание.\n\n"
            f"4. Как узнать сколько мне оплатят за задание? \n"
            f"- оплата указывается в инструкции к заданию.\n\n"
            f"5. Где гарантия оплаты?\n"
            f"- за любыми доказательствами можеет обратиться к администратору, либо выполнить задание через сайт-гарант. </blockquote>\n\n"
            f"Если не нашли ответа на свой вопрос, свяжитесь с администратором напрямую\n",
            parse_mode="HTML"
        )
    
    elif text == "Мое задание":
        user_id = update.effective_user.id
        user_step = get_user_step(user_id)
        
        # Получаем информацию о текущем задании
        task_info = get_user_task(user_id)
        
        if not task_info:
            await safe_reply(update, context, 
                "❌ <b>У вас нет активного задания.</b>\n\n"
                "Нажмите 'Получить задание' чтобы начать работу.",
                parse_mode="HTML"
            )
            return
        
        # ★★★ ИСПРАВЛЕННАЯ ЛОГИКА ★★★
        # Если пользователь еще не подтвердил звонок - показываем задание полностью
        if user_step == TASK_STATUS["CONFIRM_CALL"]:
            photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
            instruction = get_instruction()
            
            # ★★★ ДОБАВЛЯЕМ КНОПКУ ЗАМЕНЫ В КЛАВИАТУРУ ★★★
            replacement_count = get_replacement_count(user_id)
            
            keyboard = []
            if replacement_count < 2:
                keyboard.append([KeyboardButton("🔄 Заменить задание")])
                
            keyboard.extend([
                [KeyboardButton("✅ Готово"), KeyboardButton("🆘 Помощь в задании")],
                [KeyboardButton("💰 Баланс"), KeyboardButton("ℹ️ Информация")],
                [KeyboardButton("📞 Поддержка"), KeyboardButton("Меню")]
            ])
            
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            # Отправляем фото с инструкцией И КЛАВИАТУРОЙ СРАЗУ
            try:
                await update.message.reply_photo(
                    photo=photo_file_id,
                    caption=f"📝 <b>Ваше задание:</b>\n\n{instruction}\n\n"
                           f"✅ <b>После выполнения нажмите 'Готово'</b>",
                    parse_mode="HTML",
                    reply_markup=reply_markup  # ★★★ КЛАВИАТУРА ПРИКРЕПЛЯЕТСЯ СРАЗУ К ФОТО ★★★
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке фото: {e}")
                await safe_reply(update, context, 
                    f"📝 <b>Ваше задание:</b>\n\n{instruction}\n\n"
                    f"✅ <b>После выполнения нажмите 'Готово'</b>",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
        else:
            # Для других статусов используем стандартный интерфейс
            await show_enhanced_task_interface(update, context, user_id, task_info)

    elif text == "🔄 Заменить задание":
        user_id = update.effective_user.id
        user_step = get_user_step(user_id)
        
        # Проверяем, что пользователь на этапе подтверждения звонка
        if user_step != TASK_STATUS["CONFIRM_CALL"]:
            await safe_reply(update, context, 
                "❌ <b>Задание можно заменять только на этапе подтверждения звонка.</b>",
                parse_mode="HTML"
            )
            return
        
        # Проверяем лимит замен
        replacement_count = get_replacement_count(user_id)
        if replacement_count >= 2:
            await safe_reply(update, context, 
                "❌ <b>Вы исчерпали лимит замен (2 раза). Обратитесь к администратору.</b>",
                parse_mode="HTML"
            )
            return
        
        # Получаем текущее задание пользователя
        task_info = get_user_task(user_id)
        if not task_info:
            await safe_reply(update, context, 
                "❌ <b>У вас нет активного задания для замены.</b>",
                parse_mode="HTML"
            )
            return
        
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
        
        # 1. Добавляем замененное задание в выполненные
        add_completed_task(user_id, photo_id)
        
        # 2. Получаем категорию текущего задания для исключения
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT category_id FROM photos WHERE id = ?", (photo_id,))
        result = cursor.fetchone()
        exclude_category_id = result[0] if result else None
        
        # Получаем информацию о текущей категории
        cursor.execute('''
            SELECT c.name 
            FROM photos p 
            LEFT JOIN task_categories c ON p.category_id = c.id 
            WHERE p.id = ?
        ''', (photo_id,))
        current_category_result = cursor.fetchone()
        current_category_name = current_category_result[0] if current_category_result else "Без категории"
        conn.close()
        
        # 3. Получаем доступные фото из ДРУГИХ категорий
        available_photos = get_available_photos_from_other_categories(user_id, exclude_category_id)
        
        if not available_photos:
            await safe_reply(update, context, 
                "❌ <b>Нет доступных заданий в других категориях.</b>\n\n"
                f"Текущая категория: {current_category_name}\n"
                "Обратитесь к администратору.",
                parse_mode="HTML"
            )
            return
        
        new_photo = available_photos[0]
        new_photo_id = new_photo[0]
        new_photo_file_id = new_photo[1]
        
        # 4. Получаем информацию о новой категории
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.name 
            FROM photos p 
            LEFT JOIN task_categories c ON p.category_id = c.id 
            WHERE p.id = ?
        ''', (new_photo_id,))
        new_category_result = cursor.fetchone()
        new_category_name = new_category_result[0] if new_category_result else "Без категории"
        conn.close()
        
        # 5. Обновляем задание в базе данных
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE user_progress SET photo_id = ?, replacement_count = replacement_count + 1 WHERE user_id = ?",
            (new_photo_id, user_id)
        )
        conn.commit()
        conn.close()
        
        instruction = get_instruction()
        
        # Отправляем новое задание
        try:
            await update.message.reply_photo(
                photo=new_photo_file_id,
                caption=f"🔄 <b>Задание заменено!</b>\n\n"
                       f"📝 <b>Новое задание:</b>\n\n{instruction}\n\n"
                       f"📁 <b>Категория:</b> {new_category_name}\n\n"
                       f"⚠️ <b>Старое задание добавлено в выполненные и больше не будет показываться.</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
            await safe_reply(update, context, 
                f"🔄 <b>Задание заменено!</b>\n\n"
                f"📝 <b>Новое задание:</b>\n\n{instruction}\n\n"
                f"📁 <b>Категория:</b> {new_category_name}\n\n"
                f"⚠️ <b>Старое задание добавлено в выполненные и больше не будет показываться.</b>",
                parse_mode="HTML"
            )

    elif text == "📋 Показать задание":
        user_id = update.effective_user.id
        task_info = get_user_task(user_id)
        
        if not task_info:
            await safe_reply(update, context, 
                "❌ <b>У вас нет активного задания.</b>",
                parse_mode="HTML"
            )
            return
        
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
        instruction = get_instruction()
        
        try:
            await update.message.reply_photo(
                photo=photo_file_id,
                caption=f"📝 <b>Ваше задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
            await safe_reply(update, context, 
                f"📝 <b>Ваше задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )

    elif text == "📋 Показать задание":
        user_id = update.effective_user.id
        task_info = get_user_task(user_id)
        
        if not task_info:
            await safe_reply(update, context, 
                "❌ <b>У вас нет активного задания.</b>",
                parse_mode="HTML"
            )
            return
        
        photo_id, photo_file_id, assigned_at, called, called_confirmed, screenshot_sent, current_step, accounts_requested, photos_sent = task_info
        instruction = get_instruction()
        
        try:
            await update.message.reply_photo(
                photo=photo_file_id,
                caption=f"📝 <b>Ваше задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
            await safe_reply(update, context, 
                f"📝 <b>Ваше задание:</b>\n\n{instruction}",
                parse_mode="HTML"
            )
    
    elif text == "🆘 Помощь в задании":
        user_step = get_user_step(user.id)
        allowed_steps = [
            TASK_STATUS["CONFIRM_CALL"], 
            TASK_STATUS["WAITING_REVIEW_DAY"], 
            TASK_STATUS["WAITING_REVIEW_EVENING"], 
            TASK_STATUS["SEND_SCREENSHOT"], 
            TASK_STATUS["WAITING_ADMIN_REVIEW"], 
            TASK_STATUS["SCREENSHOT_REJECTED"]
        ]
        if user_step in allowed_steps:
            await handle_task_help(update, context)
        else:
            await safe_reply(update, context, 
                "❌ <b>Сначала получите задание!</b>",
                parse_mode="HTML"
            )
    elif text == "Получить задание":
        await handle_get_task(update, context)
    elif text == "💸 Вывести средства":
        await show_withdrawal_menu(update, context)
    elif text == "📋 История выводов":
        await show_withdrawal_history(update, context)
    elif text == "💳 Мои реквизиты":
        await show_my_payment_methods(update, context)
    elif text in ["💳 Банковская карта", "📱 Qiwi", "🧾 ЮMoney", "☎️ Баланс телефона", "🏦 Сбербанк Онлайн"]:
        await handle_withdrawal_method(update, context)
    elif text == "🔙 Назад к выбору метода":
        await show_withdrawal_menu(update, context)
    elif text == "✅ Использовать:" or text.startswith("✅ Использовать:"):
        pass
    elif text == "📝 Ввести новые реквизиты":
        pass
    elif text == "✅ Готово":
        user_step = get_user_step(user.id)
        if user_step == TASK_STATUS["CONFIRM_CALL"]:
            await handle_ready(update, context)
        else:
            await safe_reply(update, context, 
                "❌ <b>Сначала получите задание!</b>",
                parse_mode="HTML"
            )
    elif text == "📸 Прислать скриншот":
        await safe_reply(update, context, 
            "📸 <b>Пришлите скриншот раздела 'Мои отзывы' на Авито.</b>",
            parse_mode="HTML"
        )
    elif text == "📞 Связаться с админом":
        await safe_reply(update, context, 
            f"📞 <b>Вы можете связаться с администратором по ссылке:</b> {ADMIN_USERNAME}\n\n"
            "💬 <b>Опишите вашу проблему или вопрос, и администратор ответит вам в ближайшее время.</b>",
            parse_mode="HTML"
        )
    else:
        await safe_reply(update, context, 
            "❌ <b>Я не понимаю эту команду.</b>\n\n"
            "📋 <b>Используйте кнопки меню для навигации.</b>",
            parse_mode="HTML"
        )

# Обработчик команды /pay для выплат
async def pay_command(update: Update, context: CallbackContext):
    await handle_payout(update, context)

# Обработчик команды /viewscreenshot для просмотра скриншота
async def view_screenshot_command(update: Update, context: CallbackContext):
    await view_screenshot(update, context)

# Обработчик ошибок
async def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                update.effective_user.id,
                "❌ <b>Произошла ошибка. Попробуйте позже или обратитесь к администратору.</b>",
                parse_mode="HTML"
            )
        except:
            pass

# Функция для планировщика задач
async def scheduler(context: CallbackContext):
    # Получаем текущее время
    now = datetime.now().strftime("%H:%M")
    
    # Проверяем, не наступило ли время для отправки утренних сообщений
    morning_message, morning_time, _ = get_morning_message()
    if now == morning_time:
        await send_morning_messages(context)
    
    # Проверяем, не наступило ли время для отправки вечерних напоминаний
    evening_reminder, evening_time, _ = get_evening_reminder()
    if now == evening_time:
        await send_evening_reminders(context)

# Основная функция
def main():
    try:
        init_db()
        optimize_database()
    except sqlite3.OperationalError as e:
        logger.error(f"Ошибка инициализации базы: {e}. Пытаюсь исправить...")
        if fix_database():
            logger.info("База данных исправлена, продолжаем запуск...")
        else:
            logger.error("Не удалось исправить базу данных!")
            return
    
    application = Application.builder().token(TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pay", pay_command))
    application.add_handler(CommandHandler("viewscreenshot", view_screenshot_command))
    application.add_handler(CommandHandler("vs", view_screenshot))
    application.add_handler(CommandHandler("skip", handle_skip_command))
    application.add_handler(CommandHandler("reset_all", reset_all_tasks_command))
    application.add_handler(CommandHandler("find", find_user_command))
    application.add_handler(CommandHandler("dell", delete_user_command))
    application.add_handler(CommandHandler("force_reset_all", force_reset_all_tasks_command)) 
    application.add_handler(CommandHandler("cancel", handle_cancel_command))
    application.add_handler(CommandHandler("status", withdrawal_status_command))
    application.add_handler(CommandHandler("setbalance", set_balance_command))
    application.add_handler(CommandHandler("ahelp", admin_help_command))
    application.add_handler(CommandHandler("deleteallphotos", delete_all_photos_command))
    application.add_handler(CommandHandler("clean_db", clean_database_command))
    
    # Фото
    application.add_handler(MessageHandler(filters.PHOTO & filters.User(user_id=ADMIN_ID), add_photo_handler))
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.User(user_id=ADMIN_ID), handle_screenshot))
    
    # Видео
    application.add_handler(MessageHandler(filters.VIDEO, handle_video_input))
    
    # Текст
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Кнопки
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    application.add_handler(CallbackQueryHandler(handle_button_callback, pattern="^delete_photo_"))
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern="^select_category_"))
    application.add_handler(CallbackQueryHandler(handle_change_category, pattern="^change_category_"))
    
    # Планировщик
    job_queue = application.job_queue
    job_queue.run_repeating(scheduler, interval=60, first=10)  # Проверяем каждую минуту
    #job_queue.run_repeating(check_new_withdrawals, interval=3600, first=60)  # Каждый час
    
    application.add_error_handler(error_handler)
    
    logger.info("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()