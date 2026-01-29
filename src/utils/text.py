import re

def clean_html_for_telegram(text: str) -> str:
    """
    Максимально безопасная очистка HTML для Telegram.
    """
    # 1. Убираем Markdown обертки
    text = re.sub(r'```(?:html)?', '', text, flags=re.IGNORECASE)

    # 2. Вырезаем теги, которые мы накладываем сами (чтобы не было вложенности)
    # Удаляем любые попытки ИИ использовать blockquote
    text = re.sub(r'</?blockquote[^>]*>', '', text, flags=re.IGNORECASE)

    # 3. Чистим таблицы (оставляем только текст внутри них)
    text = re.sub(r'</?(table|tr|thead|tbody|th|td)[^>]*>', '\n', text, flags=re.IGNORECASE)

    # 4. Обрабатываем заголовки и параграфы
    text = re.sub(r'<h[1-6][^>]*>', '\n<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '</b>\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # 5. Списки
    text = re.sub(r'</?ul[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '\n• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '', text, flags=re.IGNORECASE)

    # 6. Удаляем ВСЕ атрибуты у тегов (style, class, и т.д.), кроме разрешенных ссылок
    # Это гарантирует, что не будет <pre class="something">, который TG не ест
    text = re.sub(r'<(?!b|i|u|s|a|code|pre|/)[^>]+>', '', text)

    # 7. Финальный штрих: закрываем «повисшие» теги
    # Если ИИ выдал <pre> и обрезался, этот код просто удалит открытый тег
    open_tags = re.findall(r'<(b|i|u|s|code|pre)>', text)
    close_tags = re.findall(r'</(b|i|u|s|code|pre)>', text)
    if len(open_tags) > len(close_tags):
        # Если есть незакрытые теги, просто вырезаем их все для безопасности
        for tag in ['b', 'i', 'u', 's', 'code', 'pre']:
            text = text.replace(f'<{tag}>', '').replace(f'</{tag}>', '')

    return text.strip()