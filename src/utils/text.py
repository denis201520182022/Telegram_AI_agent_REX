import re

def clean_html_for_telegram(text: str) -> str:
    """
    Превращает Markdown и кривой HTML от ИИ в чистый, валидный Telegram HTML.
    Исправляет незакрытые теги (code, b, i, blockquote) для предотвращения Bad Request.
    """
    if not text:
        return ""
    # СТИРАЕМ ТЕГ [UPDATE_PLAN] СРАЗУ
    text = text.replace("[UPDATE_PLAN]", "")
    # 1. ПРЕД-ОЧИСТКА: Убираем Markdown-обертки кода
    text = re.sub(r'```(?:html|text|markdown|python)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)

    # 2. ОБРАБОТКА СПИСКОВ (Переводим <li> в буллиты до удаления тегов)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(ul|ol)[^>]*>', '', text, flags=re.IGNORECASE)

    # 3. КОНВЕРТАЦИЯ MARKDOWN В HTML (Сохраняем твою логику)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'#{1,6}\s*(.*)', r'<b>\1</b>', text)
    text = re.sub(r'^\s*[\-\*•]\s*', '• ', text, flags=re.MULTILINE)

    # 4. ОЧИСТКА ЗАПРЕЩЕННЫХ ТЕГОВ (Разметка таблиц, дивов и т.д. -> в переносы)
    text = re.sub(r'</?(table|tr|thead|tbody|th|td|div|p|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # 5. ФИЛЬТРАЦИЯ АТРИБУТОВ (Сохраняем href и expandable)
    # Сначала очищаем атрибуты у всех, кроме а и blockquote
    text = re.sub(r'<(?!a|blockquote|/)([^> \n]+)[^>]*>', r'<\1>', text)
    # У цитат оставляем только expandable
    text = re.sub(r'<blockquote(?![ \t]+expandable)[^>]*>', '<blockquote>', text, flags=re.IGNORECASE)

    # 6. ФИНАЛЬНАЯ ГИГИЕНА (Удаляем всё, что не поддерживает Телеграм)
    allowed_tags = r'b|i|u|s|a|code|pre|blockquote|tg-spoiler|ins|del|strong|em'
    text = re.sub(r'<(?!/?(' + allowed_tags + r'))[^>]+>', '', text)

    # 7. УПЛОТНЕНИЕ СТРОК (Убираем "дыры", сохраняем максимум 1 пустую строку)
    lines = [line.strip() for line in text.split('\n')]
    final_lines = []
    for line in lines:
        if line:
            final_lines.append(line)
        elif final_lines and final_lines[-1] != "":
            final_lines.append("")
    text = '\n'.join(final_lines)

    # === 8. УЛУЧШЕНИЕ: АВТО-ЗАКРЫТИЕ ТЕГОВ (Защита от Bad Request) ===
    # Список тегов, которые требуют обязательного закрытия
    tags_to_balance = [
        'b', 'strong', 'i', 'em', 'u', 'ins', 's', 
        'strike', 'del', 'code', 'pre', 'blockquote', 'tg-spoiler'
    ]
    
    # Ищем все открытые и закрытые теги
    # Используем стек для правильного порядка закрытия (LIFO)
    opened_stack = []
    
    # Регулярка для поиска любых тегов (открывающих и закрывающих)
    tag_pattern = re.compile(r'<(/?)(b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler|a)(?: [^>]*)?>', re.IGNORECASE)
    
    for match in tag_pattern.finditer(text):
        is_closing = match.group(1) == '/'
        tag_name = match.group(2).lower()
        
        if not is_closing:
            opened_stack.append(tag_name)
        else:
            if opened_stack and opened_stack[-1] == tag_name:
                opened_stack.pop()
            elif tag_name in opened_stack:
                # Если закрывается не тот тег, что вверху стека, но он есть в стеке
                opened_stack.remove(tag_name)
                
    # Закрываем все оставшиеся в стеке теги в обратном порядке
    while opened_stack:
        tag_to_close = opened_stack.pop()
        text += f'</{tag_to_close}>'

    return text.strip()