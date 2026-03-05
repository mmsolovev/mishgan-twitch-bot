import asyncio

from twitchio.ext import commands

from config.settings import ALLOWED_USERS
from services.gpt_service import ask_gpt
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    @commands.command(name="gpt")
    async def gpt_command(ctx, *, question: str = None):

        user = ctx.author.name.lower()    # проверяем, что пользователь разрешён
        if user not in ALLOWED_USERS:
            return

        if not question:    # проверяем есть ли тело вопроса
            await ctx.send("MrDestructoid Используй: !вопрос <текст>")
            return

        if len(question) > 200:    # проверяем длину вопроса
            await ctx.send("MrDestructoid Вопрос слишком длинный")
            return

        if "http://" in question or "https://" in question:    # проверяем, что нет ссылок
            await ctx.send("MrDestructoid Ссылки запрещены")
            return

        if not check_cooldown(ctx, "вопрос", 20):    # проверяем кулдаун
            return

        await human_delay()

        try:
            answer = await asyncio.wait_for(ask_gpt(question), timeout=15)

            if not answer:
                await ctx.send("MrDestructoid Я не знаю ответа")
                return

            answer = answer.strip().replace("\n", " ")
            if len(answer) > 150:
                answer = answer[:147] + "..."

            await ctx.send(f"MrDestructoid {answer}")

        except Exception as e:
            print("gpt error:", e)
            await ctx.send("MrDestructoid GPT временно недоступен")

    bot.add_command(gpt_command)
