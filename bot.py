import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
from config import BOT_TOKEN, ADMIN_ID
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import CallbackQuery
import requests
from config import CRYPTO_PAY_TOKEN


logging.basicConfig(level=logging.INFO)


bot = Bot(token=BOT_TOKEN)


def migrate_add_photo_id():
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    try:
        cursor.execute('ALTER TABLE products ADD COLUMN photo_id TEXT')
        conn.commit()
    except Exception:
        pass  
    conn.close()

migrate_add_photo_id()


class AddProduct(StatesGroup):
    name = State()
    description = State()
    price = State()
    type = State()
    category = State()
    link = State()
    photo = State()


class TopUpBalance(StatesGroup):
    amount = State()
    asset = State()
    invoice_id = State()


class CategoryManage(StatesGroup):
    action = State()
    add_name = State()
    rename_select = State()
    rename_new = State()
    delete_select = State()
    delete_confirm = State()


storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


def init_db():
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL,
            type TEXT,
            link TEXT,
            category_id INTEGER,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

    default_cats = ['Мануалы', 'Скрипты', 'Боты', 'Дрейнеры', 'Другое']
    for cat in default_cats:
        cursor.execute('INSERT OR IGNORE INTO categories (name) VALUES (?)', (cat,))
    conn.commit()
    conn.close()

init_db()


def get_main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('🛒 Категории Товаров 🛒')
    kb.add('👥 Профиль', '🚧 Прочее')
    return kb


def get_admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('➕ Добавить товар', '✏️ Редактировать товар')
    kb.add('🗑 Удалить товар', '📋 Список товаров')
    kb.add('Категории')
    kb.add('⬅️ Выйти в меню')
    return kb


def get_admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('➕ Добавить товар', '✏️ Редактировать товар')
    kb.add('🗑 Удалить товар', '📋 Список товаров')
    kb.add('Категории')
    kb.add('⬅️ Выйти в меню')
    return kb

@dp.message_handler(lambda m: m.text == 'Категории')
async def admin_categories_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('➕ Добавить', callback_data='cat_add'))
    ikb.add(InlineKeyboardButton('✏️ Переименовать', callback_data='cat_rename'))
    ikb.add(InlineKeyboardButton('🗑 Удалить', callback_data='cat_delete'))
    await message.answer('Управление категориями:', reply_markup=ikb)


@dp.callback_query_handler(lambda c: c.data == 'cat_add')
async def cat_add_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer('Введите название новой категории:')
    await CategoryManage.add_name.set()
    await call.answer()

@dp.message_handler(state=CategoryManage.add_name)
async def cat_add_save(message: types.Message, state: FSMContext):
    name = message.text.strip()
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM categories WHERE name = ?', (name,))
    if cursor.fetchone():
        await message.answer('Категория с таким названием уже существует!')
        conn.close()
        return
    cursor.execute('INSERT INTO categories (name) VALUES (?)', (name,))
    conn.commit()
    conn.close()
    await message.answer(f'✅ Категория "{name}" добавлена!', reply_markup=get_admin_menu())
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == 'cat_rename')
async def cat_rename_select(call: CallbackQuery, state: FSMContext):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM categories')
    cats = cursor.fetchall()
    conn.close()
    ikb = InlineKeyboardMarkup()
    for cid, name in cats:
        ikb.add(InlineKeyboardButton(name, callback_data=f'cat_rename_{cid}'))
    await call.message.answer('Выберите категорию для переименования:', reply_markup=ikb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('cat_rename_'))
async def cat_rename_new(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split('_')[-1])
    await state.update_data(rename_id=cat_id)
    await call.message.answer('Введите новое название категории:')
    await CategoryManage.rename_new.set()
    await call.answer()

