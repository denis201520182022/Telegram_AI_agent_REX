#src\bot\main.py

import asyncio
import sys
import datetime
from os.path import abspath, dirname

# --- НАСТРОЙКА ПУТЕЙ ---
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
from aiogram.client.session.aiohttp import AiohttpSession # <--- ДОБАВИТЬ ЭТУ СТРОКУ
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select
from src.utils.admin import is_admin
from src.config import settings
from src.database.session import async_session_maker
from src.database.models import User, QRCode
from src.services.redis import redis_client
from src.bot.middlewares.activity import ActivityMiddleware
# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server, USER_UPDATES
from src.utils.alerting import send_alert

# --- ИМПОРТЫ РОУТЕРОВ ---
from src.bot.handlers import admin as admin_router
from src.bot.handlers import dating as dating_router
from src.bot.handlers import profile as profile_router 
from src.bot.handlers import survey as survey_router
from src.bot.handlers import menu as menu_router 
from src.bot.handlers import coach as coach_router

from src.bot.middlewares.check_sub import CheckSubscriptionMiddleware
from src.bot.keyboards.menu import get_main_menu

# Это строка, которая будет зашита в ваш единый QR-код: t.me/RexBot?start=REX_COMPANY_PROMO_2024
GLOBAL_QR_HASH = "Hs5-6Ntybfs"

