# -*- coding: utf-8 -*-
"""
Brawl Scout Bot — финальная версия.
- Обновление сообщений: при повторной публикации старое удаляется, новое добавляется
- Удаление старых сообщений (старше 3 дней)
- Расширена фильтрация (сообщения без явного намерения теперь попадают в "Общение")
- Автоматическая подпись с ссылкой на бота
"""

import asyncio
import re
import sqlite3
import logging
import os
import tempfile
import hashlib
import threading
import secrets
import html
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest

# ================== НАСТРОЙКИ ==================
API_ID = int(os.getenv("API_ID", "36615520"))
API_HASH = os.getenv("API_HASH", "cc6f9eecf6a2549b8ae38d9b9c4a19af")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "+79606694251")
SESSION_NAME = "brawl_scout"
BOT_TOKEN = os.getenv("BOT_TOKEN", "8635571798:AAFcjhLANHpLtfTzDZ00fMwk4-OJNI7ZGJM")

# ===== АДМИНИСТРАТОРЫ =====
ADMIN_IDS = [5138975011, 6370682166]

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

BOT_START_TIME = "05.07.2026 12:00"

SECOND_PHONE = "+79809456591"
SECOND_SESSION = "brawl_scout_second"
BLOCKED_CHAT = "@bschatpoisk"

CHANNELS = [
    "@ChatPoiskBrawl", "@bschatpoisk", "@poisk_bs_chatiks3",
    "@poisk_bs_chatiks", "@CHATBrawlsStars", "@beseda7271",
    "@brawlpoisktima", "@brawlstarschat", "@Po1sk_Team", "@chat_poisk_bs",
    "@kabachcache_chat", "@bubspoiskcluba", "@poiskteambs",
]
DB_NAME = "brawl.db"
TIME_OFFSET = 3
WELCOME_PHOTO_PATH = "assets/welcome.jpg"
BROADCAST_DELAY = 0.05
MEDIA_MAX_BYTES = 512_000
GLOBAL_DUPLICATE_MINUTES = 1440  # 24 часа для проверки дубликатов
BATCH_SAVE_SIZE = 10
CATEGORY_CACHE_SIZE = 1000
USERS_PER_PAGE = 30
CLEANUP_LIMIT = 5000
CLEANUP_INTERVAL = 3600
CLEANUP_DAYS = 3  # удалять сообщения старше 3 дней
CLEANUP_THRESHOLD = 15000

# Отключаем лишние логи Telethon
logging.getLogger("telethon").setLevel(logging.WARNING)

MODERATOR_USERNAMES = {"CreeperModeratorBot"}
MODERATOR_PATTERNS = [
    "отправляет спам-сообщение", "действие: ограничен",
    "нарушение правил", "сообщение удалено", "отправляет спам",
]

PROFANITY = ['нахуй', 'уебище', 'блятский', 'хуй', 'пизда', 'залупа', 'мудак', 'ебал']

