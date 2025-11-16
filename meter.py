import sqlite3
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from pytz import timezone
import atexit
import time

# === НАСТРОЙКИ ===
BOT_TOKEN = 'xxx'  # ← Замените
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

# Разрешённые таблицы
ALLOWED_TABLES = {v["table"] for v in RESOURCES.values()}

# === КОНТЕКСТ ДЛЯ БАЗЫ ДАННЫХ ===
def get_db():
    conn = sqlite3.connect("my_meter.db", timeout=10.0)
    return conn

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ===
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
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована.")

init_db()

# === ОТПРАВКА МЕНЮ ===
def send_menu(user_id):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = telebot.types.KeyboardButton("⚡ Электричество")
    btn2 = telebot.types.KeyboardButton("💧 Вода")
    btn3 = telebot.types.KeyboardButton("🔥 Газ")
    btn4 = telebot.types.KeyboardButton("📆 Статистика")

    keyboard.row(btn1, btn2, btn3)
    keyboard.row(btn4)

    bot.send_message(user_id, "Выберите действие:", reply_markup=keyboard)

# === ОБРАБОТКА /start ===
@bot.message_handler(commands=['start'])
def start_message(message):
    user_id = message.from_user.id
    if user_id not in active_users:
        active_users.add(user_id)
        remind_skipped[user_id] = False
        print(f"✅ Пользователь добавлен: {user_id}")
    send_menu(user_id)

# === ПРОВЕРКА: ВВЕДЕНЫ ЛИ ДАННЫЕ ЗА МЕСЯЦ ===
def has_user_entered_current_month_data():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT date('now', 'start of month')")
    first_day = cursor.fetchone()[0]
    for config in RESOURCES.values():
        table = config["table"]
        cursor.execute(f"SELECT 1 FROM {table} WHERE date >= ? LIMIT 1", (first_day,))
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
    bot.send_message(message.from_user.id, f"Введите показания счётчика {resource_key.split()[1].lower()}:")
    bot.register_next_step_handler(message, lambda msg: save_meter_reading(msg, table))

def save_meter_reading(message, table):
    user_id = message.from_user.id
    try:
        meter_value = float(message.text)
    except ValueError:
        bot.send_message(user_id, "❌ Ошибка: введите корректное число!")
        send_menu(message)
        return

    if table not in ALLOWED_TABLES:
        bot.send_message(user_id, "❌ Недопустимый ресурс.")
        send_menu(message)
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'INSERT INTO {table} (meter, date) VALUES (?, date("now"))', (meter_value,))
    conn.commit()
    conn.close()

    bot.send_message(user_id, f"✅ Сохранено: {meter_value}")
    send_menu(message)

# === СТАТИСТИКА ===
@bot.message_handler(func=lambda message: message.text == "📆 Статистика")
def monthly_stats(message):
    user_id = message.from_user.id

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
                meter_val = float(row[0])
                date_str = row[1]
                data.append((date_str, meter_val))
            except (ValueError, TypeError):
                continue

        if not data:
            bot.send_message(user_id, f"📋 {display_name}: нет данных.", parse_mode="MarkdownV2")
            continue

        data.sort(key=lambda x: x[0])
        lines = []
        lines.append(f"📋 {display_name}\n")
        lines.append("```\n")
        lines.append(f"{'Дата':<12} {'Показания':<10} {'Объем':<8} {'Средн.':<8} {'Ед.':<5}\n")
        lines.append("-" * 50 + "\n")

        consumptions = []  # Только числовые объёмы

        for i, (date_str, meter_val) in enumerate(data):
            reading = int(round(meter_val))
            if i == 0:
                consumption = "-"
                avg_str = "-"
            else:
                prev_meter = data[i - 1][1]
                current_consumption = int(round(meter_val - prev_meter))
                consumption = current_consumption
                consumptions.append(current_consumption)
                avg = int(round(sum(consumptions) / len(consumptions)))
                avg_str = str(avg)

            line = (
                f"{date_str:<12} "
                f"{reading:<10} "
                f"{str(consumption):<8} "
                f"{avg_str:<8} "
                f"{unit:<5}"
            )
            lines.append(line + "\n")

        lines.append("```\n")
        full_text = "".join(lines)
        try:
            bot.send_message(user_id, full_text, parse_mode="MarkdownV2")
        except Exception:
            bot.send_message(user_id, full_text.replace('\\', ''))

    conn.close()
    send_menu(message)

# === УВЕДОМЛЕНИЯ ===
scheduler = BackgroundScheduler(timezone=timezone('Europe/Moscow'))
scheduler.start()

def send_monthly_reminder():
    if has_user_entered_current_month_data():
        return
    for user_id in list(active_users):
        if remind_skipped.get(user_id, False):
            continue
        try:
            keyboard = telebot.types.InlineKeyboardMarkup()
            btn_t = telebot.types.InlineKeyboardButton("⏰ Напомнить завтра", callback_data="remind_tomorrow")
            btn_d = telebot.types.InlineKeyboardButton("✅ Уже ввёл", callback_data="remind_done")
            keyboard.add(btn_t, btn_d)
            bot.send_message(user_id, "📢 Пора ввести показания!", reply_markup=keyboard)
        except Exception as e:
            print(f"❌ Ошибка отправки {user_id}: {e}")
            if "blocked" in str(e).lower():
                active_users.discard(user_id)
                remind_skipped.pop(user_id, None)

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
    try:
        bot.send_message(user_id, "📢 Напоминание: пора ввести показания!")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "remind_done")
def remind_done(call):
    user_id = call.from_user.id
    remind_skipped[user_id] = True
    bot.answer_callback_query(call.id, "Спасибо!")
    bot.edit_message_text("✅ Отлично! До следующего месяца.", call.message.chat.id, call.message.message_id)

# === ЗАПУСК ===
if __name__ == '__main__':
    scheduler.add_job(send_monthly_reminder, 'cron', day=1, hour=9, minute=0, timezone=timezone('Europe/Moscow'))
    print("✅ Бот запущен. Готов к работе.")

    atexit.register(lambda: scheduler.shutdown())

    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            print(f"❌ Ошибка polling: {e}")
            time.sleep(5)
