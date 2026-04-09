from twitchio.ext import commands

from config.settings import RECOMMENDATIONS_STREAMER_DISPLAY_NAME, RECOMMENDATIONS_STREAMER_LOGIN
from services.command_registry import register_command
from services.recommendation_chat_service import ensure_recommendation_batch, queue_recommendation_chat_message
from services.recommendations_service import (
    admin_delete_recommendations,
    build_recommendations_help_message,
    can_recommend_as_streamer,
    delete_own_last_recommendation,
    delete_own_recommendation_by_title,
    recommend_game,
)
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


async def _schedule_recommendation_sheets_sync(ctx, reason: str):
    scheduler = getattr(ctx.bot, "recommendation_sheets_sync_scheduler", None)
    if scheduler is not None:
        await scheduler.schedule_sync(reason=reason)


def setup(bot):
    register_command(
        "рек",
        "Команда: !рек [название игры] — предложить игру для стрима или проголосовать за уже предложенную",
        "all",
    )

    @commands.command(name="рек")
    async def recommendations_command(ctx, *, game_query: str = None):
        if not check_cooldown(ctx.author.name, "рек", 8):
            return

        game_query = (game_query or "").strip()
        if not game_query:
            await ctx.send(build_recommendations_help_message())
            return

        await human_delay()
        user_login = ctx.author.name
        user_display_name = getattr(ctx.author, "display_name", None) or ctx.author.name

        if game_query == "-":
            result = await delete_own_last_recommendation(user_login=user_login)
            if result.outcome == "deleted":
                await _schedule_recommendation_sheets_sync(ctx, "recommendation_deleted_last")
            await ctx.send(result.message)
            return

        if game_query.startswith("--"):
            parts = game_query.split(maxsplit=2)
            target_user = parts[1] if len(parts) > 1 else ""
            target_query = parts[2] if len(parts) > 2 else None
            result = await admin_delete_recommendations(
                target_user=target_user,
                query=target_query,
                actor_login=user_login,
            )
            if result.outcome == "deleted":
                await _schedule_recommendation_sheets_sync(ctx, "recommendation_deleted_admin")
            await ctx.send(result.message)
            return

        if game_query.startswith("+ "):
            if not can_recommend_as_streamer(user_login):
                await ctx.send("Команда доступна только ADMINS.")
                return

            result = await recommend_game(
                query=game_query[2:].strip(),
                user_login=RECOMMENDATIONS_STREAMER_LOGIN,
                user_display_name=RECOMMENDATIONS_STREAMER_DISPLAY_NAME,
            )

            if result.accepted:
                await _schedule_recommendation_sheets_sync(ctx, "recommendation_added_streamer")
                channel_key = getattr(ctx.channel, "name", "") or "global"
                ensure_recommendation_batch(channel_key, ctx.send)
                await queue_recommendation_chat_message(
                    channel_key=channel_key,
                    user_display_name=RECOMMENDATIONS_STREAMER_DISPLAY_NAME,
                    message=result.message,
                    accepted=True,
                )
                return

            await ctx.send(result.message)
            return

        if game_query.startswith("- "):
            result = await delete_own_recommendation_by_title(
                query=game_query[2:].strip(),
                user_login=user_login,
            )
            if result.outcome == "deleted":
                await _schedule_recommendation_sheets_sync(ctx, "recommendation_deleted_by_title")
            await ctx.send(result.message)
            return

        result = await recommend_game(
            query=game_query,
            user_login=user_login,
            user_display_name=user_display_name,
        )

        if result.accepted:
            await _schedule_recommendation_sheets_sync(ctx, "recommendation_added")
            channel_key = getattr(ctx.channel, "name", "") or "global"
            ensure_recommendation_batch(channel_key, ctx.send)
            await queue_recommendation_chat_message(
                channel_key=channel_key,
                user_display_name=user_display_name,
                message=result.message,
                accepted=True,
            )
            return

        await ctx.send(result.message)

    bot.add_command(recommendations_command)
