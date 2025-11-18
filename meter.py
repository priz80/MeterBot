import sqlite3
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from pytz import timezone
import atexit
import time
import logging
import re  # Для экранирования MarkdownV2

# === ФУНКЦИЯ ЭКРАНИРОВАНИЯ ДЛЯ MARKDOWNV2 ===
def escape_markdown_v2(text):
    """Экранирует все спецсимволы для Telegram MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

# === ЛОГГИРОВАНИЕ (БЕЗ ЭМОДЗИ, С ПОДДЕРЖКОЙ UTF-8) ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# === НАСТРОЙКИ ===
BOT_TOKEN = 'xxx'  # ← Замените на свой
bot = telebot.TeleBot(BOT_TOKEN)

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
active_users = set()
remind_skipped = {}

# === КОНФИГУРАЦИЯ РЕСУРСОВ ===
RESOURCES = {
    "⚡ Электричество": {"table": "electricity", "unit": "кВт·ч"},
    "💧 Вода": {"table": "water", "unit": "м³"},
    "🔥 Газ": {"table": "gas", "unit": "м³"}
}

TABLE_TO_DISPLAY = {v["table"]: k for k, v in RESOURCES.items()}
ALLOWED_TABLES = {v["table"] for v in RESOURCES.values()}

# === РАБОТА С БАЗОЙ ===
def get_db():
    return sqlite3.connect("my_meter.db", timeout=10.0)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    for config in RESOURCES.values():
        table = config["table"]
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter REAL NOT NULL,
                date TEXT NOT NULL
            )
        ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            active BOOLEAN DEFAULT 1,
            remind_skipped BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("Database initialized.")

def load_active_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, remind_skipped FROM users WHERE active = 1")
    rows = cursor.fetchall()
    for user_id, skipped in rows:
        active_users.add(user_id)
        remind_skipped[user_id] = bool(skipped)
    conn.close()
    logging.info("Loaded %s active users.", len(active_users))

init_db()
load_active_users()

# === ДЕАКТИВАЦИЯ ПОЛЬЗОВАТЕЛЯ ===
def _deactivate_user(user_id):
    if user_id in active_users:
        active_users.discard(user_id)
    if user_id in remind_skipped:
        del remind_skipped[user_id]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info("User %s deactivated.", user_id)

# === ОТПРАВКА СООБЩЕНИЙ (С АВТО-ЭКРАНИРОВАНИЕМ) ===
def safe_send(user_id, text, parse_mode="MarkdownV2", **kwargs):
    """Отправляет сообщение с автоматическим экранированием для MarkdownV2"""
    if parse_mode == "MarkdownV2":
        text = escape_markdown_v2(text)
    try:
        bot.send_message(user_id, text, parse_mode=parse_mode, **kwargs)
    except telebot.apihelper.ApiTelegramException as e:
        if e.error_code == 400 and "chat not found" in e.description.lower():
            logging.warning("Chat not found (400) for user %s.", user_id)
            _deactivate_user(user_id)
        elif e.error_code == 403 and "blocked" in e.description.lower():
            logging.warning("User %s blocked the bot.", user_id)
            _deactivate_user(user_id)
        else:
            logging.error("Telegram API error for user %s: %s", user_id, e)
    except Exception as e:
        logging.error("Failed to send message to %s: %s", user_id, e)

# === ОТПРАВКА МЕНЮ ===
def send_menu(user_id):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = telebot.types.KeyboardButton("⚡ Электричество")
    btn2 = telebot.types.KeyboardButton("💧 Вода")
    btn3 = telebot.types.KeyboardButton("🔥 Газ")
    btn4 = telebot.types.KeyboardButton("📆 Статистика")
    keyboard.row(btn1, btn2, btn3)
    keyboard.row(btn4)
    safe_send(user_id, "Выберите действие:", reply_markup=keyboard)

# === /start ===
@bot.message_handler(commands=['start'])
def start_message(message):
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT OR REPLACE INTO users (user_id, active, remind_skipped) VALUES (?, 1, COALESCE((SELECT remind_skipped FROM users WHERE user_id = ?), 0))',
        (user_id, user_id)
    )
    conn.commit()
    conn.close()
    if user_id not in active_users:
        active_users.add(user_id)
        remind_skipped[user_id] = False
        logging.info("User %s added.", user_id)
    send_menu(user_id)

# === /help ===
@bot.message_handler(commands=['help'])
def help_message(message):
    text = (
        "📘 *Справка*\n\n"
        "Этот бот помогает вести учёт показаний счётчиков:\n"
        "• ⚡ Электричество\n"
        "• 💧 Вода\n"
        "• 🔥 Газ\n\n"
        "Каждый месяц 1-го числа вам придёт напоминание.\n\n"
        "Команды:\n"
        "• /start — главное меню\n"
        "• /help — эта справка\n"
        "• /cancel — отмена и возврат в меню"
    )
    safe_send(message.from_user.id, text, parse_mode="MarkdownV2")

@bot.message_handler(commands=['cancel'])
def cancel(message):
    send_menu(message.from_user.id)

# === ПРОВЕРКА: ВВЕДЕНЫ ЛИ ЗА МЕСЯЦ ===
def has_user_entered_current_month_data():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT date('now', 'start of month')")
    first_day = cursor.fetchone()[0]
    for config in RESOURCES.values():
        cursor.execute(f"SELECT 1 FROM {config['table']} WHERE date >= ? LIMIT 1", (first_day,))
        if cursor.fetchone():
            conn.close()
            return True
    conn.close()
    return False

# === ВВОД ПОКАЗАНИЙ ===
@bot.message_handler(func=lambda message: message.text in RESOURCES.keys())
def handle_meter_input(message):
    resource_key = message.text
    table = RESOURCES[resource_key]["table"]
    safe_send(message.from_user.id, f"Введите показания счётчика {resource_key.split()[1].lower()}:")
    bot.register_next_step_handler(message, lambda msg: save_meter_reading(msg, table))

def save_meter_reading(message, table):
    user_id = message.from_user.id
    try:
        meter_value = float(message.text)
    except ValueError:
        safe_send(user_id, "❌ Ошибка: введите корректное число!")
        send_menu(user_id)
        return

    if table not in ALLOWED_TABLES:
        safe_send(user_id, "❌ Недопустимый ресурс.")
        send_menu(user_id)
        return

    conn = get_db()
    cursor = conn.cursor()

    # Проверка: уже вводили сегодня?
    cursor.execute(f"SELECT 1 FROM {table} WHERE date = date('now') LIMIT 1")
    if cursor.fetchone():
        safe_send(user_id, "⚠️ Показания на сегодня уже внесены!")
        conn.close()
        send_menu(user_id)
        return

    # Получаем последнее значение
    cursor.execute(f"SELECT meter FROM {table} ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()

    # Проверка: не уменьшаются ли показания
    if row:
        prev_value = float(row[0])
        if meter_value < prev_value:
            prev_rounded = int(round(prev_value))
            current_rounded = int(round(meter_value))
            error_text = (
                f"⚠️ Ошибка ввода!\n"
                f"Показания не могут уменьшаться.\n"
                f"Предыдущее значение: {prev_rounded}\n"
                f"Вы ввели: {current_rounded}\n"
                f"Введите корректное значение."
            )
            safe_send(user_id, error_text)
            conn.close()
            resource_name = TABLE_TO_DISPLAY[table].split()[1].lower()
            safe_send(user_id, f"Введите показания счётчика {resource_name}:")
            bot.register_next_step_handler(message, lambda msg: save_meter_reading(msg, table))
            return

    # Сохраняем
    cursor.execute(f'INSERT INTO {table} (meter, date) VALUES (?, date("now"))', (meter_value,))
    conn.commit()
    conn.close()

    # Ответ пользователю
    display_name = TABLE_TO_DISPLAY[table]
    unit = RESOURCES[display_name]["unit"]
    rounded_value = int(round(meter_value))

    safe_send(user_id, f"✅ Показания сохранены: {rounded_value} {unit}")

    if row:
        consumption = meter_value - prev_value
        safe_send(user_id, f"💡 Расход с прошлого раза: {int(round(consumption))} {unit}")
    else:
        safe_send(user_id, "🆕 Это первое показание — нет данных для сравнения.")

    send_menu(user_id)

# === СТАТИСТИКА ===
@bot.message_handler(func=lambda message: message.text == "📆 Статистика")
def monthly_stats(message):
    user_id = message.from_user.id
    if user_id not in active_users:
        return

    conn = get_db()
    cursor = conn.cursor()

    for display_name, config in RESOURCES.items():
        table = config["table"]
        unit = config["unit"]
        cursor.execute(f"SELECT meter, date FROM {table} ORDER BY date ASC")
        rows = cursor.fetchall()

        data = []
        for row in rows:
            try:
                data.append((row[1], float(row[0])))
            except:
                continue

        if not data:
            safe_send(user_id, f"📋 {display_name}: нет данных.")
            continue

        data.sort(key=lambda x: x[0])
        lines = [f"📋 {display_name}\n", "```\n"]
        lines.append(f"{'Дата':<12} {'Показ.':<8} {'Расход':<8} {'Сред.':<8} {'Ед.':<5}\n")
        lines.append("-" * 50 + "\n")

        consumptions = []
        for i, (date_str, meter_val) in enumerate(data):
            reading = int(round(meter_val))
            if i == 0:
                consumption = "-"
                avg_str = "-"
            else:
                prev = data[i-1][1]
                current_consumption = int(round(meter_val - prev))
                consumption = current_consumption
                consumptions.append(current_consumption)
                avg = int(round(sum(consumptions) / len(consumptions))) if consumptions else 0
                avg_str = str(avg)
            lines.append(f"{date_str:<12} {reading:<8} {str(consumption):<8} {avg_str:<8} {unit:<5}\n")

        lines.append("```\n")
        safe_send(user_id, "".join(lines), parse_mode="MarkdownV2")

    conn.close()
    send_menu(user_id)

# === НАПОМИНАНИЯ ===
scheduler = BackgroundScheduler(timezone=timezone('Europe/Moscow'))
scheduler.start()

def send_monthly_reminder():
    now = datetime.now(timezone('Europe/Moscow'))
    if now.day == 1:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET remind_skipped = 0 WHERE active = 1")
        conn.commit()
        conn.close()
        for user_id in remind_skipped:
            remind_skipped[user_id] = False
        logging.info("Monthly reminder flags reset.")

    if has_user_entered_current_month_data():
        return

    for user_id in list(active_users):
        if remind_skipped.get(user_id, False):
            continue
        text = "📢 Пора ввести показания!"
        keyboard = telebot.types.InlineKeyboardMarkup()
        btn_t = telebot.types.InlineKeyboardButton("⏰ Напомнить завтра", callback_data="remind_tomorrow")
        btn_d = telebot.types.InlineKeyboardButton("✅ Уже ввёл", callback_data="remind_done")
        keyboard.add(btn_t, btn_d)
        safe_send(user_id, text, reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "remind_tomorrow")
def remind_tomorrow(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id, "Напомню завтра!")
    bot.edit_message_text("⏰ Напомню завтра!", call.message.chat.id, call.message.message_id)
    tomorrow = datetime.now(timezone('Europe/Moscow')) + timedelta(days=1)
    scheduler.add_job(
        lambda: send_remind_message_to_user(user_id),
        'date',
        run_date=tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    )

def send_remind_message_to_user(user_id):
    if user_id not in active_users or has_user_entered_current_month_data() or remind_skipped.get(user_id, False):
        return
    safe_send(user_id, "📢 Напоминание: пора ввести показания!")

@bot.callback_query_handler(func=lambda call: call.data == "remind_done")
def remind_done(call):
    user_id = call.from_user.id
    remind_skipped[user_id] = True
    bot.answer_callback_query(call.id, "Спасибо!")
    bot.edit_message_text("✅ Отлично! До следующего месяца.", call.message.chat.id, call.message.message_id)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET remind_skipped = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# === ЗАПУСК ===
if __name__ == '__main__':
    scheduler.add_job(send_monthly_reminder, 'cron', day=1, hour=9, minute=0, timezone=timezone('Europe/Moscow'))
    logging.info("Bot started. Awaiting messages.")
    atexit.register(lambda: scheduler.shutdown())
    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            logging.error("Polling error: %s", e)
            time.sleep(5)
