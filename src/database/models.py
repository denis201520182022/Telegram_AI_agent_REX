import datetime
from sqlalchemy import BigInteger, String, Boolean, DateTime, ForeignKey, Integer, JSON, Text, Date, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.sql import func

# Базовый класс для всех моделей
class Base(AsyncAttrs, DeclarativeBase):
    pass

# 1. Пользователи
class User(Base):
    __tablename__ = 'users'

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Роль и доступ
    role: Mapped[str] = mapped_column(String(20), default='user')
    subscription_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Геймификация
    qr_activations_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    natal_chart_credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    
    # --- НОВЫЕ ПОЛЯ (КОУЧИНГ И ТРЕКИНГ) ---
    # Режим коуча: 'nutrition' (Питание), 'workout' (Тренировки), 'combined' (Вместе) или None (не выбран)
    coach_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    
    # Часовой пояс (например, "Europe/Moscow")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", server_default="UTC")
    
    # Время, когда бот пишет пользователю (например, 20:00:00)
    tracking_time: Mapped[datetime.time | None] = mapped_column(Time, nullable=True)
    
    # Включен ли трекинг вообще
    is_tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    
    # Принял ли политику обработки данных
    has_accepted_policy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    coach_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # --------------------------------------

    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    
    # Связи
    surveys: Mapped[list["UserSurvey"]] = relationship(back_populates="user")
    qr_activation: Mapped["QRCode"] = relationship(back_populates="activated_by_user", uselist=False)
    





# 3. QR Коды (Без изменений)
class QRCode(Base):
    __tablename__ = 'qr_codes'

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    
    activated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.user_id'), nullable=True)
    
    activated_by_user: Mapped["User"] = relationship(back_populates="qr_activation")


# 4. Конфигурация анкет (Без изменений, но будем использовать поле mode для подтипов)
class SurveyConfig(Base):
    __tablename__ = 'survey_configs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(20)) 
    version: Mapped[str] = mapped_column(String(50))
    structure: Mapped[dict] = mapped_column(JSON) 
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


# 5. Ответы пользователей (Добавили current_plan)
class UserSurvey(Base):
    __tablename__ = 'user_surveys'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    
    mode: Mapped[str] = mapped_column(String(20)) # 'coach', 'dating', 'natal'
    sub_mode: Mapped[str | None] = mapped_column(String(20), nullable=True) # 'nutrition', 'workout', 'combined'
    
    survey_config_id: Mapped[int] = mapped_column(ForeignKey('survey_configs.id'))
    
    answers: Mapped[dict] = mapped_column(JSON)
    
    # Сгенерированная рекомендация (старое поле, оставим для совместимости)
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # НОВОЕ ПОЛЕ: Текущий активный план, который ИИ должен учитывать
    current_plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # НОВОЕ ПОЛЕ: План в виде чистого JSON (структура PlanSection)
    # Именно это поле мы будем скармливать ИИ как "память"
    current_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    
    user: Mapped["User"] = relationship(back_populates="surveys")


# 6. Дейтинг (Без изменений)
class DatingMatch(Base):
    __tablename__ = 'dating_matches'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    target_user_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(10))
    is_match: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


# 7. Ежедневный трекинг (Упростили логику)
class DailyTracking(Base):
    __tablename__ = 'daily_tracking'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    
    # mode будет совпадать с coach_mode ('nutrition', 'workout', 'combined')
    mode: Mapped[str] = mapped_column(String(20)) 

    date: Mapped[datetime.date] = mapped_column(Date, default=func.current_date())
    status: Mapped[str] = mapped_column(String(20)) # 'success', 'partial', 'fail'
    
    # Сюда можно сохранять короткий коммент от ИИ, если нужно
    ai_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())