@dp.message_handler(state=CategoryManage.rename_new)
async def cat_rename_save(message: types.Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    cat_id = data['rename_id']
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM categories WHERE name = ?', (new_name,))
    if cursor.fetchone():
        await message.answer('Категория с таким названием уже существует!')
        conn.close()
        return
    cursor.execute('UPDATE categories SET name = ? WHERE id = ?', (new_name, cat_id))
    conn.commit()
    conn.close()
    await message.answer(f'✅ Категория переименована в "{new_name}"', reply_markup=get_admin_menu())
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == 'cat_delete')
async def cat_delete_select(call: CallbackQuery, state: FSMContext):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM categories')
    cats = cursor.fetchall()
    conn.close()
    ikb = InlineKeyboardMarkup()
    for cid, name in cats:
        ikb.add(InlineKeyboardButton(name, callback_data=f'cat_delete_{cid}'))
    await call.message.answer('Выберите категорию для удаления:', reply_markup=ikb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('cat_delete_') and c.data.split('_')[-1].isdigit())
async def cat_delete_confirm(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split('_')[-1])
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('❗️ Подтвердить удаление', callback_data='cat_delete_confirm'))
    ikb.add(InlineKeyboardButton('Отмена', callback_data='cat_delete_cancel'))
    await state.update_data(delete_id=cat_id)
    await call.message.answer('Вы уверены, что хотите удалить эту категорию? Все товары из неё будут перенесены в "Другое".', reply_markup=ikb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == 'cat_delete_confirm')
async def cat_delete_do(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cat_id = data['delete_id']

    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM categories WHERE name = ?', ('Другое',))
    other = cursor.fetchone()
    if not other:
        cursor.execute('INSERT INTO categories (name) VALUES (?)', ('Другое',))
        conn.commit()
        cursor.execute('SELECT id FROM categories WHERE name = ?', ('Другое',))
        other = cursor.fetchone()
    other_id = other[0]

    cursor.execute('UPDATE products SET category_id = ? WHERE category_id = ?', (other_id, cat_id))
    cursor.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()
    await call.message.answer('✅ Категория удалена, товары перенесены в "Другое".', reply_markup=get_admin_menu())
    await state.finish()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == 'cat_delete_cancel')
async def cat_delete_cancel(call: CallbackQuery, state: FSMContext):
    await call.message.answer('Удаление категории отменено.', reply_markup=get_admin_menu())
    await state.finish()
    await call.answer()


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        '👋 Добро пожаловать в магазин!\nВыберите нужный раздел:',
        reply_markup=get_main_menu()
    )

@dp.message_handler(lambda m: m.text == '🏴‍☠️ Скуп Товара')
async def handle_skuptovar(message: types.Message):
    text = (
        '🏴‍☠️ Скупаю товар, который можно продавать в бесконечном количестве (мануалы, скрипты, боты, дрейеры и т.д.)\n\n'
        'Если у тебя есть что предложить — жми кнопку ниже!'
    )
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('💸 Продать товар', url='https://t.me/why_seven'))  # <-- Замени на свой username
    await message.answer(text, reply_markup=ikb)

@dp.message_handler(commands=['admin'])
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply('⛔️ Нет доступа')
    await message.answer('⚙️ Админ-панель', reply_markup=get_admin_menu())

@dp.message_handler(lambda m: m.text == '⬅️ Выйти в меню')
async def exit_to_menu(message: types.Message):
    await message.answer('Главное меню', reply_markup=get_main_menu())


@dp.message_handler(lambda m: m.text == '➕ Добавить товар')
async def add_product_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer('Введите название товара:')
    await AddProduct.name.set()

@dp.message_handler(state=AddProduct.name)
async def add_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer('Введите описание товара:')
    await AddProduct.next()

@dp.message_handler(state=AddProduct.description)
async def add_product_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer('Введите цену товара (число):')
    await AddProduct.next()

@dp.message_handler(state=AddProduct.price)
async def add_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer('Введите корректную цену (число):')
        return
    await state.update_data(price=price)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add('Бесконечный', 'Штучный')
    await message.answer('Тип товара?', reply_markup=kb)
    await AddProduct.next()


