import argparse
import asyncio
import os
import sys
from pathlib import Path

import psycopg
from telegram.error import InvalidToken, TelegramError

from src.database import DatabaseConfigError
from src.telegram_bot import (
    BotConfigError,
    TrackerServices,
    build_application,
    configure_logging,
    load_bot_config
)


PROJECT_ROOT = Path(__file__).resolve().parent


def check(config, services):
    """Check configuration, the database, and the bot token without polling."""
    print("Telegram settings: OK")

    try:
        count = len(services.list_applications())
        print(f"Database: OK ({count} tracked applications)")
    except (DatabaseConfigError, psycopg.Error) as error:
        print(f"Database: FAILED ({type(error).__name__})")
        return 1

    application = build_application(config, services)
    print(f"Bot handlers: OK ({len(application.handlers[0])} registered)")

    async def get_me():
        async with application.bot:
            return await application.bot.get_me()

    try:
        me = asyncio.run(get_me())
    except InvalidToken:
        print("Telegram token: FAILED (Telegram rejected the token)")
        return 1
    except TelegramError as error:
        print(f"Telegram API: FAILED ({type(error).__name__})")
        return 1

    print(f"Telegram token: OK (bot @{me.username})")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Run the Resume Validator Telegram bot.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check settings, database, and token, then exit"
    )
    arguments = parser.parse_args()

    # Paths stored in the tracker (job postings, resume versions) are
    # relative to the project folder.
    os.chdir(PROJECT_ROOT)

    try:
        config = load_bot_config()
    except BotConfigError as error:
        print(f"Error: {error}")
        return 1

    configure_logging([config["token"]])
    services = TrackerServices(default_resume_path=config["default_resume_path"])

    if arguments.check:
        return check(config, services)

    print("Bot is running. Press Ctrl+C to stop.")
    build_application(config, services).run_polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
