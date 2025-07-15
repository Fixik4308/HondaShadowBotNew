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
            fuel_pulses REAL,
            fuel_liters REAL,
            dailyDistance REAL,
            totalDistance REAL,
            dailyAvgConsumption REAL,
            totalAvgConsumption REAL,
            distanceRemCharge REAL,
            batteryVoltage REAL,
            batteryAkkVoltage REAL
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

def ensure_telemetry_columns():
    required_cols = {
        'dailyDistance': 'REAL',
        'totalDistance': 'REAL',
        'dailyAvgConsumption': 'REAL',
        'totalAvgConsumption': 'REAL',
        'distanceRemCharge': 'REAL',
        'batteryVoltage': 'REAL',
        'batteryAkkVoltage': 'REAL',
    }
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("PRAGMA table_info(telemetry)")
    columns = [row[1] for row in c.fetchall()]
    for col, col_type in required_cols.items():
        if col not in columns:
            print(f"Adding column {col} ({col_type})")
            c.execute(f"ALTER TABLE telemetry ADD COLUMN {col} {col_type}")
    conn.commit()
    conn.close()

def save_telemetry(data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO telemetry (
            device_id, engine_temperature, air_temperature,
            latitude, longitude, fuel_pulses, fuel_liters,
            dailyDistance, totalDistance, dailyAvgConsumption,
            totalAvgConsumption, distanceRemCharge, batteryVoltage,
            batteryAkkVoltage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('device_id'), data.get('engine_temperature'),
        data.get('air_temperature'), data.get('latitude'),
        data.get('longitude'), data.get('fuel_pulses'),
        data.get('fuel_liters'), data.get('dailyDistance'),
        data.get('totalDistance'), data.get('dailyAvgConsumption'),
        data.get('totalAvgConsumption'), data.get('distanceRemCharge'),
        data.get('batteryVoltage'), data.get('batteryAkkVoltage')
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
        "fuel_liters": row[8],
        "dailyDistance": row[9],
        "totalDistance": row[10],
        "dailyAvgConsumption": row[11],
        "totalAvgConsumption": row[12],
        "distanceRemCharge": row[13],
        "batteryVoltage": row[14],
        "batteryAkkVoltage": row[15]
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

HEAD_MENU = [
    [KeyboardButton("📊 Статус"), KeyboardButton("🌤 Погода")],
    [KeyboardButton("⛽️ Дизель"), KeyboardButton("🛵 Пробіг")],
    [KeyboardButton("⚙️ Управління"), KeyboardButton("🧰 ТО")],
    [KeyboardButton("🛠 Налаштування")]
]
FUEL_MENU = [
    [KeyboardButton("🛢 Залишок"), KeyboardButton("⛽ Заправився")],
    [KeyboardButton("⬅️ Назад")]
]
MANAGE_MENU = [
    [KeyboardButton("🔑 Увімкнути запалення"), KeyboardButton("🗝 Завести двигун")],
    [KeyboardButton("🚫 Вимкнути запалення"), KeyboardButton("🛑 Заглушити двигун")],
    [KeyboardButton("⬅️ Назад")]
]
SERVICE_MENU = [
    [KeyboardButton("ℹ️ Нагадування")],
    [KeyboardButton("✅ Змастив цеп"), KeyboardButton("✅ Замінив масло")],
    [KeyboardButton("⬅️ Назад")]
]
SETTING_MENU = [
    [KeyboardButton("🧮 Обнулити лічильники")],
    [KeyboardButton("🌚 Енергозберігаючий режим"), KeyboardButton("🌞 Пробудження")],
    [KeyboardButton("⬅️ Назад")]
]

def make_status_text(data):
    if not data:
        return "❌ Дані ще не надійшли від пристрою."
    text = (
        f"📊 <b>Статус Honda Shadow:</b>\n"
        f"\n"
        f"🛠 <b>Температура двигуна:</b> {data['engine_temperature']:.1f}°C\n"
        f"🌡 <b>Температура повітря:</b> {data['air_temperature']:.1f}°C\n"
        f"\n"
        f"⚡️ <b>Заряд акумулятора:</b> {data['batteryAkkVoltage']:.2f} V\n"
        f"⚡️ <b>Заряд 18650:</b> {data['batteryVoltage']:.2f} V\n"
        f"\n"
        f"⛽ <b>Залишок пального:</b> {data['fuel_liters']:.2f} л\n"
        f"🛵 <b>Пробіг сьогодні: </b> {data['dailyDistance']:.2f} км\n"
        f"🛢 <b>Середній розхід: </b> {data['totalAvgConsumption']:.2f} л/100км\n"
        f"🛣 <b>Проїхати можна ще: </b> {data['distanceRemCharge']:.2f} км\n"
        f"\n"
        f"📍 <b>GPS:</b> https://maps.google.com/?q={data['latitude']},{data['longitude']}"
    )
    return text

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def reply_and_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, html: bool = False, **kwargs):
    if html:
        msg = await update.message.reply_html(text, **kwargs)
    else:
        msg = await update.message.reply_text(text, **kwargs)
    context.job_queue.run_once(delete_message_job, when=300, data=(msg.chat_id, msg.message_id))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text( 
        "Вітаю! Я HiSha.\nГотовa розпочати:",
        reply_markup=ReplyKeyboardMarkup(HEAD_MENU, resize_keyboard=True)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, 
        "Доступні команди:\n"
        "/status — Вся інфо\n"
        "/location — Координати\n"
        "/refuel X — Додати X л\n"
        "/service_oil_reset — Скинути лічильник масла\n"
        "/service_chain_reset — Скинути лічильник цепу\n"
        "/ignite — Запалення (ПІН)\n"
        "/starter — Стартер (ПІН)\n"
        "/stop — Вимкнути все\n"
        "/reset_all — Зброс(ПІН)\n"
        "/power_save_on — Увімкнути енергозберігаючий режим(ПІН)\n"
        "/power_save_off — Вимкнути енергозберігаючий режим(ПІН)\n"
        "/help — Список команд"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_last_telemetry()
    await reply_and_delete(update, context, make_status_text(data), html=True)

async def location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_last_telemetry()
    if not data:
        await reply_and_delete(update, context, "❌ Дані ще не надійшли.")
        return
    await update.message.reply_location(data['latitude'], data['longitude'])

async def refuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        liters = float(context.args[0])
        add_command("refuel", str(liters))
        await reply_and_delete(update, context, f"✅ Заправка на {liters} л відправлена пристрою.")
    except Exception:
        await reply_and_delete(update, context, "❗️ Використання: /refuel 5")

async def ignite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, "Введіть PIN для запуску запалення:")
    context.user_data['awaiting_pin'] = 'ignite'

async def starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, "Введіть PIN для запуску стартера:")
    context.user_data['awaiting_pin'] = 'starter'

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_command("stop_ignition")
    add_command("stop_starter")
    await reply_and_delete(update, context, "✅ Відправлено: вимкнення запалення та стартера.")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, "Введіть PIN для збросу значень:")
    context.user_data['awaiting_pin'] = 'reset_all'

