from sqlalchemy import select
from twitchio.ext import commands

from config.settings import GAMES_SHEET_URL
from utils.cooldowns import check_cooldown
from utils.delays import human_delay

from database.db import AsyncSessionLocal
from database.models import BotCommand, BotCommandAlias
from services.command_registry import register_command


def _sort_key(row):
    return row.bot_name != "self", row.bot_name.casefold(), row.name.casefold()


async def _load_active_commands():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(BotCommand).where(BotCommand.is_active.is_(True))
        )
        rows = list(result.scalars().all())

        alias_result = await session.execute(
            select(BotCommandAlias.alias, BotCommandAlias.command_id)
        )
        aliases: dict[int, list[str]] = {}
        for alias, command_id in alias_result.all():
            aliases.setdefault(command_id, []).append(alias)

    rows.sort(key=_sort_key)
    return rows, aliases


def _find_command(rows: list[BotCommand], aliases: dict[int, list[str]], cmd_name: str):
    for row in rows:
        if row.name.casefold() == cmd_name:
            return row
        for alias in aliases.get(row.id, []):
            if alias.casefold() == cmd_name:
                return row
    return None


def setup(bot):

    register_command(
        "команды",
        "Команда: !команды [название команды] — описание команды, либо список доступных команд",
        "all",
        aliases = ["команда", "помощь", "help"],
    )

    @commands.command(name="команды", aliases=("команда", "помощь", "help"))
    async def commands_command(ctx):
        if not check_cooldown(ctx, "команды", 5):
            return

        args = ctx.message.content.split()[1:]

        await human_delay()

        rows, aliases = await _load_active_commands()

        # 🔹 если указан аргумент → пытаемся найти команду
        if args:
            cmd_name = args[0].lower().lstrip("!")
            if cmd_name:
                cmd = _find_command(rows, aliases, cmd_name)
                if cmd is not None:
                    if cmd.description.strip():
                        await ctx.send(cmd.description)
                    else:
                        owner = f" (команда {cmd.bot_name})" if cmd.bot_name != "self" else ""
                        await ctx.send(f"Команда !{cmd.name}{owner} — описание не задано")
                    return

        # 🔹 иначе (или не найдено) → список по активным и видимым командам
        visible = [row for row in rows if row.is_visible]

        groups: dict[str, list[str]] = {}
        for row in visible:
            groups.setdefault(row.bot_name, []).append(f"!{row.name}")

        parts = []
        if "self" in groups:
            parts.append(f"Команды чата: {' '.join(groups.pop('self'))}")

        for bot_name in sorted(groups, key=str.casefold):
            parts.append(f"{bot_name}: {' '.join(groups[bot_name])}")

        if not parts:
            await ctx.send("Команд не найдено")
            return

        message = " | ".join(parts)
        message += f" | Подробная информация о командах на листе БОТ: {GAMES_SHEET_URL}"

        await ctx.send(message)

    bot.add_command(commands_command)