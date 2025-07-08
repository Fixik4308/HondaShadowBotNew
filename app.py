import os
import sqlite3
import json
import requests
import logging
import pytz
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

# ==========  CONFIG  ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7997519934:AAGuQH9UbjnTytxe9iGe5m53xXiwdImI8p0")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "587618394"))
ESP32_DEVICE_ID = os.getenv("ESP32_DEVICE_ID", "fixik4308")
MASTER_PIN = os.getenv("MASTER_PIN", "8748")
OPENWEATHER_API = os.getenv("OPENWEATHER_API", "1fc7aa0291a70d68f04424895faf1f5a")
TIMEZONE = os.getenv("TZ", "Europe/Kyiv")
DB_FILE = "hondashadow.db"

# ==========  FLASK APP ==========
app = Flask(__name__)
bot_app = None  # set later after Application init

# ==========  DATABASE  ==========

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Телеметрія
    c.execute('''
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            device_id TEXT,
            engine_temperature REAL,
            air_temperature REAL,
            latitude REAL,
            longitude REAL,
            fuel_pulses INTEGER,
            fuel_liters REAL
        )
    ''')
    # Налаштування (наприклад, ПІН, нагадування, пробіг)
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Команди для ESP32
    c.execute('''
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            command_type TEXT,
            value TEXT,
            executed INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def save_telemetry(data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO telemetry (
            device_id, engine_temperature, air_temperature,
            latitude, longitude, fuel_pulses, fuel_liters
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('device_id'), data.get('engine_temperature'),
        data.get('air_temperature'), data.get('latitude'),
        data.get('longitude'), data.get('fuel_pulses'),
        data.get('fuel_liters')
    ))
    conn.commit()
    conn.close()

def get_last_telemetry():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM telemetry ORDER BY id DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "timestamp": row[1],
        "device_id": row[2],
        "engine_temperature": row[3],
        "air_temperature": row[4],
        "latitude": row[5],
        "longitude": row[6],
        "fuel_pulses": row[7],
        "fuel_liters": row[8]
    }

def add_command(cmd_type, value=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO commands (device_id, command_type, value, executed)
        VALUES (?, ?, ?, 0)
    ''', (ESP32_DEVICE_ID, cmd_type, value))
    conn.commit()
    conn.close()

