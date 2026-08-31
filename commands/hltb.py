from twitchio.ext import commands

from services.command_registry import register_command
from services.hltb_service import HLTB_COMMAND_DESCRIPTION, clean_user_query, get_hltb_info
from utils.delays import human_delay
from utils.inflight import run_once
from utils.logger import get_logger

logger = get_logger("commands.hltb")

HLTB_DEDUP_COOLDOWN_SECONDS = 30


def _dedupe_key(game: str | None) -> str:
    if game is None:
        return "<current-game>"
    return " ".join(clean_user_query(game).casefold().split())


def setup(bot):

    register_command(
        "hltb",
        HLTB_COMMAND_DESCRIPTION,
        "all"
    )

    @commands.command(name="hltb")
    async def hltb_command(ctx, *, game: str | None = None):
        await human_delay()

        async def _answer() -> bool:
            try:
                result = await get_hltb_info(game)
                await ctx.send(result)
                return True
            except Exception:
                logger.exception("!hltb: unexpected error")
                return False

        await run_once(_dedupe_key(game), _answer, cooldown=HLTB_DEDUP_COOLDOWN_SECONDS)

    bot.add_command(hltb_command)