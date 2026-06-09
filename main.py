# Импорт необходимых библиотек
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from openai import OpenAI
import json
import time
import random
import logging
import re
from datetime import datetime

# НАСТРОЙКА ЛОГГИРОВАНИЯ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# КОНФИГУРАЦИЯ
VK_GROUP_TOKEN = "your_token"
LLM_BASE_URL = "http://127.0.0.1:8080/v1"
LLM_API_KEY = "not-needed"
LLM_MODEL_NAME = "YankaGPT-8B-v0.1"
GROUP_ID = 123456789
HISTORY_FILE = "dialogs.json"

# Загрузка промптов
PROMPT_FILEPATH = 'prompts/prompt_final.txt'
with open(PROMPT_FILEPATH, 'r', encoding='utf-8') as prompt_file:
    SYSTEM_PROMPT = prompt_file.read()

with open('start_msg.txt', 'r', encoding='utf-8') as start_msg_file:
    START_MESSAGE = start_msg_file.read()

THINKING_MESSAGE = "Дай-ка покумекаю, обожди пару минут..."
THANKS_RESPONSE = "Да не за что, мил человек. Пусть Мойры хранят твой путь, а я всегда здесь, на связи, с колодой под рукой."

# ИНИЦИАЛИЗАЦИЯ
llm_client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
)

vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
longpoll = VkBotLongPoll(vk_session, GROUP_ID)
vk = vk_session.get_api()

# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
def approximate_tokens(text: str) -> int:
    return len(text.split(' '))

def is_thanks(text: str) -> bool:
    """Проверяет, содержит ли сообщение благодарность."""

    THANKS_PATTERNS = [
    r'\bспасибо\b',
    r'\bблагодарю\b',
    r'\bспс\b',
    r'\bблагодарствую\b'
]
    text_lower = text.lower()
    for pattern in THANKS_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def load_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def reset_user_history(user_id):
    """Сбрасывает историю диалога для конкретного пользователя."""
    history = load_history()
    if str(user_id) in history:
        del history[str(user_id)]
        save_history(history)
        send_long_message(user_id, "Всё, забыл все твои секреты, давай по-новой")
        logger.info(f"История для пользователя {user_id} сброшена.")
        return True
    else:
        send_long_message(user_id, "Что-то я не помню, чтобы ты мне строчил что-то, айда общаться")
        return False

def send_long_message(user_id, text):
    MAX_LEN = 4096
    if not text:
        return
    for i in range(0, len(text), MAX_LEN):
        part = text[i:i+MAX_LEN]
        random_id = random.randint(1, 2**31)
        vk.messages.send(
            user_id=user_id,
            message=part,
            random_id=random_id
        )
        time.sleep(0.3)

# ФУНКЦИЯ ОБЩЕНИЯ С LLM
def get_llm_response(user_id, user_message):
    history = load_history()
    user_history = history.get(str(user_id), [])

    messages_for_api = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_api.extend(user_history)
    messages_for_api.append({"role": "user", "content": user_message})

    logger.info(f"Пользователь {user_id}: запрос к LLM (длина сообщения {len(user_message)} симв.)")

    start_time = time.time()
    try:
        response = llm_client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=messages_for_api,
            temperature=0.6,
            max_tokens=900,
            frequency_penalty=0.8,
            stop=["<|im_start|>user", "<|im_end|>user", "<|im_start|>system", "<|im_end|>", "\nUser:", "\nПользователь:"]
        )
        generation_time = time.time() - start_time

        llm_answer = response.choices[0].message.content
        if not llm_answer or not llm_answer.strip():
            llm_answer = "(*молчит, кряхтит* Эх, что-то я призадумался... Что ты спрашивал, человек дорогой?)"

        # Получаем статистику токенов (если есть)
        if hasattr(response, 'usage') and response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            total_tokens = response.usage.total_tokens
        else:
            # Если API не вернул usage, считаем приблизительно
            prompt_tokens = approximate_tokens(str(messages_for_api))
            completion_tokens = approximate_tokens(llm_answer)
            total_tokens = prompt_tokens + completion_tokens

        logger.info(f"Пользователь {user_id}: генерация завершена за {generation_time:.2f} сек. "
                    f"Токены: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")

        # Сохраняем сообщение в историю вместе с метаданными
        user_history.append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.now().isoformat()
        })
        user_history.append({
            "role": "assistant",
            "content": llm_answer,
            "generation_time_sec": round(generation_time, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "timestamp": datetime.now().isoformat()
        })

        # Ограничиваем историю последними 20 сообщениями (10 пар)
        if len(user_history) > 20:
            user_history = user_history[-20:]

        history[str(user_id)] = user_history
        save_history(history)

        return llm_answer

    except Exception as e:
        logger.error(f"Ошибка при обращении к LLM API для {user_id}: {e}", exc_info=True)
        if "Connection refused" in str(e):
            return "Ошибка: Сервер LLM не запущен. Пожалуйста, запустите `llama-server`."
        return "Извините, произошла внутренняя ошибка. Попробуйте позже."

# ОСНОВНОЙ ЦИКЛ
def main():
    logger.info(f"Бот запущен. Подключен к LLM: {LLM_BASE_URL}")
    logger.info("Ожидание сообщений...")

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            if event.message and event.message.text:
                user_id = event.message.from_id
                user_message = event.message.text.strip()

                logger.info(f"Новое сообщение от {user_id}: {user_message[:100]}")

                # Команда сброса истории
                if user_message.lower() == '/reset':
                    reset_user_history(user_id)
                    continue

                # Команда "Начать"
                if user_message.lower() == 'начать' or user_message.lower() == '/start':
                    time.sleep(0.5)
                    send_long_message(user_id, START_MESSAGE)
                    logger.info(f"Отправлено приветственное сообщение пользователю {user_id}")
                    continue

                if is_thanks(user_message):
                    time.sleep(0.5)
                    send_long_message(user_id, THANKS_RESPONSE)
                    logger.info(f"Отправлено сообщение в ответ на благодарность пользователю {user_id}")
                    continue

                # Обычный диалог
                send_long_message(user_id, THINKING_MESSAGE)
                bot_answer = get_llm_response(user_id, user_message)
                if bot_answer:
                    send_long_message(user_id, bot_answer)
                    logger.info(f"Ответ пользователю {user_id} (длина {len(bot_answer)} симв.)")

if __name__ == "__main__":
    main()
