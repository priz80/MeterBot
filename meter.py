import sqlite3
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from pytz import timezone
import atexit
import time

# === НАСТРОЙКИ ===
BOT_TOKEN = 'xxx'  # ← Замените на свой
bot = telebot.TeleBot(BOT_TOKEN)

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
active_users = set()          # Активные пользователи (в памяти)
remind_skipped = {}           # Кто отложил напоминание

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

    # Таблицы для ресурсов
    for config in RESOURCES.values():
        table = config["table"]
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter REAL NOT NULL,
                date TEXT NOT NULL
            )
        ''')

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            active BOOLEAN DEFAULT 1,
            remind_skipped BOOLEAN DEFAULT 0
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ База данных инициализирована.")

# === ЗАГРУЗКА ПОЛЬЗОВАТЕЛЕЙ ИЗ БД ===
def load_active_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, remind_skipped FROM users WHERE active = 1")
    rows = cursor.fetchall()
    for user_id, skipped in rows:
        active_users.add(user_id)
        remind_skipped[user_id] = bool(skipped)
    conn.close()
    print(f"📥 Загружено {len(active_users)} активных пользователей.")

init_db()
load_active_users()

# === ОТПРАВКА МЕНЮ ===
def send_menu(user_id):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = telebot.types.KeyboardButton("⚡ Электричество")
    btn2 = telebot.types.KeyboardButton("💧 Вода")
    btn3 = telebot.types.KeyboardButton("🔥 Газ")
    btn4 = telebot.types.KeyboardButton("📆 Статистика")
    keyboard.row(btn1, btn2, btn3)
    keyboard.row(btn4)
    try:
        bot.send_message(user_id, "Выберите действие:", reply_markup=keyboard)
    except:
        pass

# === ОБРАБОТКА /start ===
@bot.message_handler(commands=['start'])
def start_message(message):
    user_id = message.from_user.id

    # Сохраняем/активируем пользователя в БД
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT OR REPLACE INTO users (user_id, active, remind_skipped) VALUES (?, 1, COALESCE((SELECT remind_skipped FROM users WHERE user_id = ?), 0))',
        (user_id, user_id)
    )
    conn.commit()
    conn.close()

    # Обновляем память
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
        lines = [f"📋 {display_name}\n", "```\n"]
        lines.append(f"{'Дата':<12} {'Показания':<10} {'Объем':<8} {'Средн.':<8} {'Ед.':<5}\n")
        lines.append("-" * 50 + "\n")

        consumptions = []
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

            line = f"{date_str:<12} {reading:<10} {str(consumption):<8} {avg_str:<8} {unit:<5}"
            lines.append(line + "\n")

        lines.append("```\n")
        full_text = "".join(lines)
        try:
            bot.send_message(user_id, full_text, parse_mode="MarkdownV2")
        except Exception as e:
            bot.send_message(user_id, full_text.replace('\\', '') + f"\n\n(Ошибка форматирования: {str(e)})")

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

        except telebot.apihelper.ApiTelegramException as e:
            description = e.description.lower()
            if e.error_code == 403 or "blocked" in description:
                print(f"🚫 Пользователь {user_id} заблокировал бота. Деактивируем.")
                active_users.discard(user_id)
                remind_skipped.pop(user_id, None)
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET active = 0 WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
            elif e.error_code == 400 and "chat not found" in description:
                print(f"⚠️ Чат не найден (400) для {user_id}. Удаляем.")
                active_users.discard(user_id)
                remind_skipped.pop(user_id, None)
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET active = 0 WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
            else:
                print(f"❌ Ошибка при отправке {user_id}: {e}")
        except Exception as e:
            print(f"❌ Неизвестная ошибка: {e}")

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
    if (user_id not in active_users or
        has_user_entered_current_month_data() or
        remind_skipped.get(user_id, False)):
        return
    try:
        bot.send_message(user_id, "📢 Напоминание: пора ввести показания!")
    except Exception as e:
        print(f"❌ Ошибка при напоминании: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "remind_done")
def remind_done(call):
    user_id = call.from_user.id
    remind_skipped[user_id] = True
    bot.answer_callback_query(call.id, "Спасибо!")
    bot.edit_message_text("✅ Отлично! До следующего месяца.", call.message.chat.id, call.message.message_id)
    # Обновим в БД
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET remind_skipped = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# === ЗАПУСК ===
if __name__ == '__main__':
    # Ежемесячное напоминание: 1-го числа в 9:00
    scheduler.add_job(send_monthly_reminder, 'cron', day=1, hour=9, minute=0, timezone=timezone('Europe/Moscow'))
    print("✅ Бот запущен. Готов к работе.")

    atexit.register(lambda: scheduler.shutdown())

    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            print(f"❌ Ошибка polling: {e}")
            time.sleep(5)
