# Архитектура проекта

## 1. Общая структура (System Context)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ВНЕШНИЕ СИСТЕМЫ                                      │
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌───────┐  ┌───────────────┐  ┌──────────┐   │
│  │ Twitch   │  │ IGDB API │  │ HLTB  │  │ Google Sheets  │  │ g4f/GPT  │   │
│  │ IRC/API  │  │ (игры)   │  │(время │  │ (UI/хранение)  │  │(описания)│   │
│  │ /EventSub│  │          │  │прох.) │  │                │  │          │   │
│  └────┬─────┘  └────┬─────┘  └───┬───┘  └───────┬───────┘  └─────┬────┘   │
│       │             │            │               │                │        │
└───────┼─────────────┼────────────┼───────────────┼────────────────┼────────┘
        │             │            │               │                │
   ┌────▼─────────────▼────────────▼───────────────▼────────────────▼────┐
   │                            ПРОЕКТ                                     │
   │                                                                       │
   │  ┌──────────────────────────────────────────────────────────┐        │
   │  │                    PIPELINE (ETL)                        │        │
   │  │  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────┐  │        │
   │  │  │  INGEST   │  │ TRANSFORM│  │  LOAD   │  │ DELIVERY │  │        │
   │  │  │ (сбор)    │─►│ (чистка, │─►│ (запись │─►│ (выгрузка)│  │        │
   │  │  │           │  │ обогащ.) │  │ в PG)   │  │          │  │        │
   │  │  └──────────┘  └──────────┘  └────────┘  └──────────┘  │        │
   │  │       ▲                            │                      │        │
   │  │       │   ┌────────────────────────┘                      │        │
   │  │       │   │  PostgreSQL                                    │        │
   │  │       └───┴───────────────────────────────────────────────┘        │
   │  └──────────────────────────────────────────────────────────┘        │
   │                                                                       │
   │  ┌──────────────────────────────────────────────────────┐            │
   │  │              CONSUMERS (Бот и сервисы)                │            │
   │  │                                                       │            │
   │  │  ┌────────────┐  ┌──────────────┐  ┌───────────────┐ │            │
   │  │  │  Twitch IRC │  │   EventSub   │  │  Runtime       │ │            │
   │  │  │  (bot.py,   │  │  (eventsub_  │  │  Stream        │ │            │
   │  │  │   core/)    │  │   service)   │  │  Collector     │ │            │
   │  │  │  ◄─чат      │  │  ◄─Live-ивенты│  │  (live-       │ │            │
   │  │  │  команды    │  │  (raid,start,│  │   метрики)     │ │            │
   │  │  │             │  │  channel_upd)│  │               │ │            │
   │  │  └────────────┘  └──────────────┘  └───────────────┘ │            │
   │  └──────────────────────────────────────────────────────┘            │
   │                                                                       │
   │  ┌──────────────────────────────────────────────────┐                │
   │  │              ORCHESTRATOR (CLI)                   │                │
   │  │  import_json_to_db  │  sync_sheets  │  import_   │                │
   │  │  parse_streams_json │               │  igdb_     │                │
   │  │  parse_games_json   │               │  releases  │                │
   │  │                     │               │            │                │
   │  │  enrich_descriptions_with_gpt                     │                │
   │  └──────────────────────────────────────────────────┘                │
   └───────────────────────────────────────────────────────────────────────┘
