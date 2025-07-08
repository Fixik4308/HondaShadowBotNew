import logging
import json
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import aiohttp
from dotenv import load_dotenv

# ---------- ЗАВАНТАЖЕННЯ СЕКРЕТІВ ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
PIN_CODE = os.getenv("PIN_CODE")  # наприклад: '1234'

DATA_FILE = "esp32_data.json"
SERVICE_FILE = "service_status.json"

logging.basicConfig(level=logging.INFO)

# ---------- FSM STATES ----------
class RefuelStates(StatesGroup):
    waiting_liters = State()

class PinStates(StatesGroup):
    waiting_pin_ignite = State()
    waiting_pin_starter = State()

# ---------- ІНІЦІАЛІЗАЦІЯ ----------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# ---------- ХЕЛПЕРИ ДЛЯ ЗБЕРІГАННЯ ----------
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_service():
    try:
        with open(SERVICE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "oil_km": 0,
            "chain_km": 0,
            "oil_last": str(datetime.now().date()),
            "chain_last": str(datetime.now().date())
        }

def save_service(service):
    with open(SERVICE_FILE, "w") as f:
        json.dump(service, f, ensure_ascii=False, indent=2)

# ---------- КНОПКИ ----------
def main_menu():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⛽ Залишок")],
            [KeyboardButton(text="🛢 Заправився"), KeyboardButton(text="🌤 Погода")],
            [KeyboardButton(text="⚙️ Управління"), KeyboardButton(text="🧰 ТО")]
        ],
        resize_keyboard=True
    )
    return kb

def management_menu():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 Увімкнути запалення"), KeyboardButton(text="🗝 Завести двигун")],
            [KeyboardButton(text="🛑 Заглушити двигун"), KeyboardButton(text="🚫 Вимкнути запалення")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )
    return kb

def service_menu():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ℹ️ Нагадування")],
            [KeyboardButton(text="✅ Змастив цеп"), KeyboardButton(text="✅ Замінив масло")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )
    return kb

def cancel_menu():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Відмінити")]
        ],
        resize_keyboard=True
    )
    return kb

# ---------- ДОПОМОЖНІ ФУНКЦІЇ ----------
def get_status_text(data, service):
    if not data:
        return "<b>Даних ще немає!</b>"

    offline = ""
    last_update = data.get("last_update")
    if last_update:
        dt = datetime.strptime(last_update, "%Y-%m-%d %H:%M:%S")
        if datetime.now() - dt > timedelta(minutes=3):
            offline = "\n⚠️ <b>ESP32 недоступна (немає даних більше 3х хв)</b>\n"

    status = (
        f"🔋 <b>Статус мотоцикла</b>:\n"
        f"🌡 Температура двигуна: <b>{data.get('engine_temp', '---')}°C</b>\n"
        f"🌡 Температура повітря: <b>{data.get('air_temp', '---')}°C</b>\n"
        f"⛽ Залишок пального: <b>{data.get('fuel_left', '---')} л</b>\n"
        f"🚗 Пробіг (заг.): <b>{data.get('odo_total', '---')} км</b>\n"
        f"📆 Пробіг за день: <b>{data.get('odo_day', '---')} км</b>\n"
        f"🕑 Пробіг за сесію: <b>{data.get('odo_session', '---')} км</b>\n"
        f"⚡ Середня витрата: <b>{data.get('avg_consumption', '---')} л/100км</b>\n"
        f"🔋 Залишок ходу: <b>{data.get('range_left', '---')} км</b>\n"
        f"📍 GPS: {data.get('lat', '---')}, {data.get('lon', '---')}\n"
        f"{offline}"
        f"\n🧰 <b>ТО</b>:\n"
        f"🛢 Масло: вост. <b>{service.get('oil_km', 0)} км</b>, остання заміна: {service.get('oil_last', '-')}\n"
        f"⛓ Цеп: вост. <b>{service.get('chain_km', 0)} км</b>, остання змазка: {service.get('chain_last', '-')}\n"
    )
    return status

