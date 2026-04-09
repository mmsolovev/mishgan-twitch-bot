COMMANDS_INFO = {}
COMMAND_ALIASES = {}


def register_command(name: str, description: str, access: str = "all", aliases: list[str] | None = None):
    """
    access:
    - all
    - mod
    """

    COMMANDS_INFO[name] = {
        "description": description,
        "access": access,
        "aliases": list(aliases or []),
    }

    for alias in aliases or []:
        COMMAND_ALIASES[alias] = name


def get_commands():
    return COMMANDS_INFO


def get_command(name: str):
    command_name = COMMAND_ALIASES.get(name, name)
    return COMMANDS_INFO.get(command_name)
