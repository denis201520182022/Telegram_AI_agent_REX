import re

def clean_html_for_telegram(text: str) -> str:
    """
    Превращает веб-HTML от ИИ в Telegram-HTML.
    """
    # 1. Убираем обертки Markdown
    text = re.sub(r'```html', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)

    # 2. Вырезаем служебные теги
    text = re.sub(r'<!DOCTYPE[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<html[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</html>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<head>.*?</head>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<body[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</body>', '', text, flags=re.IGNORECASE)

    # 3. ТАБЛИЦЫ (Заменяем на перенос строки, чтобы не было ошибки Bad Request)
    # Мы просто удаляем теги таблиц, оставляя контент внутри
    text = re.sub(r'</?(table|tr|thead|tbody|th|td)[^>]*>', '\n', text, flags=re.IGNORECASE)

    # 4. СПИСКИ
    text = re.sub(r'<ul[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</ul>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<ol[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</ol>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '\n• ', text, flags=re.IGNORECASE) # Обычная жирная точка
    text = re.sub(r'</li>', '', text, flags=re.IGNORECASE)

    # 5. Параграфы и заголовки
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<div[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<h[1-6][^>]*>', '\n<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '</b>\n', text, flags=re.IGNORECASE)

    # 6. Чистка мусора
    text = re.sub(r'<span[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</span>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()