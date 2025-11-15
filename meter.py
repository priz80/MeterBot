import sqlite3
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')  # Важно: без GUI
import matplotlib.pyplot as plt
import os
import pandas as pd
import csv
from pytz import timezone
from flask import Flask, render_template_string
import threading
import socket
import json

# === НАСТРОЙКИ ===
BOT_TOKEN = '8124340268:AAGgA3BOlHHVecnM7vDw66Hx_XvGj_N6Jtc'  # ← Замените
bot = telebot.TeleBot(BOT_TOKEN)

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
active_users = set()
remind_skipped = {}
meter_read = 0

# === ОПРЕДЕЛЕНИЕ ЛОКАЛЬНОГО IP ===
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ===
def init_db():
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    for table in ["electricity", "water", "gas"]:
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

# === ОСНОВНОЕ МЕНЮ ===
@bot.message_handler(commands=['start'])
def start_message(message):
    user_id = message.from_user.id
    if user_id not in active_users:
        active_users.add(user_id)
        remind_skipped[user_id] = False
        print(f"✅ Пользователь добавлен: {user_id}")

    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = telebot.types.KeyboardButton("⚡ Электричество")
    btn2 = telebot.types.KeyboardButton("💧 Вода")
    btn3 = telebot.types.KeyboardButton("🔥 Газ")
    btn4 = telebot.types.KeyboardButton("📋 История")
    btn5 = telebot.types.KeyboardButton("📊 График")
    btn6 = telebot.types.KeyboardButton("📈 Диаграмма")
    btn7 = telebot.types.KeyboardButton("📤 Экспорт CSV")
    btn8 = telebot.types.KeyboardButton("📆 Статистика")
    btn9 = telebot.types.KeyboardButton("🌐 Веб-статистика")

    keyboard.row(btn1, btn2, btn3)
    keyboard.row(btn4, btn5)
    keyboard.row(btn6, btn7)
    keyboard.row(btn8)
    keyboard.row(btn9)

    bot.send_message(user_id, "Выберите действие:", reply_markup=keyboard)

# === ПРОВЕРКА: ВВЕДЕНЫ ЛИ ДАННЫЕ ЗА МЕСЯЦ ===
def has_user_entered_current_month_data(user_id):
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    cursor.execute("SELECT date('now', 'start of month')")
    first_day = cursor.fetchone()[0]
    tables = ["electricity", "water", "gas"]
    entered = False
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE date >= ?", (first_day,))
        if cursor.fetchone()[0] > 0:
            entered = True
            break
    conn.close()
    return entered

# === ВВОД ПОКАЗАНИЙ ===
@bot.message_handler(func=lambda message: message.text in ["⚡ Электричество", "💧 Вода", "🔥 Газ"])
def handle_meter_input(message):
    global meter_read
    if message.text == "⚡ Электричество": meter_read = 0
    elif message.text == "💧 Вода": meter_read = 1
    elif message.text == "🔥 Газ": meter_read = 2

    resource = message.text.split()[1]
    bot.send_message(message.from_user.id, f"Введите показания счётчика {resource.lower()}:")
    bot.register_next_step_handler(message, get_meter)

def get_meter(message):
    user_id = message.from_user.id
    try:
        enter_meter = float(message.text)
    except ValueError:
        bot.send_message(user_id, "❌ Ошибка: введите число!")
        start_message(message)
        return

    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    table_names = ["electricity", "water", "gas"]
    table = table_names[meter_read]

    cursor.execute(f'INSERT INTO {table} (meter, date) VALUES (?, date("now"))', (enter_meter,))
    conn.commit()
    conn.close()

    bot.send_message(user_id, f"✅ Сохранено: {enter_meter}")
    start_message(message)

