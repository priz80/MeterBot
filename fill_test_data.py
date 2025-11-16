import sqlite3
import random
from datetime import datetime, date

# === НАСТРОЙКИ ===
DB_NAME = "my_meter.db"

# Начальные показания счётчиков (на 2023-01-01)
INITIAL_VALUES = {
    "electricity": 500.0,   # кВт·ч
    "water": 10.0,          # м³
    "gas": 5.0              # м³
}

# Ежемесячный прирост: (минимум, максимум)
GROWTH_RATES = {
    "electricity": (30, 120),  # кВт·ч
    "water": (1.5, 4.0),       # м³
    "gas": (8, 25)             # м³ — можно сделать сезонным (см. ниже)
}

# Период генерации
START_DATE = date(2023, 1, 1)
END_DATE = date(2025, 11, 1)


# === ФУНКЦИЯ: ЗАПОЛНЕНИЕ ТЕСТОВЫМИ ДАННЫМИ ===
def fill_test_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 🧹 Очищаем таблицы
    cursor.execute("DELETE FROM electricity")
    cursor.execute("DELETE FROM water")
    cursor.execute("DELETE FROM gas")
    print("🧹 Старые данные удалены из всех таблиц.")

    # Текущие показания
    current_value = INITIAL_VALUES.copy()

    # Начало генерации
    current = START_DATE
    print(f"📅 Генерация данных с {START_DATE} по {END_DATE}...")

    while current <= END_DATE:
        date_str = current.strftime("%Y-%m-%d")

        for table in ["electricity", "water", "gas"]:
            # Сезонный коэффициент для газа и электричества (зимой больше)
            month = current.month
            seasonal_factor = 1.0
            if table == "gas":
                # Зимой (декабрь-февраль) — больше потребление
                if month in [12, 1, 2]:
                    seasonal_factor = 1.8
                elif month in [3, 4, 11]:
                    seasonal_factor = 1.3
                elif month in [5, 6, 7, 8, 9, 10]:
                    seasonal_factor = 1.0
            elif table == "electricity":
                # Зимой и летом — больше (отопление и кондиционирование)
                if month in [12, 1, 2]:
                    seasonal_factor = 1.4  # зимой света больше
                elif month in [6, 7, 8]:
                    seasonal_factor = 1.3  # летом — кондиционеры
                else:
                    seasonal_factor = 1.0

            # Генерация прироста с учётом сезона
            base_min, base_max = GROWTH_RATES[table]
            growth = random.uniform(base_min, base_max) * seasonal_factor
            current_value[table] += growth

            # Округление
            meter_value = round(current_value[table], 2)

            # Вставка
            cursor.execute(
                f"INSERT INTO {table} (meter, date) VALUES (?, ?)",
                (meter_value, date_str)
            )
            print(f"✅ {table:12} → {meter_value:8} | Дата: {date_str}")

        # Переход к следующему месяцу
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # Сохранение и закрытие
    conn.commit()
    conn.close()
    print("🎉 Тестовые данные за период 2023–2025 успешно сгенерированы.")


# === ЗАПУСК ===
if __name__ == "__main__":
    fill_test_data()
