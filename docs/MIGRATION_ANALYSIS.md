# Анализ миграции: SQLite → PostgreSQL + новая схема

Дата: 2026-07-10

---

## Содержание

1. [Текущее состояние](#1-текущее-состояние)
2. [Целевая схема](#2-целевая-схема)
3. [Маппинг таблиц](#3-маппинг-таблиц)
4. [Пошаговый план миграции](#4-пошаговый-план-миграции)
5. [Список файлов для изменений](#5-список-файлов-для-изменений)
6. [Миграция данных](#6-миграция-данных)
7. [Потенциальные проблемы и ошибки](#7-потенциальные-проблемы-и-ошибки)
8. [Рекомендуемый порядок выполнения](#8-рекомендуемый-порядок-выполнения)
9. [Решённые вопросы](#9-решённые-вопросы)

---

## 1. Текущее состояние

### Стек технологий

- Python 3.13+
- SQLAlchemy 2.0.48 (синхронный)
- SQLite (`storage/streams.db`)
- Alembic не используется (ad-hoc миграции)
- Docker (`python:3.14-slim`)

### Текущие таблицы (9)

| Таблица | ORM-класс | Назначение |
|---------|-----------|------------|
| `games` | `Game` | Каталог игр |
| `streams` | `Stream` | История стримов |
| `stream_games` | `StreamGame` | Связь игр и стримов (с позицией) |
| `games_stats` | `GameStats` | Агрегированная статистика игр |
| `games_meta` | `GameMeta` | Мета-данные: liked, completed, hltb, steam, platforms, genres |
| `participants` | `Participant` | Участники стримов (стримеры) |
| `stream_participants` | Table (association) | Связь M:N стримов и участников |
| `recommended_games` | `RecommendedGame` | Рекомендации игр от пользователей |
| `recommended_game_votes` | `RecommendedGameVote` | Голоса за рекомендации |

### Ключевые особенности текущей схемы

- `GameMeta` — объединяет ручные данные (liked/completed), внешние метаданные (hltb/steam) и справочники (platforms/genres) в одну таблицу
- `GameStats` поддерживает несколько периодов через колонку `period` (используется только `"all"`)
- `StreamGame` — составной PK `(stream_id, game_id)` без собственного id
- `participants` — стримеры, имена извлекаются из заголовков стримов regex `@(\w+)`
- `recommended_games` + `recommended_game_votes` — модель с голосами: одна рекомендация имеет много голосов
- Строка `"all"` в `GameStats.period` используется повсеместно

### Файлы с работой с БД

| Категория | Количество | Файлы |
|-----------|-----------|-------|
| Ядро БД | 4 | `db.py`, `models.py`, `init_db.py`, `seed.py` |
| Миграции | 3 | `migrate_streams.py`, `migrate_games_meta.py`, `migrate_recommendations.py` |
| Utils | 1 | `utils/db_manage.py` |
| Load layer | 7 | `load_streams.py`, `load_games.py`, `load_game_meta.py`, `load_game_stats.py`, `load_participants.py`, `load_stream_games.py`, `load_recommendations.py` |
| Delivery layer | 4 | `sheets_games.py`, `sheets_streams.py`, `sheets_releases.py`, `sheets_recommendations.py` |
| Service layer | 3 | `games_service.py`, `streams_service.py`, `recommendations_service.py` |
| Orchestrator | 6 | `import_json_to_db.py`, `import_igdb_releases.py`, `enrich_descriptions_with_gpt.py`, `sync_wishlist.py`, `update_upcoming_release_dates.py`, `sync_sheets.py` |
| **Итого** | **28** | |

---

## 2. Целевая схема

### Стек технологий (целевой)

- Python 3.13+
- SQLAlchemy 2.0+ (асинхронный) + `asyncpg`
- PostgreSQL
- Alembic для миграций
- Docker Compose с PostgreSQL-сервисом

### Целевые таблицы (40+)

#### CORE DOMAIN

| Таблица | Назначение |
|---------|------------|
| `games` | Канонический каталог игр (slug, game_type, parent_game_id, franchise_id) |
| `streams` | История стримов (started_at, ended_at, duration_minutes, followers_gained, views_gained) |
| `users` | Пользователи Twitch (twitch_user_id, login, display_name, is_streamer, is_admin, duels stats) |
| `duels` | История дуэлей (challenger_id, target_id, winner_id, is_draw) |
| `games_franchises` | Франшизы игр |
| `genres` | Справочник жанров |
| `platforms` | Справочник платформ |
| `clips` | Twitch-клипы |
| `clip_tags` | Теги клипов |
| `calendar_days` | Дни календаря (366 записей) |
| `holidays` | Праздники |
| `famous_persons` | Знаменитости по дням |
| `bot_commands` | Команды бота |
| `bot_command_aliases` | Алиасы команд |
| `command_usage_logs` | Логи использования команд |
| `chat_messages` | Сообщения чата |
| `vip_history` | История VIP |
| `twitch_authorizations` | Токены Twitch (access_token, refresh_token) |
| `web_sessions` | Веб-сессии (UUID PK) |
| `twitch_rewards` | Награды канала |
| `twitch_reward_redemptions` | Использование наград |

#### DUELS

| Таблица | Назначение |
|---------|------------|
| `duels_franchises` | Франшизы дуэлей |
| `duels_characters` | Персонажи |
| `duels_character_versions` | Версии персонажей |
| `duels_abilities` | Способности |
| `duels_version_abilities` | Связь версий и способностей |
| `duels_scenarios` | Сценарии дуэлей (step1–step4, winner) |
| `duels_scenario_usage` | Использование сценариев |

#### RELATIONS

| Таблица | Назначение |
|---------|------------|
| `stream_games` | Игровые сегменты внутри стрима (id PK, started_at, ended_at, avg_viewers, peak_viewers) |
| `stream_recordings` | Записи/VOD стримов (Twitch VOD, YouTube, Boosty) |
| `streamers_on_stream` | Участники стрима (role) |
| `streamer_games` | Отношение стримера к игре (interested, liked, completed) |
| `game_recommendations` | Рекомендации (user_id + game_id + recommendation_note) |
| `game_genres` | M:N: игры ↔ жанры |
| `game_platforms` | M:N: игры ↔ платформы |

#### EXTERNAL METADATA

| Таблица | Назначение |
|---------|------------|
| `game_metadata_igdb` | Кэш IGDB (igdb_id, scores, descriptions, raw_payload JSONB, cover_url) |
| `game_metadata_hltb` | Кэш HowLongToBeat (hours, review_count) |
| `game_stats` | Агрегированная статистика (streamed_hours, viewers, streams_count) |
| `game_aliases` | Алиасы игр (normalized_alias, source) |

#### RUNTIME ANALYTICS

| Таблица | Назначение |
|---------|------------|
| `stream_runtime_samples` | Сырые замеры стрима во времени |
| `stream_runtime_buckets` | Агрегированные интервалы |

---

## 3. Маппинг таблиц

### Таблицы с изменённой структурой

#### `games` → `games`

| Текущая колонка | Целевая колонка | Изменение |
|----------------|----------------|-----------|
| `id` | `id` | Без изменений |
| `name` | `name` | Без изменений |
| — | `slug` | НОВАЯ (уникальная) |
| — | `game_type` | НОВАЯ (varchar(20)) |
| — | `parent_game_id` | НОВАЯ (self-FK для DLC/remaster) |
| — | `franchise_id` | НОВАЯ (FK → games_franchises) |
| — | `created_at` | НОВАЯ |
| — | `updated_at` | НОВАЯ |

#### `streams` → `streams`

| Текущая колонка | Целевая колонка | Изменение |
|----------------|----------------|-----------|
| `id` | `id` | Без изменений |
| `external_id` | `external_id` | Без изменений |
| `date` | `started_at` | **ПЕРЕИМЕНОВАНА** |
| — | `ended_at` | НОВАЯ |
| `duration` (Float, часы) | `duration_minutes` (integer) | **ТИП ИЗМЕНЁН**, явная колонка (не generated) |
| `avg_viewers` | `avg_viewers` | Без изменений |
| `max_viewers` | `max_viewers` | Без изменений |
| `followers` | `followers_gained` | **ПЕРЕИМЕНОВАНА** |
| `views` | `views_gained` | **ПЕРЕИМЕНОВАНА** |
| `title` | `title` | Без изменений |
| `vod_url` | `stream_recordings.url` (source='twitch') | **ПЕРЕНЕСЕНА** в `stream_recordings` |
| `clips_url` | — | **УДАЛЕНА** |
| `genres_text` | — | **УДАЛЕНА** (перенесена в связующие таблицы) |
| — | `created_at` | НОВАЯ |
| — | `updated_at` | НОВАЯ |

#### `stream_games` → `stream_games`

| Текущая колонка | Целевая колонка | Изменение |
|----------------|----------------|-----------|
| `stream_id` (PK) | `stream_id` | Теперь не PK |
| `game_id` (PK) | `game_id` | Теперь не PK |
| `position` | `position` | Без изменений |
| — | `id` (bigint PK) | НОВАЯ (автоинкремент) |
| — | `started_at` | НОВАЯ |
| — | `ended_at` | НОВАЯ |
| — | `duration_minutes` | НОВАЯ, явная колонка (не generated) |
| — | `avg_viewers` | НОВАЯ |
| — | `peak_viewers` | НОВАЯ |
| — | `followers_gained` | НОВАЯ |

#### `games_stats` → `game_stats`

| Текущая колонка | Целевая колонка | Изменение |
|----------------|----------------|-----------|
| `game_id` (PK) | `game_id` (PK) | Без изменений |
| `period` (PK) | — | **УДАЛЕНА** (только один набор статистики) |
| `hours_streamed` | `streamed_hours` | **ПЕРЕИМЕНОВАНА** |
| `avg_viewers` | `avg_viewers` | Без изменений |
| `max_viewers` | `max_viewers` | Без изменений |
| `followers_per_hour` | `followers_per_hour` | Без изменений |
| `streams_count` | `streams_count` | Без изменений |
| `last_stream` | `last_stream` | Без изменений |
| — | `synced_at` | НОВАЯ |

#### `games_meta` → РАЗБИВАЕТСЯ НА 3 ТАБЛИЦЫ

| Текущая колонка | Целевая таблица | Целевая колонка |
|----------------|----------------|----------------|
| `game_id` | `game_metadata_hltb.game_id` | PK |
| `game_id` | `game_metadata_igdb.game_id` | PK |
| `game_id` | `streamer_games` (через users) | FK |
| `hltb_hours` | `game_metadata_hltb.main_story_hours` | approx mapping |
| `steam_url` | `game_metadata_igdb.steam_url` | direct |
| `platforms_text` | `game_platforms` + `platforms` | normalized M:N |
| `genres_text` | `game_genres` + `genres` | normalized M:N |
| `liked` | `streamer_games.liked` | per-streamer |
| `completed` | `streamer_games.completed` | per-streamer |
| `review_url` | — | **УДАЛЕНА** или вручную |
| `clips_url` | — | **УДАЛЕНА** |

#### `participants` + `stream_participants` → `users` + `streamers_on_stream`

| Текущая | Целевая | Изменение |
|---------|---------|-----------|
| `participants.id` | `users.id` | Новая PK-структура |
| `participants.name` | `users.login` | **ПЕРЕИМЕНОВАНА** |
| `participants.display_name` | `users.display_name` | Без изменений |
| `participants.twitch_url` | `users.twitch_url` | Без изменений |
| — | `users.twitch_user_id` | НОВАЯ (potentially NULL) |
| — | `users.is_streamer` | НОВАЯ (TRUE для всех перенесённых) |
| — | `users.is_admin` | НОВАЯ |
| — | `users.is_trusted` | НОВАЯ |
| — | `users.duels_win/lose/draw` | НОВАЯ (0 по умолчанию) |
| — | `users.last_seen_at` | НОВАЯ |
| — | `users.created_at` | НОВАЯ |
| `stream_participants.stream_id` | `streamers_on_stream.stream_id` | Без изменений |
| `stream_participants.participant_id` | `streamers_on_stream.streamer_id` | FK → users.id |
| — | `streamers_on_stream.role` | НОВАЯ (varchar(20)) |
| — | `streamers_on_stream.created_at` | НОВАЯ |

#### `recommended_games` + `recommended_game_votes` → `game_recommendations`

| Текущая | Целевая | Изменение |
|---------|---------|-----------|
| `recommended_games.id` | — | **УДАЛЕНА** (composite PK) |
| `recommended_games.title` | `game_recommendations.game_id` | FK → games.id |
| `recommended_games.normalized_name` | — | **УДАЛЕНА** (ищем по game) |
| `recommended_games.status` | — | **УДАЛЕНА** (вычисляется backend) |
| `recommended_games.release_date` | — | **УДАЛЕНА** (в game_metadata_igdb) |
| `recommended_games.steam_url` | — | **УДАЛЕНА** (в game_metadata_igdb) |
| `recommended_games.rating_text` | — | **УДАЛЕНА** (в game_metadata_igdb) |
| `recommended_games.platforms_text` | — | **УДАЛЕНА** (в game_platforms) |
| `recommended_games.genres_text` | — | **УДАЛЕНА** (в game_genres) |
| `recommended_games.cover_url` | — | **УДАЛЕНА** (в game_metadata_igdb) |
| `recommended_games.source_name` | — | **УДАЛЕНА** |
| `recommended_games.source_game_id` | — | **УДАЛЕНА** |
| `recommended_games.source_payload` | — | **УДАЛЕНА** |
| `recommended_games.matched_game_id` | — | **УДАЛЕНА** (связь через game_id) |
| `recommended_games.streamer_interested` | — | **УДАЛЕНА** (в streamer_games) |
| `recommended_games.created_at` | `game_recommendations.created_at` | Без изменений |
| `recommended_games.updated_at` | — | **УДАЛЕНА** |
| `recommended_games.last_checked_at` | — | **УДАЛЕНА** |
| — | `game_recommendations.user_id` | НОВАЯ FK → users |
| — | `game_recommendations.game_id` | НОВАЯ FK → games |
| — | `game_recommendations.recommendation_note` | НОВАЯ (текстовый комментарий) |
| `recommended_game_votes.user_login` | → `game_recommendations.user_id` | Конвертация login → FK |
| `recommended_game_votes.user_display_name` | → `users.display_name` | Через users |
| `recommended_game_votes.created_at` | `game_recommendations.created_at` | Маппинг |

**Новая модель `game_recommendations`:**
- PK: `(user_id, game_id)` — уникальная пара
- `recommendation_note` — опциональный текстовый комментарий

### Полностью новые таблицы (без текущего аналога)

- `duels`, `duels_*` (7 таблиц) — система дуэлей
- `games_franchises` — франшизы игр
- `genres` — справочник жанров (нормализованный)
- `platforms` — справочник платформ (нормализованный)
- `clips`, `clip_tags` — Twitch-клипы
- `calendar_days`, `holidays`, `famous_persons` — календарь
- `bot_commands`, `bot_command_aliases`, `command_usage_logs` — команды
- `chat_messages`, `vip_history` — чат и VIP
- `twitch_authorizations`, `web_sessions` — авторизация
- `twitch_rewards`, `twitch_reward_redemptions` — награды
- `game_aliases` — алиасы игр
- `streamer_games` — отношение стримера к игре (из данных `games_meta`)
- `stream_recordings` — записи/VOD стримов (Twitch VOD, YouTube, Boosty)
- `stream_runtime_samples`, `stream_runtime_buckets` — runtime-аналитика

### Удаляемые таблицы

| Таблица | Причина |
|---------|---------|
| `games_meta` | Разбивается на `game_metadata_igdb`, `game_metadata_hltb`, `streamer_games` |
| `participants` | Заменяется на `users` |
| `stream_participants` | Заменяется на `streamers_on_stream` |
| `recommended_games` | Заменяется на `game_recommendations` |
| `recommended_game_votes` | Модель голосования убрана |

---

## 4. Пошаговый план миграции

### Этап 1: Инфраструктура (БД + подключение)

#### 1.1 Зависимости

Файл: `requirements.txt`

Добавить:
```
psycopg2-binary==2.9.9
asyncpg==0.30.0
SQLAlchemy[asyncio]==2.0.48
alembic==1.14.0
```

Удалить (заменить):
```
SQLAlchemy==2.0.48  →  SQLAlchemy[asyncio]==2.0.48
```

#### 1.2 Конфигурация подключения

Файл: `database/db.py`

Текущий код:
```python
DATABASE_URL = f"sqlite:///{db_path}"
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
```

Целевой код:
```python
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/mishgan_bot")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()
```

#### 1.3 Переменные окружения

Файл: `.env` / `.env.example`

Добавить:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/mishgan_bot
```

#### 1.4 Docker Compose

Файл: `docker-compose.yml`

Добавить сервис PostgreSQL:
```yaml
services:
  db:
    image: postgres:16-alpine
    container_name: mishgan-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: mishgan_bot
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  bot:
    build: .
    container_name: mishgan-twitch-bot
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - db
    volumes:
      - ./storage:/app/storage
    command: ["python", "bot.py"]

volumes:
  postgres_data:
```

#### 1.5 Dockerfile

Файл: `Dockerfile`

Добавить системные зависимости для psycopg2:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*
```

#### 1.6 Alembic — установка и инициализация

```bash
# Установка (уже в requirements.txt)
pip install alembic

# Инициализация в корне проекта
alembic init alembic
```

Структура после инициализации:
```
alembic/
  env.py          # Конфигурация подключения
  script.py.mako  # Шаблон миграций
  versions/       # Здесь будут миграции
alembic.ini       # Конфиг Alembic
```

#### 1.7 Настройка Alembic

Файл: `alembic.ini`

Изменить строку `sqlalchemy.url`:
```ini
sqlalchemy.url = postgresql+asyncpg://postgres:postgres@localhost:5432/mishgan_bot
```

Файл: `alembic/env.py`

Настроить подключение к БД и импорт моделей:
```python
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Импорт всех моделей для autogenerate
from database.db import Base
from database.models import *  # noqa: F401,F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

#### 1.8 Команды Alembic (справочник)

```bash
# Создать миграцию (autogenerate на основе изменений в моделях)
alembic revision --autogenerate -m "description"

# Применить все неприменённые миграции
alembic upgrade head

# Откатить одну миграцию
alembic downgrade -1

# Откатить до конкретной версии
alembic downgrade <revision_id>

# Посмотреть историю миграций
alembic history

# Посмотреть текущую версию
alembic current

# Просмотреть SQL миграции без применения
alembic upgrade head --sql
```

#### 1.9 Первая миграция: создание схемы

```bash
# 1. Убедиться, что модели написаны в database/models.py
# 2. Создать миграцию
alembic revision --autogenerate -m "initial schema"

# 3. Проверить сгенерированный файл в alembic/versions/
# 4. Применить
alembic upgrade head
```

---

### Этап 2: Новые ORM-модели

Файл: `database/models.py`

Все модели создаются в одном файле (текущая конвенция проекта). Разделены комментариями по секциям.

#### 2.1 CORE DOMAIN

```python
# games — канонический каталог игр
class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    game_type = Column(String(20))
    parent_game_id = Column(Integer, ForeignKey("games.id"))
    franchise_id = Column(Integer, ForeignKey("games_franchises.id"))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

# streams — история стримов
class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True)
    external_id = Column(Text, unique=True)
    title = Column(Text)
    vod_url = Column(Text)
    started_at = Column(DateTime, index=True)
    ended_at = Column(DateTime)
    duration_minutes = Column(Integer)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_gained = Column(Integer)
    views_gained = Column(Integer)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

# users — пользователи Twitch
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    twitch_user_id = Column(Text, unique=True)
    login = Column(Text, unique=True)
    display_name = Column(Text)
    profile_image_url = Column(Text)
    birthday_calendar_day_id = Column(Integer, ForeignKey("calendar_days.id"))
    birthday_set_at = Column(DateTime)
    birthday_changed_at = Column(DateTime)
    is_streamer = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    is_trusted = Column(Boolean, default=False)
    twitch_management = Column(Boolean, default=False)
    duels_win = Column(Integer)
    duels_lose = Column(Integer)
    duels_draw = Column(Integer)
    twitch_url = Column(Text)
    last_seen_at = Column(DateTime)
    created_at = Column(DateTime)
```

#### 2.2 DUELS

```python
class DuelsFranchise(Base):
    __tablename__ = "duels_franchises"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime)

class DuelsCharacter(Base):
    __tablename__ = "duels_characters"
    id = Column(Integer, primary_key=True)
    franchise_id = Column(Integer, ForeignKey("duels_franchises.id"), nullable=False)
    name = Column(Text, nullable=False)
    created_at = Column(DateTime)

class DuelsCharacterVersion(Base):
    __tablename__ = "duels_character_versions"
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("duels_characters.id"), nullable=False)
    version_name = Column(Text, nullable=False)
    description = Column(Text)
    power_tier = Column(SmallInteger)
    created_at = Column(DateTime)

class DuelsAbility(Base):
    __tablename__ = "duels_abilities"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime)

# M:N: duels_version_abilities
duels_version_abilities = Table(
    "duels_version_abilities",
    Base.metadata,
    Column("version_id", Integer, ForeignKey("duels_character_versions.id"), nullable=False),
    Column("ability_id", Integer, ForeignKey("duels_abilities.id"), nullable=False),
    UniqueConstraint("version_id", "ability_id"),
)

class DuelsScenario(Base):
    __tablename__ = "duels_scenarios"
    id = Column(BigInteger, primary_key=True)
    franchise_id = Column(Integer, ForeignKey("duels_franchises.id"), nullable=False)
    challenger_version_id = Column(Integer, ForeignKey("duels_character_versions.id"), nullable=False)
    target_version_id = Column(Integer, ForeignKey("duels_character_versions.id"), nullable=False)
    step1 = Column(String(250))
    step2 = Column(String(250))
    step3 = Column(String(250))
    step4 = Column(String(250))
    is_draw = Column(Boolean, default=False)
    winner_version_id = Column(Integer, ForeignKey("duels_character_versions.id"))
    created_at = Column(DateTime)

class DuelsScenarioUsage(Base):
    __tablename__ = "duels_scenario_usage"
    id = Column(BigInteger, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scenario_id = Column(BigInteger, ForeignKey("duels_scenarios.id"), nullable=False)
    used_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("streamer_id", "scenario_id"),
    )
```

#### 2.3 RELATIONS

```python
class StreamGame(Base):
    __tablename__ = "stream_games"

    id = Column(BigInteger, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    position = Column(Integer, nullable=False)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    duration_minutes = Column(Integer)
    avg_viewers = Column(Integer)
    peak_viewers = Column(Integer)
    followers_gained = Column(Integer)
    __table_args__ = (
        UniqueConstraint("stream_id", "position"),
    )

class StreamRecording(Base):
    __tablename__ = "stream_recordings"

    id = Column(BigInteger, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    source = Column(String(20), nullable=False)  # twitch / youtube / boosty
    url = Column(Text, nullable=False)
    duration_minutes = Column(Integer)
    recorded_at = Column(DateTime)
    created_at = Column(DateTime)

class StreamerOnStream(Base):
    __tablename__ = "streamers_on_stream"

    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False, primary_key=True)
    role = Column(String(20))
    created_at = Column(DateTime)

class StreamerGame(Base):
    __tablename__ = "streamer_games"

    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, primary_key=True)
    interested = Column(Boolean, default=False)
    liked = Column(Boolean, default=False)
    completed = Column(Boolean, default=False)
    updated_at = Column(DateTime)

class GameRecommendation(Base):
    __tablename__ = "game_recommendations"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, primary_key=True)
    recommendation_note = Column(Text)
    created_at = Column(DateTime)

# M:N: game_genres
game_genres = Table(
    "game_genres",
    Base.metadata,
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id"), nullable=False, primary_key=True),
)

# M:N: game_platforms
game_platforms = Table(
    "game_platforms",
    Base.metadata,
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("platform_id", Integer, ForeignKey("platforms.id"), nullable=False, primary_key=True),
)
```

#### 2.4 EXTERNAL METADATA

```python
class GameMetadataIGDB(Base):
    __tablename__ = "game_metadata_igdb"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    igdb_id = Column(Text, nullable=False, unique=True)
    is_primary = Column(Boolean, default=False)
    release_date = Column(DateTime)
    igdb_name = Column(Text)
    steam_url = Column(Text)
    igdb_score = Column(Float)
    steam_score = Column(Float)
    description_en = Column(Text)
    description_ru = Column(Text)
    cover_url = Column(Text)
    raw_payload = Column(JSONB)
    synced_at = Column(DateTime)

class GameMetadataHLTB(Base):
    __tablename__ = "game_metadata_hltb"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    hltb_id = Column(Text, unique=True)
    hltb_name = Column(Text)
    avg_hours = Column(Float)
    main_story_hours = Column(Float)
    main_extra_hours = Column(Float)
    completionist_hours = Column(Float)
    review_count = Column(Integer)
    synced_at = Column(DateTime)

class GameStats(Base):
    __tablename__ = "game_stats"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    streamed_hours = Column(Float)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_per_hour = Column(Float)
    streams_count = Column(Integer)
    last_stream = Column(DateTime)
    synced_at = Column(DateTime, nullable=False)

class GameAlias(Base):
    __tablename__ = "game_aliases"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    alias = Column(Text, nullable=False)
    normalized_alias = Column(Text, nullable=False)
    is_primary = Column(Boolean)
    source = Column(Text)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    __table_args__ = (
        UniqueConstraint("game_id", "normalized_alias"),
    )
```

#### 2.5 SUPPORTING TABLES

```python
class GamesFranchise(Base):
    __tablename__ = "games_franchises"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)

class Genre(Base):
    __tablename__ = "genres"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    created_at = Column(DateTime)

class Platform(Base):
    __tablename__ = "platforms"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    created_at = Column(DateTime)
```

#### 2.6 ОСТАЛЬНЫЕ ТАБЛИЦЫ

Клипы, календарь, команды чата, VIP, авторизация, награды, дуэли, runtime — определены по аналогии из DBML-схемы в `storage/db_project.txt`.

---

### Этап 3: Миграция данных

Скрипт: `database/migrate_to_pg.py`

Новый файл, создаётся для одноразового переноса данных из SQLite в PostgreSQL.

#### 3.1 Общая структура скрипта

```python
"""
Одноразовый скрипт миграции данных из SQLite (storage/streams.db) в PostgreSQL.

Использование:
    python database/migrate_to_pg.py

Требования:
    - SQLite файл storage/streams.db с данными
    - PostgreSQL запущен и доступен
    - DATABASE_URL настроен в .env
"""
import sqlite3
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database.db import AsyncSessionLocal


async def migrate():
    sqlite_conn = sqlite3.connect("storage/streams.db")
    sqlite_conn.row_factory = sqlite3.Row

    async with AsyncSessionLocal() as pg_session:
        await migrate_users(sqlite_conn, pg_session)
        await migrate_games(sqlite_conn, pg_session)
        await migrate_game_metadata(sqlite_conn, pg_session)
        await migrate_streams(sqlite_conn, pg_session)
        await migrate_stream_recordings(sqlite_conn, pg_session)
        await migrate_stream_games(sqlite_conn, pg_session)
        await migrate_game_recommendations(sqlite_conn, pg_session)
        await migrate_streamer_games(sqlite_conn, pg_session)
        await pg_session.commit()

    sqlite_conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
```

#### 3.2 Миграция users (из participants)

Алгоритм:
1. Прочитать все уникальные `participants.name` из SQLite
2. Для каждой записи создать `users`:
   - `login` = `participants.name`
   - `display_name` = `participants.display_name` (или `f"@{name}"`)
   - `twitch_url` = `participants.twitch_url`
   - `is_streamer` = `True`
   - `twitch_user_id` = `NULL` (заполнить позже)
   - `created_at` = `datetime.utcnow()`
3. Сохранить маппинг `old_participant_id → new_user_id`

```python
async def migrate_users(sqlite_conn, pg_session):
    rows = sqlite_conn.execute("SELECT * FROM participants").fetchall()
    id_mapping = {}  # old_id → new_id

    for row in rows:
        result = await pg_session.execute(
            text("""
                INSERT INTO users (login, display_name, twitch_url, is_streamer, created_at)
                VALUES (:login, :display_name, :twitch_url, true, :now)
                ON CONFLICT (login) DO NOTHING
                RETURNING id
            """),
            {
                "login": row["name"],
                "display_name": row["display_name"] or f"@{row['name']}",
                "twitch_url": row["twitch_url"],
                "now": datetime.utcnow(),
            }
        )
        new_id = result.scalar_one_or_none()
        if new_id is None:
            # Уже существует — найти по login
            result = await pg_session.execute(
                text("SELECT id FROM users WHERE login = :login"),
                {"login": row["name"]}
            )
            new_id = result.scalar_one()

        id_mapping[row["id"]] = new_id

    return id_mapping
```

#### 3.3 Миграция games

Алгоритм:
1. Прочитать все `games` из SQLite
2. Создать в PostgreSQL с новыми полями (`slug`, `created_at`)
3. Для каждого `games.name` создать `game_aliases`:
   - `alias` = `name`
   - `normalized_alias` = `name.lower().strip()`
   - `is_primary` = `True`
   - `source` = `"manual"`

```python
async def migrate_games(sqlite_conn, pg_session):
    rows = sqlite_conn.execute("SELECT * FROM games").fetchall()
    id_mapping = {}

    for row in rows:
        slug = row["name"].lower().replace(" ", "-")
        result = await pg_session.execute(
            text("""
                INSERT INTO games (name, slug, created_at, updated_at)
                VALUES (:name, :slug, :now, :now)
                ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                RETURNING id
            """),
            {"name": row["name"], "slug": slug, "now": datetime.utcnow()}
        )
        game_id = result.scalar_one()
        id_mapping[row["id"]] = game_id

        # Создать primary alias
        await pg_session.execute(
            text("""
                INSERT INTO game_aliases (game_id, alias, normalized_alias, is_primary, source, created_at)
                VALUES (:game_id, :alias, :normalized_alias, true, 'manual', :now)
                ON CONFLICT (game_id, normalized_alias) DO NOTHING
            """),
            {
                "game_id": game_id,
                "alias": row["name"],
                "normalized_alias": row["name"].lower().strip(),
                "now": datetime.utcnow(),
            }
        )

    return id_mapping
```

#### 3.4 Миграция game_metadata (из games_meta)

Алгоритм:
1. `games_meta.hltb_hours` → `game_metadata_hltb.main_story_hours`
2. `games_meta.steam_url` → `game_metadata_igdb.steam_url`
3. `games_meta.platforms_text` → нормализовать в `platforms` + `game_platforms`
4. `games_meta.genres_text` → нормализовать в `genres` + `game_genres`

```python
async def migrate_game_metadata(sqlite_conn, pg_session):
    rows = sqlite_conn.execute("SELECT * FROM games_meta").fetchall()

    for row in rows:
        game_id = row["game_id"]

        # HLTB
        if row["hltb_hours"] is not None:
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_hltb (game_id, main_story_hours, synced_at)
                    VALUES (:game_id, :hours, :now)
                    ON CONFLICT (game_id) DO UPDATE SET main_story_hours = EXCLUDED.main_story_hours
                """),
                {"game_id": game_id, "hours": row["hltb_hours"], "now": datetime.utcnow()}
            )

        # IGDB (только steam_url)
        if row["steam_url"] is not None:
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_igdb (game_id, igdb_id, steam_url, synced_at)
                    VALUES (:game_id, 'pending', :steam_url, :now)
                    ON CONFLICT (game_id) DO UPDATE SET steam_url = EXCLUDED.steam_url
                """),
                {"game_id": game_id, "steam_url": row["steam_url"], "now": datetime.utcnow()}
            )

        # Platforms → platforms + game_platforms
        if row["platforms_text"]:
            await _migrate_text_list(pg_session, game_id, row["platforms_text"], "platform")

        # Genres → genres + game_genres
        if row["genres_text"]:
            await _migrate_text_list(pg_session, game_id, row["genres_text"], "genre")
```

#### 3.5 Миграция streams

Алгоритм:
1. `streams.date` → `streams.started_at`
2. `streams.duration` (часы) → `streams.duration_minutes` (минуты, явная колонка)
3. `streams.followers` → `streams.followers_gained`
4. `streams.views` → `streams.views_gained`
5. `streams.vod_url` → `stream_recordings` (source='twitch')
6. `streams.clips_url` — игнорируется (удалена)
7. `streams.genres_text` — игнорируется (удалена)

```python
async def migrate_streams(sqlite_conn, pg_session):
    rows = sqlite_conn.execute("SELECT * FROM streams").fetchall()
    id_mapping = {}

    for row in rows:
        duration_min = int(row["duration"] * 60) if row["duration"] else None
        result = await pg_session.execute(
            text("""
                INSERT INTO streams (external_id, title, started_at, avg_viewers,
                                     max_viewers, followers_gained, views_gained, created_at)
                VALUES (:external_id, :title, :started_at, :avg_viewers,
                        :max_viewers, :followers, :views, :now)
                ON CONFLICT (external_id) DO UPDATE SET
                    title = EXCLUDED.title
                RETURNING id
            """),
            {
                "external_id": row["external_id"],
                "title": row["title"],
                "started_at": row["date"],
                "avg_viewers": row["avg_viewers"],
                "max_viewers": row["max_viewers"],
                "followers": row["followers"],
                "views": row["views"],
                "now": datetime.utcnow(),
            }
        )
        stream_id = result.scalar_one()
        id_mapping[row["id"]] = stream_id

        # vod_url → stream_recordings (source='twitch')
        if row["vod_url"]:
            await pg_session.execute(
                text("""
                    INSERT INTO stream_recordings (stream_id, source, url, created_at)
                    VALUES (:stream_id, 'twitch', :url, :now)
                """),
                {"stream_id": stream_id, "url": row["vod_url"], "now": datetime.utcnow()}
            )

    return id_mapping
```

#### 3.6 Миграция stream_games

Алгоритм:
1. `stream_games(stream_id, game_id, position)` → новая `stream_games` с `id` PK
2. `started_at`, `ended_at`, `avg_viewers`, `peak_viewers`, `followers_gained` = NULL (недоступны)

```python
async def migrate_stream_games(sqlite_conn, pg_session):
    rows = sqlite_conn.execute("SELECT * FROM stream_games").fetchall()

    for row in rows:
        await pg_session.execute(
            text("""
                INSERT INTO stream_games (stream_id, game_id, position)
                VALUES (:stream_id, :game_id, :position)
            """),
            {"stream_id": row["stream_id"], "game_id": row["game_id"], "position": row["position"]}
        )
```

#### 3.7 Миграция recommended_games → game_recommendations

Алгоритм:
1. Прочитать `recommended_games` + `recommended_game_votes` из SQLite
2. Сджойнить по `recommended_game_id`
3. Для каждой уникальной пары `(user_login, matched_game_id)` создать запись `game_recommendations`
4. `recommendation_note`:
   - Если `user_login` = `tabula` → `"В списке желаемого Steam"`
   - Если `source_name` = `igdb` → `"Игра популярна на сервисе IGDB"`
   - Иначе → `NULL`
5. `game_id` = `matched_game_id` (если NULL — пропустить запись, игра не сматчена)

```python
async def migrate_game_recommendations(sqlite_conn, pg_session):
    # 1. Найти source_name='tabula' рекомендации
    tabula_recs = set()
    rows = sqlite_conn.execute(
        "SELECT id FROM recommended_games WHERE source_name = 'tabula'"
    ).fetchall()
    for row in rows:
        tabula_recs.add(row["id"])

    # 2. Найти IGDB рекомендации
    igdb_recs = set()
    rows = sqlite_conn.execute(
        "SELECT id FROM recommended_games WHERE source_name = 'igdb'"
    ).fetchall()
    for row in rows:
        igdb_recs.add(row["id"])

    # 3. Джойн с голосами
    votes = sqlite_conn.execute("""
        SELECT rg.matched_game_id, rgv.user_login, rg.id as rec_id
        FROM recommended_games rg
        JOIN recommended_game_votes rgv ON rgv.recommended_game_id = rg.id
        WHERE rg.matched_game_id IS NOT NULL
    """).fetchall()

    inserted = set()
    for vote in votes:
        key = (vote["user_login"], vote["matched_game_id"])
        if key in inserted:
            continue
        inserted.add(key)

        # Определить recommendation_note
        note = None
        if vote["rec_id"] in tabula_recs:
            note = "В списке желаемого Steam"
        elif vote["rec_id"] in igdb_recs:
            note = "Игра популярна на сервисе IGDB"

        # Найти user_id по login
        user_result = await pg_session.execute(
            text("SELECT id FROM users WHERE login = :login"),
            {"login": vote["user_login"]}
        )
        user_row = user_result.first()
        if user_row is None:
            continue  # Пользователь не найден — пропустить

        await pg_session.execute(
            text("""
                INSERT INTO game_recommendations (user_id, game_id, recommendation_note, created_at)
                VALUES (:user_id, :game_id, :note, :now)
                ON CONFLICT (user_id, game_id) DO NOTHING
            """),
            {
                "user_id": user_row[0],
                "game_id": vote["matched_game_id"],
                "note": note,
                "now": datetime.utcnow(),
            }
        )
```

#### 3.8 Миграция streamer_games (из games_meta)

Алгоритм:
1. `games_meta.liked` → `streamer_games.liked`
2. `games_meta.completed` → `streamer_games.completed`
3. `streamer_id` = ID основного стримера (Tabula)

```python
async def migrate_streamer_games(sqlite_conn, pg_session):
    # Найти streamer_id для Tabula
    result = await pg_session.execute(
        text("SELECT id FROM users WHERE login = 'tabula'")
    )
    tabula_row = result.first()
    if tabula_row is None:
        return  # Табула не найдена

    streamer_id = tabula_row[0]

    rows = sqlite_conn.execute(
        "SELECT game_id, liked, completed FROM games_meta WHERE liked IS NOT NULL OR completed IS NOT NULL"
    ).fetchall()

    for row in rows:
        await pg_session.execute(
            text("""
                INSERT INTO streamer_games (streamer_id, game_id, interested, liked, completed, updated_at)
                VALUES (:streamer_id, :game_id, :interested, :liked, :completed, :now)
                ON CONFLICT (streamer_id, game_id) DO UPDATE SET
                    liked = EXCLUDED.liked, completed = EXCLUDED.completed
            """),
            {
                "streamer_id": streamer_id,
                "game_id": row["game_id"],
                "interested": bool(row["liked"] or row["completed"]),
                "liked": row["liked"],
                "completed": row["completed"],
                "now": datetime.utcnow(),
            }
        )
```

#### 3.9 Заполнение calendar_days

```python
async def seed_calendar_days(pg_session):
    """Создать 366 записей (1 января — 31 декабря)"""
    import calendar
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2024, month)[1] + 1):
            day_of_year = datetime(2024, month, day).timetuple().tm_yday
            await pg_session.execute(
                text("""
                    INSERT INTO calendar_days (day, month, day_of_year)
                    VALUES (:day, :month, :day_of_year)
                    ON CONFLICT (month, day) DO NOTHING
                """),
                {"day": day, "month": month, "day_of_year": day_of_year}
            )