# === ИСТОРИЯ ===
@bot.message_handler(func=lambda message: message.text == "📋 История")
def show_history(message):
    user_id = message.from_user.id
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    tables = [("electricity", "Электричество"), ("water", "Вода"), ("gas", "Газ")]

    for table, name in tables:
        cursor.execute(f"SELECT id, meter, date FROM {table} ORDER BY id DESC LIMIT 3")
        rows = cursor.fetchall()
        if rows:
            response = f"\n📊 {name}:\n"
            meters = [(r[0], r[1], r[2]) for r in rows]
            meters.reverse()
            for i in range(len(meters)):
                id_curr, meter_curr, date_curr = meters[i]
                if i > 0:
                    try:
                        diff = float(meter_curr) - float(meters[i-1][1])
                        response += f"  {meter_curr} | {date_curr} | +{diff:.2f} ⬆️\n"
                    except:
                        response += f"  {meter_curr} | {date_curr} | (ошибка)\n"
                else:
                    response += f"  {meter_curr} | {date_curr} | (первое)\n"
        else:
            response = f"\n📌 {name}: Нет данных\n"
        bot.send_message(user_id, response.strip())
    conn.close()
    start_message(message)

# === ЛИНЕЙНЫЙ ГРАФИК ===
@bot.message_handler(func=lambda message: message.text == "📊 График")
def show_graph(message):
    user_id = message.from_user.id
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    tables = [("electricity", "⚡ Электричество", "blue"), ("water", "💧 Вода", "green"), ("gas", "🔥 Газ", "red")]
    has_data = False
    plt.figure(figsize=(10, 6))

    for table, name, color in tables:
        cursor.execute(f"SELECT meter, date FROM {table} ORDER BY date ASC")
        rows = cursor.fetchall()
        x_data, y_data = [], []
        for meter, date_str in rows:
            try:
                y_data.append(float(meter))
                x_data.append(date_str)
            except (ValueError, TypeError):
                continue
        if y_data:
            has_data = True
            plt.plot(x_data, y_data, marker='o', label=name, color=color)

    if has_data:
        plt.title("📈 Показания по датам")
        plt.xlabel("Дата")
        plt.ylabel("Показания")
        plt.legend()
        plt.xticks(rotation=45)
        plt.tight_layout()
        path = "graph.png"
        plt.savefig(path)
        plt.close()
        with open(path, 'rb') as photo:
            bot.send_photo(user_id, photo, "📊 Динамика")
        os.remove(path)
    else:
        bot.send_message(user_id, "Нет данных для графика.")
    conn.close()
    start_message(message)

# === СТОЛБЧАТАЯ ДИАГРАММА (исправленная) ===
@bot.message_handler(func=lambda message: message.text == "📈 Диаграмма")
def bar_chart(message):
    user_id = message.from_user.id
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    tables = [("electricity", "⚡ Электричество"), ("water", "💧 Вода"), ("gas", "🔥 Газ")]

    monthly_consumption = {}

    for table, name in tables:
        cursor.execute(f"SELECT meter, date FROM {table} ORDER BY date ASC")
        rows = cursor.fetchall()
        monthly_values = {}
        for meter, date_str in rows:
            try:
                meter = float(meter)
                year_month = date_str[:7]
                if year_month not in monthly_values:
                    monthly_values[year_month] = []
                monthly_values[year_month].append(meter)
            except (ValueError, TypeError):
                continue
        consumption = {}
        for month, meters in monthly_values.items():
            if len(meters) >= 2:
                consumption[month] = meters[-1] - meters[0]
            elif len(meters) == 1:
                consumption[month] = 0
        monthly_consumption[table] = consumption

    conn.close()

    all_months = set()
    for data in monthly_consumption.values():
        all_months.update(data.keys())

    if not all_months:
        bot.send_message(user_id, "❌ Нет данных для диаграммы.")
        start_message(message)
        return

    sorted_months = sorted(all_months)
    x_pos = range(len(sorted_months))
    colors = {"electricity": "#3498db", "water": "#2ecc71", "gas": "#e74c3c"}
    offsets = [-0.25, 0, 0.25]
    width = 0.25

    plt.figure(figsize=(10, 6))
    for idx, (table, name) in enumerate(tables):
        values = [monthly_consumption[table].get(m, 0) for m in sorted_months]
        if any(v > 0 for v in values):
            plt.bar([x + offsets[idx] for x in x_pos], values, width, label=name, color=colors[table], alpha=0.8, edgecolor='black')

    plt.xlabel("Месяц")
    plt.ylabel("Потребление")
    plt.title("📊 Потребление по месяцам")
    plt.xticks([x + width for x in x_pos], sorted_months, rotation=45)
    plt.legend()
    plt.grid(axis='y', alpha=0.3)
    plt.tight_layout()

    path = "bar_chart.png"
    try:
        plt.savefig(path)
        plt.close()
        with open(path, 'rb') as photo:
            bot.send_photo(user_id, photo, "📈 Потребление по месяцам")
        os.remove(path)
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка графика: {e}")
        plt.close()
        if os.path.exists(path):
            os.remove(path)

    start_message(message)

