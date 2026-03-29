def set_row_heights(sheet, start, end, height):
    requests = [{
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet.id,
                "dimension": "ROWS",
                "startIndex": start - 1,
                "endIndex": end
            },
            "properties": {
                "pixelSize": height
            },
            "fields": "pixelSize"
        }
    }]

    sheet.spreadsheet.batch_update({"requests": requests})


def set_column_widths(sheet, widths):
    """
    widths: список [(start_col, end_col, width_px)]
    """
    requests = []

    for start, end, width in widths:
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": start - 1,
                    "endIndex": end
                },
                "properties": {
                    "pixelSize": width
                },
                "fields": "pixelSize"
            }
        })

    sheet.spreadsheet.batch_update({"requests": requests})


def build_header(sheet):
    # ---------- 1. Очистка ----------
    sheet.batch_clear(["A1:N8"])

    # ---------- 2. Merge layout (переработанный) ----------
    sheet.merge_cells("A1:C7")   # фото (чуть выше)
    sheet.merge_cells("D1:J4")   # ник (больше воздуха)
    sheet.merge_cells("D5:J6")   # описание
    sheet.merge_cells("K1:N7")   # соцсети (шире и выше)

    # ---------- 3. Контент ----------
    sheet.update("D1", [["Tabula"]])
    sheet.update("D5", [["Архив стримов и игр 💜"]])

    socials = [
        "🟣 Twitch",
        "📢 Telegram",
        "▶️ YouTube",
        "💰 Boosty",
        "💬 Discord"
    ]

    for i, text in enumerate(socials, start=1):
        sheet.update(f"K{i}", [[text]])

    # ---------- 4. Стили ----------

    # фон (цельный)
    sheet.format("A1:N7", {
        "backgroundColor": {"red": 0.16, "green": 0.16, "blue": 0.16}
    })

    # ник (центр баннера)
    sheet.format("D1:J4", {
        "textFormat": {
            "bold": True,
            "fontSize": 32,
            "foregroundColor": {"red": 0.6, "green": 0.3, "blue": 1.0}
        },
        "horizontalAlignment": "LEFT",
        "verticalAlignment": "BOTTOM"
    })

    # описание
    sheet.format("D5:J6", {
        "textFormat": {
            "fontSize": 12,
            "foregroundColor": {"red": 0.8, "green": 0.8, "blue": 0.8}
        },
        "horizontalAlignment": "LEFT",
        "verticalAlignment": "TOP"
    })

    # соцсети (как список)
    sheet.format("K1:N7", {
        "textFormat": {
            "fontSize": 12,
            "foregroundColor": {"red": 1, "green": 1, "blue": 1}
        },
        "horizontalAlignment": "LEFT",
        "verticalAlignment": "MIDDLE"
    })

    # ---------- 5. УБИРАЕМ внутренние границы ----------
    sheet.format("A1:N7", {
        "borders": {
            "top": {"style": "NONE"},
            "bottom": {"style": "NONE"},
            "left": {"style": "NONE"},
            "right": {"style": "NONE"}
        }
    })

    # ---------- 6. Рамка ТОЛЬКО СНАРУЖИ ----------
    sheet.format("A1:N7", {
        "borders": {
            "top": {
                "style": "SOLID",
                "width": 3,
                "color": {"red": 0.6, "green": 0.3, "blue": 1.0}
            },
            "bottom": {
                "style": "SOLID",
                "width": 3,
                "color": {"red": 0.6, "green": 0.3, "blue": 1.0}
            },
            "left": {
                "style": "SOLID",
                "width": 3,
                "color": {"red": 0.6, "green": 0.3, "blue": 1.0}
            },
            "right": {
                "style": "SOLID",
                "width": 3,
                "color": {"red": 0.6, "green": 0.3, "blue": 1.0}
            }
        }
    })

    # ---------- 7. Размеры колонок (ВАЖНО) ----------
    set_column_widths(sheet, [
        (1, 3, 140),  # фото
        (4, 10, 110),  # центр
        (11, 14, 140)  # соцсети
    ])

    # ---------- 8. Высота строк ----------
    sheet.resize(rows=1000)

    set_row_heights(sheet, 1, 7, 40)

    # ---------- 9. Freeze ----------
    sheet.freeze(rows=7)

    print("Header rebuilt (improved layout)")
