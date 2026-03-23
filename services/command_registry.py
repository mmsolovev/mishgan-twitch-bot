COMMANDS_INFO = {}


def register_command(name: str, description: str, access: str = "all"):
    """
    access:
    - all
    - mod
    """

    COMMANDS_INFO[name] = {
        "description": description,
        "access": access
    }


def get_commands():
    return COMMANDS_INFO


def get_command(name: str):
    return COMMANDS_INFO.get(name)