# === СТАТИСТИКА ===
@bot.message_handler(func=lambda message: message.text == "📆 Статистика")
def monthly_stats(message):
    user_id = message.from_user.id
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    tables = [("electricity", "⚡ Электричество"), ("water", "💧 Вода"), ("gas", "🔥 Газ")]

    for table, name in tables:
        cursor.execute(f"SELECT meter, date FROM {table} ORDER BY date ASC")
        rows = cursor.fetchall()
        monthly = {}
        for meter, date_str in rows:
            try:
                meter = float(meter)
                year_month = date_str[:7]
                if year_month not in monthly:
                    monthly[year_month] = []
                monthly[year_month].append(meter)
            except:
                continue

        if not monthly:
            bot.send_message(user_id, f"{name}: Нет данных.")
            continue

        response = f"\n📈 {name} — по месяцам:\n"
        for month in sorted(monthly.keys()):
            values = monthly[month]
            if len(values) >= 2:
                consumed = round(values[-1] - values[0], 2)
                response += f"  {month}: {values[0]:.2f} → {values[-1]:.2f} = {consumed}\n"
            else:
                response += f"  {month}: {values[0]:.2f} → ? = ?\n"
        bot.send_message(user_id, response)
    conn.close()
    start_message(message)

# === ВЕБ-СТАТИСТИКА ===
@bot.message_handler(func=lambda message: message.text == "🌐 Веб-статистика")
def send_web_link(message):
    user_id = message.from_user.id
    local_ip = get_local_ip()
    url = f"http://{local_ip}:5000"

    # Создаём кнопку с прямой ссылкой
    keyboard = telebot.types.InlineKeyboardMarkup()
    btn = telebot.types.InlineKeyboardButton("🚀 Открыть веб-панель", url=url)
    keyboard.add(btn)

    text = (
        "📊 **Веб-статистика**\n\n"
        "Нажмите кнопку ниже, чтобы открыть графики и данные напрямую в браузере.\n\n"
        f"🔗 Адрес: `{url}`"
    )

    bot.send_message(
        user_id,
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True  # чтобы Telegram не показывал превью под сообщением
    )
    start_message(message)  # возвращаем меню

# === УВЕДОМЛЕНИЯ ===
scheduler = BackgroundScheduler(timezone=timezone('Europe/Moscow'))
scheduler.start()

def send_monthly_reminder():
    for user_id in list(active_users):
        if has_user_entered_current_month_data(user_id) or remind_skipped.get(user_id, False):
            continue
        try:
            keyboard = telebot.types.InlineKeyboardMarkup()
            btn_t = telebot.types.InlineKeyboardButton("⏰ Напомнить завтра", callback_data="remind_tomorrow")
            btn_d = telebot.types.InlineKeyboardButton("✅ Уже ввёл", callback_data="remind_done")
            keyboard.add(btn_t, btn_d)
            bot.send_message(user_id, "📢 Пора ввести показания!", reply_markup=keyboard)
        except Exception as e:
            print(f"❌ Ошибка {user_id}: {e}")
            if "blocked" in str(e).lower():
                active_users.discard(user_id)
                remind_skipped.pop(user_id, None)

