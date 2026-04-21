# mishgan-twitch-bot

Twitch-бот для стримерского чата, локальной автоматизации и сбора статистики по стримам.

Проект совмещает три части:
- чат-бота на `TwitchIO` с командами для пользователей чата Twitch;
- локальный пайплайн сбора и хранения статистики по стримам и играм;
- табличный UI в `Google Sheets`, где удобно смотреть, дополнять и править данные.

## Что умеет

- отвечает на чат-команды `!команды`, `!праздник`, `!hltb`, `!инфо`, `!игры`, `!стримы`, `!r`, `!рек`;
- поддерживает команды с ограниченным доступом для админов `!таймер`, `!отбой`, `!старт`;
- умеет отвечать через GPT для разрешённых пользователей `!gpt`;
- автоматически делает `shoutout` при рейде с заданными условиями;
- собирает и хранит статистику стримов, игр и рекомендаций в `SQLite`;
- выдает и принимает информацию о стримах, играх и рекомендациях по запросу в чате Twitch;
- синхронизирует данные в `Google Sheets`, где таблица используется как UI для просмотра и ручных пометок.

## Команды чата

Подробная инструкция для зрителей и модераторов лежит в [CHAT_COMMANDS.txt](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/CHAT_COMMANDS.txt).

Кратко:
- `!команды` или `!команды <имя>`: список команд или справка по конкретной команде;
- `!время`: продолжительность стрима и отдельных категорий;
- `!праздник`: случайный праздник, список на сегодня, поиск по дате или названию;
- `!hltb <игра>`: примерное время прохождения игры;
- `!инфо <тема>`: локальная справка по стримеру, компьютеру и девайсам;
- `!игры <название>`: статистика по игре и ссылка на общую таблицу;
- `!стримы <дата>`: информация о стриме за дату и ссылка на общую таблицу;
- `!рек <название>`: предложить игру для стрима или проголосовать за уже предложенную;
- `!r`: перевод текста из неправильной раскладки при ответе через `Reply`;
- `!таймер ...`, `!gpt ...`, `!отбой`, `!старт`: команды с ограничением по роли.

## Google Sheets как UI

Проект не просто выгружает данные из бд в таблицу, а использует `Google Sheets` как рабочий интерфейс:
- лист `ИГРЫ` показывает список игр, количество стримов, времени, вручную можно отметить оценку и статус прохождения;
- лист `СТРИМЫ` показывает дату, длительность, название, игры, ссылку на запись и участников;
- лист `РЕЛИЗЫ` показывает ожидаемые игры, время до релиза, описание, интерес стримера и список рекомендовавших;
- лист `СОВЕТЫ` список уже вышедших рекомендованных игр, дата релиза, оценка, рекомендовавшие;
- часть колонок заполняется вручную и сохраняется при безопасной синхронизации;
- ссылка на таблицу для чата передаётся через переменную окружения `GAMES_SHEET_URL`.

Для синхронизации нужен service account файл [config/credentials.json](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/config/credentials.json). В репозитории есть шаблон [config/credentials.example.json](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/config/credentials.example.json).

Пример таблицы: https://bit.ly/4bGaXC0

## Структура проекта

- [bot.py](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/bot.py) — точка входа.
- [core](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/core) — инициализация бота и загрузка команд.
- [commands](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/commands) — обработчики чат-команд.
- [services](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/services) — бизнес-логика, GPT, HLTB, Sheets и прочие интеграции.
- [database](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/database) — модели и работа с `SQLite`.
- [collector](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/collector) — сбор и подготовка данных для импорта.
- [storage](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/storage) — локальные данные, кэш, база и runtime-файлы.
- [config](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/config) — конфигурация и credentials.

## Конфигурация

Основные настройки лежат в `.env`:
- `TWITCH_TOKEN`, `TWITCH_NICK`, `TWITCH_CHANNEL` для подключения к Twitch;
- `CLIENT_ID`, `CLIENT_SECRET` для Twitch API;
- `RAWG_API_KEY` для игровых интеграций;
- `GAMES_SHEET_URL` для публичной ссылки на таблицу в ответах `!игры` и `!стримы`.

Примеры:
- [.env.example](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/.env.example)
- [storage/info.example.json](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/storage/info.example.json)

Локально также используются:
- `storage/info.json` — база ответов для `!инфо`;
- `storage/info_aliases.json` — алиасы и варианты запросов;
- файлы в `storage/cache`, `storage/config`, `storage/pages`;
- `storage/*.db` и `storage/*.json` с рабочими данными.

## Запуск

Локально:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Через Docker:

```bash
docker compose up -d --build
```

Перед запуском нужно подготовить:
- `.env` на основе [.env.example](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/.env.example);
- при необходимости `config/credentials.json`;
- при необходимости локальные данные в `storage`.

## Данные и ограничения

- Бот подключается к каналу, указанному в `TWITCH_CHANNEL`, а не к одному жёстко зашитому каналу.
- Некоторые команды доступны только модераторам или списку разрешённых пользователей.
- Без `Google Sheets` интеграции бот продолжит работать, но ответы `!игры` и `!стримы` будут без полезной ссылки на таблицу.
- Без `storage/info.json` команда `!инфо` использует пример из репозитория.

## Зависимости

Основные библиотеки:
- `twitchio`
- `SQLAlchemy`
- `gspread`
- `oauth2client`
- `howlongtobeatpy`
- `openai`
- `g4f`
- `beautifulsoup4`

Список пакетов: [requirements.txt](/C:/Users/mmsolovev/PycharmProjects/mishgan-twitch-bot/requirements.txt).

## Планы развития

- расширение списка команд и функций бота;
- перевод UI на полноценный веб;
- инструменты для аналитики и прогнозирования.