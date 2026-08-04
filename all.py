import os
import json
import asyncio
import random
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import RPCError, FloodWaitError, UsernameNotOccupiedError, ChatAdminRequiredError

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = "8457061798:AAGbQiElJlMgggRv7r_Z85eqr2F4HE7blL0"
SESSION_NAME = "session_qr"

# ===== НАСТРОЙКИ =====
MONITORED_CHATS = ["kabachcache_news", "brawl_scout_find"]
OWNER_ID = 7666608094
CHECK_INTERVAL = 60

CHATS = [
    "@ChatPoiskBrawl", "@bschatpoisk", "@poisk_bs_chatiks3",
    "@poisk_bs_chatiks", "@CHATBrawlsStars", 
    "@brawlpoisktima", "@brawlstarschat", "@Po1sk_Team",
    "@chat_poisk_bs", "@bubspoiskcluba", "@poiskteambs",
    "@BraveStars_chat", "@poisktimabrawlik8", "@BS_besedal",
    "@Sotyanzchat", "@TeamSearch_Brawl", "@brawl_stars_goky",
    "@poiskBrawl_chat", "@bspoiskteam",
    "@Brawl_Chatk", "@ChatPoiskBrawls", "@timabs67",
    "@fanatikachat", "@Brawl_Chattt",
]

CUSTOM_CAPTIONS = {
    "@beseda7271": "Надоело играть с рандомами? Бот в моем био собирает объявления о поиске тимы из десятков чатов, убирает спам и сортирует по категориям. Пробуй!",
    "@poisk_bs_chatiks": "Надоело играть с рандомами? @BrawlScoutBot собирает объявления о поиске тимы из десятков чатов, убирает спам и сортирует по категориям.",
    "@poisk_bs_chatiks3": "Надоело играть с рандомами? Бот в моем био собирает объявления о поиске тимы из десятков чатов, убирает спам и сортирует по категориям. Пробуй!",
    "@brawl_stars_goky": "Надоело играть с рандомами? Бот в моем био собирает объявления о поиске тимы из десятков чатов, убирает спам и сортирует по категориям. Пробуй!",
}

CAPTIONS = [
    "Надоело играть с рандомами? Бот @BrawlScoutBot собирает объявления о поиске тимы из десятков чатов, убирает спам и сортирует по категориям. Бесплатно 🔥",
    "Нашёл бота для поиска тимы — @BrawlScoutBot. Реально помог найти людей за 5 минут. Бесплатно, советую 🔥",
    "Ищешь тиму или клуб? Попробуй @BrawlScoutBot — собирает объявления из чатов, убирает спам, сортирует по категориям. Сам проверил 💪",
    "Кто ищет тиму в Brawl Stars? Бот @BrawlScoutBot собирает все объявления в одном месте. Без спама, бесплатно 🤝",
]

DELAY_BETWEEN_MESSAGES = (60, 180)
DELAY_BETWEEN_CYCLES = 3600
IMAGE_PATH = Path("assets/spam.png")

# ===== ПАРСИНГ ОШИБОК =====
TYPO_WORDS = {
    "ищю", "ишю", "ишу",
    "памагите", "памогите", "памагити", "памогити",
    "памаги", "памоги", "памаг",
    "каманда", "комонда", "каманды",
    "напарнек", "напарниг",
    "ранк",
    "мефик",
    "алмас",
    "бронс",
    "сиребро",
    "залото",
    "купки",
    "трафеи",
    "наберает",
    "приглошает",
    "собшение", "саобщение",
    "написат",
    "паиграть", "поиграт",
    "поабщаться",
    "ат мастера",
    "з алмаза",
    "да мифика",
    "пажалуста", "плиз", "плизз", "хелп",
}

STOP_WORDS = {
    "бесплатные звёзды", "бесплатные звезды", "халява", "подарок",
    "скидка", "купить", "магазин", "промокод", "приглашай",
    "проходи капчу", "на халяву", "забери", "бонус", "акция",
    "розыгрыш", "конкурс", "пополнить", "заработок", "деньги",
    "оплата", "реклама", "зарплата", "схема", "vpn", "бесплатно",
    "недоступна", "партнёрка", "реферал", "заработай",
}

