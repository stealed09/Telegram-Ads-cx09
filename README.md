# ⚡ TeleAutoBot — Telegram Automation SaaS

> A complete, production-ready Telegram Automation SaaS Bot built with **Telethon** (user accounts) and **Aiogram 3** (bot interface).

---

## 🗂 File Structure

```
bot/
├── config.py          ⚙️  All configuration & constants
├── core.py            🧠  Telethon engine (sessions, broadcast, scraper…)
├── main.py            🤖  Aiogram 3 bot handlers & FSM flows
├── data.json          💾  Auto-generated JSON storage (no database)
├── requirements.txt   📦  Python dependencies
└── .env.example       🔐  Environment variable template
```

---

## ✅ Features

| Feature | Description |
|---|---|
| 🛡 Admin Approval | Every user must be approved before accessing any feature |
| 🔐 Multi-Account | Link multiple Telegram accounts via OTP + 2FA, stored as StringSessions |
| 📥 Group Import | Fetch all groups/channels via `get_dialogs()` with paginated selection |
| 📂 Folder Manager | Create, view, and delete named group folders |
| 📤 Broadcast | Send to all groups in a folder with anti-ban random delays |
| 🤖 Auto-Reply | Keyword-based or reply-all mode with ghost-mode delays |
| ⏰ Scheduler | One-time or repeating messages via asyncio tasks |
| 🔍 Scraper | Extract members to TXT or JSON with rate limiting |
| 👻 Ghost Mode | No typing indicators, random reply delays, human-like behaviour |
| 👑 Admin Panel | Broadcast all users, ban/unban, view sessions, manage approvals |

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- A Telegram account
- Bot token from [@BotFather](https://t.me/BotFather)
- API credentials from [my.telegram.org](https://my.telegram.org)

### 2. Setup

```bash
# Enter the bot directory
cd bot

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env    # Fill in BOT_TOKEN, ADMIN_ID, API_ID, API_HASH
```

### 3. Configure `.env`

```env
BOT_TOKEN=1234567890:ABCdef...
ADMIN_ID=123456789
DEFAULT_API_ID=12345678
DEFAULT_API_HASH=abcdef1234567890abcdef1234567890
```

### 4. Run

```bash
python main.py
```

---

## 📱 Bot Commands

| Command | Description |
|---|---|
| `/start` | Register or request access |
| `/menu` | Open the main menu |
| `/addaccount` | Add a Telegram user account |
| `/accounts` | Manage linked accounts |
| `/admin` | Admin panel (admin only) |
| `/help` | View help |

---

## 🔄 User Flow

```
/start → Pending Queue → Admin Approval → Full Access
       → Add Accounts (OTP + 2FA)
       → Import Groups (get_dialogs)
       → Create Folders
       → Broadcast / Schedule / Auto-Reply / Scrape
```

---

## 🛡 Admin Flow

```
New user /start → Admin gets [✅ Approve] [❌ Reject] buttons
Admin approves  → User gets full access
Admin rejects   → User is banned
/admin panel    → Broadcast all, ban/unban, view stats, manage sessions
```

---

## ⚙️ Configuration (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `BROADCAST_DELAY_MIN` | 5s | Min delay between messages |
| `BROADCAST_DELAY_MAX` | 30s | Max delay between messages |
| `MAX_MESSAGES_PER_HOUR` | 20 | Rate limit per account |
| `MAX_RETRIES` | 3 | Retry failed sends |
| `SCRAPER_DELAY` | 1.5s | Delay between scraper requests |
| `SCRAPER_MAX_MEMBERS` | 500 | Max members per scrape |

---

## 🌐 Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

railway login
railway init
# Set env vars in Railway dashboard

railway up
```

---

## ⚠️ Important Notes

- **Telegram folder links** (`t.me/+xxx`) cannot be accessed via API — only groups you're a member of work
- Keep `BROADCAST_DELAY_MIN/MAX` high enough to avoid rate limiting
- Test with a secondary account before using your main account
- The `data.json` file is created automatically on first run

---

## 📦 Dependencies

```
aiogram==3.13.1     # Bot interface (Telegram Bot API)
telethon==1.36.0    # User account automation
aiohttp==3.10.10    # Async HTTP
cryptg==0.4.0       # Telethon encryption
python-dotenv==1.0.1 # Env vars
```

---

*TeleAutoBot — Production-ready, lightweight, no database required.*