```

---

## 2. Слой Pipeline (ETL)

```
pipeline/
│
├── ingest/                          # Сбор — читает внешние источники
│   ├── twitch_api.py                #   Twitch Helix API: VOD list, channel info
│   ├── igdb_api.py                  #   IGDB API: game metadata, upcoming releases
│   │                                #     + sliding-window rate limiter (4 req/s)
│   │                                #     + in-memory TTL cache (1h)
│   ├── hltb_client.py               #   HowLongToBeat: время прохождения игры
│   ├── twitchtracker_parser.py      #   TwitchTracker HTML→dataclass (стримы, игры)
│   └── google_sheets_reader.py      #   Google Sheets: ручные колонки (liked,completed)
│
├── transform/                       # Трансформация — чистые функции, без I/O
│   ├── utils_transform.py           #   Нормализация строк, жанров, дедупликация
│   ├── streams_transform.py         #   Вычисление жанров стрима по заголовку
│   │                                #   VOD matching (по дате ±1д, пересечению названий)
│   ├── games_transform.py           #   Решение, какие поля GameMeta нужно обогатить
│   ├── igdb_transform.py            #   Парсинг IGDB payload: даты, платформы, жанры
│   ├── twitchtracker_transform.py   #   Склейка дубликатов игр из нескольких HTML-страниц
│   ├── sheets_transform.py          #   Нормализация для Google Sheets (padding/bool)
│   └── recommendations_transform.py #   Статусы рекомендаций (upcoming/released/streamed)
│
├── load/                            # Загрузка — пишет в PostgreSQL (через SQLAlchemy async)
│   ├── load_streams.py              #   Stream upsert, VOD sync, started_at/ended_at
│   ├── load_participants.py         #   User get_or_create, streamers_on_stream M2M
│   ├── load_stream_games.py         #   StreamGame association (порядок игр в стриме)
│   ├── load_games.py                #   Game get_or_create + GameAlias
│   ├── load_game_meta.py            #   GameMetadataIGDB / GameMetadataHLTB: обогащение
│   ├── load_game_stats.py           #   GameStats upsert (из TwitchTracker)
│   └── load_recommendations.py      #   game_recommendations CRUD + streamer_games
│
├── delivery/                        # Доставка — выгружает данные вовне
│   ├── sheets_utils.py              #   Shared: upload_table, get_or_create_worksheet
│   ├── sheets_header.py             #   Шапка "Tabula Streams" (мерж ячеек, тема)
│   ├── sheets_games.py              #   Лист ИГРЫ (sync + safe-sync с сохранением ручных колонок)
│   ├── sheets_streams.py            #   Лист СТРИМЫ
│   ├── sheets_releases.py           #   Лист РЕЛИЗЫ (с обратным отсчётом)
│   ├── sheets_recommendations.py    #   Лист СОВЕТЫ
│   ├── sheets_bot_info.py           #   Лист БОТ (docs/CHAT_COMMANDS.txt)
│   └── json_twitchtracker.py        #   JSON legacy-формат (storage/streams.json, games.json)
│
└── orchestrator/                    # Оркестрация — CLI-сборки слоёв в рабочие скрипты
    ├── parse_streams_json.py        #   HTML → JSON (Ingest → Delivery)
    ├── parse_games_json.py          #   HTML(несколько) → merge → JSON
    ├── import_json_to_db.py         #   JSON → PostgreSQL + VOD sync + HLTB/IGDB enrich
    ├── import_igdb_releases.py      #   IGDB API → PostgreSQL (новые релизы)
    ├── sync_sheets.py               #   PostgreSQL → Google Sheets (все листы)
    └── enrich_descriptions_with_gpt.py # PostgreSQL → GPT → PostgreSQL (описания на русском)
```

### Data Flow Pipeline

```
TwitchTracker HTML ──► parse_stream_json ──► storage/streams.json ──┐
                       parse_games_json  ──► storage/games.json  ───┤
                                                                     │
IGDB API ──► import_igdb_releases ──► PostgreSQL (game_recommendations) │
                                                                     │
                          ┌──────────────────────────────────────────┘
                          ▼
            import_json_to_db.py
              │
              ├── sync_streams()          ← streams.json
              ├── sync_game_stats()        ← games.json
              ├── sync_stream_vod_urls()   ← Twitch API
              ├── enrich_game_meta()       ← HLTB + IGDB
              └── compute_stream_genres()  ← по заголовкам
                          │
                          ▼
                   PostgreSQL
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
   enrich_descriptions_gpt    sync_sheets.py
   (GPT → short_description)    │
                                ├── sheets_games         → лист "ИГРЫ"
                                ├── sheets_streams       → лист "СТРИМЫ"
                                ├── sheets_releases      → лист "РЕЛИЗЫ"
                                ├── sheets_recommendations → лист "СОВЕТЫ"
                                └── sheets_bot_info      → лист "БОТ"
```

---

## 3. Слой Consumers (Бот и runtime-сервисы)

```
bot.py ───── точка входа
│
└── core/
    ├── bot.py                     # TwitchIO Bot: IRC, EventSub, kill-switch (!отбой/!старт)
    ├── context.py                 # SafeContext: цензура исходящих сообщений
    └── registry.py                # Загрузка всех команд (импорт + bot.add_command)

commands/                          # Обработчики чат-команд
  ├── ... (все команды)