async def get_weather(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=ua"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                w = await resp.json()
                text = (
                    f"🌤 <b>Погода зараз</b>:\n"
                    f"Температура: <b>{w['main']['temp']}°C</b>\n"
                    f"Відчувається як: <b>{w['main']['feels_like']}°C</b>\n"
                    f"Тиск: <b>{w['main']['pressure']} гПа</b>\n"
                    f"Вологість: <b>{w['main']['humidity']}%</b>\n"
                    f"Опис: <b>{w['weather'][0]['description'].capitalize()}</b>"
                )
                return text
            return "Помилка отримання погоди"

# ---------- ОБРОБНИКИ КОМАНД/КНОПОК ----------
@dp.message(Command("start"))
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "👋 Вітаю! Я Honda Shadow ESP32 бот.\n"
        "Обери команду з меню або /help для всіх команд.",
        reply_markup=main_menu()
    )

@dp.message(Command("help"))
async def cmd_help(msg: types.Message, state: FSMContext):
    await msg.answer(
        "<b>Список команд:</b>\n"
        "/status – Вся інфо\n"
        "/location – Координати\n"
        "/refuel 5 – Додати 5 л\n"
        "/service_oil_reset – Скинути лічильник масла\n"
        "/service_chain_reset – Скинути лічильник ланцюга\n"
        "/ignite – Увімкнути запалення (з PIN)\n"
        "/starter – Стартер (з PIN)\n"
        "/stop – Вимкнути запалення/стартер\n"
        "/help – Цей список"
    )

@dp.message(Command("status"))
async def cmd_status(msg: types.Message, state: FSMContext):
    data = load_data()
    service = load_service()
    await msg.answer(get_status_text(data, service))

@dp.message(Command("location"))
async def cmd_location(msg: types.Message, state: FSMContext):
    data = load_data()
    if data.get("lat") and data.get("lon"):
        await msg.answer_location(latitude=float(data["lat"]), longitude=float(data["lon"]))
    else:
        await msg.answer("GPS координати недоступні.")

# ---------- FSM: Заправка ----------
@dp.message(F.text == "🛢 Заправився")
async def m_refuel(msg: types.Message, state: FSMContext):
    await msg.answer("Введи кількість літрів (наприклад: 5.5) або ⬅️ Відмінити.", reply_markup=cancel_menu())
    await state.set_state(RefuelStates.waiting_liters)

@dp.message(RefuelStates.waiting_liters)
async def process_refuel_liters(msg: types.Message, state: FSMContext):
    if msg.text == "⬅️ Відмінити":
        await state.clear()
        await msg.answer("Операцію скасовано.", reply_markup=main_menu())
        return
    try:
        liters = float(msg.text.replace(",", "."))
        data = load_data()
        data["fuel_left"] = round(data.get("fuel_left", 0) + liters, 2)
        save_data(data)
        await state.clear()
        await msg.answer(f"✅ Додано {liters} л. Новий залишок: {data['fuel_left']} л.", reply_markup=main_menu())
    except Exception:
        await msg.answer("Введіть коректне число. Наприклад: 5.5 або ⬅️ Відмінити.", reply_markup=cancel_menu())

# ---------- FSM: PIN-код для запалення/стартера ----------
@dp.message(F.text == "🔑 Увімкнути запалення")
async def m_ignite(msg: types.Message, state: FSMContext):
    await msg.answer("Введіть PIN-код або ⬅️ Відмінити.", reply_markup=cancel_menu())
    await state.set_state(PinStates.waiting_pin_ignite)

@dp.message(PinStates.waiting_pin_ignite)
async def process_ignite_pin(msg: types.Message, state: FSMContext):
    if msg.text == "⬅️ Відмінити":
        await state.clear()
        await msg.answer("Операцію скасовано.", reply_markup=main_menu())
        return
    if msg.text == PIN_CODE:
        await state.clear()
        await msg.answer("🔑 Запалення увімкнено!", reply_markup=main_menu())
        # Тут можна викликати функцію для ESP32
    else:
        await msg.answer("❌ Невірний PIN! Спробуйте ще раз або ⬅️ Відмінити.", reply_markup=cancel_menu())

@dp.message(F.text == "🗝 Завести двигун")
async def m_starter(msg: types.Message, state: FSMContext):
    await msg.answer("Введіть PIN-код або ⬅️ Відмінити.", reply_markup=cancel_menu())
    await state.set_state(PinStates.waiting_pin_starter)