@bot.callback_query_handler(func=lambda call: call.data == "remind_tomorrow")
def remind_tomorrow(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id, "Напомню завтра!")
    bot.edit_message_text("⏰ Напомню завтра!", call.message.chat.id, call.message.message_id)
    tomorrow = datetime.now() + timedelta(days=1)
    scheduler.add_job(lambda: send_remind_message_to_user(user_id), 'date', run_date=tomorrow.replace(hour=9, minute=0), timezone=timezone('Europe/Moscow'))

def send_remind_message_to_user(user_id):
    if user_id not in active_users or has_user_entered_current_month_data(user_id) or remind_skipped.get(user_id, False):
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

# === ВЕБ-ИНТЕРФЕЙС ===
web_app = Flask(__name__)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 Счётчики</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 20px; background: #f5f7fa; }
        h1 { color: #2c3e50; text-align: center; font-size: 1.8rem; }
        .graph { width: 100%; height: 250px; margin: 20px 0; background: white; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 10px; }
        .table-container { margin: 30px 0; background: white; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        th, td { border: 1px solid #ddd; padding: 12px 10px; text-align: left; }
        th { background-color: #f0f4f8; color: #2c3e50; }
        @media (max-width: 600px) {
            h1 { font-size: 1.5rem; }
            .graph { height: 220px; }
            th, td { padding: 8px; font-size: 0.85rem; }
        }
    </style>
</head>
<body>
    <h1>📊 Мониторинг счётчиков</h1>
    <div id="graph-electricity" class="graph"></div>
    <div id="graph-water" class="graph"></div>
    <div id="graph-gas" class="graph"></div>
    <div class="table-container">
        <h2 style="margin: 15px; color: #2c3e50;">📋 Последние показания</h2>
        <table>
            <tr><th>Ресурс</th><th>Показание</th><th>Дата</th></tr>
            {% for row in data %}<tr><td>{{ row[0] }}</td><td><strong>{{ row[1] }}</strong></td><td>{{ row[2] }}</td></tr>{% endfor %}
        </table>
    </div>
    <script>{{ script | safe }}</script>
</body>
</html>
'''

def get_data_for_web():
    conn = sqlite3.connect("my_meter.db")
    cursor = conn.cursor()
    tables = [("electricity", "⚡ Электричество"), ("water", "💧 Вода"), ("gas", "🔥 Газ")]
    data = []
    plots = ""

    for table, name in tables:
        cursor.execute(f"SELECT meter, date FROM {table} ORDER BY date ASC")
        rows = cursor.fetchall()
        x_data, y_data = [], []
        for meter, date_str in rows:
            try:
                y_data.append(float(meter))
                x_data.append(date_str)
            except (ValueError, TypeError):
                continue
        if y_data:
            last_meter, last_date = y_data[-1], rows[-1][1]
            x_json, y_json = json.dumps(x_data), json.dumps(y_data)
            color = 'blue' if table == 'electricity' else 'green' if table == 'water' else 'red'
            plots += f'''
            Plotly.plot("graph-{table}", [
                {{ x: {x_json}, y: {y_json}, mode: 'lines+markers', name: '{name}', line: {{color: '{color}'}} }}
            ], {{ title: '{name}' }});
            '''
        else:
            last_meter, last_date = "Нет данных", "-"
        data.append((name, last_meter, last_date))

    conn.close()
    return data, plots

@web_app.route('/')
def index():
    data, plots = get_data_for_web()
    return render_template_string(HTML_TEMPLATE, data=data, script=plots)

def run_web():
    web_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# === ЗАПУСК ===
if __name__ == '__main__':
    scheduler.add_job(send_monthly_reminder, 'cron', day=1, hour=9, minute=0)
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()
    print("✅ Веб-интерфейс: http://localhost:5000")
    print("✅ Бот запущен. Готов к работе.")
    bot.polling(none_stop=True)
