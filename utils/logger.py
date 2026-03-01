from datetime import datetime


def log_command(user: str, command: str, text: str = ""):
    time = datetime.now().strftime("%H:%M:%S")
    print(f"[{time}] {user}: !{command} {text}")