@dp.message(PinStates.waiting_pin_starter)
async def process_starter_pin(msg: types.Message, state: FSMContext):
    if msg.text == "⬅️ Відмінити":
        await state.clear()
        await msg.answer("Операцію скасовано.", reply_markup=main_menu())
        return
    if msg.text == PIN_CODE:
        await state.clear()
        await msg.answer("🗝 Двигун заведено!", reply_markup=main_menu())
        # Тут можна викликати функцію для ESP32
    else:
        await msg.answer("❌ Невірний PIN! Спробуйте ще раз або ⬅️ Відмінити.", reply_markup=cancel_menu())

# ---------- Меню, ТО, Погода, Статус та інше ----------
@dp.message(F.text == "📊 Статус")
async def m_status(msg: types.Message, state: FSMContext):
    data = load_data()
    service = load_service()
    await msg.answer(get_status_text(data, service))

@dp.message(F.text == "⛽ Залишок")
async def m_fuel(msg: types.Message, state: FSMContext):
    data = load_data()
    await msg.answer(f"⛽ Залишок пального: <b>{data.get('fuel_left', '---')} л</b>")

@dp.message(F.text == "🌤 Погода")
async def m_weather(msg: types.Message, state: FSMContext):
    data = load_data()
    lat, lon = data.get("lat"), data.get("lon")
    if lat and lon:
        weather = await get_weather(lat, lon)
        await msg.answer(weather)
    else:
        await msg.answer("Координати ще не відомі, неможливо отримати погоду.")

@dp.message(F.text == "⚙️ Управління")
async def m_manage(msg: types.Message, state: FSMContext):
    await msg.answer("Управління:", reply_markup=management_menu())

@dp.message(F.text == "⬅️ Назад")
async def m_back(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Головне меню:", reply_markup=main_menu())

@dp.message(F.text == "🧰 ТО")
async def m_service(msg: types.Message, state: FSMContext):
    await msg.answer("ТО:", reply_markup=service_menu())

@dp.message(F.text == "ℹ️ Нагадування")
async def m_reminders(msg: types.Message, state: FSMContext):
    service = load_service()
    await msg.answer(
        f"🛢 Масло: вост. {service.get('oil_km', 0)} км, остання заміна: {service.get('oil_last', '-')}\n"
        f"⛓ Цеп: вост. {service.get('chain_km', 0)} км, остання змазка: {service.get('chain_last', '-')}"
    )

@dp.message(F.text == "✅ Змастив цеп")
async def m_chain_reset(msg: types.Message, state: FSMContext):
    service = load_service()
    service["chain_km"] = 0
    service["chain_last"] = str(datetime.now().date())
    save_service(service)
    await msg.answer("✅ Лічильник ланцюга скинуто.")

@dp.message(F.text == "✅ Замінив масло")
async def m_oil_reset(msg: types.Message, state: FSMContext):
    service = load_service()
    service["oil_km"] = 0
    service["oil_last"] = str(datetime.now().date())
    save_service(service)
    await msg.answer("✅ Лічильник масла скинуто.")

@dp.message((F.text == "🛑 Заглушити двигун") | (F.text == "🚫 Вимкнути запалення"))
async def m_stop(msg: types.Message, state: FSMContext):
    await msg.answer("✅ Двигун заглушено/запалення вимкнено.", reply_markup=main_menu())

# ---------- ESP32 PUSH API (для отримання даних від ESP32) ----------
from aiohttp import web

async def esp32_push(request):
    try:
        data = await request.json()
        data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_data(data)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "detail": str(e)}, status=400)

# ---------- ГОЛОВНИЙ ЗАПУСК (aiohttp + aiogram разом) ----------
import asyncio

async def start_polling():
    await dp.start_polling(bot)

async def start_web():
    app = web.Application()
    app.add_routes([web.post("/esp32_push", esp32_push)])
    runner = web.AppRunner(app)
    async def start_web():
    app = web.Application()
    app.add_routes([web.post("/esp32_push", esp32_push)])
    runner = web.AppRunner(app)
    await runner.setup()
    import os
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    while True:
        await asyncio.sleep(3600)

def main():
    asyncio.run(main_async())

async def main_async():
    await asyncio.gather(
        start_polling(),
        start_web()
    )

if __name__ == "__main__":
    main()