USERS_FILE = Path("typo_users.txt")
LOG_FILE = Path("typo_log.json")

usernames = set()
typo_log = {}
new_usernames = []  # ← ТОЛЬКО НОВЫЕ С МОМЕНТА ПОСЛЕДНЕГО /users

if USERS_FILE.exists():
    with USERS_FILE.open("r", encoding="utf-8") as f:
        usernames = {line.strip() for line in f if line.strip()}
if LOG_FILE.exists():
    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            typo_log = json.load(f)
    except Exception:
        typo_log = {}

found_count = len(usernames)


def is_typo(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for word in TYPO_WORDS:
        if word in text_lower:
            return True
    return False


def is_advertisement(text: str) -> bool:
    if not text:
        return True
    text_lower = text.lower()
    for word in STOP_WORDS:
        if word in text_lower:
            return True
    return False


def find_trigger_words(text: str) -> list:
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for word in TYPO_WORDS:
        if word in text_lower:
            found.append(word)
    return found


def save_files():
    with USERS_FILE.open("w", encoding="utf-8") as f:
        for u in sorted(usernames):
            f.write(u + "\n")
    with LOG_FILE.open("w", encoding="utf-8") as f:
        json.dump(typo_log, f, ensure_ascii=False, indent=4)


# ===== КЛИЕНТЫ =====
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
bot_app = Application.builder().token(BOT_TOKEN).build()


# ===== КОМАНДА /users (ТОЛЬКО НОВЫЕ) =====
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global new_usernames

    if not new_usernames:
        await update.message.reply_text("📭 Новых пользователей с ошибками нет.")
        return

    # Выводим всех новых (без ограничения 50)
    text = "📋 **Новые пользователи с ошибками:**\n\n"
    text += "\n".join(f"@{u}" for u in new_usernames)

    # Отправляем сообщение
    await update.message.reply_text(text)

    # Очищаем список после вывода
    new_usernames = []


bot_app.add_handler(CommandHandler("users", users_command))


# ===== МОНИТОРИНГ ПОДПИСОК =====
previous_participants = {}


async def monitor_subscriptions():
    global previous_participants
    print("📡 Мониторинг подписок запущен (каждую минуту)")
    first_run = True
    while True:
        try:
            for chat_username in MONITORED_CHATS:
                try:
                    entity = await client.get_entity(chat_username)
                    participants = await client.get_participants(entity, aggressive=True)
                    current_ids = {p.id for p in participants}
                    if first_run:
                        previous_participants[chat_username] = current_ids
                        print(f"📌 Запомнено {len(current_ids)} участников в @{chat_username}")
                        continue
                    old_ids = previous_participants.get(chat_username, set())
                    new_ids = current_ids - old_ids
                    for uid in new_ids:
                        try:
                            user = await client.get_entity(uid)
                            name = user.first_name or "Без имени"
                            username_str = f"@{user.username}" if user.username else "нет юзернейма"
                            await bot_app.bot.send_message(
                                chat_id=OWNER_ID,
                                text=f"➕ **Новый подписчик** в @{chat_username}\nИмя: {name}\nЮзернейм: {username_str}\nID: `{uid}`"
                            )
                            print(f"📩 Уведомление о новом: {username_str}")
                        except Exception as e:
                            print(f"Ошибка уведомления о новом: {e}")
                    removed_ids = old_ids - current_ids
                    for uid in removed_ids:
                        try:
                            user = await client.get_entity(uid)
                            name = user.first_name or "Без имени"
                            username_str = f"@{user.username}" if user.username else "нет юзернейма"
                            await bot_app.bot.send_message(
                                chat_id=OWNER_ID,
                                text=f"➖ **Отписался** от @{chat_username}\nИмя: {name}\nЮзернейм: {username_str}\nID: `{uid}`"
                            )
                            print(f"📩 Уведомление об отписке: {username_str}")
                        except Exception as e:
                            print(f"Ошибка уведомления об отписке: {e}")
                    previous_participants[chat_username] = current_ids
                    await asyncio.sleep(2)
                except ChatAdminRequiredError:
                    print(f"⚠️ Нет прав админа для @{chat_username}, пропускаем мониторинг.")
                    continue
                except UsernameNotOccupiedError:
                    print(f"❌ Чат @{chat_username} не существует")
                except RPCError as e:
                    print(f"❌ Ошибка получения участников @{chat_username}: {e}")
                except Exception as e:
                    print(f"❌ Ошибка мониторинга @{chat_username}: {e}")
            if first_run:
                first_run = False
                print("✅ Инициализация завершена. Теперь буду отслеживать изменения.")
        except Exception as e:
            print(f"❌ Критическая ошибка мониторинга: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


# ===== ПАРСИНГ ОШИБОК (ДОБАВЛЯЕМ В new_usernames) =====
@client.on(events.NewMessage(chats=CHATS))
async def typo_handler(event):
    global found_count, new_usernames
    try:
        text = event.raw_text or ""
        if is_advertisement(text) or not is_typo(text):
            return
        trigger_words = find_trigger_words(text)
        if not trigger_words:
            return
        sender = await event.get_sender()
        if not sender or not sender.username:
            return
        username = sender.username.strip()

        # Если юзернейм ещё не был в базе — добавляем в new_usernames
        if username not in usernames:
            new_usernames.append(username)

        usernames.add(username)
        found_count += 1

        if username not in typo_log:
            typo_log[username] = []
        typo_log[username].append({
            "text": text,
            "trigger_words": trigger_words,
            "time": str(event.message.date),
            "chat": event.chat.title if event.chat else "Unknown"
        })

        save_files()
        print(f"[#{found_count}] @{username} | Триггеры: {', '.join(trigger_words)}")
        print(f"Сообщение: {text[:100]}...")
        print("-" * 60)
        await asyncio.sleep(1)

    except Exception as e:
        print(f"[ERROR] {e}")


# ===== СПАМ-РАССЫЛКА =====
async def spam_loop():
    if not IMAGE_PATH.exists():
        print("❌ Картинка для спама не найдена, спам-задача отключена.")
        return
    print("🔄 Спам-рассылка запущена (каждый час новый круг)")
    sent_count = 0
    cycle_count = 0
    while True:
        cycle_count += 1
        random.shuffle(CHATS)
        print(f"\n🔄 === СПАМ-КРУГ №{cycle_count} ===")
        for chat in CHATS:
            try:
                sent_count += 1
                caption = CUSTOM_CAPTIONS.get(chat, random.choice(CAPTIONS))
                print(f"[{sent_count}] Отправка в {chat}...")
                uploaded = await client.upload_file(IMAGE_PATH)
                await client.send_file(chat, file=uploaded, caption=caption, force_document=False)
                delay = random.randint(*DELAY_BETWEEN_MESSAGES)
                await asyncio.sleep(delay)
            except FloodWaitError as e:
                print(f"⏳ FloodWait: ждём {e.seconds} сек")
                await asyncio.sleep(e.seconds)
            except RPCError as e:
                print(f"❌ Ошибка в {chat}: {e}")
                await asyncio.sleep(10)
        print(f"✅ Круг №{cycle_count} завершён. Следующий через {DELAY_BETWEEN_CYCLES//3600} час(а).")
        await asyncio.sleep(DELAY_BETWEEN_CYCLES)


# ===== ГЛАВНАЯ ФУНКЦИЯ =====
async def main():
    await client.start()
    me = await client.get_me()
    print(f"✅ Авторизован как: @{me.username}")
    print(f"📁 Загружено {len(usernames)} пользователей с ошибками.")

    asyncio.create_task(monitor_subscriptions())
    asyncio.create_task(spam_loop())

    await bot_app.initialize()
    await bot_app.start()
    print("🤖 Бот запущен, команда /users доступна.")

    try:
        await bot_app.updater.start_polling()
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка через Ctrl+C...")
    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        save_files()
        await client.disconnect()
        print("✅ Скрипт завершён.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Скрипт остановлен.")