@dp.message_handler(state=AddProduct.type)
async def add_product_type(message: types.Message, state: FSMContext):
    if message.text not in ['Бесконечный', 'Штучный']:
        await message.answer('Выберите тип товара кнопкой!')
        return
    await state.update_data(type=message.text)

    await message.answer('Спасибо! Теперь выберите категорию товара:', reply_markup=types.ReplyKeyboardRemove())

    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM categories')
    cats = cursor.fetchall()
    conn.close()
    ikb = InlineKeyboardMarkup()
    for cat_id, cat_name in cats:
        ikb.add(InlineKeyboardButton(cat_name, callback_data=f'addcat_{cat_id}'))
    await message.answer('Выберите категорию товара:', reply_markup=ikb)
    await AddProduct.category.set()

@dp.callback_query_handler(lambda c: c.data.startswith('addcat_'), state=AddProduct.category)
async def add_product_category_inline(call: CallbackQuery, state: FSMContext):
    cat_id = int(call.data.split('_')[1])
    await state.update_data(category_id=cat_id)
    await call.message.edit_text('Ссылка/инструкция/файл (вставьте ссылку или текст):')
    await AddProduct.link.set()

@dp.message_handler(state=AddProduct.link)
async def add_product_link(message: types.Message, state: FSMContext):
    await state.update_data(link=message.text)
    await message.answer('Отправьте фото товара или напишите "Пропустить":')
    await AddProduct.photo.set()

@dp.message_handler(lambda m: m.text and m.text.lower() == 'пропустить', state=AddProduct.photo)
async def add_product_photo_skip(message: types.Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO products (name, description, price, type, link, category_id, photo_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                   (data['name'], data['description'], data['price'], data['type'], data['link'], data['category_id'], None))
    conn.commit()
    conn.close()
    await message.answer('✅ Товар добавлен!', reply_markup=get_admin_menu())
    await state.finish()

@dp.message_handler(content_types=['photo'], state=AddProduct.photo)
async def add_product_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    data = await state.get_data()
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO products (name, description, price, type, link, category_id, photo_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                   (data['name'], data['description'], data['price'], data['type'], data['link'], data['category_id'], photo_id))
    conn.commit()
    conn.close()
    await message.answer('✅ Товар добавлен!', reply_markup=get_admin_menu())
    await state.finish()


@dp.message_handler(lambda m: m.text == '🛒 Категории Товаров 🛒')
async def show_categories(message: types.Message):
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM categories')
    cats = cursor.fetchall()
    conn.close()
    ikb = InlineKeyboardMarkup()
    for cat_id, cat_name in cats:
        ikb.add(InlineKeyboardButton(cat_name, callback_data=f'cat_{cat_id}'))
    await message.answer('Выберите категорию:', reply_markup=ikb)


@dp.message_handler(lambda m: m.text == '💭 Правила')
async def show_rules(message: types.Message):
    rules_text = (
        '💭 <b>Правила магазина:</b>\n'
        '1. Все товары проверяются перед публикацией.\n'
        '2. Возврат средств возможен только при доказанном браке товара.\n'
        '3. Запрещено использовать магазин для мошенничества.\n'
        '4. Администрация оставляет за собой право отказать в обслуживании без объяснения причин.\n'
        '5. Покупая товар, вы соглашаетесь с этими правилами.'
    )
    await message.answer(rules_text, parse_mode='HTML')


@dp.message_handler(lambda m: m.text == '🚧 Прочее')
async def show_other(message: types.Message):
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('Чат поддержки', url='https://t.me/ByZetr1x'))
    await message.answer('🚧 <b>Полезные ссылки:</b>', parse_mode='HTML', reply_markup=ikb)


@dp.message_handler(lambda m: m.text == '👥 Профиль')
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    balance = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM purchases WHERE user_id = ?', (user_id,))
    purchases_count = cursor.fetchone()[0]
    conn.close()
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('Пополнить баланс', callback_data='topup_balance'))
    ikb.add(InlineKeyboardButton('История покупок', callback_data='purchase_history'))
    await message.answer(f'👤 Ваш профиль\n🆔 ID: <code>{user_id}</code>\n💰 Баланс: <b>{balance:.2f}₽</b>\n🛒 Покупок: <b>{purchases_count}</b>', parse_mode='HTML', reply_markup=ikb)