async def start_handler(message: Message, command: CommandObject):
    """
    Обработчик /start. 
    Поддерживает deep-linking для активации QR-кодов: /start <qr_hash>
    """
    args = command.args 
    user_id = message.from_user.id
    
    log = logger.bind(user_id=user_id, command="start")
    USER_UPDATES.labels(type="command_start").inc()
    
    async with async_session_maker() as session:
        # 1. Получаем или создаем пользователя
        stmt = select(User).where(User.user_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                user_id=user_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            session.add(user)
            await session.commit()
            log.info("new_user_registered")

        # --- 2. ЛОГИКА АКТИВАЦИИ QR-КОДА ---
        if args:
            qr_hash = args
            now = datetime.datetime.now(datetime.timezone.utc)
            
            # Переменная для хранения объекта QR (если он найден в базе)
            qr_obj = None

            # А. Если это НЕ глобальный код — ищем и проверяем его в базе
            if qr_hash != GLOBAL_QR_HASH:
                q_stmt = select(QRCode).where(QRCode.code_hash == qr_hash)
                q_res = await session.execute(q_stmt)
                qr_obj = q_res.scalar_one_or_none()

                # Проверки для обычных кодов
                if not qr_obj:
                    log.warning("invalid_qr_attempt", code_hash=qr_hash)
                    await message.answer("❌ <b>Неверный QR-код.</b>\nПожалуйста, убедитесь, что ссылка корректна.")
                    return

                if not qr_obj.is_active:
                    await message.answer("❌ Этот код деактивирован.")
                    return

                if qr_obj.activated_at:
                    if qr_obj.activated_by_id == user_id:
                        await message.answer("ℹ️ Вы уже активировали этот код ранее.")
                    else:
                        await message.answer("❌ Этот код уже использован другим пользователем.")
                    
                    # Показываем меню, если есть доступ
                    if is_admin(user_id) or (user.subscription_expires_at and user.subscription_expires_at > now):
                        kb = get_main_menu(natal_credits=user.natal_chart_credits, is_admin=is_admin(user_id))
                        await message.answer("🏠 Главное меню:", reply_markup=kb)
                    return
            
            # Б. Логика "Сжигания" кода (Только если это реальный код из базы)
            if qr_obj:
                qr_obj.activated_at = now
                qr_obj.activated_by_id = user_id
            
            # В. Логика начисления бонусов (Одинаковая для ВСЕХ кодов)
            user.qr_activations_count += 1
            
            # Начисление бонусов (Натальная карта за каждые 5 кодов)
            bonus_msg = ""
            if user.qr_activations_count % 5 == 0:
                user.natal_chart_credits += 1
                bonus_msg = "\n\n🌟 <b>Бонус!</b> Вы активировали 5-й код. Вам доступна 1 генерация Натальной карты!"
            else:
                left = 5 - (user.qr_activations_count % 5)
                bonus_msg = f"\n\n<i>(Еще {left} активации до получения Натальной карты)</i>"
            
            # Продление подписки (+5 дней за ЛЮБОЙ код, включая глобальный)
            days_to_add = datetime.timedelta(days=5)
            
            if user.subscription_expires_at and user.subscription_expires_at > now:
                user.subscription_expires_at += days_to_add
            else:
                user.subscription_expires_at = now + days_to_add
            
            await session.commit()
            log.info("qr_activated_successfully", code_hash=qr_hash, is_global=(qr_hash == GLOBAL_QR_HASH))
            
            expires_str = user.subscription_expires_at.strftime("%d.%m.%Y")
            kb = get_main_menu(natal_credits=user.natal_chart_credits, is_admin=is_admin(user_id))
            
            await message.answer(
                f"✅ <b>Доступ активирован!</b>\nПодписка продлена до: <b>{expires_str}</b>" + bonus_msg,
                reply_markup=kb
            )
            return

        # --- 3. ОБЫЧНЫЙ ВХОД (БЕЗ QR) ---
        now = datetime.datetime.now(datetime.timezone.utc)
        admin_status = is_admin(user_id)
        
        if admin_status or (user.subscription_expires_at and user.subscription_expires_at > now):
             kb = get_main_menu(natal_credits=user.natal_chart_credits, is_admin=admin_status)
             await message.answer("🏠 Главное меню:", reply_markup=kb)
        else:
             await message.answer(
                 "👋 Привет! Я <b>REX Bot</b>.\n\n"
                 "Я помогу тебе с питанием, тренировками и даже подберу партнера для спорта.\n\n"
                 "⚠️ <b>Доступ ограничен.</b>\n"
                 "Чтобы начать пользоваться функциями коуча, отсканируй QR-код с упаковки REX."
             )

             
async def main():
    """Инициализация и запуск бота."""
    logger.info("service_started", service="bot_polling")
    
    # Хранилище FSM в Redis
    storage = RedisStorage(redis=redis_client)
    
    # --- НАСТРОЙКА ПРОКСИ ---
    # Формируем URL из твоих данных (http://user:password@ip:port)
    proxy_url = (
        f"http://{settings.SQUID_PROXY_USER}:{settings.SQUID_PROXY_PASSWORD}@"
        f"{settings.SQUID_PROXY_HOST}:{settings.SQUID_PROXY_PORT}"
    )
    session = AiohttpSession(proxy=proxy_url)
    # ------------------------
    
    bot = Bot(
        token=settings.BOT_TOKEN.get_secret_value(),
        session=session,  # <--- ПЕРЕДАЕМ СЕССИЮ С ПРОКСИ СЮДА
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=storage)

    # Middlewares
    dp.update.outer_middleware(ActivityMiddleware()) 
    dp.message.middleware(CheckSubscriptionMiddleware())
    dp.callback_query.middleware(CheckSubscriptionMiddleware())

    # Регистрация роутеров (ПОРЯДОК ВАЖЕН)
    dp.include_router(admin_router.router)  # Админка первая
    dp.include_router(menu_router.router)   # Главное меню
    dp.include_router(coach_router.router)  # Логика чата коуча
    dp.include_router(survey_router.router) # Опросы
    #dp.include_router(dating_router.router) # Дейтинг (лайки/дизлайки)
    dp.include_router(profile_router.router)# Настройки профиля
    
    # Регистрация стартовой команды
    dp.message.register(start_handler, CommandStart())
    
    logger.info("bot_polling_started")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical("bot_crashed", error=str(e))
        await send_alert(e, context="Bot Polling Critical Failure")
        raise e

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Метрики на порту 8002
    start_metrics_server(8002)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("service_stopped")