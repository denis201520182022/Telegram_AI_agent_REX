#src\utils\text.py
import re
import re
import html

def clean_html_for_telegram(text: str) -> str:
    """
    Превращает Markdown и кривой HTML от ИИ в чистый, валидный Telegram HTML.
    Поддерживает жирный, курсив, код и раскрывающиеся цитаты.
    """
    if not text:
        return ""

    # 1. ПРЕД-ОЧИСТКА: Убираем Markdown-обертки кода (часто ИИ сует весь ответ в них)
    text = re.sub(r'```(?:html|text|markdown|python)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)

    # 2. ОБРАБОТКА СПИСКОВ (До того как удалим теги)
    # Превращаем <li> в буллиты, а <ul>/ <ol> просто удаляем
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(ul|ol)[^>]*>', '', text, flags=re.IGNORECASE)

    # 3. КОНВЕРТАЦИЯ MARKDOWN В HTML
    # **Жирный** -> <b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # ### Заголовок -> <b> (Телеграм не знает h1-h6)
    text = re.sub(r'#{1,6}\s*(.*)', r'<b>\1</b>', text)
    # - Список или * Список -> • Список (для Markdown списков)
    text = re.sub(r'^\s*[\-\*•]\s*', '• ', text, flags=re.MULTILINE)

    # 4. ОЧИСТКА ЗАПРЕЩЕННЫХ ТЕГОВ (layout)
    # Заменяем структурные теги (div, p, h, table) на переносы строк
    text = re.sub(r'</?(table|tr|thead|tbody|th|td|div|p|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # 5. КРАЙНЕ ВАЖНО: Фильтрация атрибутов, но сохранение нужных
    # Мы оставляем только <b>, <i>, <u>, <s>, <a>, <code>, <pre>, <blockquote>, <tg-spoiler>
    # При этом у <a> оставляем href, а у <blockquote> оставляем expandable
    
    # Сначала очищаем атрибуты у всех тегов, кроме <a> и <blockquote>
    text = re.sub(r'<(?!a|blockquote|/)([^> \n]+)[^>]*>', r'<\1>', text)
    
    # У <blockquote> удаляем всё, кроме атрибута expandable
    # (ИИ может добавить class или style, что сломает Телеграм)
    text = re.sub(r'<blockquote(?![ \t]+expandable)[^>]*>', '<blockquote>', text, flags=re.IGNORECASE)

    # 6. ФИНАЛЬНАЯ ГИГИЕНА ТЕГОВ
    # Удаляем любые теги, которые не входят в "белый список" Телеграма
    allowed_tags = r'b|i|u|s|a|code|pre|blockquote|tg-spoiler|ins|del|strong|em'
    text = re.sub(r'<(?!/?(' + allowed_tags + r'))[^>]+>', '', text)

    # 7. КРАСИВОЕ ФОРМАТИРОВАНИЕ (Уплотнение и отступы)
    # Убираем лишние пробелы в конце строк
    lines = [line.strip() for line in text.split('\n')]
    
    # Собираем текст, не допуская более двух пустых строк подряд (чтобы план не был слишком растянутым)
    final_lines = []
    for line in lines:
        if line:
            final_lines.append(line)
        elif final_lines and final_lines[-1] != "": # Разрешаем максимум одну пустую строку для абзаца
            final_lines.append("")
            
    text = '\n'.join(final_lines)

    return text.strip()