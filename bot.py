# ============================
# bot.py
# ============================

"""
✅ Telegram-бот для HondaShadow ESP32
✅ Читає дані з data.json
✅ Відповідає навіть якщо ESP32 офлайн
✅ Керує ESP32 через команди
✅ Підтримує PIN
✅ Авто-звіт вранці
"""

import json
import requests
import asyncio
import logging
from datetime import datetime, time as dtime

from telegram import (
    ReplyKeyboardMarkup, KeyboardButton,
    Update, KeyboardButtonPollType
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# ============ ЗАГРУЗКА НАЛАШТУВАНЬ ============
with open("config.json") as f:
    CONFIG = json.load(f)

TELEGRAM_TOKEN = CONFIG["TELEGRAM_TOKEN"]
PIN_CODE = CONFIG["PIN_CODE"]
OPENWEATHER_API_KEY = CONFIG["OPENWEATHER_API_KEY"]
CITY = CONFIG["CITY"]
ADMIN_CHAT_ID = int(CONFIG["ADMIN_CHAT_ID"])

DATA_FILE = "data.json"

# ============ ФУНКЦІЇ ДАНИХ ============
def load_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ============ КНОПКИ ============
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Статус"), KeyboardButton("⛽ Залишок")],
        [KeyboardButton("🛢 Заправився"), KeyboardButton("🌤 Погода")],
        [KeyboardButton("⚙️ Управління"), KeyboardButton("🧰 ТО")]
    ], resize_keyboard=True)

def control_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔑 Увімкнути запалення"), KeyboardButton("🗝 Завести двигун")],
        [KeyboardButton("🛑 Заглушити двигун"), KeyboardButton("🚫 Вимкнути запалення")],
        [KeyboardButton("⬅️ Назад")]
    ], resize_keyboard=True)

def service_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Нагадування")],
        [KeyboardButton("✅ Змастив цеп"), KeyboardButton("✅ Замінив масло")],
        [KeyboardButton("⬅️ Назад")]
    ], resize_keyboard=True)

# ============ КОМАНДИ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю! Я HondaShadow ESP32 бот. Обери команду:",
        reply_markup=main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/status - Статус\n"
        "/location - Координати\n"
        "/refuel X - додати X літрів\n"
        "/service_oil_reset - скинути нагадування масла\n"
        "/service_chain_reset - скинути нагадування ланцюга\n"
        "/ignite - Увімкнути запалення (з PIN)\n"
        "/starter - Стартер (з PIN)\n"
        "/stop - Вимкнути запалення/стартер\n"
        "/help - цей список команд"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    offline = "✅ ESP32 ONLINE" if data.get("last_update") else "❌ ESP32 OFFLINE"
    msg = (
        f"{offline}\n\n"
        f"🌡️ Темп. двигуна: {data.get('engine_temperature','н/д')} °C\n"
        f"🌤️ Темп. повітря: {data.get('air_temperature','н/д')} °C\n"
        f"⛽ Залишок: {data.get('fuel_liters','н/д')} л\n"
        f"💧 Середня витрата: {data.get('average_consumption','н/д')} л/100км\n"
        f"🚀 Запас ходу: {data.get('range_km','н/д')} км\n"
        f"📍 Пробіг сесії: {data.get('session_distance','н/д')} км\n"
        f"🛣️ Загальний пробіг: {data.get('total_distance','н/д')} км"
    )
    await update.message.reply_text(msg, reply_markup=main_keyboard())

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
        await update.message.reply_text("Використай так: /refuel 5")
        return
    try:
        liters = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Невірний формат!")
        return

    data = load_data()
    data["fuel_liters"] = data.get("fuel_liters", 0) + liters
    save_data(data)
    await update.message.reply_text(f"✅ Додано {liters} л!", reply_markup=main_keyboard())

async def service_oil_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["service_oil"] = 0
    save_data(data)
    await update.message.reply_text("✅ Нагадування масла скинуто!", reply_markup=main_keyboard())

async def service_chain_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["service_chain"] = 0
    save_data(data)
    await update.message.reply_text("✅ Нагадування ланцюга скинуто!", reply_markup=main_keyboard())

async def ignite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0] != PIN_CODE:
        await update.message.reply_text("❌ Невірний PIN!")
        return
    data = load_data()
    data["last_command"] = "start_ignition"
    save_data(data)
    await update.message.reply_text("✅ Запалення увімкнено!", reply_markup=main_keyboard())

async def starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0] != PIN_CODE:
        await update.message.reply_text("❌ Невірний PIN!")
        return
    data = load_data()
    data["last_command"] = "start_starter"
    save_data(data)
    await update.message.reply_text("✅ Стартер увімкнено!", reply_markup=main_keyboard())

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["last_command"] = "stop_all"
    save_data(data)
    await update.message.reply_text("✅ Запалення/Стартер вимкнено!", reply_markup=main_keyboard())

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ua"
        res = requests.get(url)
        w = res.json()
        desc = w['weather'][0]['description']
        temp = w['main']['temp']
        await update.message.reply_text(f"🌤 Погода у {CITY}:\n{desc.capitalize()}\nТемпература: {temp} °C", reply_markup=main_keyboard())
    except:
        await update.message.reply_text("❌ Не вдалося отримати прогноз!")

# ============ АВТОЗВІТ ============
async def morning_report(app):
    while True:
        now = datetime.now()
        if now.hour == 7 and now.minute == 0:
            data = load_data()
            msg = (
                "🌅 Доброго ранку!\n"
                f"⛽ Залишок: {data.get('fuel_liters','н/д')} л\n"
                f"💧 Середня витрата: {data.get('average_consumption','н/д')} л/100км\n"
                "🧰 Нагадування ТО:\n"
                f" - Масло: {data.get('service_oil','н/д')} км\n"
                f" - Ланцюг: {data.get('service_chain','н/д')} км\n"
            )
            await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
            await asyncio.sleep(60)
        await asyncio.sleep(30)

# ============ ГОЛОВНИЙ ============
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("location", location))
    app.add_handler(CommandHandler("refuel", refuel))
    app.add_handler(CommandHandler("service_oil_reset", service_oil_reset))
    app.add_handler(CommandHandler("service_chain_reset", service_chain_reset))
    app.add_handler(CommandHandler("ignite", ignite))
    app.add_handler(CommandHandler("starter", starter))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("weather", weather))

    asyncio.create_task(morning_report(app))
    print("✅ Telegram-бот запущено!")
    await app.run_polling()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
