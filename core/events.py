class BotEvents:
    """
    Миксин с Twitch-событиями.
    Подмешивается в основной класс бота.
    """

    async def event_message(self, message):
        if not message.author:
            return

        # логируем только команды
        if message.content.startswith(self.prefix):
            print(f"[CMD] {message.author.name}: {message.content}")

        # передаём сообщение дальше,
        await self.handle_commands(message)
