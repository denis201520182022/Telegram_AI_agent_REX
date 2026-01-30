import re
def clean_html_for_telegram(text: str) -> str:
    """
    Уплотненная очистка для красивого отображения в blockquote.
    """
    # 1. Базовая чистка (Markdown и мусор)
    text = re.sub(r'```(?:html)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)
    text = re.sub(r'</?blockquote[^>]*>', '', text, flags=re.IGNORECASE)

    # 2. Уплотняем структуру: заменяем закрывающие блоки на ОДИН перенос строки
    text = re.sub(r'</?(table|tr|thead|tbody|th|td)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE) # Было \n\n
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<h[1-6][^>]*>', '\n<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '</b>\n', text, flags=re.IGNORECASE)

    # 3. Списки (убираем лишние отступы)
    text = re.sub(r'</?ul[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '\n• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '', text, flags=re.IGNORECASE)

    # 4. Удаляем все лишние атрибуты тегов
    text = re.sub(r'<(?!b|i|u|s|a|code|pre|/)[^>]+>', '', text)

    # 5. МАГИЯ УПЛОТНЕНИЯ:
    # Удаляем пробелы в начале и конце каждой строки
    lines = [line.strip() for line in text.split('\n')]
    # Собираем обратно, схлопывая 2+ пустых строки в одну
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text) # Максимум одна пустая строка между блоками
    text = re.sub(r'\n{2,}', '\n', text)   # Для blockquote лучше вообще без пустых строк

    return text.strip()