services/                          # Бизнес-логика, вызываемая из команд и EventSub
  ├── command_registry.py           # Реестр команд (COMMANDS_INFO, нормализация доступа)
  ├── deferred_service.py           # RecommendationSheetsSyncScheduler (debounced sync)
  ├── eventsub_service.py           # EventSub WebSocket: raid, shoutout, channel.update
  ├── runtime.py                    # BOT_ENABLED flag (kill-switch)
  ├── chat_service.py               # Логика чата (сообщения о смене игры)
  ├── ... (остальные сервисы)

runtime/                           # Сбор live-метрик стрима
  ├── collector.py                 # Оркестратор: жизненный цикл сессии, обработка событий
  ├── session.py                   # Структуры данных (TypedDict) для сессии
  ├── sampler.py                   # Сбор данных (Twitch API: live stream, followers)
  ├── metrics.py                   # Вычисление метрик (viewer count, HLTB, etc.)
  ├── storage.py                   # Чтение/запись JSON-файлов сессий
  └── utils.py                     # Вспомогательные чистые функции (даты, etc.)
```

---

## 4. База данных (PostgreSQL)

Стек: `SQLAlchemy 2.0` (async) + `asyncpg` + `Alembic` для миграций.

### Основные домены

```
CORE DOMAIN
───────────────────────────────────────────────────────────────────────
games                  streams                 users
┌──────────────┐      ┌──────────────┐        ┌──────────────┐
│ id (PK)      │      │ id (PK)      │        │ id (PK)      │
│ name (UQ)    │      │ external_id  │        │ twitch_user_id│
│ slug (UQ)    │      │ started_at   │        │ login (UQ)   │
│ game_type    │      │ ended_at     │        │ display_name │
│ parent_game_id│     │ duration_min │        │ is_streamer  │
│ franchise_id │      │ avg_viewers  │        │ is_admin     │
│ created_at   │      │ max_viewers  │        │ is_trusted   │
│ updated_at   │      │ followers_   │        │ duels_*      │
└──────┬───────┘      │  gained      │        │ last_seen_at │
       │              │ views_gained │        └──────┬───────┘
       │              │ title        │               │
       │              │ created_at   │        streamers_on_stream
       │              │ updated_at   │        ┌──────────────┐
       │              └──────┬───────┘        │ stream_id    │
       │                     │                │ streamer_id  │
       │              stream_games            │ role         │
       │              ┌──────────────┐        └──────────────┘
       │              │ id (PK)      │
       ├──aliases────►│ stream_id    │
       │              │ game_id      │        streamer_games
       │              │ position     │        ┌──────────────┐
       │              │ started_at   │        │ streamer_id  │
       │              │ ended_at     │        │ game_id      │
       │              │ duration_min │        │ interested   │
       │              │ avg_viewers  │        │ liked        │
       │              │ peak_viewers │        │ completed    │
       │              └──────────────┘        └──────────────┘
       │
       ├──game_genres──► genres (id, name, slug)
       │
       └──game_platforms──► platforms (id, name, slug)

EXTERNAL METADATA                RELATIONS
───────────────────────────────────────────────────────────────────────
game_metadata_igdb               stream_recordings
┌──────────────┐                 ┌──────────────┐
│ game_id (PK) │                 │ id (PK)      │
│ igdb_id (UQ) │                 │ stream_id    │
│ release_date │                 │ source       │
│ steam_url    │                 │ url          │
│ igdb_score   │                 │ duration_min │
│ description_ │                 │ recorded_at  │
│  en/ru       │                 └──────────────┘
│ cover_url    │
│ raw_payload  │                 game_recommendations
│ synced_at    │                 ┌──────────────┐
└──────────────┘                 │ user_id (PK) │
                                 │ game_id (PK) │
game_metadata_hltb               │ note         │
┌──────────────┐                 │ created_at   │
│ game_id (PK) │                 └──────────────┘
│ hltb_* hours │
│ review_count │                 game_aliases
│ synced_at    │                 ┌──────────────┐
└──────────────┘                 │ id (PK)      │
                                 │ game_id      │
