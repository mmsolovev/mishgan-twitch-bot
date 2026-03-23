import asyncio
import re
import time

MAX_SECONDS = 8 * 60 * 60
MAX_TIMERS = 5

active_timers = []


def parse_time(time_str: str) -> int | None:
    match = re.match(r"(\d+)([а-яa-z]*)", time_str.lower())
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit in ("", "м", "мин", "m", "min"):
        return value * 60
    elif unit in ("ч", "h"):
        return value * 3600

    return None


async def start_timer(ctx, seconds: int, text: str):
    user = ctx.author.name

    # ❗ лимит таймеров
    if len(active_timers) >= MAX_TIMERS:
        await ctx.send("MrDestructoid Слишком много активных таймеров pepeW")
        return

    # ❗ уникальность
    if any(t["user"] == user for t in active_timers):
        await ctx.send("MrDestructoid У тебя уже есть активный таймер damn")
        return

    end_time = time.time() + seconds

    task = asyncio.create_task(_timer_task(ctx, seconds, text, user))

    active_timers.append({
        "user": user,
        "end": end_time,
        "text": text,
        "task": task,
        "type": "normal"
    })

    print(f"[TIMER] {user} -> {seconds}s | {text}")


async def _timer_task(ctx, seconds: int, text: str, user: str):
    await asyncio.sleep(seconds)

    await ctx.send(text)
    await asyncio.sleep(1)
    await ctx.send(text)

    # удаление
    active_timers[:] = [
        t for t in active_timers if t["user"] != user
    ]

    print(f"[TIMER END] {user}")


# ❌ СТОП ТАЙМЕРА
async def stop_timer(ctx):
    user = ctx.author.name
    is_mod = ctx.author.is_mod

    # если указан ник
    args = ctx.message.content.split()
    target_user = None

    if len(args) > 2:
        target_user = args[2].lower()

    # 🔹 если мод и указан пользователь → стопаем чужой
    if is_mod and target_user:
        for t in active_timers:
            if t["user"].lower() == target_user:
                t["task"].cancel()
                active_timers.remove(t)

                print(f"[TIMER STOP] {user} stopped {target_user}")
                await ctx.send(f"MrDestructoid Таймер {target_user} остановлен 1984")
                return

        await ctx.send("MrDestructoid Таймер пользователя не найден HUH")
        return

    # 🔹 обычный стоп (свой)
    for t in active_timers:
        if t["user"] == user:
            t["task"].cancel()
            active_timers.remove(t)

            print(f"[TIMER STOP] {user}")
            await ctx.send("MrDestructoid Таймер остановлен 1984")
            return

    await ctx.send("MrDestructoid У тебя нет активного таймера damn")


# 📋 СПИСОК ТАЙМЕРОВ
async def list_timers(ctx):
    if not active_timers:
        await ctx.send("MrDestructoid Активных таймеров нет")
        return

    now = time.time()

    # ✅ сортировка по времени окончания
    timers_sorted = sorted(active_timers, key=lambda t: t["end"])

    messages = []

    for t in timers_sorted:
        remaining = int(t["end"] - now)

        if remaining < 0:
            continue

        # ✅ красивый формат времени
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        seconds = remaining % 60

        if hours > 0:
            time_str = f"{hours}ч {minutes}м"
        elif minutes > 0:
            time_str = f"{minutes}м {seconds}с"
        else:
            time_str = f"{seconds}с"

        # тип таймера
        label = "возврат" if t["type"] == "return" else "таймер"

        messages.append(
            f"[{label}] {t['user']}: {time_str}"
        )

    await ctx.send("⏳ " + " | ".join(messages))


# 🔁 ВОЗВРАТ
async def start_return_timer(ctx):
    user = ctx.author.name

    # ❗ уникальность
    if any(t["user"] == user for t in active_timers):
        await ctx.send("MrDestructoid У тебя уже есть активный таймер damn")
        return

    # ❗ лимит
    if len(active_timers) >= MAX_TIMERS:
        await ctx.send("MrDestructoid Слишком много активных таймеров pepeW")
        return

    total = 115 * 60
    end_time = time.time() + total

    async def return_task():
        try:
            await asyncio.sleep(55 * 60)
            await ctx.send("ALERT ЧАС ДО ВОЗВРАТА ALERT")

            await asyncio.sleep(30 * 60)
            await ctx.send("ALERT ПОЛЧАСА ДО ВОЗВРАТА ALERT")

            await asyncio.sleep(30 * 60)
            await ctx.send("ALERT ВРЕМЯ ВОЗВРАТА ALERT")

        except asyncio.CancelledError:
            print(f"[TIMER RETURN STOP] {user}")
            return

        finally:
            # удаление из списка
            active_timers[:] = [
                t for t in active_timers if t["user"] != user
            ]

            print(f"[TIMER RETURN END] {user}")

    task = asyncio.create_task(return_task())

    active_timers.append({
        "user": user,
        "end": end_time,
        "text": "STEAM RETURN",
        "task": task,
        "type": "return"
    })

    print(f"[TIMER RETURN START] {user}")
