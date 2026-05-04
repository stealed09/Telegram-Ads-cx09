"""
╔══════════════════════════════════════════════════════════════════╗
║         TeleAutoBot — Configuration Module                       ║
║         Telegram Automation SaaS Bot                             ║
╚══════════════════════════════════════════════════════════════════╝

SETUP INSTRUCTIONS:
  1. Create a bot via @BotFather → get BOT_TOKEN
  2. Get your Telegram user ID via @userinfobot → set ADMIN_ID
  3. Get API credentials from https://my.telegram.org → API_ID, API_HASH
  4. Fill in the values below and run: python main.py
"""

import os

# ─────────────────────────────────────────────
#  BOT CONFIGURATION  (edit these values)
# ─────────────────────────────────────────────

# Your Telegram Bot Token (from @BotFather)
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Your Telegram User ID (admin only — from @userinfobot)
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "123456789"))

# Default Telegram API credentials for bot's own Telethon client
# Users will supply their own API_ID/API_HASH when adding accounts
DEFAULT_API_ID: int = int(os.getenv("DEFAULT_API_ID", "0"))
DEFAULT_API_HASH: str = os.getenv("DEFAULT_API_HASH", "")

# ─────────────────────────────────────────────
#  ANTI-BAN / SAFETY SETTINGS
# ─────────────────────────────────────────────

# Delay range (seconds) between each broadcast message
BROADCAST_DELAY_MIN: int = 5
BROADCAST_DELAY_MAX: int = 30

# Maximum messages sent per hour per account
MAX_MESSAGES_PER_HOUR: int = 20

# Retry count for failed sends
MAX_RETRIES: int = 3

# Delay between retries (seconds)
RETRY_DELAY: int = 10

# Scraper: delay between member fetch requests (seconds)
SCRAPER_DELAY: float = 1.5

# Scraper: max members extracted per session
SCRAPER_MAX_MEMBERS: int = 500

# ─────────────────────────────────────────────
#  STORAGE
# ─────────────────────────────────────────────

DATA_FILE: str = "data.json"
LOG_FILE: str  = "teleautobot.log"

# ─────────────────────────────────────────────
#  BOT MESSAGES  (customise text freely)
# ─────────────────────────────────────────────

MSG_PENDING = (
    "⏳ <b>Access Request Sent!</b>\n\n"
    "Your request has been forwarded to the admin.\n"
    "Please wait for approval before using any features.\n\n"
    "<i>You'll receive a notification once approved.</i>"
)

MSG_APPROVED = (
    "✅ <b>Access Approved!</b>\n\n"
    "Welcome to <b>TeleAutoBot</b> 🤖\n"
    "You now have full access to all features.\n\n"
    "Use /menu to get started!"
)

MSG_REJECTED = (
    "❌ <b>Access Denied</b>\n\n"
    "Your access request was rejected by the admin.\n"
    "Contact support if you believe this is a mistake."
)

MSG_BANNED = (
    "🚫 <b>Access Blocked</b>\n\n"
    "Your account has been banned.\n"
    "Contact the admin for assistance."
)

MSG_NOT_APPROVED = (
    "⛔ <b>Not Authorised</b>\n\n"
    "You must be approved before using this bot.\n"
    "Send /start to request access."
)
