def load_commands(bot):
    from commands.hltb import setup as hltb_setup
    from commands.holiday import setup as holiday_setup
    from commands.gpt import setup as gpt_setup

    hltb_setup(bot)
    holiday_setup(bot)
    gpt_setup(bot)
