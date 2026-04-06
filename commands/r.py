from twitchio.ext import commands

from services.command_registry import register_command
from services.reply_layout_service import (
    build_reply_translation_response,
    claim_reply_translation,
    extract_reply_message_data,
)
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):
    register_command(
        "r",
        "Команда: !r — написать ответом на сообщение с неправильной раскладкой, бот переведет его в чат",
        "all",
    )

    @commands.command(name="r")
    async def reply_layout_command(ctx):
        if not check_cooldown(ctx, "r", 3):
            return

        reply_id, reply_author, reply_body = extract_reply_message_data(ctx.message.tags)
        if not reply_id or not reply_author or reply_body is None:
            await ctx.send("MrDestructoid Используй !r ответом на сообщение")
            return

        claimed = await claim_reply_translation(reply_id)
        if not claimed:
            return

        await human_delay()
        await ctx.send(build_reply_translation_response(reply_author, reply_body))

    bot.add_command(reply_layout_command)
