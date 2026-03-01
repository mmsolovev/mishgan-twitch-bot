BANNED_WORDS = ["n-word", "slur1", "badword1"]
BANNED_START = ("!", "/", ".")


def censor_text(text: str) -> str:
    """Заменяет запрещённые слова на ****"""
    censored = text
    text_lower = censored.lower()
    for word in BANNED_WORDS:
        idx = text_lower.find(word)
        while idx != -1:
            censored = censored[:idx] + "*" * len(word) + censored[idx+len(word):]
            text_lower = censored.lower()
            idx = text_lower.find(word)
    return censored


def sanitize_start(text: str) -> str:
    """Убирает запрещённые символы в начале ответа"""
    while text and text[0] in BANNED_START:
        text = text[1:]
    return text.strip()


def process_gpt_answer(raw_answer: str) -> str:
    """Обрабатывает ответ GPT: цензура, длина, запрещённые стартовые символы"""
    if not raw_answer:
        return "MrDestructoid GPT временно недоступен"

    answer = raw_answer.strip().replace("\n", " ")
    answer = censor_text(answer)
    answer = sanitize_start(answer)
    if len(answer) > 150:
        answer = answer[:147] + "..."
    if not answer:  # если после фильтров пусто
        answer = "MrDestructoid GPT ответил пусто после фильтров"
    return answer
