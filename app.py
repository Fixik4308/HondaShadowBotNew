"""
✅ HondaShadow Telegram бот
✅ PTB v21+, Python 3.13, повний робочий приклад для серверу
✅ Збереження даних у data.json, PIN, автозвіт, інтеграція з ESP32, OpenWeather, зручні меню
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta, time as dt_time
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackContext,
)

# ============== Конфігурація ==============
with open("config.json") as f:
    CONFIG = json.load(f)

TOKEN = CONFIG["TELEGRAM_TOKEN"]
PIN_CODE = CONFIG["PIN_CODE"]
OPENWEATHER_API_KEY = CONFIG["OPENWEATHER_API_KEY"]
CITY = CONFIG["CITY"]
ADMIN_CHAT_ID = int(CONFIG["ADMIN_CHAT_ID"])

DATA_FILE = "data.json"

# ============== Файл даних ==============
def load_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ============== Клавіатури ==============
def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["📊 Статус", "⛽ Залишок"],
            ["🛢 Заправився", "🌤 Погода"],
            ["⚙️ Управління", "🧰 ТО"]
        ],
        resize_keyboard=True
    )

def control_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔑 Увімкнути запалення", "🗝 Завести двигун"],
            ["🛑 Заглушити двигун", "🚫 Вимкнути запалення"],
            ["⬅️ Назад"]
        ],
        resize_keyboard=True
    )

def service_menu():
    return ReplyKeyboardMarkup(
        [
            ["ℹ️ Нагадування"],
            ["✅ Змастив цеп", "✅ Замінив масло"],
            ["⬅️ Назад"]
        ],
        resize_keyboard=True
    )

# ============== Обробники команд ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я твій HondaShadow бот 🚀 Обери команду:",
        reply_markup=main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/status - Повний статус\n"
        "/location - Координати\n"
        "/refuel 5 - Додати 5 л\n"
        "/service_oil_reset - Скинути масло\n"
        "/service_chain_reset - Скинути ланцюг\n"
        "/ignite PIN - Увімкнути запалення\n"
        "/starter PIN - Стартер\n"
        "/stop - Вимкнути все\n"
        "/help - Список команд"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    last_update = data.get("last_update")
    is_online = False
    if last_update:
        try:
            # Вважаємо online якщо update був < 70 сек тому
            last = datetime.fromisoformat(last_update)
            is_online = (datetime.utcnow() - last) < timedelta(seconds=70)
        except:
            pass
    offline = "✅ ESP32 ONLINE" if is_online else "❌ ESP32 OFFLINE"
    msg = (
        f"{offline}\n\n"
        f"🌡️ Двигун: {data.get('engine_temperature','н/д')} °C\n"
        f"🌤️ Повітря: {data.get('air_temperature','н/д')} °C\n"
        f"⛽ Залишок: {data.get('fuel_liters','н/д')} л\n"
        f"💧 Середня витрата: {data.get('average_consumption','н/д')} л/100км\n"
        f"🚀 Запас ходу: {data.get('range_km','н/д')} км\n"
        f"📍 Сесія: {data.get('session_distance','н/д')} км\n"
        f"🛣️ Загальний: {data.get('total_distance','н/д')} км\n"
        f"⏰ ТО - масло: {data.get('service_oil', 'н/д')} км, ланцюг: {data.get('service_chain', 'н/д')} км"
    )
    await update.message.reply_text(msg, reply_markup=main_menu())
    if data.get("latitude") and data.get("longitude"):
        await context.bot.send_location(
            chat_id=update.effective_chat.id,
            latitude=data["latitude"],
            longitude=data["longitude"]
        )

async def location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if data.get("latitude") and data.get("longitude"):
        await context.bot.send_location(
            chat_id=update.effective_chat.id,
            latitude=data["latitude"],
            longitude=data["longitude"]
        )
    else:
        await update.message.reply_text("❌ Немає координат.")

async def refuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Використай: /refuel 5")
        return
    try:
        liters = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Невірний формат!")
        return
    data = load_data()
    data["fuel_liters"] = data.get("fuel_liters", 0) + liters
    save_data(data)
    await update.message.reply_text(f"✅ Додано {liters} л!", reply_markup=main_menu())

async def ignite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0] != PIN_CODE:
        await update.message.reply_text("❌ Невірний PIN!")
        return
    data = load_data()
    data["last_command"] = "start_ignition"
    save_data(data)
    await update.message.reply_text("✅ Запалення увімкнено!", reply_markup=main_menu())

async def starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0] != PIN_CODE:
        await update.message.reply_text("❌ Невірний PIN!")
        return
    data = load_data()
    data["last_command"] = "start_starter"
    save_data(data)
    await update.message.reply_text("✅ Стартер увімкнено!", reply_markup=main_menu())

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["last_command"] = "stop_all"
    save_data(data)
    await update.message.reply_text("✅ Запалення і стартер вимкнено!", reply_markup=main_menu())

async def service_oil_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["service_oil"] = 0
    save_data(data)
    await update.message.reply_text("✅ Нагадування масла скинуто!", reply_markup=main_menu())

async def service_chain_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["service_chain"] = 0
    save_data(data)
    await update.message.reply_text("✅ Нагадування ланцюга скинуто!", reply_markup=main_menu())

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ua"
        res = requests.get(url)
        w = res.json()
        desc = w['weather'][0]['description']
        temp = w['main']['temp']
        await update.message.reply_text(f"🌤 Погода у {CITY}:\n{desc.capitalize()}\nТемпература: {temp} °C", reply_markup=main_menu())
    except Exception as e:
        await update.message.reply_text("❌ Не вдалося отримати прогноз!")

# ========== Обробка кнопок (Reply Keyboard) ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Статус":
        await status(update, context)
    elif text == "⛽ Залишок":
        data = load_data()
        await update.message.reply_text(f"⛽ Залишок пального: {data.get('fuel_liters','н/д')} л", reply_markup=main_menu())
    elif text == "🛢 Заправився":
        await update.message.reply_text("Введи кількість літрів (наприклад: 5.5):")
        context.user_data["waiting_refuel"] = True
    elif text == "🌤 Погода":
        await weather(update, context)
    elif text == "⚙️ Управління":
        await update.message.reply_text("Меню керування:", reply_markup=control_menu())
    elif text == "🧰 ТО":
        await update.message.reply_text("Меню ТО:", reply_markup=service_menu())
    elif text == "⬅️ Назад":
        await update.message.reply_text("Головне меню:", reply_markup=main_menu())
    elif text == "🔑 Увімкнути запалення":
        await update.message.reply_text("Введи PIN: /ignite <PIN>")
    elif text == "🗝 Завести двигун":
        await update.message.reply_text("Введи PIN: /starter <PIN>")
    elif text == "🛑 Заглушити двигун":
        await stop(update, context)
    elif text == "🚫 Вимкнути запалення":
        await stop(update, context)
    elif text == "ℹ️ Нагадування":
        data = load_data()
        await update.message.reply_text(
            f"Масло: {data.get('service_oil', 'н/д')} км\nЛанцюг: {data.get('service_chain', 'н/д')} км", 
            reply_markup=service_menu())
    elif text == "✅ Змастив цеп":
        data = load_data()
        data["service_chain"] = 0
        save_data(data)
        await update.message.reply_text("✅ Ланцюг змащено! Скинуто лічильник.", reply_markup=service_menu())
    elif text == "✅ Замінив масло":
        data = load_data()
        data["service_oil"] = 0
        save_data(data)
        await update.message.reply_text("✅ Масло замінено! Скинуто лічильник.", reply_markup=service_menu())
    else:
        await update.message.reply_text("Не розпізнано!", reply_markup=main_menu())

# Обробка вводу після "Заправився"
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_refuel"):
        try:
            liters = float(update.message.text.replace(",", "."))
            data = load_data()
            data["fuel_liters"] = data.get("fuel_liters", 0) + liters
            save_data(data)
            await update.message.reply_text(f"✅ Додано {liters} л!", reply_markup=main_menu())
        except Exception:
            await update.message.reply_text("❌ Введи число (наприклад 5.5)", reply_markup=main_menu())
        context.user_data["waiting_refuel"] = False
    else:
        await button_handler(update, context)

# ========== Автозвіт (щодня зранку) ==========
async def morning_report(context: CallbackContext):
    data = load_data()
    msg = (
        f"🌤 Погода: "
    )
    # Погода
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ua"
        res = requests.get(url)
        w = res.json()
        desc = w['weather'][0]['description']
        temp = w['main']['temp']
        msg += f"{desc.capitalize()}, {temp} °C\n"
    except:
        msg += "н/д\n"
    msg += (
        f"⛽ Залишок: {data.get('fuel_liters','н/д')} л\n"
        f"⏰ ТО: масло - {data.get('service_oil', 'н/д')} км, ланцюг - {data.get('service_chain', 'н/д')} км"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🚨 Ранковий автозвіт\n{msg}")

# ========== Основний цикл ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    # Команди
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("location", location))
    app.add_handler(CommandHandler("refuel", refuel))
    app.add_handler(CommandHandler("ignite", ignite))
    app.add_handler(CommandHandler("starter", starter))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("service_oil_reset", service_oil_reset))
    app.add_handler(CommandHandler("service_chain_reset", service_chain_reset))
    app.add_handler(CommandHandler("weather", weather))

    # Меню/кнопки
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Автозвіт: кожного ранку о 8:00 (UTC+3)
    app.job_queue.run_daily(
        morning_report,
        time=dt_time(hour=5, minute=0),  # 8:00 за Києвом = 5:00 UTC
        days=(0, 1, 2, 3, 4, 5, 6),
    )

    print("✅ Бот запущено!")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    import sys

    try:
        asyncio.run(main())
    except RuntimeError as e:
        # Хак для Render + PTB + Python 3.13
        if "already running" in str(e) or "Cannot close a running event loop" in str(e):
            loop = asyncio.get_event_loop()
            task = loop.create_task(main())
            loop.run_until_complete(task)
        else:
            print("RuntimeError:", e, file=sys.stderr)
            raise