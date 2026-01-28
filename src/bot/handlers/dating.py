from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import Counter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from src.database.session import async_session_maker
from src.database.models import DatingMatch, User
from src.bot.keyboards.dating import get_contact_kb
from src.services.rabbit import send_to_queue
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from src.bot.handlers.survey import start_generic_survey
from src.services.matching import run_daily_matching

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert

router = Router()

# --- МЕТРИКИ ---
DATING_INTERACTIONS = Counter('rex_dating_interactions_total', 'Total dating actions', ['action'])
DATING_MATCHES = Counter('rex_dating_matches_new_total', 'Total mutual matches found')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def _create_interaction_record(session: AsyncSession, user_id: int, target_user_id: int, action: str) -> bool:
    """
    Проверяет, было ли уже взаимодействие, и создает новую запись.
    """
    # Проверяем, голосовал ли юзер уже за этого кандидата
    existing = await session.execute(
        select(DatingMatch).where(
            and_(DatingMatch.user_id == user_id, DatingMatch.target_user_id == target_user_id)
        )
    )
    if existing.scalar_one_or_none():
        return False

    # Создаем новую запись
    record = DatingMatch(user_id=user_id, target_user_id=target_user_id, action=action)
    session.add(record)
    return True

def _get_user_mention(user: User) -> str:
    """Формирует кликабельное имя или юзернейм."""
    if user.username:
        return f"@{user.username}"
    return f"<b>{user.full_name}</b>"


# --- ХЕНДЛЕРЫ ---

@router.callback_query(F.data.startswith("like_"))
async def process_like(callback: CallbackQuery):
    try:
        target_user_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        log = logger.bind(user_id=user_id, target_id=target_user_id, action="like")

        if target_user_id == user_id:
            return await callback.answer("Себя лайкать нельзя 😅")

        async with async_session_maker() as session:
            # 1. Фиксируем лайк
            if not await _create_interaction_record(session, user_id, target_user_id, "like"):
                return await callback.answer("Вы уже проголосовали за эту анкету.")

            # 2. Проверяем взаимность (был ли лайк от того пользователя мне)
            mutual_like_stmt = select(DatingMatch).where(
                and_(
                    DatingMatch.user_id == target_user_id, 
                    DatingMatch.target_user_id == user_id, 
                    DatingMatch.action == "like"
                )
            )
            mutual_like = (await session.execute(mutual_like_stmt)).scalar_one_or_none()

            is_match = False
            if mutual_like:
                is_match = True
                # Достаем текущую запись, которую только что добавили через session.add
                # Чтобы пометить её как match
                my_like_stmt = select(DatingMatch).where(
                    and_(DatingMatch.user_id == user_id, DatingMatch.target_user_id == target_user_id)
                )
                my_like_record = (await session.execute(my_like_stmt)).scalar_one_or_none()
                
                if my_like_record:
                    my_like_record.is_match = True
                mutual_like.is_match = True
            
            await session.commit()

            DATING_INTERACTIONS.labels(action="like").inc()
            log.info("dating_like_processed", is_match=is_match)

            # 3. Обновляем интерфейс
            await callback.answer("❤️ Лайк отправлен!")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Анкета обработана. Новая подборка придет завтра!")

            # 4. Обработка совпадения
            if is_match:
                DATING_MATCHES.inc()
                me = await session.get(User, user_id)
                target = await session.get(User, target_user_id)

                if not me or not target:
                    return

                # Уведомление текущему юзеру (который нажал лайк)
                await callback.message.answer(
                    f"🎉 <b>IT'S A MATCH!</b>\n{_get_user_mention(target)} ответил(а) вам взаимностью!",
                    reply_markup=get_contact_kb(target.username),
                    parse_mode="HTML"
                )
                
                # Уведомление второму юзеру (через воркер)
                await send_to_queue("q_notifications", {
                    "user_id": target_user_id,
                    "text": f"🎉 <b>У вас новое совпадение!</b>\nПользователь {_get_user_mention(me)} лайкнул вас в ответ!",
                    "keyboard": get_contact_kb(me.username).model_dump()
                })
                log.info("match_notifications_triggered")

    except Exception as e:
        logger.error("dating_like_error", error=str(e), user_id=callback.from_user.id)
        await send_alert(e, context="Dating Like Handler")
        await callback.answer("Ошибка при обработке лайка", show_alert=True)


@router.callback_query(F.data.startswith("dislike_"))
async def process_dislike(callback: CallbackQuery):
    try:
        target_user_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id
        
        log = logger.bind(user_id=user_id, target_id=target_user_id, action="dislike")

        async with async_session_maker() as session:
            if not await _create_interaction_record(session, user_id, target_user_id, "dislike"):
                return await callback.answer("Вы уже проголосовали за эту анкету.")
            await session.commit()

        DATING_INTERACTIONS.labels(action="dislike").inc()
        log.info("dating_dislike_processed")

        await callback.answer("👎 Пропущено")
        await callback.message.edit_text("🚫 Анкета скрыта. Следующая подборка будет завтра.")
        
    except Exception as e:
        logger.error("dating_dislike_error", error=str(e), user_id=callback.from_user.id)
        await send_alert(e, context="Dating Dislike Handler")
        await callback.answer("Ошибка", show_alert=True)

@router.message(F.text == "📝 Изменить анкету")
async def edit_dating_profile(message: Message, state: FSMContext):
    await message.answer(
        "🔄 Обновляем анкету знакомств...",
        reply_markup=ReplyKeyboardRemove()
    )
    await start_generic_survey(message, state, mode="dating")