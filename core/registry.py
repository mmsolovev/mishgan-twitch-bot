def load_commands(bot):
    from commands.games import setup as games_setup
    from commands.streams import setup as streams_setup
    from commands.hltb import setup as hltb_setup
    from commands.holiday import setup as holiday_setup
    from commands.gpt import setup as gpt_setup
    from commands.timer import setup as timer_setup
    from commands.admin import setup as admin_setup
    from commands.help import setup as help_setup
    from commands.info import setup as info_setup

    games_setup(bot)
    streams_setup(bot)
    hltb_setup(bot)
    holiday_setup(bot)
    gpt_setup(bot)
    timer_setup(bot)
    admin_setup(bot)
    help_setup(bot)
    info_setup(bot)