```

---

### Этап 4: Бизнес-логика

#### 4.1 Переход на AsyncSession

Все файлы, работающие с БД, должны быть конвертированы:

**Паттерн замены:**

Текущий код:
```python
from database.db import SessionLocal

def some_function():
    session = SessionLocal()
    try:
        result = session.query(Model).filter_by(...).all()
        return result
    finally:
        session.close()
```

Целевой код:
```python
from database.db import AsyncSessionLocal

async def some_function():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Model).where(Model.column == value)
        )
        return result.scalars().all()
```

**Ключевые изменения в синтаксисе SQLAlchemy:**

| Текущий (sync) | Целевой (async) |
|----------------|-----------------|
| `session.query(Model)` | `await session.execute(select(Model))` |
| `.filter_by(name=x)` | `.where(Model.name == x)` |
| `.filter(Model.col == x)` | `.where(Model.col == x)` |
| `.first()` | `.scalar_one_or_none()` |
| `.all()` | `.scalars().all()` |
| `.count()` | `select(func.count()).select_from(...)` |
| `session.add(obj)` | `await session.flush()` (после add) |
| `session.commit()` | `await session.commit()` |
| `session.rollback()` | `await session.rollback()` |
| `func.count()` | `from sqlalchemy import func` (без изменений) |

#### 4.2 Файлы для конвертации

**Load layer (критические):**

| Файл | Изменения |
|------|-----------|
| `pipeline/load/load_games.py` | `get_or_create_game` → async, убрать автосоздание `GameMeta`, добавить `game_aliases` |
| `pipeline/load/load_streams.py` | `sync_streams` → async, новые колонки `started_at`/`ended_at`, vod_url переносится в `stream_recordings` |
| `pipeline/load/load_game_stats.py` | `sync_game_stats` → async, таблица `game_stats` без `period` |
| `pipeline/load/load_game_meta.py` | Полная переработка: `GameMeta` → 3 таблицы |
| `pipeline/load/load_participants.py` | `Participant` → `User`, `stream_participants` → `streamers_on_stream` |
| `pipeline/load/load_stream_games.py` | Новая модель `StreamGame` с собственным `id` |
| `pipeline/load/load_recommendations.py` | Полная переработка: новая модель `game_recommendations` |

**Service layer:**

| Файл | Изменения |
|------|-----------|
| `services/games_service.py` | JOIN через `game_stats` (без period), streamer_games вместо GameMeta |
| `services/streams_service.py` | Async, `Stream.date` → `Stream.started_at`, JOIN через `streamers_on_stream`, `vod_url` → `stream_recordings` |
| `services/recommendations_service.py` | Полная переработка: новая модель, нет голосов |

**Delivery layer:**

| Файл | Изменения |
|------|-----------|
| `pipeline/delivery/sheets_games.py` | Запросы к `game_stats` + `streamer_games` |
| `pipeline/delivery/sheets_streams.py` | Запросы к `streams` + `streamers_on_stream` |
| `pipeline/delivery/sheets_releases.py` | Запросы к `game_recommendations` |
| `pipeline/delivery/sheets_recommendations.py` | Запросы к `game_recommendations` |

**Orchestrator layer:**

| Файл | Изменения |
|------|-----------|
| `pipeline/orchestrator/import_json_to_db.py` | Async, новые модели |
| `pipeline/orchestrator/import_igdb_releases.py` | Async, game_recommendations |
| `pipeline/orchestrator/enrich_descriptions_with_gpt.py` | Async, game_metadata_igdb |
| `pipeline/orchestrator/sync_wishlist.py` | Async, game_recommendations |
| `pipeline/orchestrator/update_upcoming_release_dates.py` | Async, game_metadata_igdb |
| `pipeline/orchestrator/sync_sheets.py` | Async, все delivery-функции |

**Utils:**

| Файл | Изменения |
|------|-----------|
| `utils/db_manage.py` | Полная переработка: sqlite3 → asyncpg/SQLAlchemy async |

**Init:**

| Файл | Изменения |
|------|-----------|
| `database/init_db.py` | `Base.metadata.create_all` → async |
| `database/seed.py` | Async, новые модели |

#### 4.3 Исправление deprecated `datetime.utcnow()`

Во всех файлах заменить:
```python
datetime.utcnow()
```
на:
```python
from datetime import datetime, timezone
datetime.now(timezone.utc)
```

Затронутые файлы:
- `services/recommendations_service.py` (строки 225, 236)
- Все файлы в `pipeline/load/`
- Все файлы в `pipeline/orchestrator/`

---

### Этап 5: Удаление устаревшего

| Действие | Файлы |
|----------|-------|
| Удалить класс `GameMeta` | `database/models.py` |
| Удалить класс `Participant` | `database/models.py` |
| Удалить `stream_participants` association table | `database/models.py` |
| Удалить `RecommendedGame`, `RecommendedGameVote` | `database/models.py` |
| Удалить старые миграции | `database/migrate_streams.py`, `database/migrate_games_meta.py`, `database/migrate_recommendations.py` |
| Удалить/заархивировать SQLite | `storage/streams.db` |
| Обновить `.gitignore` | Убрать `*.db`, добавить postgres volume |

---

## 5. Список файлов для изменений

### Обязательные изменения (35+ файлов)

#### Критические (переписывание)

| # | Файл | Объём изменений |
|---|------|----------------|
| 1 | `database/db.py` | Полная переработка: async engine |
| 2 | `database/models.py` | Полная переработка: 40+ таблиц |
| 3 | `database/init_db.py` | Async create_all |
| 4 | `database/seed.py` | Async, новые модели |
| 5 | `pipeline/load/load_game_meta.py` | Полная переработка |
| 6 | `pipeline/load/load_recommendations.py` | Полная переработка |
| 7 | `services/recommendations_service.py` | Полная переработка |
| 8 | `utils/db_manage.py` | Полная переработка: sqlite3 → PostgreSQL |

#### Значительные изменения

| # | Файл | Объём изменений |
|---|------|----------------|
| 9 | `requirements.txt` | Добавить зависимости |
| 10 | `.env` | Добавить DATABASE_URL |
| 11 | `.env.example` | Добавить DATABASE_URL |
| 12 | `docker-compose.yml` | PostgreSQL-сервис |
| 13 | `Dockerfile` | Системные зависимости |
| 14 | `pipeline/load/load_streams.py` | Async, новые колонки, vod_url → stream_recordings |
| 15 | `pipeline/load/load_games.py` | Async, без GameMeta |
| 16 | `pipeline/load/load_game_stats.py` | Async, новая таблица |
| 17 | `pipeline/load/load_participants.py` | Participant → User |
| 18 | `pipeline/load/load_stream_games.py` | Новая модель |
| 19 | `services/games_service.py` | Async, новые JOIN'ы |
| 20 | `services/streams_service.py` | Async, новые колонки |
| 21 | `pipeline/delivery/sheets_games.py` | Async, новые запросы |
| 22 | `pipeline/delivery/sheets_streams.py` | Async, новые запросы |
| 23 | `pipeline/delivery/sheets_releases.py` | Async, новые запросы |
| 24 | `pipeline/delivery/sheets_recommendations.py` | Async, новые запросы |
| 25 | `pipeline/orchestrator/import_json_to_db.py` | Async |
| 26 | `pipeline/orchestrator/import_igdb_releases.py` | Async |
| 27 | `pipeline/orchestrator/enrich_descriptions_with_gpt.py` | Async |
| 28 | `pipeline/orchestrator/sync_wishlist.py` | Async |
| 29 | `pipeline/orchestrator/update_upcoming_release_dates.py` | Async |
| 30 | `pipeline/orchestrator/sync_sheets.py` | Async |

#### Удаление

| # | Файл | Причина |
|---|------|---------|
| 31 | `database/migrate_streams.py` | Заменяется Alembic |
| 32 | `database/migrate_games_meta.py` | Заменяется Alembic |
| 33 | `database/migrate_recommendations.py` | Заменяется Alembic |

#### Новые файлы

| # | Файл | Назначение |
|---|------|------------|
| 34 | `alembic.ini` | Конфигурация Alembic |
| 35 | `alembic/env.py` | Окружение миграций |
| 36 | `alembic/versions/` | Директория миграций |
| 37 | `database/migrate_to_pg.py` | Скрипт переноса данных |

---

## 6. Миграция данных

### Общие принципы

1. **Стратегия**: SQLite → PostgreSQL через Python-скрипт (не pg_dump/import)
2. **Идемпотентность**: `ON CONFLICT DO NOTHING/UPDATE` для безопасного повторного запуска
3. **Порядок**: сначала справочники, потом основные таблицы, потом связи
4. **Маппинг ID**: сохранять соответствие старых и новых ID через dict

### Порядок миграции

```
1. calendar_days (366 записей, seed)
2. games_franchises (если есть)
3. genres (из games_meta.genres_text)
4. platforms (из games_meta.platforms_text)
5. games (из games) → game_aliases (primary alias)
6. users (из participants) + streamers_on_stream (из stream_participants)
7. streams (из streams) → stream_recordings (vod_url → source='twitch')
8. stream_games (из stream_games)
9. game_stats (из games_stats, только period='all')
10. game_metadata_hltb (из games_meta.hltb_hours)
11. game_metadata_igdb (из games_meta.steam_url)
12. streamer_games (из games_meta.liked/completed, streamer=Tabula)
13. game_recommendations (из recommended_games + recommended_game_votes)
```

### Маппинг recommendation_note

| Условие | recommendation_note |
|---------|-------------------|
| `source_name = 'tabula'` | `"В списке желаемого Steam"` |
| `source_name = 'igdb'` | `"Игра популярна на сервисе IGDB"` |
| Все остальные | `NULL` |

### Потери данных

| Что теряется | Причина |
|-------------|---------|
| `clips_url` из streams | Колонка удалена в целевой схеме |
| `vod_url` из streams | Переносится в `stream_recordings` (source='twitch') |
| `genres_text` из streams | Заменяется на `game_genres` (нормализация) |
| `review_url` из games_meta | Нет аналога в целевой схеме |
| `status` из recommended_games | Вычисляется backend |
| `release_date` из recommended_games | Переносится в `game_metadata_igdb` |
| `source_name/source_game_id/source_payload` | Удаляются |
| `matched_game_id` из recommended_games | Заменяется прямой FK `game_id` |
| `streamer_interested` из recommended_games | Переносится в `streamer_games.interested` |
| Множественные `period` в games_stats | Остаётся только `"all"` |
| Голоса (votes) | Конвертируются в `game_recommendations` (1 запись на user+game) |

---

## 7. Потенциальные проблемы и ошибки

### Критические

| Проблема | Описание | Решение |
|----------|----------|---------|
| **`datetime.utcnow()` deprecated** | Используется в `recommendations_service.py:225,236` и других файлах. Deprecated в Python 3.12+ | Заменить на `datetime.now(timezone.utc)` во всех файлах |
| **JSONB тип** | `game_metadata_igdb.raw_payload` — `json` в DBML. SQLite хранит как text | В PostgreSQL использовать `JSONB` для индексации |
| **UUID PK** | `web_sessions.id` — UUID. SQLite не поддерживает нативный UUID | В PostgreSQL использовать `Uuid` тип с `uuid_generate_v4()` |
| **Нет Alembic** | Текущие миграции — ad-hoc скрипты. PostgreSQL требует нормальную миграционную систему | Установить Alembic, создать первую миграцию |
| **`ON DELETE` каскады** | Текущие ForeignKey в целевой схеме не указывают `ON DELETE`. PostgreSQL поддерживает FK, но нужно явно указать каскады | Добавить `ON DELETE CASCADE` или `ON DELETE SET NULL` где необходимо |

### Средние

| Проблема | Описание | Решение |
|----------|----------|---------|
| **Маппинг `participants` → `users`** | `participants` — простые участники (name, display_name). `users` — полноценные Twitch-пользователи | `twitch_user_id = NULL`, `is_streamer = true`, заполнить позже |
| **`recommended_games` → `game_recommendations`** | Полностью другая модель. Нужна конвертация голосов | Скрипт миграции (см. раздел 3.7) |
| **`games_meta` → 3 таблицы** | Текущие `liked`, `completed` идут в `streamer_games`. Нужно определить `streamer_id` | Использовать `streamer_id` = Tabula (или текущий стример) |
| **Потеря `period` в `game_stats`** | Текущая таблица поддерживает несколько периодов. Целевая — только один | Миграция: взять данные для `period='all'` |
| **`game_aliases`** | Текущие `games.name` должны стать алиасами | Создать primary alias для каждой игры |
| **Нет `stream_participants` в целевой** | Заменяется на `streamers_on_stream` с другим смыслом | Участники стрима — это стримеры, не зрители |
| **Синхронный → асинхронный** | Все `session.query()` → `await session.execute(select(...))`. Затронет ~30 файлов | Пошаговая конвертация каждого файла |

### Низкие

| Проблема | Описание | Решение |
|----------|----------|---------|
| **Case sensitivity** | PostgreSQL case-sensitive по умолчанию. `LIKE` → `ILIKE` | Проверить все запросы с текстовым поиском |
| **Timezone** | SQLite не хранит timezone. PostgreSQL — `TIMESTAMP` или `TIMESTAMPTZ` | Использовать `TIMESTAMP` для совместимости |
| **`float` precision** | `float` в DBML = `double precision` в PostgreSQL. Для денег лучше `Numeric` | Проверить, где нужна точность |
| **Индексы** | Текущие индексы определены в DBML, но в SQLAlchemy нужно объявить через `Index()` | Добавить в модели или миграции |
| **`storage/streams.db`** | Файл больше не нужен | Архивировать, обновить .gitignore |
| **`casefold()` в коде** | Используется для normalizaiton. В PostgreSQL может потребоваться `COLLATE` | Проверить UTF-8 collation |

---

## 8. Рекомендуемый порядок выполнения

```
Фаза 1: Инфраструктура (1-2 дня)
├── 1.1 Установить зависимости (psycopg2, asyncpg, alembic)
├── 1.2 Настроить PostgreSQL в Docker Compose
├── 1.3 Переписать database/db.py на async engine
├── 1.4 Добавить DATABASE_URL в .env
├── 1.5 Инициализировать Alembic
├── 1.6 Настроить alembic/env.py
└── 1.7 Настроить Dockerfile