def get_unexecuted_commands():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        SELECT id, command_type, value FROM commands
        WHERE executed=0 AND device_id=?
    ''', (ESP32_DEVICE_ID,))
    rows = c.fetchall()
    conn.close()
    cmds = []
    for row in rows:
        cmds.append({
            "id": row[0],
            "command_type": row[1],
            "value": row[2]
        })
    return cmds

def ack_command(command_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE commands SET executed=1 WHERE id=?', (command_id,))
    conn.commit()
    conn.close()

def save_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

# ==========  WEATHER  ==========
def get_weather(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&lang=ua&appid={OPENWEATHER_API}"
        r = requests.get(url)
        w = r.json()
        return f"🌤 {w['weather'][0]['description'].capitalize()}, {w['main']['temp']}°C, Вологість: {w['main']['humidity']}%"
    except Exception as e:
        return "⚠️ Не вдалося отримати погоду."

# ==========  TELEGRAM BOT ==========

START_MENU = [
    [KeyboardButton("📊 Статус"), KeyboardButton("⛽ Залишок"), KeyboardButton("🛢 Заправився")],
    [KeyboardButton("🌤 Погода"), KeyboardButton("⚙️ Управління"), KeyboardButton("🧰 ТО")]
]
MANAGE_MENU = [
    [KeyboardButton("🔑 Увімкнути запалення"), KeyboardButton("🗝 Завести двигун")],
    [KeyboardButton("🛑 Заглушити двигун"), KeyboardButton("🚫 Вимкнути запалення")],
    [KeyboardButton("⬅️ Назад")]
]
SERVICE_MENU = [
    [KeyboardButton("ℹ️ Нагадування")],
    [KeyboardButton("✅ Змастив цеп"), KeyboardButton("✅ Замінив масло")],
    [KeyboardButton("⬅️ Назад")]
]

def make_status_text(data):
    if not data:
        return "❌ Дані ще не надійшли від пристрою."
    text = (
        f"📊 <b>СТАТУС МОТО:</b>\n"
        f"🛠 <b>Двигун:</b> {data['engine_temperature']}°C\n"
        f"🌡 <b>Повітря:</b> {data['air_temperature']}°C\n"
        f"⛽ <b>Залишок пального:</b> {data['fuel_liters']} л\n"
        f"🏁 <b>Координати:</b> {data['latitude']:.5f}, {data['longitude']:.5f}\n"
        f"📍 <b>Карта:</b> https://maps.google.com/?q={data['latitude']},{data['longitude']}"
    )
    return text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю! Я HondaShadow ESP32 бот.\nОбери команду:",
        reply_markup=ReplyKeyboardMarkup(START_MENU, resize_keyboard=True)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Доступні команди:\n"
        "/status — Вся інфо\n"
        "/location — Координати\n"
        "/refuel X — Додати X л\n"
        "/service_oil_reset — Скинути лічильник масла\n"
        "/service_chain_reset — Скинути лічильник цепу\n"
        "/ignite — Запалення (ПІН)\n"
        "/starter — Стартер (ПІН)\n"
        "/stop — Вимкнути все\n"
        "/help — Список команд"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_last_telemetry()
    await update.message.reply_html(make_status_text(data))

async def location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_last_telemetry()
    if not data:
        await update.message.reply_text("❌ Дані ще не надійшли.")
        return
    await update.message.reply_location(data['latitude'], data['longitude'])

async def refuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        liters = float(context.args[0])
        add_command("refuel", str(liters))
        await update.message.reply_text(f"✅ Заправка на {liters} л відправлена пристрою.")
    except Exception:
        await update.message.reply_text("❗️ Використання: /refuel 5")

async def ignite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введіть PIN для запуску запалення:")
    context.user_data['awaiting_pin'] = 'ignite'

async def starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введіть PIN для запуску стартера:")
    context.user_data['awaiting_pin'] = 'starter'

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_command("stop_ignition")
    add_command("stop_starter")
    await update.message.reply_text("✅ Відправлено: вимкнення запалення та стартера.")

async def service_oil_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_setting('oil_last_reset', datetime.now(pytz.timezone(TIMEZONE)).isoformat())
    await update.message.reply_text("✅ Лічильник масла скинуто!")

async def service_chain_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_setting('chain_last_reset', datetime.now(pytz.timezone(TIMEZONE)).isoformat())
    await update.message.reply_text("✅ Лічильник ланцюга скинуто!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Статус":
        await status(update, context)
    elif text == "⛽ Залишок":
        data = get_last_telemetry()
        if data:
            await update.message.reply_text(f"⛽ {data['fuel_liters']} л")
        else:
            await update.message.reply_text("❌ Дані ще не надійшли.")
    elif text == "🛢 Заправився":
        await update.message.reply_text("Введіть кількість літрів, наприклад: /refuel 5")
    elif text == "🌤 Погода":
        data = get_last_telemetry()
        if data:
            weather = get_weather(data['latitude'], data['longitude'])
            await update.message.reply_text(weather)
        else:
            await update.message.reply_text("❌ Дані ще не надійшли.")
    elif text == "⚙️ Управління":
        await update.message.reply_text("Меню керування:", reply_markup=ReplyKeyboardMarkup(MANAGE_MENU, resize_keyboard=True))
    elif text == "🧰 ТО":
        await update.message.reply_text("Меню ТО:", reply_markup=ReplyKeyboardMarkup(SERVICE_MENU, resize_keyboard=True))
    elif text == "⬅️ Назад":
        await update.message.reply_text("Повертаюся в головне меню.", reply_markup=ReplyKeyboardMarkup(START_MENU, resize_keyboard=True))
    elif text == "🔑 Увімкнути запалення":
        await ignite(update, context)
    elif text == "🗝 Завести двигун":
        await starter(update, context)
    elif text == "🛑 Заглушити двигун":
        await stop(update, context)
    elif text == "🚫 Вимкнути запалення":
        add_command("stop_ignition")
        await update.message.reply_text("✅ Запалення вимкнено.")
    elif text == "ℹ️ Нагадування":
        oil = get_setting('oil_last_reset', 'Ніколи')
        chain = get_setting('chain_last_reset', 'Ніколи')
        await update.message.reply_text(f"🛢 Остання заміна масла: {oil}\n🔗 Остання мастка ланцюга: {chain}")
    elif text == "✅ Змастив цеп":
        await service_chain_reset(update, context)
    elif text == "✅ Замінив масло":
        await service_oil_reset(update, context)
    else:
        if context.user_data.get('awaiting_pin'):
            pin_action = context.user_data.pop('awaiting_pin')
            if text.strip() == MASTER_PIN:
                if pin_action == 'ignite':
                    add_command("start_ignition", MASTER_PIN)
                    await update.message.reply_text("✅ Запалення ввімкнено!")
                elif pin_action == 'starter':
                    add_command("start_starter", MASTER_PIN)
                    await update.message.reply_text("✅ Стартер ввімкнено!")
            else:
                await update.message.reply_text("❌ Невірний PIN.")
        else:
            await update.message.reply_text("❓ Невідома команда. Спробуйте /help")

# ==========  ESP32 API ==========
@app.route('/esp32_push', methods=['POST'])
def esp32_push():
    data = request.json
    save_telemetry(data)
    return jsonify({"status": "ok"})

@app.route('/esp32_push/commands', methods=['GET'])
def esp32_get_commands():
    device_id = request.args.get('device_id')
    cmds = get_unexecuted_commands()
    return jsonify({"commands": cmds})

@app.route('/esp32_push/commands/ack', methods=['POST'])
def esp32_ack_command():
    data = request.json
    ack_command(data['command_id'])
    return jsonify({"status": "acknowledged"})

# ==========  AUTOREPORT ==========
def send_daily_report():
    data = get_last_telemetry()
    if data:
        weather = get_weather(data['latitude'], data['longitude'])
        oil = get_setting('oil_last_reset', 'Ніколи')
        chain = get_setting('chain_last_reset', 'Ніколи')
        text = (
            "🕊 <b>Щоденний звіт</b>\n"
            + make_status_text(data) + "\n\n"
            + weather + "\n\n"
            + f"🛢 Остання заміна масла: {oil}\n🔗 Остання мастка ланцюга: {chain}"
        )
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(bot_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode='HTML'))
            else:
                loop.run_until_complete(bot_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode='HTML'))
        except Exception as e:
            print("❌ Не вдалося надіслати щоденний звіт:", e)

def setup_scheduler():
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(send_daily_report, 'cron', hour=8, minute=0)
    scheduler.start()

# ==========  MAIN ==========
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
    setup_scheduler()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_app = application

    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("location", location))
    application.add_handler(CommandHandler("refuel", refuel))
    application.add_handler(CommandHandler("ignite", ignite))
    application.add_handler(CommandHandler("starter", starter))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("service_oil_reset", service_oil_reset))
    application.add_handler(CommandHandler("service_chain_reset", service_chain_reset))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Flask+PTB in one process (webhook на Heroku/Render, або polling)
    import threading
    def run_flask():
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), threaded=True)

    threading.Thread(target=run_flask).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)