if API_ID == 0 or not API_HASH or not BOT_TOKEN:
    raise ValueError("Заполни API_ID, API_HASH и BOT_TOKEN в настройках или .env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def normalize_text_for_hash(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'@\w+', '', t)
    t = re.sub(r'\d+', '', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # Удаляем стоп-слова для лучшего сравнения
    stop_words = ['и', 'в', 'на', 'с', 'по', 'к', 'у', 'а', 'но', 'за', 'из', 'от', 'до', 'для', 'о', 'об', 'же']
    words = t.split()
    words = [w for w in words if w not in stop_words and len(w) > 1]
    return ' '.join(words)


def make_text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text_for_hash(text).encode('utf-8')).hexdigest()


def generate_ref_code() -> str:
    return secrets.token_hex(4).upper()


class Category(Enum):
    FINDING_CLUB = "finding_club"
    RECRUITING = "recruiting"
    FINDING_TEAM = "finding_team"
    RECRUITING_TEAM = "recruiting_team"
    RANKED = "ranked"
    PRIMES = "primes"
    COMMUNICATION = "communication"
    OTHER = "other"

    @classmethod
    def safe_from_string(cls, value: str):
        if not value:
            return cls.OTHER
        try:
            return cls(value)
        except ValueError:
            return cls.OTHER

    @property
    def label(self) -> str:
        return {
            Category.FINDING_CLUB: "🔍 Ищет клуб",
            Category.RECRUITING: "🏛 Набирает в клуб",
            Category.FINDING_TEAM: "👥 Ищет команду",
            Category.RECRUITING_TEAM: "👥 Набирает в команду",
            Category.RANKED: "⚔️ Ранговый бой",
            Category.PRIMES: "⭐ Праймы",
            Category.COMMUNICATION: "💬 Общение",
            Category.OTHER: "📌 Другое",
        }.get(self, "📌 Другое")


@dataclass
class ParsedMessage:
    uid: str
    channel_id: int
    channel_name: str
    channel_username: Optional[str]
    user_id: int
    user_name: str
    user_username: Optional[str]
    text: str
    text_hash: str
    category: Category
    link: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    trophies: Optional[int] = None
    rank: Optional[str] = None
    current_rank: Optional[str] = None
    rank_tier: Optional[str] = None
    common_trophies: Optional[int] = None
    prime_number: Optional[int] = None
    primes_count: Optional[int] = None
    primes_type: Optional[str] = None
    primes_description: Optional[str] = None
    has_media: bool = False
    media_file_id: Optional[str] = None
    media_data: Optional[bytes] = None
    viewed: bool = False
    viewed_time: Optional[datetime] = None
    approved: bool = False


class TriggerManager:
    def __init__(self):
        self.finding_club = [
            "ищу клуб", "нужен клуб", "хочу в клуб", "ищу клан",
            "вступить в клуб", "вступить в клан", "ищу активный клуб",
            "подобрать клуб", "место в клубе", "ищу клан для", "поиск клуба",
            "нужен активный клуб", "вступлю в клуб", "хочу в клан", "примут в клуб",
            "дайте клуб", "дай клуб", "мне нужен клуб", "кто примет в клуб",
            "ищу клан для игры", "клуб для меня", "хочу вступить в клуб",
            "возьмёт в клуб", "возьмет в клуб", "примет в клуб",
            "возьмут в клуб", "возьмете в клуб", "в клуб возьмут",
        ]
        self.recruiting = [
            "набор в клуб", "ищем игроков", "набираем", "рекрутим",
            "приглашаем в клуб", "требуются игроки", "ищем активных",
            "нужны игроки в клуб", "присоединяйтесь", "открыт набор",
            "ищем в клуб", "набор игроков", "требуются в клуб",
            "ищем людей в клуб", "приглашаем игроков",
            "мегакопилка", "свинка", "семейство", "альянс", "чат клуба",
            "идет набор", "набираем в клуб", "приглашаем в клуб",
            "заходите в наш клуб", "заходите в мой клуб", "заходите в клуб",
            "могу принять в клуб", "принимаем в клуб",
            "по поводу вступления", "по вступлению", "чтобы вступить",
            "для вступления", "нажми на ссылку, чтобы вступить",
            "ищу ребят в клуб", "ищу людей в клуб", "нужны люди в клуб",
            "хочешь норм клуб", "мы собираем игроков", "писать в лс",
            "кто в клуб", "набор в начинающий клуб", "ищем в клан",
            "набор в клан", "приглашаем в клан", "рекрутим в клуб",
            "нужны люди в клан", "открыт набор в клуб", "ищем сокланов",
            "клуб ищет игроков", "вступите в клуб", "кому нужен клуб",
            "в клуб не хочешь", "хочешь в клуб", "приглашаем в клан", "идёт набор",
        ]
        self.finding_team = [
            "ищу тиму", "ищу команду", "ищу тиммейта", "нужна тима", "нужна команда",
            "ищу тиммейтов", "хочу в тиму", "подобрать тиму", "ищу пати",
            "ищу скилл тиму", "хочу в команду", "поиск тимы", "нужна пати",
            "ищу 3х3", "ищу дуэль", "кто в бб", "кто в шд", "кто в нок", "кто в гемы",
            "кто в трио", "кто пойдет играть", "ищу дуо", "ищу тиму для",
            "ищу сокомандников", "ищу напарника", "ищу типов", "ищу типа",
            "ищу 2 типов", "ищу 3 типов", "для скрима", "скрим", "ищу людей в бб", "ищу чел",
            "ищу тиму от", "тима от", "+2 бб", "+ в бб",
            "ищу с кем",
            "кто в ранговый", "кто в ранкед", "до леги", "до мифика",
            "кто пойдет ранкед", "кто со мной", "кто играет",
            "кто в бб", "кто в шд", "кто в нок", "кто в гемы",
        ]
        self.recruiting_team = [
            "ищем в тиму", "набор в тиму", "набираем тиму", "нужны в тиму",
            "ищем тиммейтов", "собираем тиму", "нужна тима", "требуются в тиму",
            "ищем в команду", "набор в команду", "набираем команду",
            "ищем игроков в команду", "требуются в команду",
            "собираем команду", "нужны в команду",
        ]
        self.ranked = [
            "ранкед", "ранговый", "лига", "апнуть легу", "до леги", "с мифика",
            "легендарную", "ладдер", "апнуть лигу", "поднять лигу",
            "ранг", "апнуть ранг", "пуш ранга", "до мастера", "до мифика",
            "с алмаза", "с золота",
        ]
        self.primes = [
            "прайм", "престиж", "п1", "п2", "п3", "1 прайм", "2 прайм", "3 прайм",
            "апать праймы", "пуш праймов", "первый прайм", "второй прайм",
            "третий прайм", "праймы", "прокачка праймов",
        ]
        self.communication = [
            "поиграть", "пообщаться", "посмеяться", "пофаниться", "развлечься",
            "посидеть", "скучно", "найти друга", "найти подругу", "найти парня",
            "найти девушку", "поиграть по войсу", "по фану", "в удовольствие",
            "с кем поиграть", "компания", "друзья", "общение", "поболтать", "поговорить",
            "пойдет играть", "играть по рофлу", "кто со мной", "кто может", "кто хочет",
            "кто пойдет", "давайте поиграем", "хочу поиграть", "найдутся желающие",
        ]

        self.ad_markers = [
            "донат", "подписывайтесь", "скидка", "бот", "купить", "продам", "реклама",
            "руб", "₽", "$", "буст", "бущу", "бустану", "бустим", "бустну", "бустю", "bust",
            "куплю bust", "куплю буст", "крипто", "оплата", "карта", "виза", "мастеркарт",
            "звезды", "кошелек", "аккаунты", "продажа", "скидки", "подарок", "nft",
            "разыгрываю", "канал", "подпишись", "официальный сайт", "hellen", "support",
            "shop", "gameboost", "eldorado", "zeusx", "g2g", "обменяю", "покупаю акки",
            "бюджет не ограничен", "бюджет большой", "бюджет огромный", "крипта", "обмен",
            "куплю аккаунт", "продам аккаунт", "куплю акк", "продам акк",
            "скупаю", "скупаю аккаунты", "скупаю акки", "скупаю акк",
            "авито", "работа", "заработок", "плачу", "оплачу", "вход не нужен",
            "выставить объявление", "пообщаться с типами", "за рефералку",
            "за реферала", "денег", "деньги", "рублей", "золото", "монеты",
            "продайте", "купите", "скидка", "акция", "распродажа",
        ]

        self.game_context_words = [
            "бб", "brawl", "bs", "тима", "команд", "клуб", "клан", "ранг", "лег",
            "мифик", "алмаз", "прайм", "кубк", "троф", "gameroom", "band", "brawlstars",
            "шд", "нок", "гем", "дуо", "трио", "пати", "скилл", "мета", "вкач", "бой",
            "игрок", "тип", "скрим", "nap", "napарник", "ранкед", "ранговый",
        ]
        self.rank_aliases = [
            ("мастер", "master"), ("легендарн", "legendary"), ("лег", "legendary"),
            ("мифик", "mythic"), ("алмаз", "diamond"), ("золот", "gold"),
            ("серебр", "silver"), ("бронз", "bronze"),
        ]
        self._category_cache: OrderedDict[str, Category] = OrderedDict()
        rank_part = "|".join(alias for alias, _ in self.rank_aliases)
        self._re_team_req = [
            re.compile(p) for p in [
                r'ищ(?:у|ем)\s+(?:\d+\s+)?(?:тип|чел|люд|игрок|напарник|тим)',
                r'ищ(?:у|ем).*(?:от|с)\s+(?:\d+\s+)?(?:лег|мастер|мифик|алмаз)',
                r'вход\s+от', r'требуется\s+от', r'требуются\s+от',
                r'нужен\s+от', r'нужна\s+от', r'нужны\s+от',
                r'ищу\s+с\b', r'команда\s+от', r'\+2\s+бб', r'\+?\s*в\s+бб',
                r'тима\s+от', r'ищу\s+тиму\s+от',
            ]
        ]
        self._re_current_rank = [
            re.compile(rf'(?:сейчас|пока(?:\s+что)?|на\s+данный\s+момент|у\s+меня|мой\s+ранг|я\s+на)\s+(?:(\d+|1|2|3|перв\w*|втор\w*|трет\w*)\s+)?({rank_part}\w*)'),
            re.compile(rf'(?:сейчас|пока(?:\s+что)?)\s+({rank_part}\w*)\s+(\d+|1|2|3|перв\w*|втор\w*|трет\w*)'),
            re.compile(rf'(?:\bс|\bсо|\bот)\s+(?:(\d+|1|2|3|перв\w*|втор\w*|трет\w*)\s+)?({rank_part}\w*)'),
            re.compile(rf'(?:\bна|\bв)\s+(?:(\d+|1|2|3|перв\w*|втор\w*|трет\w*)\s+)?({rank_part}\w*)'),
        ]
        self._re_trophies = [
            re.compile(r'(\d+)\s*[кkK]\s*(?:кубков|трофеев)?'),
            re.compile(r'(?:кубки|трофеи|к)\s*[\:.]?\s*(\d+\.?\d*)\s*[кk]?'),
        ]
        self._re_common_trophies = re.compile(r'общ(?:их)?\s*[\:.]?\s*(\d+\.?\d*)\s*[кk]?')
        self._re_primes = [
            re.compile(r'(?:на|до|апаю\s+на)\s*([1-3])\s*прайм'),
            re.compile(r'([1-3])\s*-\s*([1-3])\s*прайм'),
            re.compile(r'(?:прайм\s*([1-3])|([1-3])\s*прайм)'),
            re.compile(r'(\d+)\s+(перв|втор|трет)(?:ых|ые|их)?\s*прайм'),
        ]
        self.question_markers = ["что", "как", "зачем", "почему", "когда", "где"]

        self.intent_verbs = [
            "ищу", "ищем", "нужен", "нужна", "нужны", "хочу", "хотим", "набираем", "набор",
            "приглашаем", "рекрутим", "примут", "возьмут", "возьмет", "ищущий", "ищущая",
            "требуются", "требуется", "вступлю", "вступить", "присоединяйтесь",
            "заходите", "принимаем", "идет набор", "открыт набор",
            "пойдет", "поиграть", "пообщаться", "скомпанией", "вместе",
        ]

        self.club_recruiting_extra = [
            "вход от", "клуб", "активный клуб", "весёлый", "дружелюбный",
            "ивенты", "скримы", "чат клуба", "заполненные ивенты",
            "мегакопилка", "семейство", "альянс", "набираем в клуб",
            "приглашаем в клуб", "хочешь в клуб", "кому нужен клуб",
            "в клуб не хочешь", "заходите в наш клуб",
        ]

    def _has_intent(self, text: str) -> bool:
        t = text.lower()
        for verb in self.intent_verbs:
            if verb in t:
                return True
        for pattern in self._re_team_req:
            if pattern.search(t):
                return True
        return False

    def _has_game_context(self, t: str) -> bool:
        return any(w in t for w in self.game_context_words)

    def _is_team_rank_requirement(self, t: str) -> bool:
        if "скрим" in t or "для скрима" in t or "ищу типов" in t or "ищу типа" in t:
            return True
        for pattern in self._re_team_req:
            if pattern.search(t):
                return True
        return False

    def _parse_rank_tier(self, *parts: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        tier = None
        rank_key = None
        num_words = {"1": "1", "2": "2", "3": "3"}
        for part in parts:
            if not part:
                continue
            p = part.lower().strip()
            if p in num_words:
                tier = num_words[p]
                continue
            if p.startswith("перв"):
                tier = "1"
                continue
            if p.startswith("втор"):
                tier = "2"
                continue
            if p.startswith("трет"):
                tier = "3"
                continue
            if p.isdigit() and p in ("1", "2", "3"):
                tier = p
                continue
            for alias, key in self.rank_aliases:
                if alias in p or p.startswith(alias):
                    rank_key = key
                    break
        return rank_key, tier

    def extract_current_rank(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        t = text.lower()
        if self._is_team_rank_requirement(t):
            return None, None
        for pattern in self._re_current_rank:
            m = pattern.search(t)
            if m:
                rank_key, tier = self._parse_rank_tier(*m.groups())
                if rank_key:
                    return rank_key, tier
        return None, None

    def format_rank_display(self, rank_key: Optional[str], tier: Optional[str]) -> Optional[str]:
        if not rank_key:
            return None
        names = {
            "master": "Мастер", "legendary": "Легендарная", "mythic": "Мифик",
            "diamond": "Алмаз", "gold": "Золото", "silver": "Серебро", "bronze": "Бронза",
        }
        name = names.get(rank_key, rank_key)
        return f"{name} {tier}" if tier else name

    def detect_category(self, text: str) -> Category:
        cache_key = normalize_text_for_hash(text)
        if cache_key in self._category_cache:
            self._category_cache.move_to_end(cache_key)
            return self._category_cache[cache_key]
        result = self._detect_category_impl(text)
        self._category_cache[cache_key] = result
        if len(self._category_cache) > CATEGORY_CACHE_SIZE:
            self._category_cache.popitem(last=False)
        return result

    def _detect_category_impl(self, text: str) -> Category:
        t = text.lower()
        
        # === 1. Проверка на рекламу/коммерцию ===
        if any(x in t for x in self.ad_markers):
            return Category.OTHER
        
        # === 2. Если нет явного намерения, но есть контекст игры — Общение ===
        if not self._has_intent(text):
            if self._has_game_context(t) and len(t.split()) >= 3:
                return Category.COMMUNICATION
            return Category.OTHER
        
        # === 3. Мат на PROFANITY ===
        if any(w in t for w in PROFANITY):
            return Category.OTHER
        
        # === 4. Особые случаи ===
        if 'кто в' in t and any(w in t for w in ['ранговый', 'ранкед', 'леги', 'мифик', 'прайм']):
            return Category.FINDING_TEAM
        if 'прайм' in t and 'не одного' in t:
            return Category.OTHER
        if any(w in t for w in ['вступить', 'присоединяйтесь', 'клуб', 'клана']) and '@' in t:
            return Category.RECRUITING
        if 'с кем можно' in t or 'пообщаться' in t:
            if not any(w in t for w in ['ищу', 'нужен', 'тима', 'команд']):
                return Category.COMMUNICATION
        
        # === 5. Вопросы без намерения ===
        if any(t.startswith(w) or f' {w} ' in t for w in self.question_markers):
            if not any(w in t for w in ['ищу', 'нужен', 'хочу', 'ищем', 'набираем', 'ищет']):
                if len(t.split()) <= 3:
                    return Category.OTHER
        if '?' in t:
            if len(t.split()) <= 2:
                return Category.OTHER
        
        # === 6. Ссылки на клубы ===
        if "link.brawlstars.com/invite/band" in t:
            return Category.RECRUITING
        if "link.brawlstars.com/invite/gameroom" in t:
            return Category.PRIMES if any(x in t for x in self.primes) else Category.COMMUNICATION
        
        # === 7. Праймы ===
        if any(x in t for x in self.primes):
            return Category.PRIMES
        
        # === 8. Поиск команды ===
        if any(x in t for x in self.finding_team):
            if any(rec in t for rec in self.club_recruiting_extra):
                return Category.RECRUITING
            return Category.FINDING_TEAM
        
        if any(x in t for x in self.recruiting_team):
            return Category.RECRUITING_TEAM
        
        # === 9. Клубы (набор / поиск) ===
        is_finding = any(x in t for x in self.finding_club)
        is_recruiting = any(x in t for x in self.recruiting)
        
        if any(rec in t for rec in self.club_recruiting_extra) and is_recruiting:
            return Category.RECRUITING
        if is_recruiting and is_finding:
            recruiting_markers = (
                "набор", "ищем", "набираем", "приглашаем", "рекрутим", "нужны",
                "присоединяйтесь", "идёт набор", "кому нужен клуб", "в клуб не хочешь",
            )
            if any(m in t for m in recruiting_markers):
                return Category.RECRUITING
            else:
                return Category.FINDING_CLUB
        if is_finding:
            return Category.FINDING_CLUB
        if is_recruiting:
            return Category.RECRUITING
        
        # === 10. Ранговый бой ===
        current_rank, _ = self.extract_current_rank(text)
        if current_rank:
            if self._has_game_context(t) or any(w in t for w in self.ranked):
                return Category.RANKED
            else:
                return Category.OTHER
        
        has_ranked_word = any(x in t for x in self.ranked) or "ранкед" in t or "ранговый" in t
        if has_ranked_word:
            personal_markers = ("у меня", "мой ранг", "сейчас", "пока", " я ", "я на", "на данный момент")
            team_markers = ("ищу", "нужен", "нужна", "нужны", "тима", "команд", "напарник", "скрим", "тип", "вход от")
            if any(m in t for m in team_markers):
                if any(rec in t for rec in self.club_recruiting_extra):
                    return Category.RECRUITING
                return Category.FINDING_TEAM
            if any(m in t for m in personal_markers):
                return Category.RANKED
            if self._has_game_context(t):
                return Category.COMMUNICATION
            return Category.OTHER
        
        # === 11. Общение ===
        if any(x in t for x in self.communication):
            if len(t.split()) < 2:
                return Category.OTHER
            if self._has_game_context(t) or len(t.split()) >= 3:
                return Category.COMMUNICATION
        
        # === 12. Если есть контекст игры, но не определилось — Общение ===
        if self._has_game_context(t) and len(t.split()) >= 3:
            return Category.COMMUNICATION
        
        return Category.OTHER

    def extract_trophies(self, text: str) -> Optional[int]:
        t = text.lower()
        for pattern in self._re_trophies:
            m = pattern.search(t)
            if m:
                try:
                    val = float(m.group(1))
                    if 1000 <= val <= 200000:
                        return int(val)
                except (ValueError, TypeError):
                    pass
        ignore = r'(?:осталось|нужно|необходимо|всего|только)'
        if not re.search(ignore, t):
            m = re.search(r'\b(\d{4,6})\s*[кk]\b', t)
            if m:
                val = int(m.group(1))
                if 1000 <= val <= 200000:
                    return val
        return None

    def extract_rank(self, text: str) -> Optional[str]:
        rank_key, tier = self.extract_current_rank(text)
        if rank_key:
            return self.format_rank_display(rank_key, tier)
        t = text.lower()
        ranks = {
            "мастер": "Мастер", "легендарн": "Легендарная", "мифик": "Мифик",
            "алмаз": "Алмаз", "золото": "Золото", "серебро": "Серебро", "бронза": "Бронза",
        }
        for key, value in ranks.items():
            if key in t:
                level = re.search(rf'{key}\s*([1-3])', t)
                return f"{value} {level.group(1)}" if level else value
        return None

    def extract_common_trophies(self, text: str) -> Optional[int]:
        m = self._re_common_trophies.search(text.lower())
        if m:
            try:
                val = float(m.group(1))
                if val >= 1000:
                    return int(val)
            except (ValueError, TypeError):
                pass
        return None

    def extract_primes_info(self, text: str) -> Tuple[Optional[int], Optional[str], Optional[int], Optional[str]]:
        t = text.lower()
        prime_number = None
        m = self._re_primes[0].search(t)
        if m:
            prime_number = int(m.group(1))
        else:
            m = self._re_primes[1].search(t)
            if m:
                prime_number = int(m.group(2))
            else:
                m = self._re_primes[2].search(t)
                if m:
                    prime_number = int(m.group(1) or m.group(2))
        primes_description = None
        primes_count = None
        primes_type = None
        matches = self._re_primes[3].findall(t)
        if matches:
            descriptions = []
            for cnt, typ in matches:
                cnt_int = int(cnt)
                typ_map = {'перв': 'первые', 'втор': 'вторые', 'трет': 'третьи'}
                typ_name = typ_map[typ]
                descriptions.append(f"{cnt} {typ_name}")
                if primes_count is None:
                    primes_count = cnt_int
                    primes_type = typ_name
            primes_description = ", ".join(descriptions)
        return prime_number, primes_type, primes_count, primes_description

    def is_ad(self, text: str) -> bool:
        return any(x in text.lower() for x in self.ad_markers)


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._configure()
        self.init()
        self.update_schema()
        self._create_indexes()

    def _configure(self):
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=-128000")
            self.conn.execute("PRAGMA mmap_size=268435456")
            self.conn.execute("PRAGMA temp_store=MEMORY")
            self.conn.commit()

    def _create_indexes(self):
        with self._lock:
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_messages_category ON messages(category)",
                "CREATE INDEX IF NOT EXISTS idx_messages_viewed ON messages(viewed, viewed_time)",
                "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_user_hash ON messages(user_id, text_hash, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_text_hash ON messages(text_hash, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_current_rank ON messages(current_rank)",
                "CREATE INDEX IF NOT EXISTS idx_messages_viewed_timestamp ON messages(viewed, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_messages_category_viewed ON messages(category, viewed, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active)",
                "CREATE INDEX IF NOT EXISTS idx_users_joined ON users(joined_date)",
                "CREATE INDEX IF NOT EXISTS idx_ref_clicks_code ON ref_clicks(ref_code)",
                "CREATE INDEX IF NOT EXISTS idx_ref_clicks_user ON ref_clicks(user_id)",
            ]
            for sql in indexes:
                try:
                    self.conn.execute(sql)
                except sqlite3.Error as e:
                    logger.warning(f"Индекс не создан: {e}")
            self.conn.commit()

    def init(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    uid TEXT PRIMARY KEY, channel_id INTEGER, channel_name TEXT,
                    channel_username TEXT, user_id INTEGER, user_name TEXT,
                    user_username TEXT, text TEXT, text_hash TEXT, category TEXT,
                    trophies INTEGER, rank TEXT, current_rank TEXT, rank_tier TEXT,
                    common_trophies INTEGER, prime_number INTEGER, primes_count INTEGER,
                    primes_type TEXT, primes_description TEXT, timestamp TEXT, link TEXT,
                    has_media INTEGER DEFAULT 0, media_file_id TEXT, media_data BLOB,
                    viewed INTEGER DEFAULT 0, viewed_time TEXT, approved INTEGER DEFAULT 0
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users(
                    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                    last_name TEXT, joined_date TEXT, last_active TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
                    chat_username TEXT, title TEXT, added_at TEXT, expires_at TEXT,
                    added_by INTEGER, stats_clicks INTEGER DEFAULT 0
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ref_links(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ref_clicks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ref_code TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    clicked_at TEXT NOT NULL,
                    FOREIGN KEY (ref_code) REFERENCES ref_links(code)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self.conn.commit()

    def update_schema(self):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("PRAGMA table_info(messages)")
            columns = {row[1] for row in cur.fetchall()}
            cur.close()
            needed_msg = [
                'channel_username', 'user_username', 'text_hash', 'current_rank', 'rank_tier',
                'prime_number', 'primes_count', 'primes_type', 'primes_description',
                'has_media', 'media_file_id', 'media_data', 'viewed', 'viewed_time', 'approved',
            ]
            for col in needed_msg:
                if col not in columns:
                    dtype = (
                        'TEXT' if col in (
                            'channel_username', 'user_username', 'text_hash', 'current_rank',
                            'rank_tier', 'primes_type', 'primes_description', 'media_file_id', 'viewed_time'
                        )
                        else 'BLOB' if col == 'media_data'
                        else 'INTEGER DEFAULT 0'
                    )
                    try:
                        self.conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {dtype}")
                        logger.info(f"Добавлен столбец messages.{col}")
                    except sqlite3.Error as e:
                        logger.warning(f"Не удалось добавить messages.{col}: {e}")
            cur = self.conn.cursor()
            cur.execute("PRAGMA table_info(subscriptions)")
            sub_columns = {row[1] for row in cur.fetchall()}
            cur.close()
            for col in ['title', 'added_at', 'expires_at', 'added_by', 'chat_username', 'stats_clicks']:
                if col not in sub_columns:
                    dtype = 'TEXT' if col in ('title', 'added_at', 'expires_at', 'chat_username') else 'INTEGER'
                    try:
                        self.conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {dtype}")
                    except sqlite3.Error as e:
                        logger.warning(f"Не удалось добавить subscriptions.{col}: {e}")
            self.conn.commit()

    def get_last_cleanup_date(self) -> Optional[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM metadata WHERE key = 'last_cleanup'")
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def set_last_cleanup_date(self, date_str: str):
        with self._lock:
            self.conn.execute("REPLACE INTO metadata (key, value) VALUES ('last_cleanup', ?)", (date_str,))
            self.conn.commit()

    def get_total_messages(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages")
        count = cur.fetchone()[0]
        cur.close()
        return count

    def delete_old_messages(self, days: int = CLEANUP_DAYS, limit: int = CLEANUP_LIMIT) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM messages")
            total = cur.fetchone()[0]
            if total <= CLEANUP_THRESHOLD:
                cur.close()
                return 0
            cur.execute("SELECT uid FROM messages WHERE datetime(timestamp) < ? LIMIT ?", (cutoff, limit))
            uids = [r[0] for r in cur.fetchall()]
            if not uids:
                cur.close()
                return 0
            placeholders = ','.join('?' * len(uids))
            cur.execute(f"DELETE FROM messages WHERE uid IN ({placeholders})", uids)
            deleted = cur.rowcount
            self.conn.commit()
            cur.close()
            return deleted

    def get_old_message_by_user_and_hash(self, user_id: int, text_hash: str, hours: int = 24) -> Optional[ParsedMessage]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM messages WHERE user_id = ? AND text_hash = ? AND datetime(timestamp) > ? ORDER BY timestamp DESC LIMIT 1",
            (user_id, text_hash, cutoff)
        )
        row = cur.fetchone()
        cur.close()
        return self.row_to_msg(row) if row else None

    def delete_message_by_uid(self, uid: str):
        with self._lock:
            self.conn.execute("DELETE FROM messages WHERE uid = ?", (uid,))
            self.conn.commit()

    def create_ref_link(self, name: str, created_by: int) -> Optional[str]:
        code = generate_ref_code()
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT INTO ref_links (code, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (code, name, created_by, datetime.now().isoformat())
                )
                self.conn.commit()
                return code
            except sqlite3.IntegrityError:
                return self.create_ref_link(name, created_by)

    def get_ref_links(self) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT r.id, r.code, r.name, r.created_by, r.created_at, r.is_active,
                   COUNT(c.id) as clicks
            FROM ref_links r
            LEFT JOIN ref_clicks c ON r.code = c.ref_code
            GROUP BY r.id
            ORDER BY r.created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": row[0], "code": row[1], "name": row[2],
            "created_by": row[3], "created_at": row[4],
            "is_active": bool(row[5]), "clicks": row[6] or 0
        } for row in rows]

    def get_ref_clicks(self, code: str) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT user_id, username, first_name, clicked_at
            FROM ref_clicks WHERE ref_code = ?
            ORDER BY clicked_at DESC
        """, (code,))
        rows = cur.fetchall()
        cur.close()
        return [{
            "user_id": row[0], "username": row[1],
            "first_name": row[2], "clicked_at": row[3]
        } for row in rows]

    def add_ref_click(self, code: str, user_id: int, username: str, first_name: str):
        with self._lock:
            self.conn.execute(
                "INSERT INTO ref_clicks (ref_code, user_id, username, first_name, clicked_at) VALUES (?, ?, ?, ?, ?)",
                (code, user_id, username, first_name, datetime.now().isoformat())
            )
            self.conn.commit()

    def get_ref_link_by_code(self, code: str) -> Optional[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, code, name, is_active FROM ref_links WHERE code = ?", (code,))
        row = cur.fetchone()
        cur.close()
        if row:
            return {"id": row[0], "code": row[1], "name": row[2], "is_active": bool(row[3])}
        return None

    def toggle_ref_link(self, code: str, active: bool):
        with self._lock:
            self.conn.execute(
                "UPDATE ref_links SET is_active = ? WHERE code = ?",
                (1 if active else 0, code)
            )
            self.conn.commit()

    def delete_ref_link(self, code: str):
        with self._lock:
            self.conn.execute("DELETE FROM ref_clicks WHERE ref_code = ?", (code,))
            self.conn.execute("DELETE FROM ref_links WHERE code = ?", (code,))
            self.conn.commit()

    def add_subscription(self, chat_id, title, username=None, expires_at=None, added_by=None):
        with self._lock:
            now = datetime.now().isoformat()
            self.conn.execute(
                "INSERT INTO subscriptions (chat_id, chat_username, title, added_at, expires_at, added_by) VALUES (?,?,?,?,?,?)",
                (chat_id, username, title, now, expires_at, added_by)
            )
            self.conn.commit()

    def remove_subscription(self, sub_id):
        with self._lock:
            self.conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            self.conn.commit()

    def update_subscription_expires(self, sub_id, expires_at):
        with self._lock:
            self.conn.execute("UPDATE subscriptions SET expires_at = ? WHERE id = ?", (expires_at, sub_id))
            self.conn.commit()

    def get_subscriptions(self) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, chat_id, chat_username, title, added_at, expires_at, stats_clicks FROM subscriptions ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            expires_at = None
            if row[5]:
                try:
                    expires_at = datetime.fromisoformat(row[5])
                except ValueError:
                    pass
            result.append({
                "id": row[0], "chat_id": row[1], "chat_username": row[2], "title": row[3],
                "added_at": datetime.fromisoformat(row[4]) if row[4] else None,
                "expires_at": expires_at, "stats_clicks": row[6] if len(row) > 6 else 0,
            })
        return result

    def get_subscription_by_chat_id(self, chat_id: str) -> Optional[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM subscriptions WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            return dict(row)
        return None

    def init_default_subscription(self, chat_id, title, username=None):
        existing = self.get_subscription_by_chat_id(chat_id)
        if not existing:
            self.add_subscription(chat_id, title, username, None, 0)
            logger.info(f"Добавлена начальная подписка на {title} (ID: {chat_id})")
        else:
            logger.info(f"Подписка на {title} (ID: {chat_id}) уже существует")

    def save_message(self, msg: ParsedMessage):
        self.save_messages_batch([msg])

    def save_messages_batch(self, msgs: List[ParsedMessage]):
        if not msgs:
            return
        with self._lock:
            for msg in msgs:
                self.conn.execute("""
                    INSERT OR REPLACE INTO messages
                    (uid, channel_id, channel_name, channel_username, user_id, user_name, user_username,
                     text, text_hash, category, trophies, rank, current_rank, rank_tier, common_trophies,
                     prime_number, primes_count, primes_type, primes_description,
                     timestamp, link, has_media, media_file_id, media_data, viewed, viewed_time, approved)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    msg.uid, msg.channel_id, msg.channel_name, msg.channel_username,
                    msg.user_id, msg.user_name, msg.user_username, msg.text, msg.text_hash,
                    msg.category.value, msg.trophies, msg.rank, msg.current_rank, msg.rank_tier,
                    msg.common_trophies, msg.prime_number, msg.primes_count, msg.primes_type,
                    msg.primes_description, msg.timestamp.isoformat(), msg.link,
                    1 if msg.has_media else 0, msg.media_file_id, msg.media_data,
                    1 if msg.viewed else 0,
                    msg.viewed_time.isoformat() if msg.viewed_time else None,
                    1 if msg.approved else 0,
                ))
            self.conn.commit()

    def is_global_text_duplicate(self, user_id: int, text_hash: str, minutes: int = GLOBAL_DUPLICATE_MINUTES) -> bool:
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND text_hash = ? AND datetime(timestamp) > ?",
            (user_id, text_hash, cutoff)
        )
        count = cur.fetchone()[0]
        cur.close()
        if count > 0:
            logger.info(f"🔁 Дубликат найден: user={user_id}, hash={text_hash[:8]}, повторов={count}")
        return count > 0

    def get_unviewed(self, category=None, rank_filter=None, prime_filter=None) -> List[ParsedMessage]:
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        query = """
            SELECT * FROM messages
            WHERE viewed = 0 AND datetime(timestamp) > ?
        """
        params: list = [cutoff]
        if category:
            query += " AND category = ?"
            params.append(category.value)
        if rank_filter:
            query += " AND current_rank = ?"
            params.append(rank_filter)
        if prime_filter is not None:
            query += " AND prime_number = ?"
            params.append(prime_filter)
        query += " ORDER BY timestamp DESC LIMIT 50"
        
        cur = self.conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        
        result = []
        for r in rows:
            try:
                msg = self.row_to_msg(r)
                if msg:
                    result.append(msg)
            except Exception as e:
                logger.error(f"Ошибка конвертации: {e}")
        return result[:20]

    def mark_viewed(self, uid: str):
        with self._lock:
            now = datetime.now().isoformat()
            cur = self.conn.cursor()
            cur.execute("SELECT user_id, text_hash FROM messages WHERE uid = ?", (uid,))
            row = cur.fetchone()
            if row and row[0] and row[1]:
                user_id = row[0]
                text_hash = row[1]
                cur.execute(
                    "UPDATE messages SET viewed = 1, viewed_time = ? WHERE user_id = ? AND text_hash = ? AND viewed = 0",
                    (now, user_id, text_hash)
                )
            else:
                cur.execute(
                    "UPDATE messages SET viewed = 1, viewed_time = ? WHERE uid = ?",
                    (now, uid)
                )
            self.conn.commit()
            cur.close()

    def get_message_by_uid(self, uid: str) -> Optional[ParsedMessage]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM messages WHERE uid = ?", (uid,))
        row = cur.fetchone()
        cur.close()
        return self.row_to_msg(row) if row else None

    def row_to_msg(self, row) -> Optional[ParsedMessage]:
        if not row:
            return None
        data = dict(row)
        try:
            timestamp = datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now()
        except (ValueError, TypeError):
            timestamp = datetime.now()
        viewed_time = None
        if data.get("viewed_time"):
            try:
                viewed_time = datetime.fromisoformat(data["viewed_time"])
            except ValueError:
                pass
        trophies = data.get("trophies")
        if trophies is not None:
            try:
                trophies = int(trophies)
                if trophies < 100 or trophies > 200000:
                    trophies = None
            except (ValueError, TypeError):
                trophies = None
        rank = data.get("rank")
        if rank is not None and isinstance(rank, (int, float)):
            if trophies is None:
                trophies = int(rank)
            rank = None
        return ParsedMessage(
            uid=data["uid"], channel_id=data["channel_id"],
            channel_name=data.get("channel_name") or "Unknown",
            channel_username=data.get("channel_username"),
            user_id=data["user_id"], user_name=data.get("user_name") or "Unknown",
            user_username=data.get("user_username"), text=data.get("text") or "",
            text_hash=data.get("text_hash") or "",
            category=Category.safe_from_string(data.get("category")),
            link=data.get("link") or "", timestamp=timestamp,
            trophies=trophies, rank=rank,
            current_rank=data.get("current_rank"), rank_tier=data.get("rank_tier"),
            common_trophies=data.get("common_trophies"),
            prime_number=data.get("prime_number"), primes_count=data.get("primes_count"),
            primes_type=data.get("primes_type"), primes_description=data.get("primes_description"),
            has_media=bool(data.get("has_media")),
            media_file_id=data.get("media_file_id"), media_data=data.get("media_data"),
            viewed=bool(data.get("viewed")), viewed_time=viewed_time,
            approved=bool(data.get("approved")),
        )

    def get_stats(self) -> dict:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM messages WHERE viewed = 0")
        unviewed = cur.fetchone()[0]
        cur.execute("SELECT category, COUNT(*) FROM messages GROUP BY category")
        by_category = cur.fetchall()
        cur.close()
        return {"total": total, "unviewed": unviewed, "by_category": by_category}

    def get_oldest_message_date(self) -> Optional[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT MIN(timestamp) FROM messages")
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0]) + timedelta(hours=TIME_OFFSET)
            return dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            return None

    def add_user(self, user_id, username, first_name, last_name=None):
        with self._lock:
            now = datetime.now().isoformat()
            self.conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, joined_date, last_active)
                VALUES (?,?,?,?, COALESCE((SELECT joined_date FROM users WHERE user_id = ?), ?), ?)
            """, (user_id, username, first_name, last_name, user_id, now, now))
            self.conn.commit()

    def update_user_activity(self, user_id):
        with self._lock:
            now = datetime.now().isoformat()
            self.conn.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now, user_id))
            self.conn.commit()

    def get_all_users(self) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT user_id, username, first_name, last_name, joined_date, last_active FROM users ORDER BY joined_date DESC")
        rows = cur.fetchall()
        cur.close()
        return [{"user_id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3],
                 "joined_date": r[4], "last_active": r[5]} for r in rows]

    def get_user_count(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        cur.close()
        return count

    def get_user_count_since(self, days: int) -> int:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE joined_date >= ?", (since,))
        count = cur.fetchone()[0]
        cur.close()
        return count

    def get_active_users_since(self, days: int) -> int:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (since,))
        count = cur.fetchone()[0]
        cur.close()
        return count

    def get_users_paginated(self, page: int = 0, per_page: int = USERS_PER_PAGE) -> Tuple[List[dict], int]:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        offset = page * per_page
        cur.execute(
            "SELECT user_id, username, first_name, last_name, joined_date, last_active "
            "FROM users ORDER BY joined_date DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        )
        rows = cur.fetchall()
        cur.close()
        users = [{"user_id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3],
                  "joined_date": r[4], "last_active": r[5]} for r in rows]
        return users, total

    def search_users(self, query: str) -> List[dict]:
        cur = self.conn.cursor()
        query = query.strip().lstrip('@')
        if query.isdigit():
            cur.execute(
                "SELECT user_id, username, first_name, last_name, joined_date, last_active "
                "FROM users WHERE user_id = ?", (int(query),)
            )
        else:
            cur.execute(
                "SELECT user_id, username, first_name, last_name, joined_date, last_active "
                "FROM users WHERE username LIKE ? COLLATE NOCASE LIMIT 20",
                (f"%{query}%",)
            )
        rows = cur.fetchall()
        cur.close()
        return [{"user_id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3],
                 "joined_date": r[4], "last_active": r[5]} for r in rows]


def format_collection_start(db: Database) -> str:
    date_str = db.get_oldest_message_date()
    return date_str if date_str else "данные отсутствуют"


def fmt_dt(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return iso_str


class Collector:
    def __init__(self, db: Database, parser: TriggerManager, bot: Bot):
        self.db = db
        self.parser = parser
        self.bot = bot
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.second_client = None
        self.channels = []
        self._save_buffer: List[ParsedMessage] = []
        self._buffer_lock = asyncio.Lock()

    async def _flush_buffer(self):
        async with self._buffer_lock:
            if not self._save_buffer:
                return
            batch = self._save_buffer[:]
            self._save_buffer.clear()
        await asyncio.to_thread(self.db.save_messages_batch, batch)

    async def start(self):
        await self.client.start(phone=PHONE_NUMBER)
        me = await self.client.get_me()
        logger.info(f"Telethon: основной аккаунт {me.first_name} (@{me.username})")

        self.second_client = TelegramClient(SECOND_SESSION, API_ID, API_HASH)
        await self.second_client.start(phone=SECOND_PHONE)
        me2 = await self.second_client.get_me()
        logger.info(f"Telethon: второй аккаунт {me2.first_name} (@{me2.username}) для {BLOCKED_CHAT}")

        for ch in CHANNELS:
            try:
                if ch == BLOCKED_CHAT:
                    entity = await self.second_client.get_entity(ch)
                else:
                    entity = await self.client.get_entity(ch)
                self.channels.append(entity)
                logger.info(f"  OK {ch}")
            except Exception as e:
                logger.error(f"  FAIL {ch}: {e}")

        @self.client.on(events.NewMessage(chats=self.channels))
        async def handler_main(event):
            await self._process_message(event, self.client)

        @self.second_client.on(events.NewMessage(chats=[BLOCKED_CHAT]))
        async def handler_second(event):
            await self._process_message(event, self.second_client)

        async def buffer_flusher():
            while True:
                await asyncio.sleep(5)
                await self._flush_buffer()

        asyncio.create_task(buffer_flusher())
        await self.client.run_until_disconnected()

    async def _process_message(self, event, client):
        msg = event.message
        text = msg.text or ""
        if not text:
            return

        sender = await event.get_sender()
        user_username = None
        user_id = 0
        user_name = "Unknown"
        if sender:
            user_name = getattr(sender, 'first_name', None) or getattr(sender, 'title', None) or "Unknown"
            user_id = sender.id
            user_username = getattr(sender, 'username', None)
            if getattr(sender, 'bot', False):
                return
            if user_username and user_username in MODERATOR_USERNAMES:
                return
            if not user_username and user_id:
                try:
                    full_user = await client.get_entity(user_id)
                    user_username = getattr(full_user, 'username', None)
                    if user_username in MODERATOR_USERNAMES:
                        return
                except Exception:
                    pass

        tl = text.lower()
        if any(p in tl for p in MODERATOR_PATTERNS):
            return
        if self.parser.is_ad(text):
            return

        category = self.parser.detect_category(text)
        if category == Category.OTHER:
            return

        channel_username = getattr(msg.chat, 'username', None)
        if channel_username == "bubspoiskcluba" and category not in (Category.FINDING_CLUB, Category.RECRUITING):
            return
        if channel_username == "poiskteambs" and category in (Category.FINDING_CLUB, Category.RECRUITING):
            return

        chat_id_str = str(msg.chat_id)
        link = (
            f"https://t.me/c/{chat_id_str.replace('-100', '')}/{msg.id}"
            if chat_id_str.startswith('-100')
            else f"https://t.me/{getattr(msg.chat, 'title', msg.chat_id)}/{msg.id}"
        )

        has_media = False
        media_data = None
        if msg.media and isinstance(msg.media, MessageMediaPhoto):
            has_media = True
            try:
                file_data = await client.download_media(msg.media, bytes)
                if file_data and len(file_data) <= MEDIA_MAX_BYTES:
                    media_data = file_data
            except Exception as e:
                logger.error(f"Ошибка скачивания фото: {e}")

        text_hash = make_text_hash(text)
        
        # Проверяем, есть ли похожее сообщение от этого пользователя за последние 24 часа
        old_msg = self.db.get_old_message_by_user_and_hash(user_id, text_hash, hours=24)
        if old_msg:
            # Удаляем старое сообщение
            self.db.delete_message_by_uid(old_msg.uid)
            logger.info(f"🗑 Удалено старое сообщение от {user_id}, заменено новым")

        current_rank_key, rank_tier = self.parser.extract_current_rank(text)
        rank_display = self.parser.format_rank_display(current_rank_key, rank_tier) or self.parser.extract_rank(text)
        prime_number, primes_type, primes_count, primes_description = self.parser.extract_primes_info(text)

        parsed = ParsedMessage(
            uid=f"{msg.chat_id}_{msg.id}",
            channel_id=msg.chat_id,
            channel_name=str(getattr(msg.chat, 'title', msg.chat_id) or "unknown"),
            channel_username=channel_username,
            user_id=user_id, user_name=user_name, user_username=user_username,
            text=text, text_hash=text_hash, category=category, link=link,
            timestamp=msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
            trophies=self.parser.extract_trophies(text),
            rank=rank_display, current_rank=current_rank_key, rank_tier=rank_tier,
            common_trophies=self.parser.extract_common_trophies(text),
            prime_number=prime_number, primes_type=primes_type,
            primes_count=primes_count, primes_description=primes_description,
            has_media=has_media, media_data=media_data,
        )

        async with self._buffer_lock:
            self._save_buffer.append(parsed)
            if len(self._save_buffer) >= BATCH_SAVE_SIZE:
                batch = self._save_buffer[:]
                self._save_buffer.clear()
                await asyncio.to_thread(self.db.save_messages_batch, batch)


class AdminBot:
    def __init__(self, db: Database, collector: Collector, bot: Bot):
        self.db = db
        self.collector = collector
        self.bot = bot
        self.dp = Dispatcher()
        self.current_category = None
        self.current_rank_filter = None
        self.current_prime_filter = None
        self.waiting_for_sub = False
        self.waiting_for_sub_expire = False
        self.waiting_for_user_search = False
        self.waiting_for_ref_name = False
        self.history = {}
        self.broadcast_data = None
        self.broadcast_datetime = None
        self.waiting_for_broadcast_text = False
        self.waiting_for_broadcast_time = False
        self.users_page = 0
        self._unviewed_cache = {}
        self._cache_time = {}
        self._sending_lock = {}
        self.temp_sub_id = None
        self.setup_handlers()

    def clean_text(self, text: str) -> str:
        return html.escape(text)

    def _main_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        kb = [
            [InlineKeyboardButton(text="📋 Лента", callback_data="menu_feed")],
            [InlineKeyboardButton(text="🏛 Клубы", callback_data="menu_clubs")],
            [InlineKeyboardButton(text="💬 Общение", callback_data="menu_communication")],
            [InlineKeyboardButton(text="⚔️ Ранговый бой", callback_data="menu_ranked")],
            [InlineKeyboardButton(text="⭐ Праймы", callback_data="menu_primes")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
        ]
        if is_admin(user_id):
            kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

    def _format_sender(self, m: ParsedMessage) -> str:
        return f"@{m.user_username}" if m.user_username else self.clean_text(m.user_name)

    def _format_channel(self, m: ParsedMessage) -> str:
        name = self.clean_text(m.channel_name)
        if m.channel_username:
            return f"<a href='https://t.me/{m.channel_username}'>{name}</a>"
        cid = str(m.channel_id)
        if cid.startswith("-100"):
            return f"<a href='https://t.me/c/{cid.replace('-100', '')}'>{name}</a>"
        return name

    async def check_user_subscriptions(self, user_id: int) -> List[Tuple[str, str, str]]:
        not_subscribed = []
        for sub in self.db.get_subscriptions():
            if sub["expires_at"] and datetime.now() > sub["expires_at"]:
                continue
            chat_id = sub["chat_id"]
            try:
                member = await self.bot.get_chat_member(chat_id, user_id)
                if member.status in ("left", "kicked"):
                    not_subscribed.append((sub["title"], sub["chat_username"], chat_id))
            except Exception as e:
                logger.debug(f"Ошибка проверки подписки на {sub['title']}: {e}")
                pass
        return not_subscribed

    async def show_main_menu(self, message, delete_old=True, user_id=None, ref_code: str = None):
        if user_id is None:
            user_id = message.chat.id
        
        if ref_code:
            ref_link = self.db.get_ref_link_by_code(ref_code)
            if ref_link and ref_link["is_active"]:
                username = message.from_user.username or ""
                first_name = message.from_user.first_name or ""
                self.db.add_ref_click(ref_code, user_id, username, first_name)
                await message.answer(
                    f"🔗 Вы перешли по реферальной ссылке: <b>{self.clean_text(ref_link['name'])}</b>\n"
                    "Добро пожаловать! Бот готов к работе.",
                    parse_mode="HTML"
                )
            else:
                await message.answer("❌ Реферальная ссылка неактивна или не найдена.")
        
        welcome_text = (
            "<b>🗓 Brawl Scout Bot</b>\n\n"
            "📩 Собираю объявления из чатов поиска Brawl Stars и сортирую по категориям.\n\n"
            "<b>💬 Как пользоваться:</b>\n"
            "1️⃣ Выбери категорию.\n"
            "2️⃣ Смотри объявления по одному.\n"
            "3️⃣ Напиши автору или пропусти.\n"
            "4️⃣ «Предыдущее» — вернуться к последнему.\n\n"
            "📢 Канал: @kabachcache_news\n"
            "💬 Чатик: @kabachcache_chat"
        )
        
        keyboard = self._main_keyboard(user_id)
        
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        
        if os.path.exists(WELCOME_PHOTO_PATH):
            try:
                photo = FSInputFile(WELCOME_PHOTO_PATH)
                await message.answer_photo(photo, caption=welcome_text, reply_markup=keyboard, parse_mode="HTML")
                return
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
        
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")

    async def send_message_display(self, message, uid, category=None, delete_old=True, add_to_history=True, actor_id=None):
        user_id = actor_id or message.chat.id
        
        if user_id in self._sending_lock and self._sending_lock[user_id]:
            return
        self._sending_lock[user_id] = True
        
        try:
            m = self.db.get_message_by_uid(uid)
            if not m:
                if user_id in self.history and uid in self.history.get(user_id, []):
                    self.history[user_id].remove(uid)
                await self.show_previous_or_error(message, actor_id=user_id)
                return
            
            if add_to_history:
                self.history.setdefault(user_id, [])
                if uid in self.history[user_id]:
                    self.history[user_id].remove(uid)
                self.history[user_id].append(uid)
                self.history[user_id] = self.history[user_id][-20:]

            lines = [
                f"<b>{m.category.label}</b>\n",
                f"<b>👤 От:</b> {self._format_sender(m)} (ID: {m.user_id})",
                f"<b>📢 Канал:</b> {self._format_channel(m)}",
            ]
            if m.trophies and m.trophies > 0:
                lines.append(f"<b>🏆 Кубки:</b> {m.trophies}K")
            if m.rank and m.category != Category.RECRUITING:
                lines.append(f"<b>🎖 Ранг:</b> {self.clean_text(m.rank)}")
            if m.category == Category.RECRUITING and m.common_trophies:
                lines.append(f"<b>🏛 Общие кубки клуба:</b> {m.common_trophies}K")
            if m.category in (Category.PRIMES, Category.FINDING_TEAM, Category.RECRUITING_TEAM):
                if m.prime_number is not None:
                    lines.append(f"<b>⭐ Прайм:</b> {m.prime_number}")
                if m.primes_description:
                    lines.append(f"<b>⭐ Апнутые праймы:</b> {self.clean_text(m.primes_description)}")
            
            clean_msg = self.clean_text(m.text)
            lines += [
                f"\n<b>📝 Сообщение:</b>\n{clean_msg}",
                f"\n🔗 <a href='{m.link}'>Ссылка на сообщение</a>",
                f"🕒 {(m.timestamp + timedelta(hours=TIME_OFFSET)).strftime('%H:%M %d.%m.%Y')}",
            ]

            signature = (
                "\n\n---\n"
                "🔍 <a href='https://t.me/BrawlScoutBot'>бот для поиска тимы/клубов</a>"
            )
            lines.append(signature)

            text = "\n".join(lines)

            prev_uid = None
            if user_id in self.history and uid in self.history[user_id]:
                idx = self.history[user_id].index(uid)
                if idx > 0:
                    prev_uid = self.history[user_id][idx - 1]

            keyboard_rows = []
            if m.user_username:
                keyboard_rows.append([
                    InlineKeyboardButton(text="✉️ Написать", callback_data=f"write_{m.uid}"),
                    InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip_{m.uid}"),
                ])
            else:
                keyboard_rows.append([
                    InlineKeyboardButton(text="🔗 К сообщению", url=m.link),
                    InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip_{m.uid}"),
                ])
            if prev_uid:
                keyboard_rows.append([InlineKeyboardButton(text="⬅️ Предыдущее", callback_data=f"prev_{prev_uid}")])
            keyboard_rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

            if delete_old:
                try:
                    await message.delete()
                except TelegramBadRequest:
                    pass

            if m.has_media and m.media_data:
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                        tmp.write(m.media_data)
                        tmp_path = tmp.name
                    photo = FSInputFile(tmp_path)
                    await message.answer_photo(photo, caption=text, reply_markup=keyboard, parse_mode="HTML")
                    return
                except Exception as e:
                    logger.error(f"Ошибка отправки фото: {e}")
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
        finally:
            self._sending_lock[user_id] = False

    async def show_previous_or_error(self, message, actor_id=None):
        user_id = actor_id or message.chat.id
        if user_id in self.history and self.history[user_id]:
            prev_uid = self.history[user_id][-1]
            if self.db.get_message_by_uid(prev_uid):
                await self.send_message_display(message, prev_uid, self.current_category, True, False, user_id)
                return
            self.history[user_id].remove(prev_uid)
            await self.show_previous_or_error(message, actor_id=user_id)
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")]
        ])
        await message.answer("❌ Сообщение не найдено.", reply_markup=keyboard, parse_mode="HTML")

    async def send_next(self, message, category=None, rank_filter=None, prime_filter=None, delete_old=True, actor_id=None):
        cache_key = f"{category}_{rank_filter}_{prime_filter}"
        now = datetime.now()
        
        if cache_key in self._unviewed_cache and (now - self._cache_time.get(cache_key, datetime.min)).seconds < 3:
            msgs = self._unviewed_cache[cache_key]
        else:
            msgs = self.db.get_unviewed(category, rank_filter, prime_filter)
            self._unviewed_cache[cache_key] = msgs
            self._cache_time[cache_key] = now
        
        if not msgs:
            if delete_old:
                try:
                    await message.delete()
                except TelegramBadRequest:
                    pass
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")]
            ])
            await message.answer("❌ Нет новых объявлений в этой категории.", reply_markup=keyboard, parse_mode="HTML")
            return
        
        m = msgs[0]
        self.db.mark_viewed(m.uid)
        self._unviewed_cache.pop(cache_key, None)
        await self.send_message_display(message, m.uid, category, delete_old, True, actor_id)

    # ===== РЕФЕРАЛКИ =====
    async def render_ref_links(self, message, delete_old=True):
        links = self.db.get_ref_links()
        if not links:
            text = "<b>🔗 Рефералки</b>\n\nНет созданных рефералок."
            kb = [
                [InlineKeyboardButton(text="➕ Создать", callback_data="ref_create")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")],
            ]
        else:
            lines = ["<b>🔗 Рефералки</b>\n"]
            for link in links:
                status = "✅" if link["is_active"] else "❌"
                lines.append(
                    f"{status} <b>{self.clean_text(link['name'])}</b>\n"
                    f"  Код: <code>{link['code']}</code>\n"
                    f"  Переходов: {link['clicks']}\n"
                    f"  Создана: {fmt_dt(link['created_at'])}\n"
                )
            text = "\n".join(lines)
            kb = []
            for link in links:
                kb.append([
                    InlineKeyboardButton(
                        text=f"📊 {self.clean_text(link['name'][:20])}",
                        callback_data=f"ref_stats_{link['code']}"
                    )
                ])
            kb.append([
                InlineKeyboardButton(text="➕ Создать", callback_data="ref_create"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel"),
            ])
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

    async def render_ref_stats(self, message, code: str, delete_old=True):
        link = self.db.get_ref_link_by_code(code)
        if not link:
            await message.answer("❌ Рефералка не найдена.")
            return
        clicks = self.db.get_ref_clicks(code)
        text = (
            f"<b>📊 Статистика рефералки</b>\n\n"
            f"<b>Название:</b> {self.clean_text(link['name'])}\n"
            f"<b>Код:</b> <code>{code}</code>\n"
            f"<b>Ссылка:</b> <code>t.me/{self.bot.username}?start=ref_{code}</code>\n"
            f"<b>Статус:</b> {'✅ Активна' if link['is_active'] else '❌ Неактивна'}\n"
            f"<b>Всего переходов:</b> {len(clicks)}\n\n"
        )
        if clicks:
            text += "<b>Последние переходы:</b>\n"
            for click in clicks[:10]:
                username = f"@{click['username']}" if click['username'] else click['first_name'] or "Аноним"
                text += f"• {self.clean_text(username)} (ID: {click['user_id']}) — {fmt_dt(click['clicked_at'])}\n"
        else:
            text += "Переходов пока нет."

        kb = [
            [
                InlineKeyboardButton(
                    text="🔄 Деактивировать" if link["is_active"] else "🔄 Активировать",
                    callback_data=f"ref_toggle_{code}"
                ),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ref_delete_{code}"),
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="ref_list")],
        ]
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

    async def render_ref_create(self, message, delete_old=True):
        self.waiting_for_ref_name = True
        text = (
            "<b>➕ Создание рефералки</b>\n\n"
            "Введите название рефералки (например: «Для канала X» или «Партнёрская программа»).\n\n"
            "Название будет видно пользователям при переходе по ссылке."
        )
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ref_list")]
        ]), parse_mode="HTML")

    # ===== ПОЛЬЗОВАТЕЛИ =====
    async def render_users_list(self, message, page: int = 0, delete_old=True):
        users, total = self.db.get_users_paginated(page)
        total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
        lines = [f"<b>👥 Список пользователей</b> (стр. {page + 1}/{total_pages}, всего: {total})\n"]
        for u in users:
            uname = f"@{u['username']}" if u['username'] else "—"
            name = u['first_name'] or "—"
            name_short = self.clean_text(name)[:20] if name != "—" else "—"
            uname_short = self.clean_text(uname)[:15] if uname != "—" else "—"
            lines.append(
                f"• <code>{u['user_id']}</code> | {name_short} | {uname_short}\n"
                f"  📅 {fmt_dt(u['joined_date'])} | 🟢 {fmt_dt(u['last_active'])}"
            )
        if not users:
            lines.append("Пользователи не найдены.")
        text = "\n".join(lines)
        kb = []
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"users_page_{page - 1}"))
        nav.append(InlineKeyboardButton(text="🔄 Обновить", callback_data=f"users_page_{page}"))
        if (page + 1) * USERS_PER_PAGE < total:
            nav.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"users_page_{page + 1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="users_search")])
        kb.append([InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")])
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

    async def render_user_search_results(self, message, users: List[dict]):
        if not users:
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К списку", callback_data="users_page_0")]
                ])
            )
            return
        lines = ["<b>🔍 Результаты поиска</b>\n"]
        for u in users:
            uname = f"@{u['username']}" if u['username'] else "—"
            lines.append(
                f"• <code>{u['user_id']}</code> | {self.clean_text(u['first_name'] or '—')} | {self.clean_text(uname)}\n"
                f"  📅 {fmt_dt(u['joined_date'])} | 🟢 {fmt_dt(u['last_active'])}"
            )
        await message.answer(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К списку", callback_data="users_page_0")]
            ]),
            parse_mode="HTML"
        )

    # ===== РАССЫЛКА =====
    async def _execute_broadcast(self, broadcast_info):
        users = self.db.get_all_users()
        total = len(users)
        sent = failed = 0
        admin_msg = await self.bot.send_message(ADMIN_IDS[0], f"📨 Рассылка начата. Всего: {total}")
        for i, user in enumerate(users, 1):
            try:
                await self.bot.copy_message(user["user_id"], broadcast_info["from_chat_id"], broadcast_info["message_id"])
                sent += 1
            except Exception as e:
                failed += 1
                logger.error(f"Рассылка {user['user_id']}: {e}")
            if i % 50 == 0:
                try:
                    await admin_msg.edit_text(f"📨 Рассылка... {i}/{total}")
                except TelegramBadRequest:
                    pass
            await asyncio.sleep(BROADCAST_DELAY)
        await admin_msg.edit_text(f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}")

    async def _delayed_broadcast(self, delay: float, broadcast_info: dict):
        await asyncio.sleep(delay)
        await self._execute_broadcast(broadcast_info)

    # ========== УПРАВЛЕНИЕ ОП ==========
    async def render_op_list(self, message, delete_old=True, edit=False):
        subs = self.db.get_subscriptions()
        lines = ["<b>🔒 Обязательная подписка (ОП)</b>\n\n"]
        if not subs:
            lines.append("❌ Нет активных ОП.")
            kb = [
                [InlineKeyboardButton(text="➕ Добавить ОП", callback_data="op_add")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")],
            ]
        else:
            for idx, sub in enumerate(subs, 1):
                active = not (sub['expires_at'] and datetime.now() > sub['expires_at'])
                status = "✅ активна" if active else "❌ истекла"
                expires = sub['expires_at'].strftime('%d.%m.%Y %H:%M') if sub['expires_at'] else "бессрочно"
                username = f"@{sub['chat_username']}" if sub['chat_username'] else "—"
                lines.append(
                    f"{idx}. <b>{self.clean_text(sub['title'])}</b>\n"
                    f"   ID: <code>{sub['chat_id']}</code>\n"
                    f"   Username: {self.clean_text(username)}\n"
                    f"   Статус: {status}, до {expires}\n"
                )
            kb = []
            for sub in subs:
                idx = next((i for i, s in enumerate(subs, 1) if s["id"] == sub["id"]), 0)
                btn_row = [
                    InlineKeyboardButton(
                        text=f"🗑 {idx}. {self.clean_text(sub['title'][:10])}",
                        callback_data=f"op_delete_{sub['id']}"
                    ),
                    InlineKeyboardButton(
                        text="📅 Изменить срок",
                        callback_data=f"op_expire_{sub['id']}"
                    ),
                ]
                kb.append(btn_row)
            kb.append([InlineKeyboardButton(text="➕ Добавить ОП", callback_data="op_add")])
            kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")])
        
        text = "\n".join(lines)
        if delete_old:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        if edit:
            try:
                await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
            except TelegramBadRequest:
                await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

    def setup_handlers(self):
        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            uid = message.from_user.id
            self.db.add_user(uid, message.from_user.username or "", message.from_user.first_name or "", message.from_user.last_name or "")
            self.db.update_user_activity(uid)
            
            ref_code = None
            args = message.text.split()
            if len(args) > 1 and args[1].startswith("ref_"):
                ref_code = args[1][4:]
            
            if ref_code:
                ref_link = self.db.get_ref_link_by_code(ref_code)
                if ref_link and ref_link["is_active"]:
                    username = message.from_user.username or ""
                    first_name = message.from_user.first_name or ""
                    self.db.add_ref_click(ref_code, uid, username, first_name)
                    await message.answer(
                        f"🔗 Вы перешли по реферальной ссылке: <b>{self.clean_text(ref_link['name'])}</b>\n"
                        "Добро пожаловать! Бот готов к работе.",
                        parse_mode="HTML"
                    )
                else:
                    await message.answer("❌ Реферальная ссылка неактивна или не найдена.")
            
            not_sub = await self.check_user_subscriptions(uid)
            if not_sub:
                text = "<b>⚠️ Для работы бота подпишись на каналы:</b>\n\n"
                kb = []
                for title, uname, cid in not_sub:
                    if uname:
                        url = f"https://t.me/{uname}"
                    elif cid.startswith("-100"):
                        url = f"https://t.me/c/{cid.replace('-100', '')}"
                    else:
                        url = f"https://t.me/{cid}"
                    text += f"• <a href='{url}'>{self.clean_text(title)}</a>\n"
                    kb.append([InlineKeyboardButton(text=f"📎 {self.clean_text(title[:20])}", url=url)])
                text += "\nПосле подписки нажмите «Проверить»."
                callback_data = f"check_sub_{ref_code}" if ref_code else "check_subscription"
                kb.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data=callback_data)])
                await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
                return
            
            await self.show_main_menu(message, False, uid, ref_code)

        @self.dp.callback_query(lambda c: c.data.startswith("check_sub"))
        async def check_sub_cb(callback):
            ref_code = None
            if callback.data.startswith("check_sub_"):
                ref_code = callback.data.split("_")[2] if len(callback.data.split("_")) > 2 else None
            
            if await self.check_user_subscriptions(callback.from_user.id):
                await callback.answer("❌ Вы ещё не подписаны на все каналы", show_alert=True)
            else:
                await callback.answer("✅ Подписка подтверждена", show_alert=True)
                try:
                    await callback.message.delete()
                except TelegramBadRequest:
                    pass
                await self.show_main_menu(callback.message, False, callback.from_user.id, ref_code)

        @self.dp.message(Command("help"))
        async def cmd_help(message):
            await message.answer(
                "<b>📖 Помощь</b>\n\nБот собирает объявления из чатов Brawl Stars.\n\n"
                "<b>🏛 Клубы:</b>\n• «Я ищу клуб» — объявления от клубов\n• «Я ищу людей в клуб» — заявки игроков\n\n"
                "📢 Канал: @kabachcache_news\n💬 Чатик: @kabachcache_chat",
                parse_mode="HTML"
            )

        @self.dp.callback_query(lambda c: c.data == "admin_panel")
        async def admin_panel(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton(text="👥 Список пользователей", callback_data="users_page_0")],
                [InlineKeyboardButton(text="🔗 Рефералки", callback_data="ref_list")],
                [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="🔒 Управление ОП", callback_data="admin_op")],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
            ])
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer("⚙️ Админ-панель\nВыберите действие:", reply_markup=keyboard, parse_mode="HTML")
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data == "admin_stats")
        async def admin_stats(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            total_users = self.db.get_user_count()
            text = (
                "<b>📊 Статистика пользователей</b>\n\n"
                f"👥 Всего зарегистрировано: {total_users}\n"
                f"🆕 Новых за сегодня: {self.db.get_user_count_since(1)}\n"
                f"🆕 Новых за неделю: {self.db.get_user_count_since(7)}\n"
                f"🆕 Новых за месяц: {self.db.get_user_count_since(30)}\n\n"
                f"🟢 Активных за сегодня: {self.db.get_active_users_since(1)}\n"
                f"🟢 Активных за неделю: {self.db.get_active_users_since(7)}\n"
                f"🟢 Активных за месяц: {self.db.get_active_users_since(30)}"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
            ])
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data == "ref_list")
        async def ref_list(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            await self.render_ref_links(callback.message)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("ref_stats_"))
        async def ref_stats(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            code = callback.data.split("_")[2]
            await self.render_ref_stats(callback.message, code)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("ref_toggle_"))
        async def ref_toggle(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            code = callback.data.split("_")[2]
            link = self.db.get_ref_link_by_code(code)
            if link:
                self.db.toggle_ref_link(code, not link["is_active"])
                await callback.answer("✅ Статус изменён")
                await self.render_ref_stats(callback.message, code)
            else:
                await callback.answer("❌ Рефералка не найдена", show_alert=True)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("ref_delete_"))
        async def ref_delete(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            code = callback.data.split("_")[2]
            self.db.delete_ref_link(code)
            await callback.answer("🗑 Рефералка удалена")
            await self.render_ref_links(callback.message)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data == "ref_create")
        async def ref_create_prompt(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            await self.render_ref_create(callback.message)
            await callback.answer()

        @self.dp.message(lambda msg: msg.from_user.id in ADMIN_IDS and getattr(self, 'waiting_for_ref_name', False))
        async def ref_create_name(message: types.Message):
            self.waiting_for_ref_name = False
            name = message.text.strip()
            if len(name) < 3:
                await message.answer("❌ Название слишком короткое (минимум 3 символа). Попробуйте снова.")
                self.waiting_for_ref_name = True
                return
            code = self.db.create_ref_link(name, message.from_user.id)
            if code:
                bot_username = (await self.bot.get_me()).username
                await message.answer(
                    f"✅ Рефералка создана!\n\n"
                    f"<b>Название:</b> {self.clean_text(name)}\n"
                    f"<b>Код:</b> <code>{code}</code>\n"
                    f"<b>Ссылка:</b> <code>https://t.me/{bot_username}?start=ref_{code}</code>\n\n"
                    f"Скопируйте ссылку и распространяйте.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📋 К списку", callback_data="ref_list")]
                    ]),
                    parse_mode="HTML"
                )
            else:
                await message.answer("❌ Ошибка создания рефералки. Попробуйте ещё раз.")

        @self.dp.callback_query(lambda c: c.data.startswith("users_page_"))
        async def users_page(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            page = int(callback.data.split("_")[-1])
            self.users_page = page
            await self.render_users_list(callback.message, page)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data == "users_search")
        async def users_search_prompt(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            self.waiting_for_user_search = True
            await callback.message.answer(
                "🔍 Введите ID или @username пользователя:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="users_page_0")]
                ])
            )
            await callback.answer()

        @self.dp.message(lambda msg: msg.from_user.id in ADMIN_IDS and msg.text and not msg.text.startswith('/') and not self.waiting_for_sub and not self.waiting_for_sub_expire and not self.waiting_for_user_search and not self.waiting_for_ref_name and not self.waiting_for_broadcast_text and not self.waiting_for_broadcast_time)
        async def admin_text_router(message: types.Message):
            pass

        @self.dp.callback_query(lambda c: c.data == "admin_broadcast")
        async def admin_broadcast(callback):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            self.waiting_for_broadcast_text = True
            self.broadcast_data = None
            self.broadcast_datetime = None
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer(
                "<b>📨 Рассылка</b>\n\nОтправьте сообщение для рассылки.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()

        @self.dp.message(lambda msg: self.waiting_for_broadcast_text and msg.from_user.id in ADMIN_IDS)
        async def broadcast_text_received(message: types.Message):
            self.broadcast_data = message
            self.waiting_for_broadcast_text = False
            self.waiting_for_broadcast_time = True
            await message.answer(
                "<b>⏰ Время рассылки</b>\n\n"
                "Введите время, когда нужно отправить рассылку.\n"
                "Форматы:\n"
                "• <code>сейчас</code> — отправить немедленно\n"
                "• <code>через 1 час</code> / <code>через 30 минут</code>\n"
                "• <code>в 15:30</code> — сегодня в указанное время\n"
                "• <code>2026-07-08 10:00</code> — конкретная дата и время\n\n"
                "Или нажмите кнопку «Назад» для отмены.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
                ]),
                parse_mode="HTML"
            )

        @self.dp.message(lambda msg: self.waiting_for_broadcast_time and msg.from_user.id in ADMIN_IDS)
        async def broadcast_time_received(message: types.Message):
            if message.text and message.text.lower().strip() == "назад":
                self.waiting_for_broadcast_time = False
                self.broadcast_data = None
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                    [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast")],
                    [InlineKeyboardButton(text="🔒 Управление ОП", callback_data="admin_op")],
                    [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
                ])
                await message.answer("⚙️ Админ-панель\nВыберите действие:", reply_markup=keyboard, parse_mode="HTML")
                return
            time_text = message.text.strip()
            target_dt = None
            now = datetime.now()
            try:
                if time_text.lower() == "сейчас":
                    target_dt = now
                elif time_text.lower().startswith("через "):
                    match = re.match(r'через\s+(\d+)\s+(час|часа|часов|минут|минуты|минуту)', time_text.lower())
                    if match:
                        num = int(match.group(1))
                        unit = match.group(2)
                        if 'час' in unit:
                            target_dt = now + timedelta(hours=num)
                        elif 'минут' in unit:
                            target_dt = now + timedelta(minutes=num)
                        else:
                            raise ValueError("Неизвестная единица времени")
                elif time_text.lower().startswith("в "):
                    time_part = time_text[2:].strip()
                    match = re.match(r'(\d{1,2}):(\d{2})', time_part)
                    if match:
                        h = int(match.group(1))
                        m = int(match.group(2))
                        target_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                        if target_dt < now:
                            target_dt += timedelta(days=1)
                    else:
                        raise ValueError("Неверный формат времени")
                else:
                    try:
                        target_dt = datetime.fromisoformat(time_text)
                    except ValueError:
                        try:
                            target_dt = datetime.strptime(time_text, "%Y-%m-%d")
                            target_dt = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                        except ValueError:
                            raise ValueError("Не удалось распознать время")
            except Exception as e:
                await message.answer(
                    f"❌ Не удалось распознать время: {e}\n"
                    "Попробуйте ещё раз или нажмите «Назад».",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
                    ]),
                    parse_mode="HTML"
                )
                return
            if target_dt is None:
                await message.answer(
                    "❌ Не удалось распознать время. Попробуйте ещё раз.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
                    ]),
                    parse_mode="HTML"
                )
                return
            self.broadcast_datetime = target_dt
            self.broadcast_time = time_text
            self.waiting_for_broadcast_time = False
            original_msg = self.broadcast_data
            preview_header = (
                f"<b>✅ Подтверждение рассылки</b>\n\n"
                f"Вы хотите отправить это сообщение <b>всем пользователям бота</b> в:\n"
                f"<b>{target_dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            )
            if original_msg.text:
                original_content = f"<b>Сообщение:</b>\n{self.clean_text(original_msg.text)}"
            elif original_msg.caption:
                original_content = f"<b>Сообщение:</b>\n{self.clean_text(original_msg.caption)}"
            else:
                original_content = "📎 (медиа без текста)"
            preview_caption = f"{preview_header}\n{original_content}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="broadcast_confirm")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")],
            ])
            if original_msg.photo or original_msg.video or original_msg.document or original_msg.audio or original_msg.voice or original_msg.animation:
                try:
                    sent = await original_msg.copy_to(
                        chat_id=message.chat.id,
                        caption=preview_caption,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    self.preview_message_id = sent.message_id
                except Exception as e:
                    logger.error(f"Ошибка копирования медиа для предпросмотра: {e}")
                    await message.answer(
                        preview_caption + "\n\n⚠️ Не удалось скопировать медиа.",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
            else:
                full_text = f"{preview_header}\n{original_content}"
                sent = await message.answer(full_text, reply_markup=keyboard, parse_mode="HTML")
                self.preview_message_id = sent.message_id

        @self.dp.callback_query(lambda c: c.data == "broadcast_confirm")
        async def broadcast_confirm(callback: types.CallbackQuery):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            if not self.broadcast_data or not self.broadcast_datetime:
                await callback.answer("❌ Нет данных для рассылки", show_alert=True)
                return
            original_msg = self.broadcast_data
            broadcast_info = {
                "from_chat_id": original_msg.chat.id,
                "message_id": original_msg.message_id,
                "text": original_msg.text or original_msg.caption or "📎 (медиа)"
            }
            broadcast_dt = self.broadcast_datetime
            self.broadcast_data = None
            self.broadcast_datetime = None
            self.broadcast_time = None
            await callback.answer("✅ Рассылка запланирована")
            try:
                await callback.message.edit_text(
                    f"✅ Рассылка запланирована на {broadcast_dt.strftime('%d.%m.%Y %H:%M')}.\n\n"
                    f"Сообщение:\n{self.clean_text(broadcast_info['text'][:500])}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")]
                    ]),
                    parse_mode="HTML"
                )
            except Exception:
                await callback.message.answer(
                    f"✅ Рассылка запланирована на {broadcast_dt.strftime('%d.%m.%Y %H:%M')}.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")]
                    ]),
                    parse_mode="HTML"
                )
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            now = datetime.now()
            delay = max(0, (broadcast_dt - now).total_seconds())
            async def scheduled_broadcast():
                await asyncio.sleep(delay)
                await self._execute_broadcast(broadcast_info)
            asyncio.create_task(scheduled_broadcast())

        @self.dp.callback_query(lambda c: c.data == "admin_op")
        async def admin_op_menu(callback: types.CallbackQuery):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            await self.render_op_list(callback.message)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("op_delete_"))
        async def op_delete_callback(callback: types.CallbackQuery):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            sub_id = int(callback.data.split("_")[2])
            self.db.remove_subscription(sub_id)
            await callback.answer("🗑 ОП удалена", show_alert=True)
            await self.render_op_list(callback.message, delete_old=False, edit=False)

        @self.dp.callback_query(lambda c: c.data.startswith("op_expire_"))
        async def op_expire_prompt(callback: types.CallbackQuery):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            sub_id = int(callback.data.split("_")[2])
            subs = self.db.get_subscriptions()
            sub = next((s for s in subs if s["id"] == sub_id), None)
            if not sub:
                await callback.answer("❌ ОП не найдена", show_alert=True)
                return
            self.waiting_for_sub_expire = True
            self.temp_sub_id = sub_id
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer(
                f"<b>📅 Изменение срока для ОП «{self.clean_text(sub['title'])}»</b>\n\n"
                "Введите новый срок (или <code>бессрочно</code>):\n"
                "Форматы: <code>24h</code>, <code>7d</code>, <code>30d</code>, <code>2026-07-10</code>.\n\n"
                "Или нажмите «Назад» для отмены.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_op")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data == "op_add")
        async def op_add_prompt(callback: types.CallbackQuery):
            if not is_admin(callback.from_user.id):
                await callback.answer("⛔ Доступ запрещён", show_alert=True)
                return
            self.waiting_for_sub = True
            self.temp_sub_id = None
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer(
                "<b>➕ Добавление ОП</b>\n\n"
                "Отправьте ID канала (начинается с <code>-100</code>) или ссылку.\n"
                "Можно указать срок через пробел: <code>24h</code> или <code>7d</code>.\n\n"
                "Примеры:\n"
                "<code>-1001234567890</code>\n"
                "<code>-1001234567890 24h</code>\n"
                "<code>https://t.me/username</code>\n\n"
                "Или нажмите «Назад» для отмены.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_op")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()

        @self.dp.message(lambda msg: self.waiting_for_sub and msg.from_user.id in ADMIN_IDS)
        async def op_add_handler(message: types.Message):
            self.waiting_for_sub = False
            chat_input = message.text.strip()
            parts = chat_input.split()
            chat_id_str = parts[0]
            expire_str = None
            if len(parts) > 1:
                expire_str = parts[1]
            chat_id_str_clean = chat_id_str
            chat_title = None
            chat_username = None
            if chat_id_str.startswith('-100') and chat_id_str.lstrip('-').isdigit():
                pass
            elif 't.me/' in chat_id_str:
                match = re.search(r't\.me/([^/\s?]+)', chat_id_str)
                if match:
                    username = match.group(1)
                    try:
                        chat = await self.bot.get_chat(f"@{username}")
                        chat_id_str_clean = str(chat.id)
                        chat_title = chat.title
                        chat_username = chat.username
                    except Exception as e:
                        await message.answer(f"❌ Не удалось найти группу по ссылке: {e}")
                        return
                else:
                    await message.answer("❌ Не удалось распознать ссылку.")
                    return
            elif chat_id_str.startswith('@'):
                try:
                    chat = await self.bot.get_chat(chat_id_str)
                    chat_id_str_clean = str(chat.id)
                    chat_title = chat.title
                    chat_username = chat.username
                except Exception as e:
                    await message.answer(f"❌ Не удалось найти группу по username: {e}")
                    return
            elif chat_id_str.lstrip('-').isdigit():
                if not chat_id_str.startswith('-100'):
                    chat_id_str_clean = '-100' + chat_id_str.lstrip('-')
                else:
                    chat_id_str_clean = chat_id_str
            else:
                await message.answer(
                    "❌ Неверный формат.\n\n"
                    "Введите:\n"
                    "• ID группы: -1001234567890\n"
                    "• Ссылку: https://t.me/username\n"
                    "• Username: @username"
                )
                return
            if not chat_title:
                try:
                    chat = await self.bot.get_chat(int(chat_id_str_clean))
                    chat_title = chat.title or chat_id_str_clean
                    chat_username = chat.username
                except Exception as e:
                    await message.answer(f"❌ Не удалось получить информацию о чате: {e}")
                    return
            subs = self.db.get_subscriptions()
            for sub in subs:
                if sub["chat_id"] == chat_id_str_clean:
                    await message.answer("❌ Этот чат уже добавлен в ОП.")
                    return
            expires_at = None
            if expire_str:
                if expire_str.lower() == "бессрочно":
                    expires_at = None
                else:
                    now = datetime.now()
                    match = re.match(r'(\d+)([hd])', expire_str.lower())
                    if match:
                        num = int(match.group(1))
                        unit = match.group(2)
                        if unit == 'h':
                            expires_at = (now + timedelta(hours=num)).isoformat()
                        elif unit == 'd':
                            expires_at = (now + timedelta(days=num)).isoformat()
                    else:
                        try:
                            dt = datetime.fromisoformat(expire_str)
                            if dt < now:
                                dt = dt.replace(year=now.year+1)
                            expires_at = dt.isoformat()
                        except ValueError:
                            await message.answer("❌ Неверный формат срока. Используйте 24h, 7d, 30d или дату YYYY-MM-DD.")
                            return
            self.db.add_subscription(chat_id_str_clean, chat_title, chat_username, expires_at, message.from_user.id)
            expires_display = fmt_dt(expires_at) if expires_at else "бессрочно"
            await message.answer(
                f"✅ Чат добавлен в обязательные подписки!\n\n"
                f"📌 <b>Название:</b> {self.clean_text(chat_title)}\n"
                f"🆔 <b>ID:</b> <code>{chat_id_str_clean}</code>\n"
                f"🔗 <b>Ссылка:</b> {'https://t.me/' + chat_username if chat_username else '🔒 Приватный'}\n"
                f"⏳ <b>Действует до:</b> {expires_display}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку ОП", callback_data="admin_op")]
                ]),
                parse_mode="HTML"
            )
            self.waiting_for_sub = False
            self.temp_sub_id = None

        @self.dp.message(lambda msg: self.waiting_for_sub_expire and msg.from_user.id in ADMIN_IDS)
        async def op_expire_handler(message: types.Message):
            self.waiting_for_sub_expire = False
            sub_id = self.temp_sub_id
            self.temp_sub_id = None
            expire_input = message.text.strip()
            subs = self.db.get_subscriptions()
            sub = next((s for s in subs if s["id"] == sub_id), None)
            if not sub:
                await message.answer("❌ ОП не найдена.")
                return
            if expire_input.lower() == "бессрочно":
                expires_at = None
            else:
                now = datetime.now()
                match = re.match(r'(\d+)([hd])', expire_input.lower())
                if match:
                    num = int(match.group(1))
                    unit = match.group(2)
                    if unit == 'h':
                        expires_at = (now + timedelta(hours=num)).isoformat()
                    elif unit == 'd':
                        expires_at = (now + timedelta(days=num)).isoformat()
                else:
                    try:
                        dt = datetime.fromisoformat(expire_input)
                        if dt < now:
                            dt = dt.replace(year=now.year+1)
                        expires_at = dt.isoformat()
                    except ValueError:
                        await message.answer("❌ Неверный формат срока. Используйте 24h, 7d, 30d или дату YYYY-MM-DD.")
                        return
            self.db.update_subscription_expires(sub_id, expires_at)
            expires_display = fmt_dt(expires_at) if expires_at else "бессрочно"
            await message.answer(f"✅ Срок ОП «{self.clean_text(sub['title'])}» обновлён до {expires_display}.")
            await self.render_op_list(message, delete_old=False, edit=False)

        @self.dp.callback_query(lambda c: c.data.startswith("menu_"))
        async def menu_callback(callback):
            user_id = callback.from_user.id
            self.db.update_user_activity(user_id)
            data = callback.data
            if data == "menu_feed":
                self.current_category = None
                self.current_rank_filter = None
                self.current_prime_filter = None
                await self.send_next(callback.message, category=None, delete_old=True, actor_id=user_id)
            elif data == "menu_clubs":
                await callback.message.delete()
                await callback.message.answer(
                    "<b>🏛 Клубы</b>\n\n"
                    "«Я ищу клуб» — объявления от клубов, которые набирают игроков.\n"
                    "«Я ищу людей в клуб» — заявки игроков, которые ищут клуб.\n\n"
                    "Выберите категорию:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Я ищу клуб", callback_data="club_find")],
                        [InlineKeyboardButton(text="🏛 Я ищу людей в клуб", callback_data="club_recruit")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back")],
                    ]),
                    parse_mode="HTML"
                )
            elif data == "menu_communication":
                self.current_category = Category.COMMUNICATION
                await self.send_next(callback.message, category=Category.COMMUNICATION, delete_old=True, actor_id=user_id)
            elif data == "menu_ranked":
                await callback.message.delete()
                await callback.message.answer(
                    "⚔️ Ранговый бой\nВыберите Ваш текущий ранг.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💎 Алмаз", callback_data="ranked_diamond")],
                        [InlineKeyboardButton(text="🔥 Мифик", callback_data="ranked_mythic")],
                        [InlineKeyboardButton(text="⭐ Легендарная", callback_data="ranked_legendary")],
                        [InlineKeyboardButton(text="👑 Мастер", callback_data="ranked_master")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back")],
                    ]),
                    parse_mode="HTML"
                )
            elif data == "menu_primes":
                await callback.message.delete()
                await callback.message.answer(
                    "⭐ Праймы",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⭐ 1", callback_data="primes_1")],
                        [InlineKeyboardButton(text="⭐⭐ 2", callback_data="primes_2")],
                        [InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data="primes_3")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back")],
                    ])
                )
            elif data == "menu_stats":
                stats = self.db.get_stats()
                cat_lines = []
                for cat, cnt in stats["by_category"]:
                    label = Category.safe_from_string(cat).label
                    cat_lines.append(f"{label}: {cnt}")
                text = (
                    "<b>📊 Статистика</b>\n\n"
                    f"Всего сообщений: {stats['total']}\n"
                    f"Непросмотренных: {stats['unviewed']}\n\n"
                    f"Бот собирает базу с {format_collection_start(self.db)}\n"
                    f"Бот работает с {BOT_START_TIME}\n\n"
                    "<b>По категориям:</b>\n" + "\n".join(cat_lines)
                )
                await callback.message.delete()
                await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back")]
                ]), parse_mode="HTML")
            elif data == "menu_back":
                self.current_category = None
                self.current_rank_filter = None
                self.current_prime_filter = None
                await self.show_main_menu(callback.message, delete_old=True, user_id=user_id)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data in ("club_find", "club_recruit"))
        async def club_submenu(callback):
            user_id = callback.from_user.id
            if callback.data == "club_find":
                self.current_category = Category.RECRUITING
                await self.send_next(callback.message, category=Category.RECRUITING, delete_old=True, actor_id=user_id)
            else:
                self.current_category = Category.FINDING_CLUB
                await self.send_next(callback.message, category=Category.FINDING_CLUB, delete_old=True, actor_id=user_id)
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("ranked_"))
        async def ranked_submenu(callback):
            rank_map = {
                "ranked_diamond": "diamond",
                "ranked_mythic": "mythic",
                "ranked_legendary": "legendary",
                "ranked_master": "master",
            }
            rank_key = rank_map.get(callback.data)
            if not rank_key:
                await callback.answer("❌ Неизвестный ранг", show_alert=True)
                return
            self.current_category = Category.RANKED
            self.current_rank_filter = rank_key
            await self.send_next(
                callback.message,
                category=Category.RANKED,
                rank_filter=rank_key,
                delete_old=True,
                actor_id=callback.from_user.id,
            )
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith("primes_"))
        async def primes_submenu(callback):
            prime_num = int(callback.data.split("_")[1])
            self.current_category = Category.PRIMES
            self.current_prime_filter = prime_num
            await self.send_next(
                callback.message,
                category=Category.PRIMES,
                prime_filter=prime_num,
                delete_old=True,
                actor_id=callback.from_user.id,
            )
            await callback.answer()

        @self.dp.callback_query(lambda c: c.data.startswith(("write_", "skip_", "prev_")))
        async def action_callback(callback):
            user_id = callback.from_user.id
            self.db.update_user_activity(user_id)
            if callback.data.startswith("write_"):
                uid = callback.data.split("_", 1)[1]
                msg = self.db.get_message_by_uid(uid)
                if msg and msg.user_username:
                    url = f"https://t.me/{msg.user_username}"
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📩 Перейти в диалог", url=url)],
                        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
                    ])
                    await callback.message.answer(
                        f"✉️ Напишите пользователю: @{msg.user_username}",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    await callback.answer()
                elif msg:
                    await callback.answer(f"❌ У пользователя нет username, ссылка: {msg.link}", show_alert=True)
                else:
                    await callback.answer("❌ Сообщение не найдено", show_alert=True)
                return
            if callback.data.startswith("skip_"):
                uid = callback.data.split("_", 1)[1]
                self.db.mark_viewed(uid)
                await callback.answer("⏭ Пропущено")
                await self.send_next(
                    callback.message,
                    category=self.current_category,
                    rank_filter=self.current_rank_filter,
                    prime_filter=self.current_prime_filter,
                    delete_old=True,
                    actor_id=user_id,
                )
                return
            if callback.data.startswith("prev_"):
                uid = callback.data.split("_", 1)[1]
                if not self.db.get_message_by_uid(uid):
                    await callback.answer("❌ Сообщение удалено", show_alert=True)
                    if user_id in self.history and uid in self.history[user_id]:
                        self.history[user_id].remove(uid)
                    await self.show_previous_or_error(callback.message, actor_id=user_id)
                    return
                await self.send_message_display(
                    callback.message, uid, self.current_category,
                    delete_old=True, add_to_history=False, actor_id=user_id,
                )
                await callback.answer()

    async def run(self):
        await self.dp.start_polling(self.bot)


async def main():
    db = Database()
    parser = TriggerManager()
    bot = Bot(token=BOT_TOKEN)

    try:
        chat = await bot.get_chat("@kabachcache_news")
        db.init_default_subscription(str(chat.id), chat.title or "kabachcache_news", chat.username)
        logger.info(f"Канал @kabachcache_news подключён: {chat.title}")
    except Exception as e:
        logger.error(f"Не удалось найти канал @kabachcache_news: {e}")

    total = db.get_total_messages()
    if total > CLEANUP_THRESHOLD:
        deleted = db.delete_old_messages(days=CLEANUP_DAYS, limit=CLEANUP_LIMIT)
        if deleted:
            db.set_last_cleanup_date(datetime.now().isoformat())
            logger.info(f"При старте удалено {deleted} старых сообщений (всего было {total})")
        else:
            logger.info(f"При старте старых сообщений не найдено (всего {total})")
    else:
        logger.info(f"При старте очистка не требуется: сообщений {total} (порог {CLEANUP_THRESHOLD})")

    async def cleaner_loop():
        while True:
            try:
                total = db.get_total_messages()
                if total > CLEANUP_THRESHOLD:
                    deleted = db.delete_old_messages(days=CLEANUP_DAYS, limit=CLEANUP_LIMIT)
                    if deleted:
                        db.set_last_cleanup_date(datetime.now().isoformat())
                        logger.info(f"Удалено {deleted} старых сообщений (всего было {total})")
                else:
                    logger.debug(f"Очистка не требуется: всего сообщений {total} (порог {CLEANUP_THRESHOLD})")
            except Exception as e:
                logger.error(f"Ошибка очистки: {e}")
            await asyncio.sleep(CLEANUP_INTERVAL)

    collector = Collector(db, parser, bot)
    admin_bot = AdminBot(db, collector, bot)

    await asyncio.gather(
        collector.start(),
        admin_bot.run(),
        cleaner_loop(),
    )

from flask import Flask
import threading
import os
import asyncio

app = Flask(__name__)

@app.route('/')
def home():
    return "Brawl Scout Bot is running!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

async def main():
    db = Database()
    parser = TriggerManager()
    bot = Bot(token=BOT_TOKEN)

    try:
        chat = await bot.get_chat("@kabachcache_news")
        db.init_default_subscription(str(chat.id), chat.title or "kabachcache_news", chat.username)
        logger.info(f"Канал @kabachcache_news подключён: {chat.title}")
    except Exception as e:
        logger.error(f"Не удалось найти канал @kabachcache_news: {e}")

    total = db.get_total_messages()
    if total > CLEANUP_THRESHOLD:
        deleted = db.delete_old_messages(days=CLEANUP_DAYS, limit=CLEANUP_LIMIT)
        if deleted:
            db.set_last_cleanup_date(datetime.now().isoformat())
            logger.info(f"При старте удалено {deleted} старых сообщений (всего было {total})")
        else:
            logger.info(f"При старте старых сообщений не найдено (всего {total})")
    else:
        logger.info(f"При старте очистка не требуется: сообщений {total} (порог {CLEANUP_THRESHOLD})")

    async def cleaner_loop():
        while True:
            try:
                total = db.get_total_messages()
                if total > CLEANUP_THRESHOLD:
                    deleted = db.delete_old_messages(days=CLEANUP_DAYS, limit=CLEANUP_LIMIT)
                    if deleted:
                        db.set_last_cleanup_date(datetime.now().isoformat())
                        logger.info(f"Удалено {deleted} старых сообщений (всего было {total})")
                else:
                    logger.debug(f"Очистка не требуется: всего сообщений {total} (порог {CLEANUP_THRESHOLD})")
            except Exception as e:
                logger.error(f"Ошибка очистки: {e}")
            await asyncio.sleep(CLEANUP_INTERVAL)

    collector = Collector(db, parser, bot)
    admin_bot = AdminBot(db, collector, bot)

    # Запускаем Flask в отдельном потоке (для Railway)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запускаем бота
    await asyncio.gather(
        collector.start(),
        admin_bot.run(),
        cleaner_loop(),
    )

if __name__ == "__main__":
    asyncio.run(main())