Фаза 2: ORM-модели (2-3 дня)
├── 2.1 Создать все новые модели в database/models.py
├── 2.2 Создать первую миграцию: alembic revision --autogenerate
├── 2.3 Применить: alembic upgrade head
├── 2.4 Проверить что все таблицы созданы
└── 2.5 При необходимости — редактировать миграцию вручную

Фаза 3: Миграция данных (1-2 дня)
├── 3.1 Создать database/migrate_to_pg.py
├── 3.2 Запустить миграцию на тестовых данных
├── 3.3 Проверить целостность данных
├── 3.4 Валидировать количество записей в каждой таблице
└── 3.5 Запустить на реальных данных

Фаза 4: Бизнес-логика (3-5 дней)
├── 4.1 Load layer (7 файлов)
│   ├── load_games.py
│   ├── load_streams.py
│   ├── load_game_stats.py
│   ├── load_game_meta.py
│   ├── load_participants.py
│   ├── load_stream_games.py
│   └── load_recommendations.py
├── 4.2 Service layer (3 файла)
│   ├── games_service.py
│   ├── streams_service.py
│   └── recommendations_service.py
├── 4.3 Delivery layer (4 файла)
│   ├── sheets_games.py
│   ├── sheets_streams.py
│   ├── sheets_releases.py
│   └── sheets_recommendations.py
├── 4.4 Orchestrator layer (6 файлов)
│   ├── import_json_to_db.py
│   ├── import_igdb_releases.py
│   ├── enrich_descriptions_with_gpt.py
│   ├── sync_wishlist.py
│   ├── update_upcoming_release_dates.py
│   └── sync_sheets.py
└── 4.5 Utils (1 файл)
    └── db_manage.py

