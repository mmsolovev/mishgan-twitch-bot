from database.db import SessionLocal
from database.models import Stream
from database.models import Game, GameStats
from services.google_sheets_service import get_client, format_dt


def sync_streams():
    client = get_client()
    sheet = client.open("Tabula Streams").worksheet("Стримы")

    session = SessionLocal()

    streams = (
        session.query(Stream)
        .order_by(Stream.date.desc())
        .all()
    )

    rows = []

    for s in streams:

        games = " → ".join(sg.game.name for sg in s.stream_games)

        participants = " ".join(p.display_name for p in s.participants)

        # запись (пока только twitch)
        vod = f'=HYPERLINK("{s.vod_url}"; "Twitch")' if s.vod_url else ""

        # клипы (заглушка)
        clips = f'=HYPERLINK("{s.clips_url}"; "Клипы")' if s.clips_url else ""

        rows.append([
            format_dt(s.date, True),  # A ДАТА
            s.duration,               # B ЧАСОВ
            s.title,                  # C НАЗВАНИЕ
            games,                    # D ИГРЫ
            vod,                      # E ЗАПИСЬ
            clips,                    # F КЛИПЫ
            "", "", "", "",           # G-J пусто
            "",                       # K ТЕГИ
            participants              # L УЧАСТНИКИ
        ])

    session.close()

    # очищаем только данные (не шапку!)
    sheet.batch_clear(["A9:L1000"])

    # записываем
    sheet.update("A9", rows)

    print(f"Streams synced: {len(rows)}")


def sync_games():
    client = get_client()
    sheet = client.open("Tabula Streams").worksheet("Игры")

    session = SessionLocal()

    games = session.query(Game).all()

    rows = []

    games_data = []

    for game in games:

        stats = (
            session.query(GameStats)
            .filter_by(game_id=game.id)
            .first()
        )

        if not stats:
            continue

        games_data.append({
            "game": game,
            "stats": stats
        })

        games_data.sort(
            key=lambda x: x["stats"].hours_streamed or 0,
            reverse=True
        )

        steam = f'=HYPERLINK("{game.meta.steam_url}"; "Steam")' if game.meta and game.meta.steam_url else ""

        rows.append([
            format_dt(stats.last_stream),  # A ЛАСТ СТРИМ
            stats.streams_count,           # B РАЗ
            game.name,                     # C ИГРА
            "",                            # D РАНГ
            stats.hours_streamed,          # E ЧАСОВ
            game.meta.hltb_hours if game.meta else "",  # F HLTB
            steam,                         # G Steam
            "",                            # H ЗАШЛО (ручное)
            "",                            # I ПРОЙДЕНО (ручное)
            "",                            # J пусто
            "",                            # K ТЕГИ
            game.meta.review_url if game.meta else ""  # L ОТЗЫВ
        ])

    session.close()

    # сортировка по последнему стриму
    rows.sort(key=lambda x: x[0], reverse=True)

    # очищаем только данные
    sheet.batch_clear(["A9:L1000"])

    sheet.update("A9", rows)

    print(f"Games synced: {len(rows)}")


def sync_streams_safe():
    client = get_client()
    sheet = client.open("Twitch Stats").worksheet("Стримы")

    session = SessionLocal()

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing_keys = {row[0] for row in data_rows if row}

    streams = session.query(Stream).order_by(Stream.date.desc()).all()

    new_rows = []

    for s in streams:
        key = format_dt(s.date, True)

        if key in existing_keys:
            continue

        games = " → ".join(
            sg.game.name for sg in s.stream_games
        )

        participants = " ".join(p.display_name for p in s.participants)

        vod = f'=HYPERLINK("{s.vod_url}"; "🟣 Twitch")' if s.vod_url else ""
        clips = f'=HYPERLINK("{s.clips_url}"; "Клипы")' if s.clips_url else ""

        new_rows.append([
            key,
            s.duration,
            s.title,
            games,
            vod,
            clips,
            "", "", "", "",
            "",
            participants
        ])

    if new_rows:
        sheet.insert_rows(new_rows, row=9)
        print(f"Inserted {len(new_rows)} new streams")

    session.close()


def sync_games_safe():
    client = get_client()
    sheet = client.open("Twitch Stats").worksheet("Игры")

    session = SessionLocal()

    # --- 1. читаем таблицу ---
    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    # game_name -> (row_index, row_data)
    existing = {}

    for i, row in enumerate(data_rows, start=9):
        if len(row) < 3:
            continue
        game_name = row[2]
        if game_name:
            existing[game_name] = (i, row)

    # --- 2. данные из БД ---
    games = session.query(Game).all()

    games_data = []

    for game in games:
        stats = session.query(GameStats).filter_by(
            game_id=game.id,
            period="all"
        ).first()

        if not stats:
            continue

        games_data.append((game, stats))

    # --- 3. сортировка ---
    games_data.sort(
        key=lambda x: x[1].hours_streamed or 0,
        reverse=True
    )

    updates = []
    appends = []

    # --- 4. формирование строк ---
    for game, stats in games_data:

        meta = game.meta

        steam = (
            f'=HYPERLINK("{meta.steam_url}"; "Steam")'
            if meta and meta.steam_url else ""
        )

        new_row = [
            format_dt(stats.last_stream) if stats.last_stream else "",
            stats.streams_count or 0,
            game.name,
            "",  # rank не храним
            stats.hours_streamed or 0,
            meta.hltb_hours if meta and meta.hltb_hours else "",
            steam,
        ]

        # --- если уже есть ---
        if game.name in existing:
            row_index, old_row = existing[game.name]

            # берём только A-G из старой строки
            old_slice = old_row[:7] if len(old_row) >= 7 else []

            # если реально изменилось — обновляем
            if old_slice != [str(x) for x in new_row]:
                updates.append({
                    "range": f"A{row_index}:G{row_index}",
                    "values": [new_row]
                })

        else:
            # новая строка (с сохранением структуры)
            appends.append(new_row + ["", "", "", "", ""])

    # --- 5. применяем ---
    if updates:
        sheet.batch_update(updates)
        print(f"Updated {len(updates)} games")

    if appends:
        sheet.append_rows(appends, value_input_option="USER_ENTERED")
        print(f"Added {len(appends)} new games")

    session.close()
