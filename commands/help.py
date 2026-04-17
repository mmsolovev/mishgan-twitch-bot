from twitchio.ext import commands

from config.settings import GAMES_SHEET_URL
from utils.cooldowns import check_cooldown
from utils.delays import human_delay

from services.command_registry import get_commands, get_command, register_command


def setup(bot):

    register_command(
        "команды",
        "Команда: !команды [название команды] — описание команды, либо список доступных команд",
        "all"
    )

    @commands.command(name="команды")
    async def commands_command(ctx):
        if not check_cooldown(ctx, "команды", 5):
            return

        args = ctx.message.content.split()[1:]

        await human_delay()

        commands = get_commands()

        # 🔹 если указан аргумент → пытаемся найти команду
        if args:
            cmd_name = args[0].lower().lstrip("!")

            cmd = get_command(cmd_name)

            if cmd:
                await ctx.send(cmd["description"])
                return

        # 🔹 иначе (или не найдено) → список
        public_cmds = []
        mod_cmds = []

        for name, info in commands.items():
            if info["access"] == "all":
                public_cmds.append(f"!{name}")
            else:
                mod_cmds.append(f"!{name}")

        message = ""

        if public_cmds:
            message += "для всех: " + ", ".join(sorted(public_cmds))

        if mod_cmds:
            if message:
                message += " | "
            message += "для не только лишь всех: " + ", ".join(sorted(mod_cmds))

        message += f" | Подробная информация о командах на листе БОТ тут: {GAMES_SHEET_URL}"

        await ctx.send(message)

    bot.add_command(commands_command)
