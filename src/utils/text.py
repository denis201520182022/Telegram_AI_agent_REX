import re

def clean_html_for_telegram(text: str) -> str:
    """
    Превращает Markdown и кривой HTML от ИИ в чистый, плотный Telegram HTML.
    """
    # 1. ПРЕД-ОЧИСТКА: Убираем Markdown-обертки кода
    text = re.sub(r'```(?:html|text)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)
    
    # 2. КОНВЕРТАЦИЯ MARKDOWN В HTML (так как ИИ часто игнорирует инструкции)
    # **Жирный** -> <b>Жирный</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # ### Заголовок -> <b>Заголовок</b>
    text = re.sub(r'###\s*(.*)', r'<b>\1</b>', text)
    # - Список или * Список -> • Список
    text = re.sub(r'^\s*[\-\*]\s*', '• ', text, flags=re.MULTILINE)

    # 3. ЧИСТКА ЗАПРЕЩЕННЫХ ТЕГОВ
    # Вырезаем blockquote (чтобы не было вложенности), таблицы и служебные теги
    text = re.sub(r'</?blockquote[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(table|tr|thead|tbody|th|td|div|span|p)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h[1-6][^>]*>', '\n<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '</b>\n', text, flags=re.IGNORECASE)

    # 4. УДАЛЯЕМ ВСЕ ОСТАЛЬНЫЕ АТРИБУТЫ (style, class и т.д.)
    # Оставляем только базовые теги без параметров
    text = re.sub(r'<(?!b|i|u|s|a|code|pre|ul|li|/)[^>]+>', '', text)

    # 5. МАГИЯ УПЛОТНЕНИЯ (Убираем "дыры")
    # Разбиваем на строки, удаляем пробелы по краям каждой строки
    lines = [line.strip() for line in text.split('\n')]
    # Фильтруем, оставляя только непустые строки
    non_empty_lines = [l for l in lines if l]
    
    # Собираем обратно через ОДИН перенос строки
    # Внутри <blockquote> Telegram сам добавит нужные отступы
    text = '\n'.join(non_empty_lines)

    return text.strip()