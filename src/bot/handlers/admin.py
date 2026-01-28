import secrets
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func

from src.database.session import async_session_maker
from src.database.models import User, QRCode, UserSurvey, DatingMatch
from src.config import settings

router = Router()

def get_admin_ids():
    """Парсит ID админов из конфига с защитой от пробелов."""
    try:
        return [int(x.strip()) for x in settings.ADMIN_IDS.split(',')]
    except Exception:
        return []

# Применяем фильтр на весь роутер: доступ только у админов
router.message.filter(F.from_user.id.in_(get_admin_ids()))

@router.message(F.text == "🔒 Админка")
async def admin_stats(message: Message):
    """Показывает общую статистику проекта."""
    
    async with async_session_maker() as session:
        # 1. Всего пользователей
        total_users = await session.scalar(select(func.count(User.user_id)))
        
        # 2. Активные подписки
        active_subs = await session.scalar(
            select(func.count(User.user_id)).where(User.subscription_expires_at > func.now())
        )
        
        # 3. QR коды (Использовано / Всего)
        activated_qrs = await session.scalar(
            select(func.count(QRCode.code_hash)).where(QRCode.activated_at.is_not(None))
        )
        total_qrs = await session.scalar(select(func.count(QRCode.code_hash)))
        
        # 4. Анкеты
        total_surveys = await session.scalar(select(func.count(UserSurvey.id)))
        
        # 5. Мэтчи
        matches = await session.scalar(
            select(func.count(DatingMatch.id)).where(DatingMatch.is_match == True)
        )

    text = (
        "📊 <b>Статистика REX Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Пользователей:</b> {total_users}\n"
        f"💎 <b>Активных подписок:</b> {active_subs}\n\n"
        f"🎫 <b>QR-коды:</b> {activated_qrs} / {total_qrs}\n"
        f"📝 <b>Заполнено анкет:</b> {total_surveys}\n"
        f"💘 <b>Мэтчей:</b> {matches}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛠 <b>Инструменты:</b>\n"
        "/gen_qr — Сгенерировать 1 код доступа"
    )
    
    await message.answer(text, parse_mode="HTML")

@router.message(Command("gen_qr"))
async def generate_single_qr(message: Message):
    """
    Быстрая генерация QR-кода (хеша) для выдачи доступа вручную.
    """
    code = secrets.token_urlsafe(8) # Генерируем случайную строку
    
    async with async_session_maker() as session:
        qr = QRCode(
            code_hash=code, 
            batch_id="admin_manual", 
            is_active=True
        )
        session.add(qr)
        await session.commit()
        
    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={code}"
    
    await message.answer(
        f"✅ <b>Код доступа создан!</b>\n\n"
        f"🔑 Код: <code>{code}</code>\n"
        f"🔗 Ссылка: {link}",
        parse_mode="HTML"
    )