async def power_save_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, "Введіть PIN для увімкнення енергозберігаючого режиму:")
    context.user_data['awaiting_pin'] = 'power_save_on'

async def power_save_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_and_delete(update, context, "Введіть PIN для вимкнення енергозберігаючого режиму:")
    context.user_data['awaiting_pin'] = 'power_save_off'

async def service_oil_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_setting('oil_last_reset', datetime.now(pytz.timezone(TIMEZONE)).isoformat())
    await reply_and_delete(update, context, "✅ Лічильник масла скинуто!")

async def service_chain_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_setting('chain_last_reset', datetime.now(pytz.timezone(TIMEZONE)).isoformat())
    await reply_and_delete(update, context, "✅ Лічильник ланцюга скинуто!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Старт 🚀":
        await start(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    elif text == "🛵 Пробіг":
        data = get_last_telemetry()
        if data:
           await reply_and_delete(update, context, f"🏍 Загальний пробіг: {data['totalDistance']:.2f} км")
           await reply_and_delete(update, context, f"🛵 Пробіг сьогодні: {data['dailyDistance']:.2f} км")
        else:
            await reply_and_delete(update, context, "❌ Дані ще не надійшли.")
    elif text == "⛽️ Дизель": 
        await reply_and_delete(update, context, "Меню пального:", reply_markup=ReplyKeyboardMarkup(FUEL_MENU, resize_keyboard=True))
    elif text == "🛢 Залишок":
        data = get_last_telemetry()
        if data:
           await reply_and_delete(update, context, f"🛢 Дизель: {data['fuel_liters']:.2f} л")
           await reply_and_delete(update, context, f"⚡️ Імпульси: {data['fuel_pulses']}")
           await reply_and_delete(update, context, f"⛽️ Середній розхід: {data['totalAvgConsumption']:.2f} л/100 км")
           await reply_and_delete(update, context, f"⛽️ Середній розхід сьогодні: {data['dailyAvgConsumption']:.2f} л/100 км")
           await reply_and_delete(update, context, f"🛣 Проїхати можна ще: {data['distanceRemCharge']:.2f} км")
        else:
            await reply_and_delete(update, context, "❌ Дані ще не надійшли.")
    elif context.user_data.get('awaiting_refuel'):
        try:
            liters = float(text.replace(',', '.'))  # дозволяємо 1.5 або 1,5
            add_command("refuel", str(liters))
            await reply_and_delete(update, context, f"✅ Заправка на {liters} л відправлена пристрою.")
        except ValueError:
            await reply_and_delete(update, context, "❗️ Невірний формат — введіть число, наприклад: 5 або 1.5")
        finally:
            context.user_data.pop('awaiting_refuel', None)
        return
    elif text == "⛽ Заправився":
        context.user_data['awaiting_refuel'] = True
        await reply_and_delete(update, context, "Введіть, будь ласка, кількість літрів:")
    elif text == "🌤 Погода":
        data = get_last_telemetry()
        if data:
            weather = get_weather(data['latitude'], data['longitude'])
            await reply_and_delete(update, context, weather)
        else:
            await reply_and_delete(update, context, "❌ Дані ще не надійшли.")
    elif text == "⚙️ Управління":
        await reply_and_delete(update, context, "Меню керування:", reply_markup=ReplyKeyboardMarkup(MANAGE_MENU, resize_keyboard=True))
    elif text == "🛠 Налаштування":
        await reply_and_delete(update, context, "Меню керування:", reply_markup=ReplyKeyboardMarkup(SETTING_MENU, resize_keyboard=True))
    elif text == "🧰 ТО":
        await reply_and_delete(update, context, "Меню ТО:", reply_markup=ReplyKeyboardMarkup(SERVICE_MENU, resize_keyboard=True))
    elif text == "⬅️ Назад":
        await reply_and_delete(update, context, "Повертаюся в головне меню.", reply_markup=ReplyKeyboardMarkup(HEAD_MENU, resize_keyboard=True))
    elif text == "🧮 Обнулити лічильники":
        await reset_all(update, context)
    elif text == "🌚 Енергозберігаючий режим":
        await power_save_on(update, context)
    elif text == "🌞 Пробудження":
        await power_save_off(update, context)
    elif text == "🔑 Увімкнути запалення":
        await ignite(update, context)
    elif text == "🗝 Завести двигун":
        await starter(update, context)
    elif text == "🛑 Заглушити двигун":
        await stop(update, context)
    elif text == "🚫 Вимкнути запалення":
        add_command("stop_ignition")
        await reply_and_delete(update, context, "✅ Запалення вимкнено.")
    elif text == "ℹ️ Нагадування":
        oil = get_setting('oil_last_reset', 'Ніколи')
        chain = get_setting('chain_last_reset', 'Ніколи')
        await reply_and_delete(update, context, f"🛢 Остання заміна масла: {oil}\n🔗 Остання мастка ланцюга: {chain}")
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
                    await reply_and_delete(update, context, "✅ Запалення ввімкнено!")
                elif pin_action == 'starter':
                    add_command("start_starter", MASTER_PIN)
                    await reply_and_delete(update, context, "✅ Стартер ввімкнено!")
                elif pin_action == 'reset_all':
                    add_command("reset_all", MASTER_PIN)
                    await reply_and_delete(update, context, "✅ Лічильники скинуто!")
                elif pin_action == 'power_save_on':
                    add_command("power_save_on", MASTER_PIN)
                    await reply_and_delete(update, context, "✅ Спимо!")
                elif pin_action == 'power_save_off':
                    add_command("power_save_off", MASTER_PIN)
                    await reply_and_delete(update, context, "✅ Прокинулась!")
            else:
                await reply_and_delete(update, context, "❌ Невірний PIN.")
        else:
            await reply_and_delete(update, context, "❓ Невідома команда. Спробуйте /help")

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
    ensure_telemetry_columns()
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
    application.add_handler(CommandHandler("reset_all", reset_all))
    application.add_handler(CommandHandler("power_save_on", power_save_on))
    application.add_handler(CommandHandler("power_save_off", power_save_off))
    application.add_handler(CommandHandler("service_oil_reset", service_oil_reset))
    application.add_handler(CommandHandler("service_chain_reset", service_chain_reset))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Flask+PTB in one process (webhook на Heroku/Render, або polling)
    import threading
    def run_flask():
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), threaded=True)

    threading.Thread(target=run_flask).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)
