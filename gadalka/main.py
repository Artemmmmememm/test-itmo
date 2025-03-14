import logging
import os
import sqlite3
import pytz
from zoneinfo import ZoneInfo
from datetime import datetime, time
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    JobQueue,
)
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

# Конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("horoscope_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Состояния разговора
CHOOSE_SIGN, GET_HOROSCOPE = range(2)

ZODIAC_SIGNS = [
    "Овен", "Телец", "Близнецы", "Рак",
    "Лев", "Дева", "Весы", "Скорпион",
    "Стрелец", "Козерог", "Водолей", "Рыбы"
]

class DatabaseManager:
    def __init__(self, db_name='horoscope_bot.db'):
        self.conn = sqlite3.connect(db_name)
        self._init_db()

    def _init_db(self):
        """Инициализация таблиц в базе данных"""
        with self.conn:
            # Таблица пользователей
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    zodiac_sign TEXT,
                    notification_time TIME,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Таблица гороскопов
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS horoscopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    zodiac_sign TEXT,
                    prediction TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')

    def update_user_zodiac(self, user_id: int, zodiac_sign: str):
        """Обновление знака зодиака пользователя (исправленный запрос)"""
        with self.conn:
            self.conn.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, zodiac_sign, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, zodiac_sign))

    def save_horoscope(self, user_id: int, zodiac_sign: str, prediction: str):
        """Сохранение гороскопа в историю"""
        with self.conn:
            self.conn.execute('''
                INSERT INTO horoscopes 
                (user_id, zodiac_sign, prediction)
                VALUES (?, ?, ?)
            ''', (user_id, zodiac_sign, prediction))

    def get_users_for_notification(self):
        """Получение списка пользователей для рассылки"""
        with self.conn:
            cursor = self.conn.execute('''
                SELECT user_id, zodiac_sign 
                FROM users 
                WHERE notification_time IS NOT NULL
            ''')
            return cursor.fetchall()

    def close(self):
        """Закрытие соединения с БД"""
        self.conn.close()

class HoroscopeBot:
    SYSTEM_PROMPT = """Ты профессиональный астролог с 20-летним опытом. 
    Составь подробный гороскоп на сегодня для указанного знака зодиака.
    Структура гороскопа:
    1. Общая характеристика дня
    2. Любовь и отношения
    3. Финансы и карьера
    4. Здоровье
    5. Советы дня
    
    Стиль: позитивный, мотивирующий, с элементами юмора. 
    Избегай общих фраз, сделай прогноз персонализированным.
    Объем: 200-250 слов."""

    def __init__(self):
        self._check_env_vars()
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.giga_credentials = os.getenv("GIGACHAT_CREDENTIALS")
        self.db = DatabaseManager()
        self.giga_client = GigaChat(
            credentials=self.giga_credentials,
            verify_ssl_certs=False
        )

    def _check_env_vars(self):
        required_vars = ["TELEGRAM_BOT_TOKEN", "GIGACHAT_CREDENTIALS"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            logger.error(f"Отсутствуют переменные: {', '.join(missing_vars)}")
            raise ValueError("Необходимые переменные окружения не заданы")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        user = update.effective_user
        keyboard = [[InlineKeyboardButton("Получить гороскоп ♉", callback_data='get_horoscope')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✨ Приветствую, {user.first_name}! Я ваш персональный астролог.\n"
            "Я могу составить для вас персональный гороскоп на сегодня.\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

    async def show_zodiac_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать меню выбора знака зодиака"""
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton(sign, callback_data=sign)]
            for sign in ZODIAC_SIGNS
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="🔮 Выберите ваш знак зодиака:",
            reply_markup=reply_markup
        )

    async def generate_horoscope(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Генерация гороскопа"""
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        zodiac_sign = query.data

        await query.edit_message_text(text=f"🔍 Составляю гороскоп для {zodiac_sign}...")

        try:
            # Генерация гороскопа
            prediction = await self._get_horoscope_prediction(zodiac_sign)
            
            # Сохранение данных
            self.db.update_user_zodiac(user_id, zodiac_sign)
            self.db.save_horoscope(user_id, zodiac_sign, prediction)
            
            # Отправка результата
            await query.message.reply_text(
                f"♉ Ваш гороскоп на сегодня ({zodiac_sign}):\n\n{prediction}"
            )

        except Exception as e:
            logger.error(f"Ошибка генерации гороскопа: {str(e)}")
            await query.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

    async def _get_horoscope_prediction(self, zodiac_sign: str) -> str:
        """Получение прогноза от GigaChat"""
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self.SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=f"Знак зодиака: {zodiac_sign}")
        ]
        
        response = self.giga_client.chat(Chat(messages=messages, model="GigaChat"))
        return response.choices[0].message.content

    async def daily_horoscope_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Ежедневная рассылка гороскопов"""
        users = self.db.get_users_for_notification()
        
        for user_id, zodiac_sign in users:
            try:
                prediction = await self._get_horoscope_prediction(zodiac_sign)
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🌅 Ваш ежедневный гороскоп ({zodiac_sign}):\n\n{prediction}"
                )
                self.db.save_horoscope(user_id, zodiac_sign, prediction)
            except Exception as e:
                logger.error(f"Ошибка отправки гороскопа для {user_id}: {str(e)}")

    def run(self):
        """Запуск бота"""
        application = Application.builder().token(self.tg_token).build()
        job_queue = application.job_queue

        # Настройка ежедневной рассылки в 09:00 по Москве
        job_queue.run_daily(
            self.daily_horoscope_job,
            time(hour=9, minute=0, tzinfo=ZoneInfo("Europe/Moscow")),
            name="daily_horoscope"
        )

        # Регистрация обработчиков
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CallbackQueryHandler(self.show_zodiac_menu, pattern='^get_horoscope$'))
        application.add_handler(CallbackQueryHandler(self.generate_horoscope, pattern=f'^({"|".join(ZODIAC_SIGNS)})$'))

        try:
            logger.info("Horoscope Bot started")
            application.run_polling()
        finally:
            self.db.close()

if __name__ == "__main__":
    try:
        bot = HoroscopeBot()
        bot.run()
    except Exception as e:
        logger.critical(f"Ошибка запуска: {str(e)}")