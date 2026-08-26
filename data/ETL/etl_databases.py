"""Which database an ETL script is about to touch, asked rather than assumed.

Shared by the scripts that read or write one, so the names live in one place and
a typo cannot invent a third environment. The same two names the migrations
guard on.

Test is offered first and is what plain Enter picks, because going the wrong way
round is the mistake worth making hard.
"""

from __future__ import annotations

KNOWN_DATABASES = ("dataloggingTest", "dataloggingDev")


def ask_database(suggested: str = "", action: str = "done") -> str:
    """Which database to work against.

    Args:
        suggested: What to offer as the default, normally whatever the
            connection file already names. Ignored when it is not one of ours.
        action: What will not have happened if the answer is rejected, so the
            message reads as the caller's own.
    """
    default = suggested if suggested in KNOWN_DATABASES else KNOWN_DATABASES[0]

    print("\nWhich database?")
    for number, name in enumerate(KNOWN_DATABASES, start=1):
        marker = "  (default)" if name == default else ""
        print(f"   {number}) {name}{marker}")

    answer = input(f"Database [{default}]: ").strip()
    if not answer:
        return default
    if answer.isdigit() and 1 <= int(answer) <= len(KNOWN_DATABASES):
        return KNOWN_DATABASES[int(answer) - 1]

    for name in KNOWN_DATABASES:
        if answer.lower() == name.lower():
            return name
    raise SystemExit(f"{answer!r} is not one of {', '.join(KNOWN_DATABASES)}. "
                     f"Nothing was {action}.")


def confirm_database(database: str, action: str = "done") -> None:
    """Ask again before touching anything but the test database.

    Typing the name out is deliberate: a yes or no is easy to answer without
    reading, and this is the last thing standing between a tired operator and
    production.
    """
    if database == KNOWN_DATABASES[0]:
        return
    typed = input(f"\n{database} is not the test database. Type its name to "
                  f"continue: ").strip()
    if typed != database:
        raise SystemExit(f"That did not match. Nothing was {action}.")
