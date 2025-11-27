import logging
import sqlite3
import asyncio
import aiohttp
import random
import time
import signal
import sys
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, RetryAfter, TimedOut, BadRequest, Conflict
import traceback
import json
from datetime import datetime, timedelta

BOT_TOKEN = "7719879882:BBGghPoR1KbKeekPM9lNG3wS2FIeIEX8elc"
ADMIN_ID = 48583093
SUPPORT_USERNAME = "@why_seven"
CRYPTO_BOT_TOKEN = "493176:AAkrR1xC8Gn3FIZlBFX9skRupboBx2BXqhe"
CHANNEL_ID = "-1003290615927"
CHANNEL_LINK = "https://t.me/+4bnOPVF2idA0ZTA1"

# Настройка расширенного логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]',
    handlers=[
        logging.FileHandler('bot_errors.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Дополнительный логгер для ошибок
error_logger = logging.getLogger('error_logger')
error_handler = logging.FileHandler('critical_errors.log', encoding='utf-8')
error_handler.setFormatter(logging.Formatter('%(asctime)s - CRITICAL - %(message)s'))
error_logger.addHandler(error_handler)
error_logger.setLevel(logging.ERROR)

def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    logger.info(f"Получен сигнал {signum}. Завершение работы...")
    sys.exit(0)

class ErrorHandler:
    """Класс для обработки и логирования ошибок"""
    
    @staticmethod
    def log_error(error_type: str, error: Exception, user_id: int = None, additional_info: str = None):
        """Логирование ошибок с дополнительной информацией"""
        error_data = {
            'timestamp': datetime.now().isoformat(),
            'error_type': error_type,
            'error_message': str(error),
            'user_id': user_id,
            'additional_info': additional_info,
            'traceback': traceback.format_exc()
        }
        
        logger.error(f"{error_type}: {error} | User: {user_id} | Info: {additional_info}")
        error_logger.error(json.dumps(error_data, ensure_ascii=False))
        
        # Логируем полный traceback для отладки
        logger.debug(f"Full traceback:\n{traceback.format_exc()}")

    @staticmethod
    async def notify_admin(bot, error: Exception, context: str = ""):
        """Уведомление администратора о критических ошибках"""
        try:
            error_msg = f"🚨 КРИТИЧЕСКАЯ ОШИБКА\n\nКонтекст: {context}\nОшибка: {str(error)[:200]}"
            await bot.send_message(ADMIN_ID, error_msg)
        except Exception as e:
            logger.error(f"Не удалось уведомить администратора: {e}")

class DatabaseErrorHandler:
    """Обработчик ошибок базы данных"""
    
    @staticmethod
    def handle_db_error(error: Exception, operation: str):
        """Обработка ошибок БД с повторными попытками"""
        ErrorHandler.log_error("DATABASE_ERROR", error, additional_info=f"Operation: {operation}")
        
        if isinstance(error, sqlite3.OperationalError):
            if "database is locked" in str(error):
                logger.warning("База данных заблокирована, повторная попытка...")
                time.sleep(0.1)
                return True  # Повторить операцию
            elif "no such table" in str(error):
                logger.error("Отсутствует таблица в БД")
                return False
        return False

class CryptoBotAPI:
    def __init__(self, token):
        self.base_url = 'https://pay.crypt.bot/api/'
        self.headers = {'Crypto-Pay-API-Token': token}
        self.session = None
        self.retry_count = 3
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(self, method: str, endpoint: str, **kwargs):
        """Универсальный метод для запросов с обработкой ошибок"""
        for attempt in range(self.retry_count):
            try:
                if not self.session:
                    self.session = aiohttp.ClientSession(timeout=self.timeout)
                
                async with self.session.request(method, f'{self.base_url}{endpoint}', **kwargs) as response:
                    if response.status == 429:  # Too Many Requests
                        wait_time = int(response.headers.get('Retry-After', 10))
                        logger.warning(f"Rate limit, waiting {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                    data = await response.json()
                    return data
                    
            except aiohttp.ClientConnectorError as e:
                ErrorHandler.log_error("NETWORK_ERROR", e, additional_info=f"Attempt {attempt + 1}")
                if attempt == self.retry_count - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                
            except aiohttp.ServerTimeoutError as e:
                ErrorHandler.log_error("TIMEOUT_ERROR", e, additional_info=f"Attempt {attempt + 1}")
                if attempt == self.retry_count - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
                
            except aiohttp.ClientError as e:
                ErrorHandler.log_error("HTTP_ERROR", e, additional_info=f"Attempt {attempt + 1}")
                if attempt == self.retry_count - 1:
                    raise
                await asyncio.sleep(1)
                
            except Exception as e:
                ErrorHandler.log_error("CRYPTOBOT_ERROR", e, additional_info=f"Attempt {attempt + 1}")
                if attempt == self.retry_count - 1:
                    raise
                await asyncio.sleep(1)
        
        return None

    async def create_invoice(self, amount: float, description: str = "Покупка в VIXEN_LOGS"):
        """Создание инвойса с улучшенной обработкой ошибок"""
        try:
            params = {
                'asset': 'USDT',
                'amount': str(amount),
                'description': description
            }
            
            data = await self._make_request('POST', 'createInvoice', json=params, headers=self.headers)
            
            if data and data.get('ok'):
                return data['result']
            else:
                error_msg = data.get('error', {}).get('name', 'Unknown error') if data else 'No response'
                ErrorHandler.log_error("INVOICE_CREATION_ERROR", Exception(error_msg), 
                                    additional_info=f"Amount: {amount}")
                return None
                
        except Exception as e:
            ErrorHandler.log_error("INVOICE_CREATION_CRITICAL", e, 
                                additional_info=f"Amount: {amount}")
            return None

    async def check_invoice(self, invoice_id: int):
        """Проверка статуса инвойса с обработкой ошибок"""
        try:
            params = {'invoice_ids': str(invoice_id)}
            data = await self._make_request('GET', 'getInvoices', params=params, headers=self.headers)
            
            if data and data.get('ok') and data['result']['items']:
                return data['result']['items'][0]['status']
            return None
            
        except Exception as e:
            ErrorHandler.log_error("INVOICE_CHECK_ERROR", e, 
                                additional_info=f"Invoice ID: {invoice_id}")
            return None

def create_necessary_directories():
    """Создание необходимых директорий"""
    directories = ['logs', 'soft', 'accounts']
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Создана директория: {directory}")

def init_db():
    """Инициализация БД с обработкой ошибок"""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect('vixen_logs.db', check_same_thread=False, timeout=20)
            cursor = conn.cursor()
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0, 
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP, subscribed BOOLEAN DEFAULT FALSE)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, 
                invoice_id INTEGER, status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
                content_delivered BOOLEAN DEFAULT FALSE, quantity INTEGER DEFAULT 1)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS used_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, log_content TEXT UNIQUE, log_type TEXT, 
                used_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS balance_invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, invoice_id INTEGER, 
                amount REAL, status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            
            # Таблица для логирования ошибок
            cursor.execute('''CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER, error_type TEXT, error_message TEXT, traceback TEXT,
                additional_info TEXT)''')
            
            # Новые таблицы для управления пользователями
            cursor.execute('''CREATE TABLE IF NOT EXISTS user_discounts (
                user_id INTEGER PRIMARY KEY, discount_percent REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id))''')
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY, banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reason TEXT, FOREIGN KEY (user_id) REFERENCES users (user_id))''')
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS user_actions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, admin_id INTEGER,
                action_type TEXT, action_details TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            
            # Таблицы для рулетки
            cursor.execute('''CREATE TABLE IF NOT EXISTS daily_roulette (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER, 
                discount_won REAL,
                spin_date DATE DEFAULT CURRENT_DATE,
                expires_at DATETIME DEFAULT (datetime('now', '+1 day')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, spin_date)
            )''')
            
            # Таблица для хранения добавленных товаров
            cursor.execute('''CREATE TABLE IF NOT EXISTS custom_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_type TEXT UNIQUE,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                category TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Таблица для хранения цен базовых товаров
            cursor.execute('''CREATE TABLE IF NOT EXISTS base_products_prices (
                product_type TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                category TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            conn.commit()
            conn.close()
            
            # СОЗДАЕМ НЕОБХОДИМЫЕ ДИРЕКТОРИИ
            create_necessary_directories()
            
            logger.info("База данных успешно инициализирована")
            return True
            
        except sqlite3.Error as e:
            ErrorHandler.log_error("DB_INIT_ERROR", e, additional_info=f"Attempt {attempt + 1}")
            if attempt == max_retries - 1:
                logger.critical("Не удалось инициализировать базу данных после всех попыток")
                return False
            time.sleep(2 ** attempt)

def execute_db_query(query: str, params: tuple = (), fetch: bool = False, many: bool = False):
    """Безопасное выполнение запросов к БД с повторными попытками"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect('vixen_logs.db', check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            
            logger.info(f"Executing query: {query} with params: {params}")
            cursor.execute(query, params)
            
            if fetch:
                result = cursor.fetchall() if many else cursor.fetchone()
                logger.info(f"Query result: {result}")
            else:
                result = cursor.lastrowid
            
            if not fetch:
                conn.commit()
                
            conn.close()
            return result
            
        except sqlite3.OperationalError as e:
            if DatabaseErrorHandler.handle_db_error(e, query):
                continue
            else:
                ErrorHandler.log_error("DB_QUERY_ERROR", e, additional_info=f"Query: {query}")
                raise
        except sqlite3.Error as e:
            ErrorHandler.log_error("DB_QUERY_ERROR", e, additional_info=f"Query: {query}")
            if attempt == max_retries - 1:
                raise
            time.sleep(0.5)

def add_user(user_id, username):
    """Добавление пользователя с обработкой ошибок"""
    try:
        execute_db_query(
            'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', 
            (user_id, username)
        )
    except Exception as e:
        ErrorHandler.log_error("ADD_USER_ERROR", e, user_id)

def get_user_balance(user_id):
    """Получение баланса пользователя с обработкой ошибок"""
    try:
        result = execute_db_query(
            'SELECT balance FROM users WHERE user_id = ?', 
            (user_id,), fetch=True
        )
        return result[0] if result else 0
    except Exception as e:
        ErrorHandler.log_error("GET_BALANCE_ERROR", e, user_id)
        return 0

def update_user_balance(user_id, amount):
    """Обновление баланса пользователя с обработкой ошибок"""
    try:
        # Сначала проверяем существует ли пользователь
        result = execute_db_query(
            'SELECT balance FROM users WHERE user_id = ?', 
            (user_id,), fetch=True
        )
        
        if result:
            # Пользователь существует - обновляем баланс
            current_balance = result[0]
            new_balance = current_balance + amount
            execute_db_query(
                'UPDATE users SET balance = ? WHERE user_id = ?', 
                (new_balance, user_id)
            )
        else:
            # Пользователь не существует - создаем нового
            execute_db_query(
                'INSERT INTO users (user_id, balance) VALUES (?, ?)', 
                (user_id, amount)
            )
            new_balance = amount
        
        logger.info(f"Баланс пользователя {user_id} обновлен: {new_balance}")
        return new_balance
        
    except Exception as e:
        ErrorHandler.log_error("UPDATE_BALANCE_ERROR", e, user_id, f"Amount: {amount}")
        raise

def set_user_balance(user_id, new_balance):
    """Установка конкретного значения баланса пользователя"""
    try:
        # Сначала проверяем существует ли пользователь
        result = execute_db_query(
            'SELECT 1 FROM users WHERE user_id = ?', 
            (user_id,), fetch=True
        )
        
        if result:
            # Пользователь существует - обновляем баланс
            execute_db_query(
                'UPDATE users SET balance = ? WHERE user_id = ?', 
                (new_balance, user_id)
            )
        else:
            # Пользователь не существует - создаем нового
            execute_db_query(
                'INSERT INTO users (user_id, balance) VALUES (?, ?)', 
                (user_id, new_balance)
            )
        
        logger.info(f"Баланс пользователя {user_id} установлен: {new_balance}")
        return new_balance
        
    except Exception as e:
        ErrorHandler.log_error("SET_BALANCE_ERROR", e, user_id, f"New balance: {new_balance}")
        raise

def set_user_subscribed(user_id):
    """Установка статуса подписки пользователя"""
    try:
        execute_db_query(
            'UPDATE users SET subscribed = TRUE WHERE user_id = ?', 
            (user_id,)
        )
    except Exception as e:
        ErrorHandler.log_error("SET_SUBSCRIBED_ERROR", e, user_id)

def is_user_subscribed(user_id):
    """Проверка подписки пользователя"""
    try:
        result = execute_db_query(
            'SELECT subscribed FROM users WHERE user_id = ?', 
            (user_id,), fetch=True
        )
        return result[0] if result else False
    except Exception as e:
        ErrorHandler.log_error("CHECK_SUBSCRIPTION_ERROR", e, user_id)
        return False

def get_all_users():
    """Получение всех пользователей"""
    try:
        result = execute_db_query('SELECT user_id FROM users', fetch=True, many=True)
        # Проверяем, что результат не пустой и возвращаем список user_id
        if result:
            return [row[0] for row in result]
        return []
    except Exception as e:
        ErrorHandler.log_error("GET_ALL_USERS_ERROR", e)
        return []

def get_all_users_detailed():
    """Получение всех пользователей с детальной информацией"""
    try:
        result = execute_db_query(
            'SELECT user_id, username, balance, subscribed, joined_at FROM users ORDER BY joined_at DESC', 
            fetch=True, many=True
        )
        logger.info(f"Detailed users query result: {result}")
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_ALL_USERS_DETAILED_ERROR", e)
        return []

def create_balance_invoice(user_id, invoice_id, amount):
    """Создание инвойса для пополнения баланса"""
    try:
        execute_db_query(
            'INSERT INTO balance_invoices (user_id, invoice_id, amount) VALUES (?, ?, ?)', 
            (user_id, invoice_id, amount)
        )
    except Exception as e:
        ErrorHandler.log_error("CREATE_BALANCE_INVOICE_ERROR", e, user_id)

def get_balance_invoice_by_user(user_id):
    """Получение инвойса пополнения баланса по пользователю"""
    try:
        result = execute_db_query(
            'SELECT * FROM balance_invoices WHERE user_id = ? AND status = "pending" ORDER BY created_at DESC LIMIT 1', 
            (user_id,), fetch=True
        )
        return result
    except Exception as e:
        ErrorHandler.log_error("GET_BALANCE_INVOICE_ERROR", e, user_id)
        return None

def update_balance_invoice_status(invoice_id, status):
    """Обновление статуса инвойса"""
    try:
        execute_db_query(
            'UPDATE balance_invoices SET status = ? WHERE invoice_id = ?', 
            (status, invoice_id)
        )
    except Exception as e:
        ErrorHandler.log_error("UPDATE_INVOICE_STATUS_ERROR", e, additional_info=f"Invoice: {invoice_id}")

def get_active_balance_invoices():
    """Получение всех активных инвойсов"""
    try:
        result = execute_db_query(
            'SELECT * FROM balance_invoices WHERE status = "pending"', 
            fetch=True, many=True
        )
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_ACTIVE_INVOICES_ERROR", e)
        return []

def add_used_log(log_content, log_type):
    """Добавление использованного лога"""
    try:
        execute_db_query(
            'INSERT INTO used_logs (log_content, log_type) VALUES (?, ?)', 
            (log_content, log_type)
        )
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        ErrorHandler.log_error("ADD_USED_LOG_ERROR", e, additional_info=f"Type: {log_type}")
        return False

# Глобальный словарь product_files
product_files = {
    # Папка Мt$
    "mts_gu_valid_pass": 'logs/mts_gu_valid_pass.txt',
    "mts_ya": 'logs/mts_ya.txt',
    "mts_wb": 'logs/mts_wb.txt',
    
    # Папка T2 $мeнa
    "t2_mena_ya": 'logs/t2_mena_ya.txt',
    "t2_mena_wb": 'logs/t2_mena_wb.txt',
    "t2_mena_valid_pass_kazan": 'logs/t2_mena_valid_pass_kazan.txt',
    "t2_mena_valid_pass_nizhny": 'logs/t2_mena_valid_pass_nizhny.txt',
    "t2_mena_valid_pass_spb": 'logs/t2_mena_valid_pass_spb.txt',
    
    # Папка Meg@
    "mega_gu_valid_pass": 'logs/mega_gu_valid_pass.txt',
    "mega_ya": 'logs/mega_ya.txt',
    "mega_wb": 'logs/mega_wb.txt',
}

# Базовые цены товаров (по умолчанию)
BASE_PRODUCTS = {
    # Папка Мt$
    "mts_gu_valid_pass": {"name": "Мt$ GU Valid PA$$", "price": 4.5, "category": "logs"},
    "mts_ya": {"name": "Мt$ YA", "price": 0.5, "category": "logs"},
    "mts_wb": {"name": "Мt$ WB", "price": 0.5, "category": "logs"},
    
    # Папка T2 $мeнa
    "t2_mena_ya": {"name": "$мeнa YA", "price": 0.5, "category": "logs"},
    "t2_mena_wb": {"name": "$мeнa WB", "price": 0.5, "category": "logs"},
    "t2_mena_valid_pass_kazan": {"name": "$мeнa Valid PA$$ Kазань", "price": 3.5, "category": "logs"},
    "t2_mena_valid_pass_nizhny": {"name": "$мeнa Valid PA$$ Нижегoродская", "price": 3.5, "category": "logs"},
    "t2_mena_valid_pass_spb": {"name": "$мeнa Valid PA$$ СПБ", "price": 3.5, "category": "logs"},
    
    # Папка Meg@
    "mega_gu_valid_pass": {"name": "Meg@ GU Valid PA$$", "price": 3.5, "category": "logs"},
    "mega_ya": {"name": "Meg@ YA", "price": 0.5, "category": "logs"},
    "mega_wb": {"name": "Meg@ WB", "price": 0.5, "category": "logs"},
}

def init_base_prices():
    """Инициализация цен базовых товаров в БД"""
    try:
        for product_type, product_info in BASE_PRODUCTS.items():
            execute_db_query(
                '''INSERT OR REPLACE INTO base_products_prices 
                   (product_type, name, price, category, updated_at) 
                   VALUES (?, ?, ?, ?, datetime("now"))''',
                (product_type, product_info["name"], product_info["price"], product_info["category"])
            )
        logger.info("Цены базовых товаров инициализированы")
    except Exception as e:
        ErrorHandler.log_error("INIT_BASE_PRICES_ERROR", e)

def get_product_price(product_type):
    """Получение цены товара (базового или кастомного)"""
    try:
        # Сначала проверяем кастомные товары
        custom_product = get_custom_product_info(product_type)
        if custom_product:
            return custom_product[1]  # price
        
        # Затем проверяем базовые товары в БД
        result = execute_db_query(
            'SELECT price FROM base_products_prices WHERE product_type = ?',
            (product_type,), fetch=True
        )
        if result:
            return result[0]
        
        # Если не нашли, возвращаем цену по умолчанию
        return BASE_PRODUCTS.get(product_type, {}).get("price", 1.0)
        
    except Exception as e:
        ErrorHandler.log_error("GET_PRODUCT_PRICE_ERROR", e, additional_info=f"Product: {product_type}")
        return BASE_PRODUCTS.get(product_type, {}).get("price", 1.0)

def update_product_price(product_type, new_price):
    """Обновление цены товара"""
    try:
        # Сначала проверяем кастомные товары
        custom_product = get_custom_product_info(product_type)
        if custom_product:
            execute_db_query(
                'UPDATE custom_products SET price = ? WHERE product_type = ?',
                (new_price, product_type)
            )
            logger.info(f"Цена кастомного товара {product_type} обновлена: ${new_price}")
            return True
        
        # Затем обновляем базовые товары
        execute_db_query(
            'UPDATE base_products_prices SET price = ?, updated_at = datetime("now") WHERE product_type = ?',
            (new_price, product_type)
        )
        logger.info(f"Цена базового товара {product_type} обновлена: ${new_price}")
        return True
        
    except Exception as e:
        ErrorHandler.log_error("UPDATE_PRODUCT_PRICE_ERROR", e, additional_info=f"Product: {product_type}, Price: {new_price}")
        return False

def get_all_products():
    """Получение всех товаров (базовых и кастомных)"""
    try:
        all_products = []
        
        # Добавляем базовые товары
        result = execute_db_query(
            'SELECT product_type, name, price, category FROM base_products_prices ORDER BY category, name',
            fetch=True, many=True
        )
        if result:
            for product_type, name, price, category in result:
                all_products.append({
                    "type": product_type,
                    "name": name,
                    "price": price,
                    "category": category,
                    "is_custom": False
                })
        
        # Добавляем кастомные товары
        custom_products = get_all_custom_products()
        for product_type, name, price, category, file_path in custom_products:
            all_products.append({
                "type": product_type,
                "name": name,
                "price": price,
                "category": category,
                "is_custom": True
            })
        
        return all_products
        
    except Exception as e:
        ErrorHandler.log_error("GET_ALL_PRODUCTS_ERROR", e)
        return []

def product_type_exists(product_type):
    """Проверка существования типа товара"""
    try:
        # Проверяем в базовых товарах
        if product_type in BASE_PRODUCTS:
            return True
            
        # Проверяем в кастомных товарах
        result = execute_db_query(
            'SELECT 1 FROM custom_products WHERE product_type = ?',
            (product_type,), fetch=True
        )
        return result is not None
    except Exception as e:
        ErrorHandler.log_error("PRODUCT_TYPE_CHECK_ERROR", e, additional_info=f"Type: {product_type}")
        return False

def add_custom_product(product_type, name, price, category, file_path):
    """Добавление кастомного товара в базу данных - ОБНОВЛЕННАЯ"""
    try:
        # Проверяем существование файла
        if not os.path.exists(file_path):
            logger.error(f"Файл {file_path} не существует")
            return False
        
        # Проверяем, что файл доступен для чтения
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            logger.info(f"Файл {file_path} успешно прочитан, размер: {len(content)} байт")
        except Exception as e:
            logger.error(f"Ошибка чтения файла {file_path}: {e}")
            return False
            
        execute_db_query(
            'INSERT OR REPLACE INTO custom_products (product_type, name, price, category, file_path) VALUES (?, ?, ?, ?, ?)',
            (product_type, name, price, category, file_path)
        )
        # Также добавляем в глобальный словарь
        product_files[product_type] = file_path
        logger.info(f"Товар {product_type} успешно добавлен, файл: {file_path}")
        return True
    except Exception as e:
        ErrorHandler.log_error("ADD_CUSTOM_PRODUCT_ERROR", e, additional_info=f"Product: {name}, File: {file_path}")
        return False

def get_custom_products_by_category(category):
    """Получение кастомных товаров по категории"""
    try:
        result = execute_db_query(
            'SELECT product_type, name, price FROM custom_products WHERE category = ? ORDER BY name',
            (category,), fetch=True, many=True
        )
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_CUSTOM_PRODUCTS_ERROR", e, additional_info=f"Category: {category}")
        return []

def get_all_custom_products():
    """Получение всех кастомных товаров"""
    try:
        result = execute_db_query(
            'SELECT product_type, name, price, category, file_path FROM custom_products ORDER BY category, name',
            fetch=True, many=True
        )
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_ALL_CUSTOM_PRODUCTS_ERROR", e)
        return []

def delete_custom_product(product_type):
    """Удаление кастомного товара"""
    try:
        execute_db_query(
            'DELETE FROM custom_products WHERE product_type = ?',
            (product_type,)
        )
        # Удаляем из глобального словаря
        if product_type in product_files:
            del product_files[product_type]
        logger.info(f"Товар {product_type} удален")
        return True
    except Exception as e:
        ErrorHandler.log_error("DELETE_CUSTOM_PRODUCT_ERROR", e, additional_info=f"Product: {product_type}")
        return False

def get_custom_product_info(product_type):
    """Получение информации о кастомном товаре"""
    try:
        result = execute_db_query(
            'SELECT name, price, category, file_path FROM custom_products WHERE product_type = ?',
            (product_type,), fetch=True
        )
        return result if result else None
    except Exception as e:
        ErrorHandler.log_error("GET_CUSTOM_PRODUCT_INFO_ERROR", e, additional_info=f"Product: {product_type}")
        return None

def check_logs_availability(log_type, quantity=1):
    """Проверка доступности логов - ОБНОВЛЕННАЯ ВЕРСИЯ"""
    # Сначала проверяем базовые товары
    if log_type in product_files:
        filename = product_files[log_type]
        if not filename:
            return False
            
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                logs = [line.strip() for line in file.readlines() if line.strip()]
            return len(logs) >= quantity
        except Exception as e:
            ErrorHandler.log_error("CHECK_LOGS_AVAILABILITY_ERROR", e, additional_info=f"File: {filename}")
            return False
    
    # Затем проверяем кастомные товары
    else:
        product_info = get_custom_product_info(log_type)
        if not product_info:
            return False
            
        filename = product_info[3]  # file_path
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                logs = [line.strip() for line in file.readlines() if line.strip()]
            return len(logs) >= quantity
        except Exception as e:
            ErrorHandler.log_error("CHECK_CUSTOM_LOGS_AVAILABILITY_ERROR", e, additional_info=f"File: {filename}")
            return False

def get_available_logs_count(log_type):
    """Получение количества доступных логов - ОБНОВЛЕННАЯ ВЕРСИЯ"""
    # Сначала проверяем базовые товары
    if log_type in product_files:
        filename = product_files[log_type]
        if not filename:
            return 0
            
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                logs = [line.strip() for line in file.readlines() if line.strip()]
            return len(logs)
        except Exception as e:
            ErrorHandler.log_error("GET_LOGS_COUNT_ERROR", e, additional_info=f"File: {filename}")
            return 0
    
    # Затем проверяем кастомные товары
    else:
        product_info = get_custom_product_info(log_type)
        if not product_info:
            return 0
            
        filename = product_info[3]  # file_path
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                logs = [line.strip() for line in file.readlines() if line.strip()]
            return len(logs)
        except Exception as e:
            ErrorHandler.log_error("GET_CUSTOM_LOGS_COUNT_ERROR", e, additional_info=f"File: {filename}")
            return 0

def get_random_logs(log_type, quantity=1):
    """Получение случайных логов - ОБНОВЛЕННАЯ ВЕРСИЯ"""
    # Определяем путь к файлу
    filename = None
    if log_type in product_files:
        filename = product_files[log_type]
    else:
        product_info = get_custom_product_info(log_type)
        if product_info:
            filename = product_info[3]  # file_path
    
    if not filename:
        return None
        
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            logs = [line.strip() for line in file.readlines() if line.strip()]
        
        if not logs or len(logs) < quantity:
            return None
            
        selected_logs = random.sample(logs, quantity)
        
        for log in selected_logs:
            add_used_log(log, log_type)
        
        updated_logs = [l for l in logs if l not in selected_logs]
        with open(filename, 'w', encoding='utf-8') as file:
            file.write('\n'.join(updated_logs) + '\n')
                
        logger.info(f"Выдано {quantity} логов {log_type}, осталось: {len(updated_logs)}")
        return selected_logs
            
    except Exception as e:
        ErrorHandler.log_error("GET_RANDOM_LOGS_ERROR", e, additional_info=f"File: {filename}")
        return None

# Функции для управления пользователями
def get_user_purchase_history(user_id):
    """Получение истории покупок пользователя"""
    try:
        result = execute_db_query(
            '''SELECT o.created_at, p.name, o.quantity, (p.price * o.quantity) as total_price 
               FROM orders o 
               JOIN products p ON o.product_id = p.id 
               WHERE o.user_id = ? AND o.status = 'completed'
               ORDER BY o.created_at DESC LIMIT 10''',
            (user_id,), fetch=True, many=True
        )
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_USER_HISTORY_ERROR", e, user_id)
        return []

def add_user_discount(user_id, discount_percent):
    """Добавление скидки пользователю"""
    try:
        execute_db_query(
            'INSERT OR REPLACE INTO user_discounts (user_id, discount_percent, created_at) VALUES (?, ?, datetime("now"))',
            (user_id, discount_percent)
        )
        return True
    except Exception as e:
        ErrorHandler.log_error("ADD_USER_DISCOUNT_ERROR", e, user_id)
        return False

def get_user_discount(user_id):
    """Получение скидки пользователя"""
    try:
        result = execute_db_query(
            'SELECT discount_percent FROM user_discounts WHERE user_id = ?',
            (user_id,), fetch=True
        )
        return result[0] if result else 0
    except Exception as e:
        ErrorHandler.log_error("GET_USER_DISCOUNT_ERROR", e, user_id)
        return 0

def ban_user(user_id):
    """Блокировка пользователя"""
    try:
        execute_db_query(
            'INSERT OR REPLACE INTO banned_users (user_id, banned_at) VALUES (?, datetime("now"))',
            (user_id,)
        )
        return True
    except Exception as e:
        ErrorHandler.log_error("BAN_USER_ERROR", e, user_id)
        return False

def unban_user(user_id):
    """Разблокировка пользователя"""
    try:
        execute_db_query(
            'DELETE FROM banned_users WHERE user_id = ?',
            (user_id,)
        )
        return True
    except Exception as e:
        ErrorHandler.log_error("UNBAN_USER_ERROR", e, user_id)
        return False

def is_user_banned(user_id):
    """Проверка забанен ли пользователь"""
    try:
        result = execute_db_query(
            'SELECT 1 FROM banned_users WHERE user_id = ?',
            (user_id,), fetch=True
        )
        return result is not None
    except Exception as e:
        ErrorHandler.log_error("CHECK_BAN_STATUS_ERROR", e, user_id)
        return False

def log_user_action(admin_id, user_id, action_type, action_details):
    """Логирование действий администратора"""
    try:
        execute_db_query(
            'INSERT INTO user_actions_log (admin_id, user_id, action_type, action_details) VALUES (?, ?, ?, ?)',
            (admin_id, user_id, action_type, action_details)
        )
    except Exception as e:
        ErrorHandler.log_error("LOG_USER_ACTION_ERROR", e, admin_id)

# Функции для рулетки
def can_spin_roulette_today(user_id):
    """Проверка, может ли пользователь крутить рулетку сегодня"""
    try:
        result = execute_db_query(
            'SELECT 1 FROM daily_roulette WHERE user_id = ? AND date(spin_date) = date("now")',
            (user_id,), fetch=True
        )
        
        can_spin = result is None
        logger.info(f"Roulette check for user {user_id}: can_spin={can_spin}")
        return can_spin
        
    except Exception as e:
        logger.error(f"Error in can_spin_roulette_today: {e}")
        # В случае ошибки запрещаем крутить для безопасности
        return False

def save_roulette_spin(user_id, discount):
    """Сохранение результата рулетки"""
    try:
        # Сначала удаляем старую запись на сегодня (если есть)
        execute_db_query(
            'DELETE FROM daily_roulette WHERE user_id = ? AND date(spin_date) = date("now")',
            (user_id,)
        )
        
        # Вставляем новую запись
        execute_db_query(
            'INSERT INTO daily_roulette (user_id, discount_won, expires_at) VALUES (?, ?, datetime("now", "+1 day"))',
            (user_id, discount)
        )
        
        logger.info(f"Roulette spin saved for user {user_id}: discount {discount}%")
        return True
        
    except sqlite3.IntegrityError as e:
        logger.error(f"Integrity error saving roulette: {e}")
        return False
    except Exception as e:
        ErrorHandler.log_error("SAVE_ROULETTE_SPIN_ERROR", e, user_id)
        return False

def get_todays_discount(user_id):
    """Получение сегодняшней скидки"""
    try:
        result = execute_db_query(
            'SELECT discount_won, expires_at FROM daily_roulette WHERE user_id = ? AND spin_date = DATE("now")',
            (user_id,), fetch=True
        )
        logger.info(f"Today's discount for user {user_id}: {result}")
        return result if result else None
    except Exception as e:
        ErrorHandler.log_error("GET_TODAYS_DISCOUNT_ERROR", e, user_id)
        return None

def get_last_roulette_spins(user_id, limit=5):
    """Получение последних спинов рулетки"""
    try:
        result = execute_db_query(
            'SELECT discount_won, spin_date FROM daily_roulette WHERE user_id = ? ORDER BY spin_date DESC LIMIT ?',
            (user_id, limit), fetch=True, many=True
        )
        return result if result else []
    except Exception as e:
        ErrorHandler.log_error("GET_ROULETTE_HISTORY_ERROR", e, user_id)
        return []

def get_active_discounts_count():
    """Получение количества активных скидок"""
    try:
        result = execute_db_query(
            "SELECT COUNT(*) FROM daily_roulette WHERE datetime(expires_at) > datetime('now')",
            fetch=True
        )
        return result[0] if result else 0
    except Exception as e:
        ErrorHandler.log_error("GET_ACTIVE_DISCOUNTS_COUNT_ERROR", e)
        return 0

def spin_roulette():
    """Крутка рулетки - возвращает выигранную скидку"""
    chances = {
        1: 40,   # 40% шанс
        2: 25,   # 25% шанс  
        3: 15,   # 15% шанс
        5: 10,   # 10% шанс
        7: 6,    # 6% шанс
        10: 4    # 4% шанс
    }
    
    weighted_discounts = []
    for discount, probability in chances.items():
        weighted_discounts.extend([discount] * probability)
    
    result = random.choice(weighted_discounts)
    logger.info(f"Roulette spin result: {result}% from weighted list")
    return result

async def check_subscription(bot, user_id):
    """Проверка подписки на канал"""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.info(f"Subscription check for user {user_id}: status = {member.status}")
        
        # Проверяем, что пользователь является участником канала
        if member.status in ['member', 'administrator', 'creator']:
            set_user_subscribed(user_id)
            return True
        else:
            return False
    except Exception as e:
        ErrorHandler.log_error("SUBSCRIPTION_CHECK_ERROR", e, user_id)
        return False

# Клавиатуры
def main_keyboard():
    """Главная клавиатура"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛍️ Товар"), KeyboardButton("👨‍💻 Поддержка")],
        [KeyboardButton("💰 Профиль"), KeyboardButton("💳 Пополнить баланс")],
        [KeyboardButton("🎡 Ежедневная рулетка"), KeyboardButton("👑 Создатель")]
    ], resize_keyboard=True)

def admin_keyboard():
    """Админская клавиатура - ОБНОВЛЕННАЯ"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📢 Рассылка всем"), KeyboardButton("💰 Выдать баланс")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("👥 Управление пользователями")],
        [KeyboardButton("🛍️ Добавить товар"), KeyboardButton("🗑️ Удалить товар")],
        [KeyboardButton("💳 Изменить баланс"), KeyboardButton("💰 Изменить цены")],
        [KeyboardButton("◀️ Назад")]
    ], resize_keyboard=True)

def categories_keyboard():
    """Клавиатура категорий товаров"""
    keyboard = [
        [InlineKeyboardButton("📁 ЛОГИ", callback_data="category_logs")],
        [InlineKeyboardButton("💻 СОФТЫ", callback_data="category_soft")],
        [InlineKeyboardButton("👤 АККАУНТЫ", callback_data="category_accounts")],
    ]
    return InlineKeyboardMarkup(keyboard)

def logs_keyboard():
    """Клавиатура логов - ДИНАМИЧЕСКАЯ"""
    # Базовые товары
    base_products = [
        # Папка Мt$
        {"type": "mts_gu_valid_pass", "name": "Мt$ GU Valid PA$$", "price": get_product_price("mts_gu_valid_pass")},
        {"type": "mts_ya", "name": "Мt$ YA", "price": get_product_price("mts_ya")},
        {"type": "mts_wb", "name": "Мt$ WB", "price": get_product_price("mts_wb")},
        
        # Папка T2 $мeнa
        {"type": "t2_mena_ya", "name": "$мeнa YA", "price": get_product_price("t2_mena_ya")},
        {"type": "t2_mena_wb", "name": "$мeнa WB", "price": get_product_price("t2_mena_wb")},
        {"type": "t2_mena_valid_pass_kazan", "name": "$мeнa Valid PA$$ Kазань", "price": get_product_price("t2_mena_valid_pass_kazan")},
        {"type": "t2_mena_valid_pass_nizhny", "name": "$мeнa Valid PA$$ Нижегoродская", "price": get_product_price("t2_mena_valid_pass_nizhny")},
        {"type": "t2_mena_valid_pass_spb", "name": "$мeнa Valid PA$$ СПБ", "price": get_product_price("t2_mena_valid_pass_spb")},
        
        # Папка Meg@
        {"type": "mega_gu_valid_pass", "name": "Meg@ GU Valid PA$$", "price": get_product_price("mega_gu_valid_pass")},
        {"type": "mega_ya", "name": "Meg@ YA", "price": get_product_price("mega_ya")},
        {"type": "mega_wb", "name": "Meg@ WB", "price": get_product_price("mega_wb")},
    ]
    
    # Добавляем кастомные товары
    custom_products = get_custom_products_by_category("logs")
    for product_type, name, price in custom_products:
        base_products.append({"type": product_type, "name": name, "price": price})
    
    keyboard = []
    for product in base_products:
        available_count = get_available_logs_count(product['type'])
        button_text = f"{product['name']} - ${product['price']} ({available_count} шт)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"product_{product['type']}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад к категориям", callback_data="back_to_categories")])
    return InlineKeyboardMarkup(keyboard)

def soft_keyboard():
    """Клавиатура софтов - ТЕПЕРЬ ПУСТАЯ"""
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к категориям", callback_data="back_to_categories")]
    ]
    return InlineKeyboardMarkup(keyboard)

def accounts_keyboard():
    """Клавиатура аккаунтов - ТЕПЕРЬ ПУСТАЯ"""
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к категориям", callback_data="back_to_categories")]
    ]
    return InlineKeyboardMarkup(keyboard)

def balance_payment_keyboard(invoice_url):
    """Клавиатура оплаты баланса"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить через Crypto Bot", url=invoice_url)],
        [InlineKeyboardButton("✅ Проверить пополнение", callback_data="check_balance_payment")]
    ])

def support_keyboard():
    """Клавиатура поддержки"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨‍💻 Написать в поддержку", url="https://t.me/kdiskskskis")]
    ])

def subscribe_keyboard():
    """Клавиатура подписки"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")]
    ])

def roulette_keyboard():
    """Клавиатура рулетки"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎡 Крутить рулетку!", callback_data="spin_roulette")],
        [InlineKeyboardButton("📊 Мои прошлые выигрыши", callback_data="roulette_history")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ])

def users_list_keyboard(page=0, users_per_page=10):
    """Клавиатура списка пользователей с пагинацией"""
    users = get_all_users_detailed()
    total_users = len(users)
    start_idx = page * users_per_page
    end_idx = start_idx + users_per_page
    
    keyboard = []
    
    for user in users[start_idx:end_idx]:
        user_id, username, balance, subscribed, joined_at = user
        user_display = f"👤 {username or 'No username'} (ID: {user_id})"
        if len(user_display) > 30:
            user_display = user_display[:27] + "..."
        
        keyboard.append([
            InlineKeyboardButton(
                user_display,
                callback_data=f"user_detail_{user_id}"
            )
        ])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"users_page_{page-1}"))
    
    if end_idx < total_users:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"users_page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Назад в админку", callback_data="back_to_admin")])
    
    return InlineKeyboardMarkup(keyboard)

def user_detail_keyboard(user_id):
    """Клавиатура детальной информации о пользователе"""
    keyboard = [
        [
            InlineKeyboardButton("🎁 Выдать скидку", callback_data=f"give_discount_{user_id}"),
            InlineKeyboardButton("🔨 Забанить", callback_data=f"ban_user_{user_id}")
        ],
        [
            InlineKeyboardButton("📊 История покупок", callback_data=f"user_history_{user_id}"),
            InlineKeyboardButton("💰 Баланс", callback_data=f"user_balance_{user_id}")
        ],
        [
            InlineKeyboardButton("💳 Изменить баланс", callback_data=f"edit_balance_{user_id}"),
            InlineKeyboardButton("📧 Написать", callback_data=f"message_user_{user_id}")
        ],
        [
            InlineKeyboardButton("🔄 Обновить", callback_data=f"user_detail_{user_id}"),
            InlineKeyboardButton("🔙 К списку", callback_data="users_list_0")
        ]
    ]
    
    if is_user_banned(user_id):
        keyboard[0][1] = InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_user_{user_id}")
    
    return InlineKeyboardMarkup(keyboard)

def discount_keyboard(user_id):
    """Клавиатура выбора размера скидки"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5%", callback_data=f"set_discount_{user_id}_5"),
            InlineKeyboardButton("10%", callback_data=f"set_discount_{user_id}_10"),
            InlineKeyboardButton("15%", callback_data=f"set_discount_{user_id}_15")
        ],
        [
            InlineKeyboardButton("20%", callback_data=f"set_discount_{user_id}_20"),
            InlineKeyboardButton("25%", callback_data=f"set_discount_{user_id}_25"),
            InlineKeyboardButton("30%", callback_data=f"set_discount_{user_id}_30")
        ],
        [
            InlineKeyboardButton("💎 50%", callback_data=f"set_discount_{user_id}_50"),
            InlineKeyboardButton("👑 100%", callback_data=f"set_discount_{user_id}_100")
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data=f"user_detail_{user_id}"),
            InlineKeyboardButton("❌ Сбросить скидку", callback_data=f"reset_discount_{user_id}")
        ]
    ])

def balance_edit_keyboard(user_id):
    """Клавиатура изменения баланса"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Пополнить", callback_data=f"add_balance_{user_id}"),
            InlineKeyboardButton("➖ Списать", callback_data=f"subtract_balance_{user_id}")
        ],
        [
            InlineKeyboardButton("💵 Установить сумму", callback_data=f"set_balance_{user_id}"),
            InlineKeyboardButton("🔄 Обнулить", callback_data=f"reset_balance_{user_id}")
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data=f"user_detail_{user_id}")
        ]
    ])

def products_list_keyboard(page=0, products_per_page=10):
    """Клавиатура списка товаров для изменения цен"""
    products = get_all_products()
    total_products = len(products)
    start_idx = page * products_per_page
    end_idx = start_idx + products_per_page
    
    keyboard = []
    
    for product in products[start_idx:end_idx]:
        product_type = product['type']
        name = product['name']
        price = product['price']
        category = product['category']
        is_custom = product['is_custom']
        
        product_display = f"{category}: {name} - ${price}"
        if len(product_display) > 30:
            product_display = product_display[:27] + "..."
        
        if is_custom:
            product_display = "🛍️ " + product_display
        else:
            product_display = "📦 " + product_display
        
        keyboard.append([
            InlineKeyboardButton(
                product_display,
                callback_data=f"edit_price_{product_type}"
            )
        ])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"products_page_{page-1}"))
    
    if end_idx < total_products:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"products_page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Назад в админку", callback_data="back_to_admin")])
    
    return InlineKeyboardMarkup(keyboard)

async def notify_admin(bot, user_id: int, username: str, product_name: str, price: float, quantity: int):
    """Уведомление администратора"""
    try:
        user_info = f"@{username}" if username else f"ID: {user_id}"
        message = (
            "🛒 НОВАЯ ПОКУПКА!\n\n"
            f"👤 Покупатель: {user_info}\n"
            f"📦 Товар: {product_name}\n"
            f"🔢 Количество: {quantity} шт.\n"
            f"💵 Сумма: ${price}\n"
            f"🆔 ID пользователя: {user_id}"
        )
        await bot.send_message(ADMIN_ID, message)
        logger.info(f"Уведомление отправлено админу о покупке пользователя {user_id}")
    except Exception as e:
        ErrorHandler.log_error("NOTIFY_ADMIN_ERROR", e, user_id)

def get_welcome_message(user):
    """Сообщение приветствия"""
    if user.id == ADMIN_ID:
        return (
            "🦊 Добро пожаловать в VIXEN_LOGS \n\n"
            " • Самые лучшие и дешевые логи только в VIXEN_LOGS \n\n"
            "🔑Покупай только у нас годные логи \n\n"
            "📂 Наш ассортимент:\n\n"
            "📁 *ЛОГИ:*\n"
            "• Мt$ GU Valid PA$$ - $4.5\n"
            "• Мt$ YA - $0.5\n"
            "• Мt$ WB - $0.5\n\n"
            "• $мeнa YA - $0.5\n"
            "• $мeнa WB - $0.5\n"
            "• $мeнa Valid PA$$ Kазань - $3.5\n"
            "• $мeнa Valid PA$$ Нижегoродская - $3.5\n"
            "• $мeнa Valid PA$$ СПБ - $3.5\n\n"
            "• Meg@ GU Valid PA$$ - $3.5\n"
            "• Meg@ YA - $0.5\n"
            "• Meg@ WB - $0.5\n\n"
            "🎀 Возврат только если не найден в self и нету лимита 🎀\n\n"
            "Выбирайте нужную категорию и получайте товары моментально после оплаты! 👇\n\n"
            "Для управления магазином используйте команду /admin"
        )
    else:
        return (
            "🦊 Добро пожаловать в VIXEN_LOGS\n\n"
            "• Самые лучшие и дешевые логи только в VIXEN_LOGS\n\n"
            "🔑 Покупай только у нас годные логи\n\n"
            "📂 Наш ассортимент разделен на категории:\n\n"
            "📁 *ЛОГИ* - операторы и доступы\n"
            "💻 *СОФТЫ* - программы и парсеры\n"  
            "👤 *АККАУНТЫ* - готовые аккаунты\n\n"
            "🎀 Возврат только если не найден в self и нету лимита 🎀\n"
            "Выбирайте категорию и получайте товары моментально после оплаты! 👇"
        )

async def safe_send_message(bot, chat_id: int, text: str, **kwargs):
    """Безопасная отправка сообщений с обработкой ошибок Telegram"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
            
        except RetryAfter as e:
            wait_time = e.retry_after
            logger.warning(f"Rate limit, waiting {wait_time} seconds...")
            await asyncio.sleep(wait_time)
            
        except TimedOut as e:
            ErrorHandler.log_error("TELEGRAM_TIMEOUT", e, chat_id, f"Attempt {attempt + 1}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(2 ** attempt)
            
        except BadRequest as e:
            ErrorHandler.log_error("TELEGRAM_BAD_REQUEST", e, chat_id, f"Text: {text[:100]}")
            return False
            
        except NetworkError as e:
            ErrorHandler.log_error("TELEGRAM_NETWORK_ERROR", e, chat_id, f"Attempt {attempt + 1}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(2 ** attempt)
            
        except TelegramError as e:
            ErrorHandler.log_error("TELEGRAM_ERROR", e, chat_id, f"Attempt {attempt + 1}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(1)
            
        except Exception as e:
            ErrorHandler.log_error("SEND_MESSAGE_ERROR", e, chat_id, f"Attempt {attempt + 1}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(1)
    
    return False

async def show_custom_products_for_deletion(bot, user_id):
    """Показать кастомные товары для удаления"""
    try:
        custom_products = get_all_custom_products()
        
        if not custom_products:
            await safe_send_message(
                bot, user_id,
                "❌ Нет кастомных товаров для удаления."
            )
            return
        
        keyboard = []
        for product_type, name, price, category, file_path in custom_products:
            available_count = get_available_logs_count(product_type)
            button_text = f"{category}: {name} - ${price} ({available_count} шт)"
            if len(button_text) > 50:
                button_text = button_text[:47] + "..."
            
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"delete_product_{product_type}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в админку", callback_data="back_to_admin")])
        
        await safe_send_message(
            bot, user_id,
            "🗑️ Выберите товар для удаления:\n\n"
            "⚠️ Внимание: удаление товара не удаляет файл с логами!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        ErrorHandler.log_error("SHOW_PRODUCTS_DELETION_ERROR", e, user_id)

async def show_products_for_price_edit(bot, user_id, page=0):
    """Показать товары для изменения цен"""
    try:
        products = get_all_products()
        
        if not products:
            await safe_send_message(
                bot, user_id,
                "❌ Нет товаров для изменения цен."
            )
            return
        
        total_products = len(products)
        await safe_send_message(
            bot, user_id,
            f"💰 Изменение цен на товары\n\nВсего товаров: {total_products}\nСтраница {page + 1}\n\nВыберите товар:",
            reply_markup=products_list_keyboard(page)
        )
        
    except Exception as e:
        ErrorHandler.log_error("SHOW_PRODUCTS_PRICE_EDIT_ERROR", e, user_id)

async def handle_delete_product(query, context, user, data):
    """Обработка удаления товара"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
    
    product_type = data.split('_', 2)[2]
    product_info = get_custom_product_info(product_type)
    
    if not product_info:
        await query.answer("❌ Товар не найден!", show_alert=True)
        return
    
    name, price, category, file_path = product_info
    
    # Удаляем товар
    if delete_custom_product(product_type):
        await query.answer(f"✅ Товар '{name}' удален!", show_alert=True)
        
        # Обновляем клавиатуру категории
        await update_category_keyboard(context.bot, user.id, category)
        
        # Возвращаемся к списку товаров для удаления
        await show_custom_products_for_deletion(context.bot, user.id)
    else:
        await query.answer("❌ Ошибка при удалении товара!", show_alert=True)

async def update_category_keyboard(bot, user_id, category):
    """Обновление клавиатуры категории после добавления товара"""
    try:
        if category == "logs":
            await safe_send_message(
                bot, user_id,
                "📁 Категория ЛОГИ обновлена! Выберите товар:",
                reply_markup=logs_keyboard()
            )
        elif category == "soft":
            await safe_send_message(
                bot, user_id,
                "💻 Категория СОФТЫ обновлена! Выберите товар:",
                reply_markup=soft_keyboard()
            )
        elif category == "accounts":
            await safe_send_message(
                bot, user_id,
                "👤 Категория АККАУНТЫ обновлена! Выберите товар:",
                reply_markup=accounts_keyboard()
            )
    except Exception as e:
        ErrorHandler.log_error("UPDATE_CATEGORY_KEYBOARD_ERROR", e, user_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start с проверкой подписки"""
    try:
        user = update.effective_user
        add_user(user.id, user.username)
        
        # Для администратора пропускаем проверку подписки
        if user.id == ADMIN_ID:
            await safe_send_message(
                context.bot, user.id,
                get_welcome_message(user),
                reply_markup=main_keyboard()
            )
            return
        
        # Проверяем подписку для обычных пользователей
        is_subscribed = await check_subscription(context.bot, user.id)
        if not is_subscribed:
            await safe_send_message(
                context.bot, user.id,
                "📢 Для использования бота необходимо подписаться на наш канал!\n\n"
                "После подписки нажмите кнопку '✅ Я подписался'",
                reply_markup=subscribe_keyboard()
            )
            return
        
        # Если подписан - устанавливаем статус и показываем главное меню
        set_user_subscribed(user.id)
        await safe_send_message(
            context.bot, user.id,
            get_welcome_message(user),
            reply_markup=main_keyboard()
        )
        
    except Exception as e:
        ErrorHandler.log_error("START_COMMAND_ERROR", e, user.id if user else None)
        await ErrorHandler.notify_admin(context.bot, e, "Команда /start")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /admin"""
    try:
        user = update.effective_user
        if user.id == ADMIN_ID:
            await safe_send_message(
                context.bot, user.id,
                "⚙️ Панель администратора:",
                reply_markup=admin_keyboard()
            )
        else:
            await safe_send_message(
                context.bot, user.id,
                "❌ У вас нет прав доступа!"
            )
    except Exception as e:
        ErrorHandler.log_error("ADMIN_COMMAND_ERROR", e, user.id if user else None)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /balance"""
    try:
        user = update.effective_user
        balance = get_user_balance(user.id)
        await safe_send_message(
            context.bot, user.id,
            f"💰 Ваш текущий баланс: ${balance}"
        )
    except Exception as e:
        ErrorHandler.log_error("BALANCE_COMMAND_ERROR", e, user.id if user else None)

# Команды для тестирования рулетки
async def force_roulette_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительный сброс рулетки для тестирования"""
    try:
        user = update.effective_user
        
        # Удаляем все записи о рулетке для этого пользователя
        execute_db_query(
            'DELETE FROM daily_roulette WHERE user_id = ?',
            (user.id,)
        )
        
        # Сбрасываем скидку
        add_user_discount(user.id, 0)
        
        await safe_send_message(
            context.bot, user.id,
            "✅ Рулетка сброшена! Теперь вы можете крутить снова.\n\n"
            "Нажмите 'Ежедневная рулетка' в главном меню."
        )
        
    except Exception as e:
        ErrorHandler.log_error("FORCE_ROULETTE_ERROR", e, user.id if user else None)

async def check_roulette_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка состояния рулетки"""
    try:
        user = update.effective_user
        
        can_spin = can_spin_roulette_today(user.id)
        current_discount = get_user_discount(user.id)
        todays_spin = get_todays_discount(user.id)
        
        status_message = (
            f"🎡 Статус рулетки для пользователя {user.id}:\n\n"
            f"🔄 Может крутить сегодня: {'✅ ДА' if can_spin else '❌ НЕТ'}\n"
            f"🎁 Текущая скидка: {current_discount}%\n"
            f"📊 Сегодняшний спин: {todays_spin if todays_spin else 'Нет'}\n"
        )
        
        if not can_spin and todays_spin:
            discount, expires = todays_spin
            status_message += f"⏰ Скидка действует до: {expires[:16]}"
        
        await safe_send_message(context.bot, user.id, status_message)
        
    except Exception as e:
        ErrorHandler.log_error("CHECK_ROULETTE_ERROR", e, user.id if user else None)

# Основные обработчики сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    try:
        text = update.message.text
        user = update.effective_user
        
        # Для администратора пропускаем проверку подписки
        if user.id != ADMIN_ID:
            is_subscribed = await check_subscription(context.bot, user.id)
            if not is_subscribed:
                await safe_send_message(
                    context.bot, user.id,
                    "📢 Для использования бота необходимо подписаться на наш канал!\n\n"
                    "После подписки нажмите кнопку '✅ Я подписался'",
                    reply_markup=subscribe_keyboard()
                )
                return
            else:
                set_user_subscribed(user.id)
        
        if text == "🛍️ Товар":
            await safe_send_message(
                context.bot, user.id,
                "📦 Выберите категорию товаров:",
                reply_markup=categories_keyboard()
            )
        elif text == "👨‍💻 Поддержка":
            await safe_send_message(
                context.bot, user.id,
                "📞 Связаться с поддержкой:",
                reply_markup=support_keyboard()
            )
        elif text == "💰 Профиль":
            balance = get_user_balance(user.id)
            user_discount = get_user_discount(user.id)
            can_spin_today = can_spin_roulette_today(user.id)
            todays_discount = get_todays_discount(user.id)
            
            profile_text = (
                f"👤 Ваш профиль\n\n"
                f"🆔 ID: {user.id}\n"
                f"📛 Имя: {user.first_name}\n"
                f"👤 Username: @{user.username if user.username else 'Не указан'}\n"
                f"💰 Баланс: ${balance}"
            )
            
            if user_discount > 0 and todays_discount:
                expires_at = todays_discount[1]
                profile_text += f"\n🎁 Текущая скидка: {user_discount}%"
                profile_text += f"\n⏰ Действует до: {expires_at[:16]}"
            else:
                profile_text += f"\n🎁 Активных скидок: нет"
            
            if can_spin_today:
                profile_text += "\n\n🎡 *Сегодня можно крутить рулетку!*"
            else:
                profile_text += "\n\n⏳ Рулетка будет доступна завтра"
            
            await safe_send_message(
                context.bot, user.id,
                profile_text
            )
        elif text == "💳 Пополнить баланс":
            context.user_data['awaiting_balance_amount'] = True
            await safe_send_message(
                context.bot, user.id,
                "💵 Введите сумму для пополнения баланса ($):"
            )
        elif text == "🎡 Ежедневная рулетка":
            await handle_roulette_command(update, context, user)
        elif text == "👑 Создатель":
            await safe_send_message(
                context.bot, user.id,
                "❤️ Created by @kdiskskskis❤️"
            )
        elif text == "/admin" and user.id == ADMIN_ID:
            await admin_command(update, context)
        elif text == "📢 Рассылка всем" and user.id == ADMIN_ID:
            context.user_data['awaiting_broadcast'] = True
            await safe_send_message(
                context.bot, user.id,
                "📝 Введите сообщение для рассылки:"
            )
        elif text == "💰 Выдать баланс" and user.id == ADMIN_ID:
            context.user_data['awaiting_balance_user'] = True
            await safe_send_message(
                context.bot, user.id,
                "Введите ID пользователя:"
            )
        elif text == "💳 Изменить баланс" and user.id == ADMIN_ID:
            context.user_data['awaiting_balance_edit_user'] = True
            await safe_send_message(
                context.bot, user.id,
                "💳 Изменение баланса пользователя\n\nВведите ID пользователя:"
            )
        elif text == "💰 Изменить цены" and user.id == ADMIN_ID:
            await show_products_for_price_edit(context.bot, user.id)
        elif text == "📊 Статистика" and user.id == ADMIN_ID:
            users_count = len(get_all_users())
            active_discounts = get_active_discounts_count()
            
            today_wins = execute_db_query(
                "SELECT COUNT(*), SUM(discount_won) FROM daily_roulette WHERE spin_date = DATE('now')",
                fetch=True
            )
            today_count = today_wins[0] if today_wins and today_wins[0] else 0
            today_total = today_wins[1] if today_wins and today_wins[1] else 0
            
            stats_text = (
                f"📊 Статистика бота\n\n"
                f"👥 Всего пользователей: {users_count}\n"
                f"🎁 Активных скидок: {active_discounts}\n"
                f"🔄 Рулеток сегодня: {today_count}\n"
                f"💎 Скидок выдано: {today_total}%"
            )
            
            await safe_send_message(context.bot, user.id, stats_text)
        elif text == "👥 Управление пользователями" and user.id == ADMIN_ID:
            users_count = len(get_all_users())
            await safe_send_message(
                context.bot, user.id,
                f"👥 Управление пользователями\n\nВсего пользователей: {users_count}\n\nВыберите пользователя:",
                reply_markup=users_list_keyboard()
            )
        elif text == "🛍️ Добавить товар" and user.id == ADMIN_ID:
            context.user_data['awaiting_product_category'] = True
            await safe_send_message(
                context.bot, user.id,
                "🛍️ Добавление нового товара\n\nВыберите категорию:\n\n"
                "1. logs - Логи\n"
                "2. soft - Софты\n" 
                "3. accounts - Аккаунты\n\n"
                "Введите название категории:"
            )
        elif text == "🗑️ Удалить товар" and user.id == ADMIN_ID:
            await show_custom_products_for_deletion(context.bot, user.id)
        elif text == "◀️ Назад" and user.id == ADMIN_ID:
            await safe_send_message(
                context.bot, user.id,
                "Главное меню:",
                reply_markup=main_keyboard()
            )
        elif context.user_data.get('awaiting_broadcast') and user.id == ADMIN_ID:
            users = get_all_users()
            success_count = 0
            for user_id in users:
                try:
                    await context.bot.send_message(user_id, text)
                    success_count += 1
                except Exception as e:
                    ErrorHandler.log_error("BROADCAST_ERROR", e, user_id)
            context.user_data['awaiting_broadcast'] = False
            await safe_send_message(
                context.bot, user.id,
                f"✅ Рассылка отправлена {success_count} из {len(users)} пользователям"
            )
        elif context.user_data.get('awaiting_balance_user') and user.id == ADMIN_ID:
            try:
                target_user_id = int(text)
                context.user_data['target_user_id'] = target_user_id
                context.user_data['awaiting_balance_user'] = False
                context.user_data['awaiting_admin_balance_amount'] = True
                await safe_send_message(
                    context.bot, user.id,
                    "Введите сумму для выдачи:"
                )
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверный ID пользователя!"
                )
        elif context.user_data.get('awaiting_balance_edit_user') and user.id == ADMIN_ID:
            try:
                target_user_id = int(text)
                context.user_data['target_user_id'] = target_user_id
                context.user_data['awaiting_balance_edit_user'] = False
                
                current_balance = get_user_balance(target_user_id)
                user_info = execute_db_query(
                    'SELECT username FROM users WHERE user_id = ?',
                    (target_user_id,), fetch=True
                )
                username = user_info[0] if user_info else "Неизвестно"
                
                await safe_send_message(
                    context.bot, user.id,
                    f"💳 Изменение баланса пользователя\n\n"
                    f"👤 Пользователь: @{username} (ID: {target_user_id})\n"
                    f"💰 Текущий баланс: ${current_balance}\n\n"
                    f"Выберите действие:",
                    reply_markup=balance_edit_keyboard(target_user_id)
                )
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверный ID пользователя!"
                )
        elif context.user_data.get('awaiting_admin_balance_amount') and user.id == ADMIN_ID:
            try:
                amount = float(text)
                target_user_id = context.user_data['target_user_id']
                
                new_balance = update_user_balance(target_user_id, amount)
                
                await safe_send_message(
                    context.bot, user.id,
                    f"✅ Баланс пользователя {target_user_id} пополнен на ${amount}\n💰 Новый баланс: ${new_balance}"
                )
                context.user_data.clear()
                
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная сумма!"
                )
        elif context.user_data.get('awaiting_balance_add_amount') and user.id == ADMIN_ID:
            try:
                amount = float(text)
                target_user_id = context.user_data['target_user_id']
                
                new_balance = update_user_balance(target_user_id, amount)
                
                await safe_send_message(
                    context.bot, user.id,
                    f"✅ Баланс пользователя {target_user_id} пополнен на ${amount}\n💰 Новый баланс: ${new_balance}"
                )
                context.user_data.clear()
                
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная сумма!"
                )
        elif context.user_data.get('awaiting_balance_subtract_amount') and user.id == ADMIN_ID:
            try:
                amount = float(text)
                target_user_id = context.user_data['target_user_id']
                
                new_balance = update_user_balance(target_user_id, -amount)
                
                await safe_send_message(
                    context.bot, user.id,
                    f"✅ С баланса пользователя {target_user_id} списано ${amount}\n💰 Новый баланс: ${new_balance}"
                )
                context.user_data.clear()
                
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная сумма!"
                )
        elif context.user_data.get('awaiting_balance_set_amount') and user.id == ADMIN_ID:
            try:
                amount = float(text)
                target_user_id = context.user_data['target_user_id']
                
                new_balance = set_user_balance(target_user_id, amount)
                
                await safe_send_message(
                    context.bot, user.id,
                    f"✅ Баланс пользователя {target_user_id} установлен на ${amount}\n💰 Новый баланс: ${new_balance}"
                )
                context.user_data.clear()
                
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная сумма!"
                )
        elif context.user_data.get('awaiting_product_price_edit') and user.id == ADMIN_ID:
            try:
                new_price = float(text)
                product_type = context.user_data['product_type_for_edit']
                
                if update_product_price(product_type, new_price):
                    await safe_send_message(
                        context.bot, user.id,
                        f"✅ Цена товара успешно обновлена!\n\n"
                        f"🆔 Тип: {product_type}\n"
                        f"💰 Новая цена: ${new_price}"
                    )
                    
                    # Обновляем клавиатуру категории если нужно
                    product_info = None
                    if product_type in BASE_PRODUCTS:
                        product_info = BASE_PRODUCTS[product_type]
                    else:
                        custom_info = get_custom_product_info(product_type)
                        if custom_info:
                            product_info = {"category": custom_info[2]}
                    
                    if product_info:
                        await update_category_keyboard(context.bot, user.id, product_info["category"])
                else:
                    await safe_send_message(
                        context.bot, user.id,
                        "❌ Ошибка при обновлении цены!"
                    )
                
                context.user_data.clear()
                
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная цена! Введите число:"
                )
        elif context.user_data.get('awaiting_user_message') and user.id == ADMIN_ID:
            target_user_id = context.user_data.get('message_target_user')
            if target_user_id:
                try:
                    await safe_send_message(
                        context.bot, target_user_id,
                        f"📨 Сообщение от администратора:\n\n{text}"
                    )
                    await safe_send_message(
                        context.bot, user.id,
                        f"✅ Сообщение отправлено пользователю {target_user_id}"
                    )
                    log_user_action(user.id, target_user_id, "message_sent", f"Сообщение: {text[:50]}...")
                except Exception as e:
                    await safe_send_message(
                        context.bot, user.id,
                        f"❌ Не удалось отправить сообщение пользователю {target_user_id}"
                    )
                    ErrorHandler.log_error("ADMIN_MESSAGE_ERROR", e, target_user_id)
            
            context.user_data.clear()
        elif context.user_data.get('awaiting_balance_amount') and not context.user_data.get('awaiting_admin_balance_amount'):
            try:
                amount = float(text)
                if amount <= 0:
                    await safe_send_message(
                        context.bot, user.id,
                        "❌ Сумма должна быть больше 0! Введите сумму:"
                    )
                    return
                
                crypto_bot = CryptoBotAPI(CRYPTO_BOT_TOKEN)
                invoice = await crypto_bot.create_invoice(amount=amount, description=f"Пополнение баланса")
                
                if invoice and 'invoice_id' in invoice and 'pay_url' in invoice:
                    create_balance_invoice(user.id, invoice['invoice_id'], amount)
                    
                    payment_text = (
                        f"💳 Пополнение баланса\n"
                        f"💵 Сумма: ${amount}\n\n"
                        f"Нажмите кнопку для оплаты:"
                    )
                    await safe_send_message(
                        context.bot, user.id,
                        payment_text,
                        reply_markup=balance_payment_keyboard(invoice['pay_url'])
                    )
                    context.user_data.clear()
                else:
                    await safe_send_message(
                        context.bot, user.id,
                        "❌ Ошибка при создании счета! Попробуйте позже."
                    )
                    context.user_data.clear()
                    
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Пожалуйста, введите корректную сумму:"
                )
        elif context.user_data.get('awaiting_quantity'):
            try:
                quantity = int(text)
                if quantity <= 0:
                    await safe_send_message(
                        context.bot, user.id,
                        "❌ Количество должно быть больше 0! Введите число:"
                    )
                    return
                    
                product_type = context.user_data['selected_product']
                
                # Получаем информацию о товаре
                product_info = {
                    # Папка Мt$
                    "mts_gu_valid_pass": {"name": "Мt$ GU Valid PA$$", "price": get_product_price("mts_gu_valid_pass")},
                    "mts_ya": {"name": "Мt$ YA", "price": get_product_price("mts_ya")},
                    "mts_wb": {"name": "Мt$ WB", "price": get_product_price("mts_wb")},
                    
                    # Папка T2 $мeнa
                    "t2_mena_ya": {"name": "$мeнa YA", "price": get_product_price("t2_mena_ya")},
                    "t2_mena_wb": {"name": "$мeнa WB", "price": get_product_price("t2_mena_wb")},
                    "t2_mena_valid_pass_kazan": {"name": "$мeнa Valid PA$$ Kазань", "price": get_product_price("t2_mena_valid_pass_kazan")},
                    "t2_mena_valid_pass_nizhny": {"name": "$мeнa Valid PA$$ Нижегoродская", "price": get_product_price("t2_mena_valid_pass_nizhny")},
                    "t2_mena_valid_pass_spb": {"name": "$мeнa Valid PA$$ СПБ", "price": get_product_price("t2_mena_valid_pass_spb")},
                    
                    # Папка Meg@
                    "mega_gu_valid_pass": {"name": "Meg@ GU Valid PA$$", "price": get_product_price("mega_gu_valid_pass")},
                    "mega_ya": {"name": "Meg@ YA", "price": get_product_price("mega_ya")},
                    "mega_wb": {"name": "Meg@ WB", "price": get_product_price("mega_wb")},
                }
                
                # Проверяем кастомные товары
                custom_products = []
                for category in ["logs", "soft", "accounts"]:
                    custom_products.extend(get_custom_products_by_category(category))
                
                for custom_type, custom_name, custom_price in custom_products:
                    if custom_type == product_type:
                        product_info[product_type] = {"name": custom_name, "price": custom_price}
                        break
                
                product_data = product_info.get(product_type, {"name": "Товар", "price": 1.0})
                product_name = product_data["name"]
                price_per_item = product_data["price"]
                
                user_discount = get_user_discount(user.id)
                total_price = quantity * price_per_item
                discount_amount = 0
                total_price_with_discount = total_price
                
                if user_discount > 0:
                    discount_amount = total_price * (user_discount / 100)
                    total_price_with_discount = total_price - discount_amount
                    
                    discount_info = get_todays_discount(user.id)
                    expires_time = discount_info[1][11:16] if discount_info else "24:00"
                
                user_balance = get_user_balance(user.id)
                
                if user_balance < total_price_with_discount:
                    balance_message = (
                        f"❌ Недостаточно средств на балансе!\n\n"
                        f"💵 Нужно: ${total_price_with_discount:.2f}\n"
                        f"💰 На балансе: ${user_balance}"
                    )
                    
                    if user_discount > 0:
                        balance_message += f"\n\n🎁 *Ваша скидка: {user_discount}%*\n⏰ Действует до: {expires_time}"
                    
                    await safe_send_message(context.bot, user.id, balance_message)
                    context.user_data.clear()
                    return
                
                discount_info_text = ""
                if user_discount > 0:
                    discount_info_text = (
                        f"\n🎁 *Ваша скидка: {user_discount}%*\n"
                        f"⏰ Действует до: {expires_time}\n"
                        f"💵 Цена без скидки: ${total_price:.2f}\n"
                        f"💰 Экономия: ${discount_amount:.2f}\n"
                        f"💳 К оплате: ${total_price_with_discount:.2f}"
                    )
                
                if not check_logs_availability(product_type, quantity):
                    available_count = get_available_logs_count(product_type)
                    await safe_send_message(
                        context.bot, user.id,
                        f"❌ Извините, недостаточно {product_name}!\n\nДоступно: {available_count} шт.{discount_info_text}"
                    )
                    context.user_data.clear()
                    return
                
                update_user_balance(user.id, -total_price_with_discount)
                
                success = await deliver_content(context.bot, user.id, product_type, quantity)
                if success:
                    purchase_message = (
                        f"✅ Покупка успешна! {quantity} лог(ов) отправлен(ы).\n\n"
                        f"💰 Списано: ${total_price_with_discount:.2f}"
                    )
                    
                    if user_discount > 0:
                        purchase_message += (
                            f"\n\n🎁 *Скидка {user_discount}% применена!*\n"
                            f"💵 Сэкономлено: ${discount_amount:.2f}\n"
                            f"⏰ Скидка действует до: {expires_time}"
                        )
                    
                    await safe_send_message(context.bot, user.id, purchase_message)
                    await notify_admin(context.bot, user.id, user.username, product_name, total_price_with_discount, quantity)
                else:
                    update_user_balance(user.id, total_price_with_discount)
                    await safe_send_message(
                        context.bot, user.id,
                        "❌ Ошибка при получении логов! Средства возвращены."
                    )
                
                context.user_data.clear()
                    
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Пожалуйста, введите корректное число:"
                )
        # Обработка добавления товара
        elif context.user_data.get('awaiting_product_category') and user.id == ADMIN_ID:
            category = text.lower().strip()
            valid_categories = ['logs', 'soft', 'accounts']
            
            if category not in valid_categories:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Неверная категория! Введите одну из: logs, soft, accounts"
                )
                return
                
            context.user_data['product_category'] = category
            context.user_data['awaiting_product_category'] = False
            context.user_data['awaiting_product_name'] = True
            await safe_send_message(
                context.bot, user.id,
                "📝 Введите название товара:"
            )
        elif context.user_data.get('awaiting_product_name') and user.id == ADMIN_ID:
            context.user_data['product_name'] = text
            context.user_data['awaiting_product_name'] = False
            context.user_data['awaiting_product_price'] = True
            await safe_send_message(
                context.bot, user.id,
                "💵 Введите цену товара ($):"
            )
        elif context.user_data.get('awaiting_product_price') and user.id == ADMIN_ID:
            try:
                price = float(text)
                context.user_data['product_price'] = price
                context.user_data['awaiting_product_price'] = False
                context.user_data['awaiting_product_file'] = True
                await safe_send_message(
                    context.bot, user.id,
                    "📁 Введите путь к файлу с логами (например: logs/new_product.txt):"
                )
            except ValueError:
                await safe_send_message(
                    context.bot, user.id,
                    "❌ Пожалуйста, введите корректную цену:"
                )
        elif context.user_data.get('awaiting_product_file') and user.id == ADMIN_ID:
            file_path = text.strip()
            category = context.user_data['product_category']
            name = context.user_data['product_name']
            price = context.user_data['product_price']
            
            # Создаем уникальный тип товара
            product_type = f"{category}_{name.lower().replace(' ', '_').replace('-', '_').replace('+', 'plus')}"
            
            # Проверяем и создаем файл если нужно
            try:
                if not os.path.exists(file_path):
                    # Создаем пустой файл
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write("# Файл создан автоматически\n")
                    logger.info(f"Создан новый файл: {file_path}")
            except Exception as e:
                await safe_send_message(
                    context.bot, user.id,
                    f"❌ Ошибка при создании файла: {e}"
                )
                context.user_data.clear()
                return
            
            # Добавляем товар в базу данных
            if add_custom_product(product_type, name, price, category, file_path):
                await safe_send_message(
                    context.bot, user.id,
                    f"✅ Товар '{name}' успешно добавлен в категорию {category}!\n\n"
                    f"💵 Цена: ${price}\n"
                    f"📁 Файл: {file_path}\n"
                    f"🔗 Тип: {product_type}\n\n"
                    f"⚠️ Не забудьте добавить данные в файл {file_path}"
                )
                
                # Обновляем клавиатуру категории
                await update_category_keyboard(context.bot, user.id, category)
                
            else:
                await safe_send_message(
                    context.bot, user.id,
                    f"❌ Ошибка при добавлении товара!\n\n"
                    f"Проверьте:\n"
                    f"1. Существует ли файл: {file_path}\n"
                    f"2. Доступен ли файл для чтения\n"
                    f"3. Не занят ли тип товара: {product_type}"
                )
            
            context.user_data.clear()
                
    except Exception as e:
        ErrorHandler.log_error("HANDLE_MESSAGE_ERROR", e, user.id if user else None)

# Обработчики callback
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback запросов"""
    query = update.callback_query
    user = query.from_user
    
    try:
        await query.answer()
        data = query.data
        
        logger.info(f"Callback data received: {data} from user {user.id}")
        
        if data == "check_subscription":
            await handle_subscription_check(query, context, user)
        elif data.startswith('category_'):
            await handle_category_selection(query, context, user, data)
        elif data == "back_to_categories":
            await handle_back_to_categories(query, context, user)
        elif data.startswith('product_'):
            await handle_product_selection(query, context, user)
        elif data == "check_balance_payment":
            await handle_balance_payment_check(query, context, user)
        elif data == "spin_roulette":
            await handle_spin_roulette(query, context, user)
        elif data == "roulette_history":
            await handle_roulette_history(query, context, user)
        elif data == "back_to_main":
            await handle_back_to_main(query, context, user)
        elif data.startswith('users_page_'):
            await handle_users_page(query, context, user, data)
        elif data.startswith('user_detail_'):
            await handle_user_detail(query, context, user, data)
        elif data.startswith('give_discount_'):
            await handle_give_discount(query, context, user, data)
        elif data.startswith('set_discount_'):
            await handle_set_discount(query, context, user, data)
        elif data.startswith('reset_discount_'):
            await handle_reset_discount(query, context, user, data)
        elif data.startswith('ban_user_'):
            await handle_ban_user(query, context, user, data)
        elif data.startswith('unban_user_'):
            await handle_unban_user(query, context, user, data)
        elif data.startswith('user_history_'):
            await handle_user_history(query, context, user, data)
        elif data.startswith('user_balance_'):
            await handle_user_balance(query, context, user, data)
        elif data.startswith('message_user_'):
            await handle_message_user(query, context, user, data)
        elif data.startswith('edit_balance_'):
            await handle_edit_balance(query, context, user, data)
        elif data.startswith('add_balance_'):
            await handle_add_balance(query, context, user, data)
        elif data.startswith('subtract_balance_'):
            await handle_subtract_balance(query, context, user, data)
        elif data.startswith('set_balance_'):
            await handle_set_balance(query, context, user, data)
        elif data.startswith('reset_balance_'):
            await handle_reset_balance(query, context, user, data)
        elif data.startswith('edit_price_'):
            await handle_edit_price(query, context, user, data)
        elif data.startswith('products_page_'):
            await handle_products_page(query, context, user, data)
        elif data == "users_list_0":
            await handle_users_list(query, context, user)
        elif data == "back_to_admin":
            await handle_back_to_admin(query, context, user)
        elif data.startswith('delete_product_'):
            await handle_delete_product(query, context, user, data)
        else:
            logger.warning(f"Unknown callback data: {data} from user {user.id}")
            await query.answer("❌ Неизвестная команда", show_alert=True)
            
    except Exception as e:
        ErrorHandler.log_error("CALLBACK_HANDLER_ERROR", e, user.id, f"Callback data: {data}")
        try:
            await query.answer("❌ Произошла ошибка. Попробуйте позже.", show_alert=True)
        except:
            pass

# Новые обработчики категорий
async def handle_category_selection(query, context, user, data):
    """Обработка выбора категории"""
    try:
        category = data.split('_')[1]
        
        if category == "logs":
            await query.edit_message_text(
                "📁 *ЛОГИ - операторы и доступы*\n\nВыберите товар:",
                reply_markup=logs_keyboard(),
                parse_mode='Markdown'
            )
        elif category == "soft":
            await query.edit_message_text(
                "💻 *СОФТЫ - программы и парсеры*\n\nВыберите товар:",
                reply_markup=soft_keyboard(),
                parse_mode='Markdown'
            )
        elif category == "accounts":
            await query.edit_message_text(
                "👤 *АККАУНТЫ - готовые аккаунты*\n\nВыберите товар:",
                reply_markup=accounts_keyboard(),
                parse_mode='Markdown'
            )
            
    except Exception as e:
        ErrorHandler.log_error("CATEGORY_SELECTION_ERROR", e, user.id)
        await query.answer("❌ Ошибка при выборе категории!", show_alert=True)

async def handle_back_to_categories(query, context, user):
    """Обработка возврата к категориям"""
    try:
        await query.edit_message_text(
            "📦 Выберите категорию товаров:",
            reply_markup=categories_keyboard()
        )
    except Exception as e:
        ErrorHandler.log_error("BACK_TO_CATEGORIES_ERROR", e, user.id)

# Существующие обработчики
async def handle_subscription_check(query, context, user):
    """Обработка проверки подписки - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    try:
        is_subscribed = await check_subscription(context.bot, user.id)
        
        if is_subscribed:
            set_user_subscribed(user.id)
            # Удаляем сообщение с просьбой подписаться
            await query.delete_message()
            # Отправляем приветственное сообщение
            await safe_send_message(
                context.bot, user.id,
                "✅ Спасибо за подписку! Добро пожаловать в бот!",
                reply_markup=main_keyboard()
            )
            # Отправляем основное сообщение
            await safe_send_message(
                context.bot, user.id,
                get_welcome_message(user),
                reply_markup=main_keyboard()
            )
        else:
            await query.answer("❌ Вы не подписаны на канал! Подпишитесь и попробуйте снова.", show_alert=True)
            # Обновляем сообщение с кнопкой подписки
            await query.edit_message_text(
                "📢 Для использования бота необходимо подписаться на наш канал!\n\n"
                "После подписки нажмите кнопку '✅ Я подписался'",
                reply_markup=subscribe_keyboard()
            )
    except Exception as e:
        ErrorHandler.log_error("SUBSCRIPTION_CHECK_ERROR", e, user.id)
        await query.answer("❌ Ошибка при обработке!", show_alert=True)

async def handle_product_selection(query, context, user):
    """Обработка выбора товара"""
    try:
        product_type = query.data.split('_', 1)[1]
        
        # Получаем информацию о товаре
        product_info = {
            # Папка Мt$
            "mts_gu_valid_pass": {"name": "Мt$ GU Valid PA$$", "price": get_product_price("mts_gu_valid_pass")},
            "mts_ya": {"name": "Мt$ YA", "price": get_product_price("mts_ya")},
            "mts_wb": {"name": "Мt$ WB", "price": get_product_price("mts_wb")},
            
            # Папка T2 $мeнa
            "t2_mena_ya": {"name": "$мeнa YA", "price": get_product_price("t2_mena_ya")},
            "t2_mena_wb": {"name": "$мeнa WB", "price": get_product_price("t2_mena_wb")},
            "t2_mena_valid_pass_kazan": {"name": "$мeнa Valid PA$$ Kазань", "price": get_product_price("t2_mena_valid_pass_kazan")},
            "t2_mena_valid_pass_nizhny": {"name": "$мeнa Valid PA$$ Нижегoродская", "price": get_product_price("t2_mena_valid_pass_nizhny")},
            "t2_mena_valid_pass_spb": {"name": "$мeнa Valid PA$$ СПБ", "price": get_product_price("t2_mena_valid_pass_spb")},
            
            # Папка Meg@
            "mega_gu_valid_pass": {"name": "Meg@ GU Valid PA$$", "price": get_product_price("mega_gu_valid_pass")},
            "mega_ya": {"name": "Meg@ YA", "price": get_product_price("mega_ya")},
            "mega_wb": {"name": "Meg@ WB", "price": get_product_price("mega_wb")},
        }
        
        # Проверяем кастомные товары
        custom_products = []
        for category in ["logs", "soft", "accounts"]:
            custom_products.extend(get_custom_products_by_category(category))
        
        for custom_type, custom_name, custom_price in custom_products:
            if custom_type == product_type:
                product_info[product_type] = {"name": custom_name, "price": custom_price}
                break
        
        product_data = product_info.get(product_type, {"name": "Товар", "price": 1.0})
        
        context.user_data['selected_product'] = product_type
        context.user_data['awaiting_quantity'] = True
        
        available_count = get_available_logs_count(product_type)
        
        await query.edit_message_text(
            f"📦 Вы выбрали: {product_data['name']}\n\n"
            f"💵 Цена: ${product_data['price']} за 1 шт.\n"
            f"📊 Всего - {available_count} шт.\n\n"
            f"🔢 Введите количество логов:"
        )
        
    except Exception as e:
        ErrorHandler.log_error("PRODUCT_SELECTION_ERROR", e, user.id)
        await query.answer("❌ Ошибка при выборе товара!", show_alert=True)

async def handle_balance_payment_check(query, context, user):
    """Обработка проверки платежа"""
    try:
        logger.info(f"Checking balance payment for user {user.id}")
        balance_invoice = get_balance_invoice_by_user(user.id)
        
        if not balance_invoice:
            logger.info(f"No balance invoice found for user {user.id}")
            await query.answer("❌ Нет активных счетов на пополнение", show_alert=True)
            return

        logger.info(f"Found balance invoice: {balance_invoice}")
        
        async with CryptoBotAPI(CRYPTO_BOT_TOKEN) as crypto_bot:
            status = await crypto_bot.check_invoice(balance_invoice[2])
        
        logger.info(f"Invoice status: {status}")
        
        if status == 'paid':
            await process_successful_payment(query, context, user, balance_invoice)
        elif status == 'active':
            await query.answer("❌ Счет еще не оплачен", show_alert=True)
        else:
            await query.answer("❌ Счет не оплачен или отменен", show_alert=True)
            update_balance_invoice_status(balance_invoice[2], 'expired')
            
    except Exception as e:
        ErrorHandler.log_error("BALANCE_PAYMENT_CHECK_ERROR", e, user.id)
        await query.answer("❌ Ошибка при проверке платежа", show_alert=True)

async def process_successful_payment(query, context, user, balance_invoice):
    """Обработка успешного платежа"""
    try:
        await query.answer("✅ Оплата подтверждена!", show_alert=True)
        
        new_balance = update_user_balance(user.id, balance_invoice[3])
        update_balance_invoice_status(balance_invoice[2], 'paid')
        
        success_message = (
            f"✅ Баланс пополнен на ${balance_invoice[3]}!\n\n"
            f"💰 Теперь на вашем балансе: ${new_balance}"
        )
        
        if hasattr(query, 'edit_message_text'):
            await query.edit_message_text(success_message)
        else:
            await safe_send_message(context.bot, user.id, success_message)
        
        await notify_admin(
            context.bot, user.id, user.username, 
            "Пополнение баланса", balance_invoice[3], 1
        )
        
    except Exception as e:
        ErrorHandler.log_error("PAYMENT_PROCESSING_ERROR", e, user.id, f"Amount: {balance_invoice[3]}")
        await query.answer("❌ Ошибка обработки платежа", show_alert=True)

# Обработчики рулетки
async def handle_roulette_command(update, context, user):
    """Обработка команды рулетки"""
    try:
        can_spin = can_spin_roulette_today(user.id)
        current_discount = get_user_discount(user.id)
        todays_discount = get_todays_discount(user.id)
        
        if can_spin:
            message_text = (
                "🎡 *Ежедневная рулетка удачи!*\n\n"
                "Крутите колесо и получайте случайную скидку на 24 часа!\n\n"
                "🎁 *Возможные выигрыши:*\n"
                "• 1% скидка - 40% шанс\n"
                "• 2% скидка - 25% шанс\n"  
                "• 3% скидка - 15% шанс\n"
                "• 5% скидка - 10% шанс\n"
                "• 7% скидка - 6% шанс\n"
                "• 10% скидка - 4% шанс\n\n"
                "✨ *Удача на вашей стороне!*"
            )
        else:
            if current_discount > 0 and todays_discount:
                expires_at = todays_discount[1]
                message_text = (
                    f"🎡 *Ежедневная рулетка*\n\n"
                    f"✅ *У вас активная скидка!*\n\n"
                    f"🎁 Размер скидки: *{current_discount}%*\n"
                    f"⏰ Действует до: *{expires_at[:16]}*\n\n"
                    f"✨ Скидка автоматически применяется к покупкам!\n"
                    f"🛍️ Успейте воспользоваться!"
                )
            else:
                last_spins = get_last_roulette_spins(user.id, 1)
                last_discount = last_spins[0][0] if last_spins else 0
                
                message_text = (
                    "🎡 *Ежедневная рулетка*\n\n"
                    "❌ Вы уже крутили рулетку сегодня!\n\n"
                    f"🎁 Ваш вчерашний выигрыш: *{last_discount}%* скидки\n\n"
                    "🕐 Возвращайтесь завтра для новой попытки!"
                )
        
        await safe_send_message(
            context.bot, user.id,
            message_text,
            reply_markup=roulette_keyboard(),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        ErrorHandler.log_error("ROULETTE_COMMAND_ERROR", e, user.id)

async def handle_spin_roulette(query, context, user):
    """Обработка крутки рулетки - УПРОЩЕННАЯ ВЕРСИЯ"""
    try:
        logger.info(f"=== START ROULETTE SPIN FOR USER {user.id} ===")
        
        # Проверяем, может ли пользователь крутить сегодня
        if not can_spin_roulette_today(user.id):
            await query.answer("❌ Вы уже крутили рулетку сегодня! Приходите завтра.", show_alert=True)
            logger.info(f"User {user.id} already spun today")
            return

        logger.info(f"User {user.id} can spin roulette today")
        
        # Простая анимация - сразу переходим к результату
        await query.edit_message_text("🎡 Крутим колесо...")
        await asyncio.sleep(2)
        
        # Определяем выигрыш
        discount_won = spin_roulette()
        logger.info(f"User {user.id} won: {discount_won}% discount")
        
        # Сохраняем результат
        save_success = save_roulette_spin(user.id, discount_won)
        
        if save_success:
            # Устанавливаем скидку
            add_user_discount(user.id, discount_won)
            
            # Формируем сообщение о победе
            win_message = (
                f"🎉 *ПОЗДРАВЛЯЕМ!*\n\n"
                f"🎁 Вы выиграли: *{discount_won}% скидки*\n\n"
                f"⏰ Скидка действует: *24 часа*\n"
                f"✨ Применяется автоматически ко всем покупкам!\n\n"
            )
            
            # Добавляем эмоции
            if discount_won >= 10:
                win_message += "🎊 *Вам сегодня невероятно везет!* 🎊"
            elif discount_won >= 7:
                win_message += "🔥 *Отличный результат!* 🔥"
            elif discount_won >= 5:
                win_message += "⭐ *Хорошая удача!* ⭐"
            else:
                win_message += "🙂 *Удача на вашей стороне!*"
            
            await query.edit_message_text(
                win_message,
                reply_markup=roulette_keyboard(),
                parse_mode='Markdown'
            )
            
            logger.info(f"SUCCESS: Roulette completed for user {user.id} with {discount_won}%")
            
        else:
            error_msg = "❌ Ошибка при сохранении результата. Попробуйте позже."
            await query.edit_message_text(
                error_msg,
                reply_markup=roulette_keyboard()
            )
            logger.error(f"FAILED: Could not save roulette for user {user.id}")
            
    except Exception as e:
        logger.error(f"CRITICAL ERROR in roulette: {str(e)}")
        ErrorHandler.log_error("SPIN_ROULETTE_ERROR", e, user.id)
        
        try:
            await query.answer("❌ Ошибка при крутке рулетки!", show_alert=True)
            await query.edit_message_text(
                "❌ Произошла ошибка. Попробуйте позже.",
                reply_markup=roulette_keyboard()
            )
        except Exception as inner_e:
            logger.error(f"Could not send error message: {inner_e}")

async def handle_roulette_history(query, context, user):
    """Обработка просмотра истории рулетки"""
    try:
        spins = get_last_roulette_spins(user.id, 10)
        current_discount = get_user_discount(user.id)
        todays_discount = get_todays_discount(user.id)
        
        if not spins:
            history_text = "📊 *История рулетки*\n\nУ вас еще не было выигрышей!"
        else:
            history_text = "📊 *Последние выигрыши:*\n\n"
            
            for i, (discount, date) in enumerate(spins, 1):
                status = "✅ АКТИВНА" if i == 1 and current_discount > 0 else "⏰ истекла"
                history_text += f"{i}. {date}: *{discount}%* - {status}\n"
        
        if current_discount > 0 and todays_discount:
            expires_at = todays_discount[1]
            history_text += f"\n🎁 *Текущая скидка: {current_discount}%*\n"
            history_text += f"⏰ Действует до: {expires_at[:16]}"
        else:
            history_text += f"\n❌ *Активных скидок нет*"
            
        total_won = sum(spin[0] for spin in spins)
        history_text += f"\n\n💎 Всего выиграно: *{total_won}%* скидки"
        
        await query.edit_message_text(
            history_text,
            reply_markup=roulette_keyboard(),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        ErrorHandler.log_error("ROULETTE_HISTORY_ERROR", e, user.id)
        await query.answer("❌ Ошибка при загрузке истории!", show_alert=True)

async def handle_back_to_main(query, context, user):
    """Обработка возврата в главное меню"""
    try:
        await query.edit_message_text(
            get_welcome_message(user),
            reply_markup=main_keyboard()
        )
    except Exception as e:
        ErrorHandler.log_error("BACK_TO_MAIN_ERROR", e, user.id)

# Обработчики управления пользователями
async def handle_users_page(query, context, user, data):
    """Обработка переключения страниц пользователей"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    page = int(data.split('_')[2])
    users_count = len(get_all_users())
    
    await query.edit_message_text(
        f"👥 Управление пользователями\n\nВсего пользователей: {users_count}\nСтраница {page + 1}\n\nВыберите пользователя:",
        reply_markup=users_list_keyboard(page)
    )

async def handle_users_list(query, context, user):
    """Обработка возврата к списку пользователей"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    users_count = len(get_all_users())
    await query.edit_message_text(
        f"👥 Управление пользователями\n\nВсего пользователей: {users_count}\n\nВыберите пользователя:",
        reply_markup=users_list_keyboard()
    )

async def handle_user_detail(query, context, user, data):
    """Обработка просмотра деталей пользователя"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    user_info = execute_db_query(
        'SELECT username, balance, subscribed, joined_at FROM users WHERE user_id = ?',
        (target_user_id,), fetch=True
    )
    
    if not user_info:
        await query.answer("❌ Пользователь не найден!", show_alert=True)
        return
        
    username, balance, subscribed, joined_at = user_info
    discount = get_user_discount(target_user_id)
    is_banned = is_user_banned(target_user_id)
    
    user_detail_text = (
        f"👤 Детали пользователя\n\n"
        f"🆔 ID: {target_user_id}\n"
        f"📛 Username: @{username if username else 'Не указан'}\n"
        f"💰 Баланс: ${balance}\n"
        f"🎁 Скидка: {discount}%\n"
        f"📅 Зарегистрирован: {joined_at[:16]}\n"
        f"✅ Подписка: {'Да' if subscribed else 'Нет'}\n"
        f"🚫 Статус: {'Забанен' if is_banned else 'Активен'}"
    )
    
    await query.edit_message_text(
        user_detail_text,
        reply_markup=user_detail_keyboard(target_user_id)
    )

async def handle_give_discount(query, context, user, data):
    """Обработка выдачи скидки"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    current_discount = get_user_discount(target_user_id)
    
    await query.edit_message_text(
        f"🎁 Выдача скидки пользователю {target_user_id}\n\nТекущая скидка: {current_discount}%\n\nВыберите размер скидки:",
        reply_markup=discount_keyboard(target_user_id)
    )

async def handle_set_discount(query, context, user, data):
    """Обработка установки скидки"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    parts = data.split('_')
    target_user_id = int(parts[3])
    discount_percent = int(parts[4])
    
    if add_user_discount(target_user_id, discount_percent):
        log_user_action(user.id, target_user_id, "set_discount", f"Установлена скидка {discount_percent}%")
        await query.answer(f"✅ Скидка {discount_percent}% установлена!", show_alert=True)
        
        try:
            await safe_send_message(
                context.bot, target_user_id,
                f"🎉 Вам выдана скидка {discount_percent}% на все товары!\n\nСпасибо за вашу лояльность! ❤️"
            )
        except Exception as e:
            ErrorHandler.log_error("DISCOUNT_NOTIFICATION_ERROR", e, target_user_id)
        
        await handle_user_detail(query, context, user, f"user_detail_{target_user_id}")
    else:
        await query.answer("❌ Ошибка при установке скидки!", show_alert=True)

async def handle_reset_discount(query, context, user, data):
    """Обработка сброса скидки"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    
    if add_user_discount(target_user_id, 0):
        log_user_action(user.id, target_user_id, "reset_discount", "Скидка сброшена")
        await query.answer("✅ Скидка сброшена!", show_alert=True)
        await handle_user_detail(query, context, user, f"user_detail_{target_user_id}")
    else:
        await query.answer("❌ Ошибка при сбросе скидки!", show_alert=True)

async def handle_ban_user(query, context, user, data):
    """Обработка бана пользователя"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    
    if ban_user(target_user_id):
        log_user_action(user.id, target_user_id, "ban", "Пользователь забанен")
        await query.answer("✅ Пользователь забанен!", show_alert=True)
        
        try:
            await safe_send_message(
                context.bot, target_user_id,
                "🚫 Ваш аккаунт был заблокирован администратором.\n\n"
                "По вопросам разблокировки обращайтесь в поддержку."
            )
        except Exception as e:
            ErrorHandler.log_error("BAN_NOTIFICATION_ERROR", e, target_user_id)
        
        await handle_user_detail(query, context, user, f"user_detail_{target_user_id}")
    else:
        await query.answer("❌ Ошибка при бане пользователя!", show_alert=True)

async def handle_unban_user(query, context, user, data):
    """Обработка разбана пользователя"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    
    if unban_user(target_user_id):
        log_user_action(user.id, target_user_id, "unban", "Пользователь разбанен")
        await query.answer("✅ Пользователь разбанен!", show_alert=True)
        
        try:
            await safe_send_message(
                context.bot, target_user_id,
                "✅ Ваш аккаунт был разблокирован администратором.\n\n"
                "Добро пожаловать обратно! 🎉"
            )
        except Exception as e:
            ErrorHandler.log_error("UNBAN_NOTIFICATION_ERROR", e, target_user_id)
        
        await handle_user_detail(query, context, user, f"user_detail_{target_user_id}")
    else:
        await query.answer("❌ Ошибка при разбане пользователя!", show_alert=True)

async def handle_user_history(query, context, user, data):
    """Обработка просмотра истории покупок"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    history = get_user_purchase_history(target_user_id)
    
    if not history:
        history_text = "📊 История покупок пуста"
    else:
        history_text = "📊 История покупок:\n\n"
        total_spent = 0
        for purchase in history:
            date, product_name, quantity, total_price = purchase
            history_text += f"📅 {date[:16]}\n"
            history_text += f"🛍️ {product_name} x{quantity}\n"
            history_text += f"💵 ${total_price}\n\n"
            total_spent += total_price
        
        history_text += f"💎 Всего потрачено: ${total_spent}"
    
    await query.edit_message_text(
        history_text,
        reply_markup=user_detail_keyboard(target_user_id)
    )

async def handle_user_balance(query, context, user, data):
    """Обработка просмотра баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    balance = get_user_balance(target_user_id)
    
    await query.edit_message_text(
        f"💰 Баланс пользователя {target_user_id}: ${balance}",
        reply_markup=user_detail_keyboard(target_user_id)
    )

async def handle_edit_balance(query, context, user, data):
    """Обработка изменения баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    current_balance = get_user_balance(target_user_id)
    
    user_info = execute_db_query(
        'SELECT username FROM users WHERE user_id = ?',
        (target_user_id,), fetch=True
    )
    username = user_info[0] if user_info else "Неизвестно"
    
    await query.edit_message_text(
        f"💳 Изменение баланса пользователя\n\n"
        f"👤 Пользователь: @{username} (ID: {target_user_id})\n"
        f"💰 Текущий баланс: ${current_balance}\n\n"
        f"Выберите действие:",
        reply_markup=balance_edit_keyboard(target_user_id)
    )

async def handle_add_balance(query, context, user, data):
    """Обработка пополнения баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    context.user_data['target_user_id'] = target_user_id
    context.user_data['awaiting_balance_add_amount'] = True
    
    await query.edit_message_text(
        f"💵 Пополнение баланса пользователя {target_user_id}\n\n"
        f"Введите сумму для пополнения:"
    )

async def handle_subtract_balance(query, context, user, data):
    """Обработка списания баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    context.user_data['target_user_id'] = target_user_id
    context.user_data['awaiting_balance_subtract_amount'] = True
    
    await query.edit_message_text(
        f"💵 Списание баланса пользователя {target_user_id}\n\n"
        f"Введите сумму для списания:"
    )

async def handle_set_balance(query, context, user, data):
    """Обработка установки баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    context.user_data['target_user_id'] = target_user_id
    context.user_data['awaiting_balance_set_amount'] = True
    
    await query.edit_message_text(
        f"💵 Установка баланса пользователя {target_user_id}\n\n"
        f"Введите новую сумму баланса:"
    )

async def handle_reset_balance(query, context, user, data):
    """Обработка обнуления баланса"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    
    new_balance = set_user_balance(target_user_id, 0)
    
    await query.answer("✅ Баланс обнулен!", show_alert=True)
    await handle_user_detail(query, context, user, f"user_detail_{target_user_id}")

# Обработчики изменения цен
async def handle_products_page(query, context, user, data):
    """Обработка переключения страниц товаров"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    page = int(data.split('_')[2])
    await show_products_for_price_edit(context.bot, user.id, page)

async def handle_edit_price(query, context, user, data):
    """Обработка изменения цены товара"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    product_type = data.split('_', 2)[2]
    
    # Получаем информацию о товаре
    product_info = None
    current_price = get_product_price(product_type)
    
    # Проверяем базовые товары
    if product_type in BASE_PRODUCTS:
        product_info = BASE_PRODUCTS[product_type]
    else:
        # Проверяем кастомные товары
        custom_info = get_custom_product_info(product_type)
        if custom_info:
            product_info = {
                "name": custom_info[0],
                "price": custom_info[1],
                "category": custom_info[2]
            }
    
    if not product_info:
        await query.answer("❌ Товар не найден!", show_alert=True)
        return
    
    context.user_data['product_type_for_edit'] = product_type
    context.user_data['awaiting_product_price_edit'] = True
    
    await query.edit_message_text(
        f"💰 Изменение цены товара\n\n"
        f"📦 Товар: {product_info['name']}\n"
        f"📁 Категория: {product_info['category']}\n"
        f"💰 Текущая цена: ${current_price}\n\n"
        f"Введите новую цену ($):"
    )

async def handle_message_user(query, context, user, data):
    """Обработка отправки сообщения пользователю"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    target_user_id = int(data.split('_')[2])
    context.user_data['message_target_user'] = target_user_id
    context.user_data['awaiting_user_message'] = True
    
    await query.edit_message_text(
        f"✉️ Введите сообщение для пользователя {target_user_id}:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_detail_{target_user_id}")]
        ])
    )

async def handle_back_to_admin(query, context, user):
    """Обработка возврата в админку"""
    if user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
        
    await query.edit_message_text(
        "⚙️ Панель администратора:",
        reply_markup=admin_keyboard()
    )

# Остальные существующие функции
async def deliver_content(bot, user_id: int, product_type: str, quantity: int):
    """Доставка контента"""
    try:
        logs = get_random_logs(product_type, quantity)
        if not logs:
            await handle_out_of_stock(bot, user_id, product_type)
            return False
        
        # Получаем информацию о товаре
        product_info = {
            # Папка Мt$
            "mts_gu_valid_pass": "Мt$ GU Valid PA$$",
            "mts_ya": "Мt$ YA", 
            "mts_wb": "Мt$ WB",
            
            # Папка T2 $мeнa
            "t2_mena_ya": "$мeнa YA",
            "t2_mena_wb": "$мeнa WB",
            "t2_mena_valid_pass_kazan": "$мeнa Valid PA$$ Kазань",
            "t2_mena_valid_pass_nizhny": "$мeнa Valid PA$$ Нижегoродская", 
            "t2_mena_valid_pass_spb": "$мeнa Valid PA$$ СПБ",
            
            # Папка Meg@
            "mega_gu_valid_pass": "Meg@ GU Valid PA$$",
            "mega_ya": "Meg@ YA",
            "mega_wb": "Meg@ WB",
        }
        
        # Проверяем кастомные товары
        custom_products = []
        for category in ["logs", "soft", "accounts"]:
            custom_products.extend(get_custom_products_by_category(category))
        
        for custom_type, custom_name, custom_price in custom_products:
            if custom_type == product_type:
                product_info[product_type] = custom_name
                break
        
        product_name = product_info.get(product_type, "товара")
        
        if quantity == 1:
            message = f"🎁 Ваш лог {product_name}:\n\n{logs[0]}\n\nСпасибо за покупку! ❤️"
        else:
            logs_text = "\n".join([f"{log}" for log in logs])
            message = f"🎁 Ваши {quantity} логов {product_name}:\n\n{logs_text}\n\nСпасибо за покупку! ❤️"
        
        success = await safe_send_message(bot, user_id, message)
        return success
        
    except Exception as e:
        ErrorHandler.log_error("CONTENT_DELIVERY_ERROR", e, user_id, f"Product: {product_type}, Quantity: {quantity}")
        return False

async def handle_out_of_stock(bot, user_id, product_type):
    """Обработка ситуации, когда товара нет в наличии"""
    try:
        await safe_send_message(
            bot, user_id,
            f"❌ Извините, товар временно отсутствует в наличии!\n\n"
            f"Попробуйте позже или выберите другой товар."
        )
    except Exception as e:
        ErrorHandler.log_error("OUT_OF_STOCK_ERROR", e, user_id)

async def main():
    """Основная функция запуска бота"""
    try:
        # Установка обработчиков сигналов
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info("Запуск инициализации базы данных...")
        if not init_db():
            logger.critical("Не удалось инициализировать базу данных!")
            return
        
        logger.info("Инициализация цен базовых товаров...")
        init_base_prices()
        
        logger.info("Создание приложения Telegram...")
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Добавление обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("balance", balance_command))
        application.add_handler(CommandHandler("forceroulette", force_roulette_command))
        application.add_handler(CommandHandler("checkroulette", check_roulette_command))
        
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(handle_callback))
        
        logger.info("Бот запускается...")
        await application.run_polling()
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")
        ErrorHandler.log_error("MAIN_CRITICAL_ERROR", e)

if __name__ == '__main__':
    asyncio.run(main())
