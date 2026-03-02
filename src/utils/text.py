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
    # 0. ЭКРАНИРОВАНИЕ (Защита от случайных < и > в тексте)
    # Сначала заменим реальные < и > на временные маркеры, чтобы не испортить их
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    
    # Теперь возвращаем обратно только те, что похожи на наши будущие HTML теги
    # (Мы их восстановим позже через регулярки или вернем стандартные)
    # Для простоты — пока просто работаем с текстом, а теги создадим ниже через <b> и <i>
    # 1. ПРЕД-ОЧИСТКА: Убираем Markdown-обертки кода
    text = re.sub(r'```(?:html|text|markdown|python)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)

    # 2. ОБРАБОТКА СПИСКОВ (Ищем &lt;li&gt; вместо <li>)
    text = re.sub(r'&lt;li[^&]*&gt;', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'&lt;/li&gt;', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'&lt;/?(ul|ol)[^&]*&gt;', '', text, flags=re.IGNORECASE)

    # 3. КОНВЕРТАЦИЯ MARKDOWN В HTML (Сохраняем твою логику)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'#{1,6}\s*(.*)', r'<b>\1</b>', text)
    text = re.sub(r'^\s*[\-\*•]\s*', '• ', text, flags=re.MULTILINE)

    # 4. ОЧИСТКА ЗАПРЕЩЕННЫХ ТЕГОВ (Разметка таблиц, дивов и т.д. -> в переносы)
    text = re.sub(r'&lt;/?(table|tr|thead|tbody|th|td|div|p|h[1-6])[^&]*&gt;', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'&lt;br\s*/?&gt;', '\n', text, flags=re.IGNORECASE)

    # 5 & 6. ГИГИЕНА ТЕГОВ И АТРИБУТОВ
    # Сначала удаляем все атрибуты у всех тегов
    # (кроме <a> с href и <blockquote> с expandable)
    
    # Сохраняем ссылки: <a href="..."> -> [A_START_..._A_END]
    text = re.sub(r'&lt;a\s+[^&]*href=["\'](.*?)["\'][^&]*&gt;', r'STARTA HREF="\1"ENDA', text, flags=re.IGNORECASE)
    # Сохраняем цитаты: <blockquote expandable> -> [BQ_EXPAND]
    text = text.replace('&lt;blockquote expandable&gt;', 'STARTBQ EXPENDABLEENDA')
    text = text.replace('&lt;blockquote&gt;', 'STARTBQENDA')
    
    # Очищаем все остальные теги от атрибутов и мусора
    text = re.sub(r'&lt;/?([a-z1-6]+).*?&gt;', r'<\1>', text, flags=re.IGNORECASE)
    
    # Возвращаем наши сохраненные теги
    text = text.replace('STARTA HREF="', '<a href="').replace('STARTBQ EXPENDABLEENDA', '<blockquote expandable>')
    text = text.replace('STARTBQENDA', '<blockquote>').replace('ENDA', '>')
    
    # Удаляем запрещенные теги (оставляем только список Телеграма)
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

    # === 8. УЛУЧШЕННЫЙ БАЛАНСИРОВЩИК (LIFO) ===
    opened_stack = []
    # Регулярка для поиска тегов
    tag_pattern = re.compile(r'<(/?)(b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler|a)(?: [^>]*)?>', re.IGNORECASE)
    
    output = []
    last_pos = 0
    
    for match in tag_pattern.finditer(text):
        tag_full = match.group(0)
        is_closing = match.group(1) == '/'
        tag_name = match.group(2).lower()
        
        # Добавляем текст до тега
        output.append(text[last_pos:match.start()])
        
        if not is_closing:
            opened_stack.append(tag_name)
            output.append(tag_full)
        else:
            if tag_name in opened_stack:
                # Если закрывается не тот тег, который на вершине стека (ПЕРЕХЛЕСТ)
                # Закрываем все теги до нужного в обратном порядке
                while opened_stack:
                    current_tag = opened_stack.pop()
                    if current_tag == tag_name:
                        output.append(f'</{current_tag}>')
                        break
                    else:
                        output.append(f'</{current_tag}>')
            # Если тег закрывается, а в стеке его нет — просто игнорируем его
        
        last_pos = match.end()
    
    # Добавляем остаток текста
    output.append(text[last_pos:])
    
    # Закрываем все, что осталось в стеке
    while opened_stack:
        output.append(f'</{opened_stack.pop()}>')
        
    text = "".join(output)

    return text.strip()