@dp.callback_query_handler(lambda c: c.data == 'purchase_history')
async def show_purchase_history(call: CallbackQuery):
    user_id = call.from_user.id
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.id, p.purchase_time, pr.name
        FROM purchases p
        JOIN products pr ON p.product_id = pr.id
        WHERE p.user_id = ?
        ORDER BY p.purchase_time DESC
        LIMIT 10
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await call.message.answer('У вас пока нет покупок.')
        await call.answer()
        return
    ikb = InlineKeyboardMarkup()
    for pid, dt, name in rows:
        label = f'📦 {name} | {dt[:16]}'
        ikb.add(InlineKeyboardButton(label, callback_data=f'buyhistory_{pid}'))
    await call.message.answer('🛒 <b>История покупок:</b>', parse_mode='HTML', reply_markup=ikb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('buyhistory_'))
async def resend_purchased_product(call: CallbackQuery):
    purchase_id = int(call.data.split('_')[1])
    user_id = call.from_user.id
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT pr.name, pr.description, pr.price, pr.type, pr.link, pr.photo_id
        FROM purchases p
        JOIN products pr ON p.product_id = pr.id
        WHERE p.id = ? AND p.user_id = ?
    ''', (purchase_id, user_id))
    product = cursor.fetchone()
    conn.close()
    if not product:
        await call.answer('Товар не найден или не куплен вами.', show_alert=True)
        return
    name, desc, price, typ, link, photo_id = product
    text = f'<b>{name}</b> | {price}₽ | {typ}\n{desc}\n\n{link}'
    if photo_id:
        await call.message.answer_photo(photo_id, caption=text, parse_mode='HTML')
    else:
        await call.message.answer(text, parse_mode='HTML')
    await call.answer('Товар выслан повторно!')


@dp.callback_query_handler(lambda c: c.data == 'topup_balance')
async def topup_balance_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer('Введите сумму для пополнения (в числовом формате):')
    await TopUpBalance.amount.set()
    await call.answer()

@dp.message_handler(state=TopUpBalance.amount)
async def topup_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError
    except Exception:
        await message.answer('Введите корректную сумму!')
        return
    await state.update_data(amount=amount)

    assets = ['TON', 'USDT', 'BTC', 'ETH', 'BUSD', 'TRX']
    ikb = InlineKeyboardMarkup()
    for asset in assets:
        ikb.add(InlineKeyboardButton(asset, callback_data=f'topup_asset_{asset}'))
    await message.answer('Выберите актив для пополнения:', reply_markup=ikb)
    await TopUpBalance.asset.set()


def get_asset_price_in_rub(asset):
    asset_map = {
        'TON': 'the-open-network',
        'USDT': 'tether',
        'BTC': 'bitcoin',
        'ETH': 'ethereum',
        'BUSD': 'binance-usd',
        'TRX': 'tron',
    }
    coingecko_id = asset_map.get(asset)
    if not coingecko_id:
        return None
    url = f'https://api.coingecko.com/api/v3/simple/price?ids={coingecko_id}&vs_currencies=rub'
    try:
        resp = requests.get(url, timeout=10)
        price = resp.json()[coingecko_id]['rub']
        return float(price)
    except Exception:
        return None

@dp.callback_query_handler(lambda c: c.data.startswith('topup_asset_'), state=TopUpBalance.asset)
async def topup_balance_asset(call: CallbackQuery, state: FSMContext):
    asset = call.data.split('_')[-1]
    data = await state.get_data()
    rub_amount = data['amount']

    price = get_asset_price_in_rub(asset)
    if not price:
        await call.message.answer('Не удалось получить курс актива. Попробуйте позже.')
        await state.finish()
        return
    asset_amount = round(rub_amount / price, 6)

    url = 'https://pay.crypt.bot/api/createInvoice'
    payload = {
        'asset': asset,
        'amount': asset_amount,
        'description': f'Пополнение баланса на {rub_amount}₽ для user_id {call.from_user.id}',
        'hidden_message': f'Пополнение баланса на {rub_amount}₽ для user_id {call.from_user.id}',
        'paid_btn_name': 'openBot',
        'paid_btn_url': f'https://t.me/{(await bot.me).username}'
    }
    headers = {'Crypto-Pay-API-Token': CRYPTO_PAY_TOKEN}
    resp = requests.post(url, json=payload, headers=headers)
    result = resp.json().get('result')
    if not result:
        await call.message.answer('Ошибка при создании инвойса. Попробуйте позже.')
        await state.finish()
        return
    pay_url = result['pay_url']
    invoice_id = result['invoice_id']
    await state.update_data(invoice_id=invoice_id, asset=asset, rub_amount=rub_amount)
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton('Оплатить', url=pay_url))
    ikb.add(InlineKeyboardButton('Проверить оплату', callback_data='check_invoice'))
    await call.message.answer(f'Сумма к оплате: <b>{asset_amount} {asset}</b> (≈ {rub_amount}₽)', parse_mode='HTML', reply_markup=ikb)
    await call.answer()
    await TopUpBalance.invoice_id.set()

@dp.callback_query_handler(lambda c: c.data == 'check_invoice', state=TopUpBalance.invoice_id)
async def check_invoice_status(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    invoice_id = data['invoice_id']
    rub_amount = data.get('rub_amount')
    url = f'https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}'
    headers = {'Crypto-Pay-API-Token': CRYPTO_PAY_TOKEN}
    resp = requests.get(url, headers=headers)

    result = resp.json().get('result', {})
    items = result.get('items', [])
    if not items or items[0].get('status') != 'paid':
        await call.answer('Платёж не найден или не оплачен.', show_alert=True)
        return

    user_id = call.from_user.id
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (rub_amount, user_id))
    conn.commit()
    conn.close()
    await call.message.answer(f'✅ Баланс пополнен на {rub_amount}₽!')
    await state.finish()
    await call.answer('Баланс пополнен!', show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def show_products_by_category_callback(call: CallbackQuery):
    cat_id = int(call.data.split('_')[1])
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM categories WHERE id = ?', (cat_id,))
    cat = cursor.fetchone()
    if not cat:
        await call.message.edit_text('Категория не найдена.')
        conn.close()
        return
    cat_name = cat[0]
    cursor.execute('SELECT id, name FROM products WHERE category_id = ?', (cat_id,))
    products = cursor.fetchall()
    conn.close()
    if not products:
        await call.message.edit_text('В этой категории пока нет товаров.')
        return
    ikb = InlineKeyboardMarkup()
    for pid, name in products:
        ikb.add(InlineKeyboardButton(name, callback_data=f'prod_{pid}'))
    await call.message.edit_text(f'🛒 <b>{cat_name}</b>\nВыберите товар:', parse_mode='HTML', reply_markup=ikb)
    await call.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('prod_'))
async def show_product_card(call: CallbackQuery):
    product_id = int(call.data.split('_')[1])
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, description, price, type, link, photo_id FROM products WHERE id = ?', (product_id,))
    product = cursor.fetchone()
    conn.close()
    if not product:
        await call.answer('Товар не найден.', show_alert=True)
        return
    name, desc, price, typ, link, photo_id = product
    ikb = InlineKeyboardMarkup()
    ikb.add(InlineKeyboardButton(f'Купить за {price}₽', callback_data=f'buy_{product_id}'))
    text = f'<b>{name}</b> | {price}₽ | {typ}\n{desc}\n\n{link}'
    if photo_id:
        await call.message.delete()
        await call.message.answer_photo(photo_id, caption=text, parse_mode='HTML', reply_markup=ikb)
    else:
        await call.message.edit_text(text, parse_mode='HTML', reply_markup=ikb)
    await call.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('buy_'))
async def buy_product(call: CallbackQuery):
    user_id = call.from_user.id
    product_id = int(call.data.split('_')[1])
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, link FROM products WHERE id = ?', (product_id,))
    product = cursor.fetchone()
    if not product:
        await call.answer('Товар не найден.', show_alert=True)
        conn.close()
        return
    name, price, link = product
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    balance = cursor.fetchone()[0]
    if balance < price:
        await call.answer('Недостаточно средств на балансе!', show_alert=True)
        conn.close()
        return

    cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (price, user_id))
    cursor.execute('INSERT INTO purchases (user_id, product_id) VALUES (?, ?)', (user_id, product_id))
    conn.commit()
    conn.close()
    await call.message.answer(f'✅ Покупка товара <b>{name}</b> успешна!\n\n{link}', parse_mode='HTML')
    await call.answer('Покупка успешна!', show_alert=True)


def get_all_category_names():
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM categories')
    cats = [row[0] for row in cursor.fetchall()]
    conn.close()
    return cats


@dp.message_handler(lambda m: m.text == '📋 Список товаров')
async def list_products(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, price, type FROM products')
    products = cursor.fetchall()
    conn.close()
    if not products:
        await message.answer('❌ Товаров нет.', reply_markup=get_admin_menu())
        return
    text = '📋 <b>Список товаров:</b>\n\n'
    for pid, name, price, ptype in products:
        text += f'ID: <code>{pid}</code> | <b>{name}</b> | {price}₽ | {ptype}\n'
    await message.answer(text, parse_mode='HTML', reply_markup=get_admin_menu())


@dp.message_handler(lambda m: m.text == '🗑 Удалить товар')
async def delete_product_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer('Введите ID товара для удаления (см. "Список товаров"):')
    await DeleteProduct.id.set()

class DeleteProduct(StatesGroup):
    id = State()

@dp.message_handler(state=DeleteProduct.id)
async def delete_product_confirm(message: types.Message, state: FSMContext):
    try:
        pid = int(message.text)
    except ValueError:
        await message.answer('Введите корректный ID товара (число):')
        return
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM products WHERE id = ?', (pid,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted:
        await message.answer('✅ Товар удалён.', reply_markup=get_admin_menu())
    else:
        await message.answer('❌ Товар с таким ID не найден.', reply_markup=get_admin_menu())
    await state.finish()


@dp.message_handler(lambda m: m.text == '✏️ Редактировать товар')
async def edit_product_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer('Введите ID товара для редактирования (см. "Список товаров"):')
    await EditProduct.id.set()

class EditProduct(StatesGroup):
    id = State()
    field = State()
    value = State()

@dp.message_handler(state=EditProduct.id)
async def edit_product_choose_field(message: types.Message, state: FSMContext):
    try:
        pid = int(message.text)
    except ValueError:
        await message.answer('Введите корректный ID товара (число):')
        return
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, description, price, type, link FROM products WHERE id = ?', (pid,))
    product = cursor.fetchone()
    conn.close()
    if not product:
        await message.answer('❌ Товар с таким ID не найден.', reply_markup=get_admin_menu())
        await state.finish()
        return
    await state.update_data(id=pid)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add('name', 'description', 'price', 'type', 'link')
    await message.answer('Что редактировать? (name, description, price, type, link)', reply_markup=kb)
    await EditProduct.next()

@dp.message_handler(state=EditProduct.field)
async def edit_product_new_value(message: types.Message, state: FSMContext):
    if message.text not in ['name', 'description', 'price', 'type', 'link']:
        await message.answer('Выберите поле кнопкой!')
        return
    await state.update_data(field=message.text)
    await message.answer('Введите новое значение:', reply_markup=types.ReplyKeyboardRemove())
    await EditProduct.next()

@dp.message_handler(state=EditProduct.value)
async def edit_product_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    pid = data['id']
    field = data['field']
    value = message.text
    if field == 'price':
        try:
            value = float(value.replace(',', '.'))
        except ValueError:
            await message.answer('Введите корректную цену (число):')
            return
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute(f'UPDATE products SET {field} = ? WHERE id = ?', (value, pid))
    conn.commit()
    conn.close()
    await message.answer('✅ Товар обновлён.', reply_markup=get_admin_menu())
    await state.finish()


@dp.message_handler(commands=['addbalance'])
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply('⛔️ Нет доступа')
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = float(amount.replace(',', '.'))
    except Exception:
        await message.reply('Используй: /addbalance user_id сумма')
        return
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    balance = cursor.fetchone()[0]
    conn.close()
    await message.reply(f'Баланс пользователя {user_id} пополнен. Новый баланс: {balance:.2f}₽')

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True) 