game_stats                       │ alias        │
┌──────────────┐                 │ normalized_  │
│ game_id (PK) │                 │  alias (UQ)  │
│ streamed_hrs │                 │ is_primary   │
│ avg/max_view │                 │ source       │
│ streams_count│                 └──────────────┘
│ last_stream  │
│ synced_at    │
└──────────────┘
```

### Полный список таблиц (40+)

| Домен | Таблицы |
|-------|---------|
| Core | `games`, `streams`, `users`, `games_franchises`, `duels` |
| Duels | `duels_franchises`, `duels_characters`, `duels_character_versions`, `duels_abilities`, `duels_version_abilities`, `duels_scenarios`, `duels_scenario_usage` |
| Relations | `stream_games`, `stream_recordings`, `streamers_on_stream`, `streamer_games`, `game_recommendations`, `game_genres`, `game_platforms` |
| External metadata | `game_metadata_igdb`, `game_metadata_hltb`, `game_stats`, `game_aliases` |
| Supporting | `genres`, `platforms` |
| Clips | `clips`, `clip_tags` |
| Calendar | `calendar_days`, `holidays`, `famous_persons` |
| Bot | `bot_commands`, `bot_command_aliases`, `command_usage_logs` |
| Chat | `chat_messages`, `vip_history` |
| Auth | `twitch_authorizations`, `web_sessions` |
| Rewards | `twitch_rewards`, `twitch_reward_redemptions` |
| Analytics | `stream_runtime_samples`, `stream_runtime_buckets` |

### Миграции

Схема управляется через `Alembic`. Текущие миграции в `alembic/versions/`:
- `0001_initial_schema` — полная начальная схема
- `0002_add_genre_platform_abbreviation` — аббревиатуры жанров/платформ
- `0003_update_hltb_metadata` — расширенные поля HLTB

```bash
# Применить все миграции
alembic upgrade head

# Создать новую миграцию
alembic revision --autogenerate -m "description"

# Откатить одну миграцию
alembic downgrade -1
```

---

## 5. Data Flow runtime (EventSub live-сбор)

```
Twitch EventSub WebSocket
       │
       ├── channel.update ──────────────────────────► eventsub_service
       │     (смена игры/тайтла)                         │
       │                                                ├──► runtime.Collector.handle_channel_update()
       │                                                │     (game_segments, title_history)
       │                                                └──► announce_game_change() (чат)
       │
       ├── stream.online ─────────────────────────────► eventsub_service
       │     (стрим начался)                              │
       │                                                └──► runtime.Collector.start_session()
       │
       ├── stream.offline ────────────────────────────► eventsub_service
       │                                                └──► runtime.Collector.finalize_session()
       │
       ├── channel.follow.v2 ─────────────────────────► eventsub_service
       │                                                └──► runtime.Collector.handle_follow()
       │
       └── ... (raid, shoutout)

runtime.Collector
  ├── sampling_loop()              каждые N секунд
  │    ├── runtime.Sampler.fetch_live_stream()     Twitch API (viewers, title, game)
  │    ├── runtime.Sampler.fetch_followers_count() Twitch API (total followers)
  │    └── runtime.Metrics.recalculate_all_metrics()
  │
  ├── active session (in-memory)   ► runtime.Storage.save_active_session()
  └── completed sessions           ► runtime.Storage.append_completed_session()
```

---

## 6. Ключевые архитектурные решения

| Решение | Где реализовано |
|---|---|
| **Pipeline как CLI** | Все оркестраторы — `python -m pipeline.orchestrator.*`. Нет встроенного планировщика |
| **Safe-sync с Sheets** | `sync_games_safe()` / `sync_streams_safe()` — сохраняет ручные колонки H,J |
| **Sliding-window rate limiter** | `_SlidingWindowRateLimiter` в `ingest/igdb_api.py` (4 req/s для IGDB) |
| **Debounced sheets sync** | `RecommendationSheetsSyncScheduler` (15s debounce после `!рек`) |
| **Kill-switch** | `runtime.BOT_ENABLED` — блокирует все команды кроме `!старт` |
| **VOD matching** | `streams_transform.py` — по дате ±1 день + пересечение title |
| **Game segments** | `runtime.Collector` — сегменты игр внутри стрима с метриками |

---

## 7. Известные узкие места

```
Проблема                              Где                    План
─────────────────────────────────────────────────────────────────────────
Циркулярные зависимости               pipeline ↔ services    Выделить shared/core
Конфиг — плоские глобальные переменные config/settings.py    pydantic-settings
Нет тестов                            Весь проект            После выделения shared/
Нет API для веба/телеграма            Нет                    FastAPI отдельным приложением
```