import sqlite3
import random
from datetime import datetime

# === НАСТРОЙКИ ===
DB_NAME = "my_meter.db"

# Начальные значения счётчиков
INITIAL = {
    "electricity": 1000.0,  # кВт·ч
    "water": 50.0,         # м³
    "gas": 100.0           # м³
}

# Месячное потребление в зависимости от сезона
# (мин, макс) для каждого ресурса
USAGE_BY_SEASON = {
    # Зима: ноябрь, декабрь, январь, февраль, март
    "winter": {
        "electricity": (300, 500),  # отопление, свет
        "gas": (25, 40),            # отопление
        "water": (4, 7)
    },
    # Весна: апрель, май, июнь
    "spring": {
        "electricity": (150, 250),
        "gas": (10, 20),
        "water": (5, 8)
    },
    # Лето: июль, август, сентябрь
    "summer": {
        "electricity": (200, 400),  # кондиционер
        "gas": (5, 15),             # почти не используется
        "water": (6, 10)            # душ, полив
    },
    # Осень: октябрь, ноябрь
    "autumn": {
        "electricity": (180, 300),
        "gas": (15, 25),
        "water": (4, 7)
    }
}

# Определяем сезон по месяцу
def get_season(month):
    if month in [11, 12, 1, 2, 3]:
        return "winter"
    elif month in [4, 5, 6]:
        return "spring"
    elif month in [7, 8, 9]:
        return "summer"
    else:  # 10
        return "autumn"

# === ОСНОВНОЙ СКРИПТ ===
def fill_test_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    now = datetime.now()
    current = datetime(now.year, now.month, 1)
    start_year = now.year - 3
    start = datetime(start_year, now.month, 1)

    tables = ["electricity", "water", "gas"]
    current_values = INITIAL.copy()

    print("🔄 Заполняем тестовыми данными с сезонностью...")

    date = start
    while date <= current:
        date_str = date.strftime("%Y-%m-%d")
        month = date.month
        season = get_season(month)

        for table in tables:
            # Берём диапазон по сезону
            low, high = USAGE_BY_SEASON[season][table]
            usage = round(random.uniform(low, high), 2)
            current_values[table] += usage

            # Вставляем
            cursor.execute(
                f"INSERT OR IGNORE INTO {table} (meter, date) VALUES (?, ?)",
                (round(current_values[table], 2), date_str)
            )
            print(f"✅ {date_str} | {table}: {current_values[table]:.2f} ({season})")

        # Следующий месяц
        if date.month == 12:
            date = date.replace(year=date.year + 1, month=1)
        else:
            date = date.replace(month=date.month + 1)

    conn.commit()
    conn.close()
    print("✅ Реалистичные тестовые данные добавлены.")

# === ЗАПУСК ===
if __name__ == "__main__":
    fill_test_data()
