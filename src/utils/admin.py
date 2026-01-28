from src.config import settings

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    try:
        admin_ids = [int(x.strip()) for x in settings.ADMIN_IDS.split(',')]
        return user_id in admin_ids
    except Exception:
        return False