Фаза 5: Очистка (0.5 дня)
├── 5.1 Удалить старые миграции (migrate_*.py)
├── 5.2 Удалить/заархивировать streams.db
├── 5.3 Обновить .gitignore
└── 5.4 Обновить README

Фаза 6: Тестирование (1-2 дня)
├── 6.1 ETL pipeline тест (import_json_to_db)
├── 6.2 Chat-команды тест (!игры, !стримы, !рек)
├── 6.3 Google Sheets sync тест
├── 6.4 Docker build & run тест
└── 6.5 Деплой на продакшн
```

**Итого: ~8-14 рабочих дней**

---

## 9. Решённые вопросы

| Вопрос | Решение |
|--------|---------|
| Перенос данных | Да, данные переносятся из SQLite в PostgreSQL через миграционный скрипт |
| Миграционная система | Alembic с autogenerate |
| Async vs sync | Async (`asyncpg` + `AsyncSession`) |
| Голоса → recommendations | Конвертация: одна запись `(user_id, game_id)` на уникальную пару |
| Participants → Users | `login` = `name`, `is_streamer = true`, `twitch_user_id = NULL` |
| recommendation_note | Tabula → "В списке желаемого Steam", IGDB → "Игра популярна на сервисе IGDB", остальные → NULL |
| Формат анализа | Markdown (`docs/MIGRATION_ANALYSIS.md`) |
