"""
╔══════════════════════════════════════════════════════════════════╗
║         TeleAutoBot — Core Telethon Engine                       ║
║         Handles: sessions, dialogs, broadcast, scraper,          ║
║                  auto-reply, scheduler, ghost mode               ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel, Chat, User,
    InputPeerChannel, InputPeerChat,
)
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch

import config

logger = logging.getLogger("core")

# ══════════════════════════════════════════════════════════════════
#  DATA LAYER  — thin JSON store (no database needed)
# ══════════════════════════════════════════════════════════════════

_DEFAULT_DATA: Dict[str, Any] = {
    "approved_users": [],
    "pending_users":  [],
    "banned_users":   [],
    "users": {},          # keyed by str(user_id)
}

def load_data() -> Dict[str, Any]:
    """Load JSON data from disk, returning defaults on first run."""
    if not os.path.exists(config.DATA_FILE):
        save_data(_DEFAULT_DATA.copy())
        return _DEFAULT_DATA.copy()
    with open(config.DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: Dict[str, Any]) -> None:
    """Persist data to disk atomically."""
    tmp = config.DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, config.DATA_FILE)

def get_user_record(data: Dict, uid: int) -> Dict:
    """Return (or create) the per-user record."""
    key = str(uid)
    if key not in data["users"]:
        data["users"][key] = {
            "accounts": {},      # phone → {api_id, api_hash, session, me}
            "active_account": None,
            "folders": {},       # folder_name → [{id, title}]
            "auto_reply": {
                "enabled": False,
                "mode": "keyword",   # "keyword" | "all"
                "keywords": {},      # word → reply_text
            },
            "broadcast_tasks": {},   # task_id → meta
            "scheduled_tasks": {},   # task_id → meta
            "ghost_mode": False,
            "stats": {
                "messages_sent": 0,
                "messages_this_hour": 0,
                "hour_reset": time.time() + 3600,
            },
        }
    return data["users"][key]

# ══════════════════════════════════════════════════════════════════
#  USER STATUS HELPERS
# ══════════════════════════════════════════════════════════════════

def is_approved(uid: int) -> bool:
    d = load_data()
    return uid in d["approved_users"]

def is_pending(uid: int) -> bool:
    d = load_data()
    return uid in d["pending_users"]

def is_banned(uid: int) -> bool:
    d = load_data()
    return uid in d["banned_users"]

def request_access(uid: int) -> str:
    """
    Returns: 'already_approved' | 'already_pending' | 'banned' | 'pending_added'
    """
    d = load_data()
    if uid in d["approved_users"]:
        return "already_approved"
    if uid in d["banned_users"]:
        return "banned"
    if uid in d["pending_users"]:
        return "already_pending"
    d["pending_users"].append(uid)
    get_user_record(d, uid)
    save_data(d)
    return "pending_added"

def approve_user(uid: int) -> bool:
    d = load_data()
    if uid in d["banned_users"]:
        d["banned_users"].remove(uid)
    if uid in d["pending_users"]:
        d["pending_users"].remove(uid)
    if uid not in d["approved_users"]:
        d["approved_users"].append(uid)
    get_user_record(d, uid)
    save_data(d)
    return True

def reject_user(uid: int) -> bool:
    d = load_data()
    if uid in d["pending_users"]:
        d["pending_users"].remove(uid)
    if uid not in d["banned_users"]:
        d["banned_users"].append(uid)
    save_data(d)
    return True

def ban_user(uid: int) -> bool:
    d = load_data()
    for lst in ("approved_users", "pending_users"):
        if uid in d[lst]:
            d[lst].remove(uid)
    if uid not in d["banned_users"]:
        d["banned_users"].append(uid)
    save_data(d)
    return True

def unban_user(uid: int) -> bool:
    d = load_data()
    if uid in d["banned_users"]:
        d["banned_users"].remove(uid)
    if uid not in d["approved_users"]:
        d["approved_users"].append(uid)
    save_data(d)
    return True

def get_all_users() -> Dict:
    d = load_data()
    return {
        "approved": d["approved_users"],
        "pending":  d["pending_users"],
        "banned":   d["banned_users"],
        "total":    len(d["approved_users"]) + len(d["pending_users"]),
    }

# ══════════════════════════════════════════════════════════════════
#  SESSION / ACCOUNT MANAGEMENT
# ══════════════════════════════════════════════════════════════════

# In-memory client pool: {user_id: {phone: TelegramClient}}
_client_pool: Dict[int, Dict[str, TelegramClient]] = {}

# Pending login state: {user_id: {phone, api_id, api_hash, client, phone_code_hash}}
_login_state: Dict[int, Dict] = {}

async def start_login(uid: int, api_id: int, api_hash: str, phone: str) -> str:
    """
    Initiate OTP login for a Telegram user account.
    Returns phone_code_hash (needed for sign_in).
    """
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    result = await client.send_code_request(phone)
    _login_state[uid] = {
        "phone": phone,
        "api_id": api_id,
        "api_hash": api_hash,
        "client": client,
        "phone_code_hash": result.phone_code_hash,
    }
    return result.phone_code_hash

async def complete_login(uid: int, code: str, password: str = "") -> Tuple[bool, str]:
    """
    Complete OTP (+ optional 2FA) login.
    Returns (success, message).
    """
    state = _login_state.get(uid)
    if not state:
        return False, "No pending login. Please start again with /addaccount."

    client: TelegramClient = state["client"]
    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except errors.SessionPasswordNeededError:
        if not password:
            return False, "2FA_REQUIRED"
        await client.sign_in(password=password)
    except errors.PhoneCodeInvalidError:
        return False, "Invalid OTP code. Please try again."
    except Exception as exc:
        logger.error("Login error: %s", exc)
        return False, f"Login failed: {exc}"

    # Save session
    session_str = client.session.save()
    me = await client.get_me()
    me_info = {
        "id": me.id,
        "first_name": me.first_name or "",
        "last_name":  me.last_name  or "",
        "username":   me.username   or "",
        "phone":      me.phone      or phone,
    }

    d = load_data()
    rec = get_user_record(d, uid)
    rec["accounts"][phone] = {
        "api_id":   state["api_id"],
        "api_hash": state["api_hash"],
        "session":  session_str,
        "me":       me_info,
    }
    if rec["active_account"] is None:
        rec["active_account"] = phone
    save_data(d)

    # Pool
    if uid not in _client_pool:
        _client_pool[uid] = {}
    _client_pool[uid][phone] = client

    _login_state.pop(uid, None)
    return True, f"✅ Logged in as {me_info['first_name']} ({phone})"

async def get_client(uid: int, phone: Optional[str] = None) -> Optional[TelegramClient]:
    """Return (and reconnect if needed) a Telethon client."""
    d = load_data()
    rec = get_user_record(d, uid)
    if phone is None:
        phone = rec["active_account"]
    if not phone or phone not in rec["accounts"]:
        return None

    # Check pool
    if uid in _client_pool and phone in _client_pool[uid]:
        cl = _client_pool[uid][phone]
        if cl.is_connected():
            return cl
        try:
            await cl.connect()
            return cl
        except Exception:
            pass

    # Reconnect from stored session
    acc = rec["accounts"][phone]
    cl = TelegramClient(
        StringSession(acc["session"]),
        acc["api_id"],
        acc["api_hash"],
    )
    await cl.connect()
    if not await cl.is_user_authorized():
        return None

    if uid not in _client_pool:
        _client_pool[uid] = {}
    _client_pool[uid][phone] = cl
    return cl

def list_accounts(uid: int) -> List[Dict]:
    d = load_data()
    rec = get_user_record(d, uid)
    out = []
    for phone, acc in rec["accounts"].items():
        out.append({
            "phone":  phone,
            "me":     acc["me"],
            "active": (phone == rec["active_account"]),
        })
    return out

def switch_account(uid: int, phone: str) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if phone not in rec["accounts"]:
        return False
    rec["active_account"] = phone
    save_data(d)
    return True

def remove_account(uid: int, phone: str) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if phone not in rec["accounts"]:
        return False
    rec["accounts"].pop(phone)
    if rec["active_account"] == phone:
        remaining = list(rec["accounts"].keys())
        rec["active_account"] = remaining[0] if remaining else None
    save_data(d)
    if uid in _client_pool and phone in _client_pool[uid]:
        asyncio.create_task(_client_pool[uid][phone].disconnect())
        del _client_pool[uid][phone]
    return True

# ══════════════════════════════════════════════════════════════════
#  GROUP IMPORT  (Method 2 — via get_dialogs)
# ══════════════════════════════════════════════════════════════════

async def import_groups(uid: int) -> List[Dict]:
    """
    Fetch all groups/channels the user account belongs to.
    Returns [{id, title, type, members}]
    """
    client = await get_client(uid)
    if not client:
        return []

    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Channel, Chat)):
            entry = {
                "id":      entity.id,
                "title":   dialog.name,
                "type":    "channel" if isinstance(entity, Channel) else "group",
                "members": getattr(entity, "participants_count", 0) or 0,
            }
            groups.append(entry)
    return groups

# ══════════════════════════════════════════════════════════════════
#  FOLDER MANAGEMENT
# ══════════════════════════════════════════════════════════════════

def create_folder(uid: int, name: str) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if name in rec["folders"]:
        return False
    rec["folders"][name] = []
    save_data(d)
    return True

def delete_folder(uid: int, name: str) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if name not in rec["folders"]:
        return False
    del rec["folders"][name]
    save_data(d)
    return True

def add_groups_to_folder(uid: int, folder: str, groups: List[Dict]) -> int:
    d = load_data()
    rec = get_user_record(d, uid)
    if folder not in rec["folders"]:
        rec["folders"][folder] = []
    existing_ids = {g["id"] for g in rec["folders"][folder]}
    added = 0
    for g in groups:
        if g["id"] not in existing_ids:
            rec["folders"][folder].append({"id": g["id"], "title": g["title"]})
            added += 1
    save_data(d)
    return added

def remove_group_from_folder(uid: int, folder: str, group_id: int) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if folder not in rec["folders"]:
        return False
    before = len(rec["folders"][folder])
    rec["folders"][folder] = [g for g in rec["folders"][folder] if g["id"] != group_id]
    save_data(d)
    return len(rec["folders"][folder]) < before

def list_folders(uid: int) -> Dict[str, List[Dict]]:
    d = load_data()
    rec = get_user_record(d, uid)
    return rec["folders"]

# ══════════════════════════════════════════════════════════════════
#  BROADCAST ENGINE
# ══════════════════════════════════════════════════════════════════

# Running broadcast tasks: {task_id: asyncio.Task}
_broadcast_tasks: Dict[str, asyncio.Task] = {}

def _rate_ok(rec: Dict) -> bool:
    """Check & reset hourly message counter."""
    stats = rec["stats"]
    now = time.time()
    if now >= stats["hour_reset"]:
        stats["messages_this_hour"] = 0
        stats["hour_reset"] = now + 3600
    return stats["messages_this_hour"] < config.MAX_MESSAGES_PER_HOUR

async def _send_to_group(
    client: TelegramClient,
    group_id: int,
    message: str,
    ghost: bool,
) -> bool:
    """Send a single message with retry + ghost-mode delay."""
    if ghost:
        await asyncio.sleep(random.uniform(2, 8))

    for attempt in range(config.MAX_RETRIES):
        try:
            await client.send_message(group_id, message, parse_mode="html")
            return True
        except errors.FloodWaitError as e:
            logger.warning("FloodWait %ds for group %s", e.seconds, group_id)
            await asyncio.sleep(e.seconds + 5)
        except errors.ChatWriteForbiddenError:
            logger.warning("Write forbidden: group %s", group_id)
            return False
        except Exception as exc:
            logger.error("Send error (attempt %d): %s", attempt + 1, exc)
            if attempt < config.MAX_RETRIES - 1:
                await asyncio.sleep(config.RETRY_DELAY)
    return False

async def _broadcast_loop(
    uid: int,
    task_id: str,
    folder_name: str,
    phone: str,
    message: str,
    interval: int,          # seconds between full cycles
    one_shot: bool,
    callback,               # async fn(task_id, sent, failed, done)
) -> None:
    """Core broadcast coroutine — runs until cancelled or one_shot."""
    d = load_data()
    rec = get_user_record(d, uid)
    groups = rec["folders"].get(folder_name, [])

    sent, failed = 0, 0
    try:
        while True:
            client = await get_client(uid, phone)
            if not client:
                break

            for group in groups:
                # Check task still alive
                if task_id not in _broadcast_tasks:
                    break

                # Rate limit guard
                d = load_data()
                rec = get_user_record(d, uid)
                if not _rate_ok(rec):
                    logger.info("Rate limit hit — sleeping 60s")
                    await asyncio.sleep(60)
                    continue

                ok = await _send_to_group(
                    client, group["id"], message,
                    ghost=rec.get("ghost_mode", False)
                )
                if ok:
                    sent += 1
                    d = load_data()
                    rec = get_user_record(d, uid)
                    rec["stats"]["messages_sent"] += 1
                    rec["stats"]["messages_this_hour"] += 1
                    save_data(d)
                else:
                    failed += 1

                # Random anti-ban delay
                delay = random.uniform(
                    config.BROADCAST_DELAY_MIN,
                    config.BROADCAST_DELAY_MAX
                )
                await asyncio.sleep(delay)

            await callback(task_id, sent, failed, False)

            if one_shot:
                break
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        pass
    finally:
        _broadcast_tasks.pop(task_id, None)
        await callback(task_id, sent, failed, True)

def start_broadcast(
    uid: int,
    folder: str,
    phone: str,
    message: str,
    interval: int,
    one_shot: bool,
    callback,
) -> str:
    """Start a broadcast task. Returns task_id."""
    task_id = f"bc_{uid}_{int(time.time())}"
    coro = _broadcast_loop(uid, task_id, folder, phone, message, interval, one_shot, callback)
    task = asyncio.create_task(coro)
    _broadcast_tasks[task_id] = task

    d = load_data()
    rec = get_user_record(d, uid)
    rec["broadcast_tasks"][task_id] = {
        "folder":   folder,
        "phone":    phone,
        "message":  message,
        "interval": interval,
        "one_shot": one_shot,
        "started":  datetime.utcnow().isoformat(),
        "status":   "running",
    }
    save_data(d)
    return task_id

def stop_broadcast(uid: int, task_id: str) -> bool:
    task = _broadcast_tasks.pop(task_id, None)
    if task:
        task.cancel()
    d = load_data()
    rec = get_user_record(d, uid)
    if task_id in rec["broadcast_tasks"]:
        rec["broadcast_tasks"][task_id]["status"] = "stopped"
        save_data(d)
    return task is not None

def list_broadcasts(uid: int) -> Dict:
    d = load_data()
    rec = get_user_record(d, uid)
    return rec.get("broadcast_tasks", {})

def update_broadcast_message(uid: int, task_id: str, new_message: str) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    if task_id not in rec.get("broadcast_tasks", {}):
        return False
    rec["broadcast_tasks"][task_id]["message"] = new_message
    save_data(d)
    return True

# ══════════════════════════════════════════════════════════════════
#  AUTO-REPLY ENGINE
# ══════════════════════════════════════════════════════════════════

# Registered handlers: {uid: handler_ref}
_auto_reply_handlers: Dict[int, Any] = {}

async def _auto_reply_handler_factory(uid: int, client: TelegramClient) -> None:
    """Register a Telethon event handler for auto-reply."""
    d = load_data()
    rec = get_user_record(d, uid)
    ar = rec["auto_reply"]

    async def on_message(event):
        if event.is_private and event.is_reply:
            return  # Avoid loops

        d2 = load_data()
        rec2 = get_user_record(d2, uid)
        ar2 = rec2["auto_reply"]
        if not ar2["enabled"]:
            return

        text = (event.message.text or "").lower()
        ghost = rec2.get("ghost_mode", False)

        reply = None
        if ar2["mode"] == "all":
            reply = ar2.get("default_reply", "👋 Hi! I'll get back to you soon.")
        elif ar2["mode"] == "keyword":
            for kw, rep in ar2["keywords"].items():
                if kw.lower() in text:
                    reply = rep
                    break

        if reply:
            if ghost:
                await asyncio.sleep(random.uniform(3, 12))
            try:
                await event.reply(reply)
            except Exception as exc:
                logger.warning("Auto-reply failed: %s", exc)

    client.add_event_handler(on_message, events.NewMessage(incoming=True))
    _auto_reply_handlers[uid] = on_message

def set_auto_reply(uid: int, enabled: bool, mode: str = "keyword",
                   keywords: Optional[Dict[str, str]] = None,
                   default_reply: str = "") -> None:
    d = load_data()
    rec = get_user_record(d, uid)
    ar = rec["auto_reply"]
    ar["enabled"] = enabled
    ar["mode"] = mode
    if keywords is not None:
        ar["keywords"] = keywords
    if default_reply:
        ar["default_reply"] = default_reply
    save_data(d)

def get_auto_reply_config(uid: int) -> Dict:
    d = load_data()
    rec = get_user_record(d, uid)
    return rec["auto_reply"]

async def enable_auto_reply_for_account(uid: int, phone: Optional[str] = None) -> bool:
    client = await get_client(uid, phone)
    if not client:
        return False
    await _auto_reply_handler_factory(uid, client)
    set_auto_reply(uid, True)
    return True

# ══════════════════════════════════════════════════════════════════
#  SCHEDULER ENGINE
# ══════════════════════════════════════════════════════════════════

_scheduled_tasks: Dict[str, asyncio.Task] = {}

async def _scheduler_loop(
    uid: int,
    task_id: str,
    targets: List[int],    # group IDs
    phone: str,
    message: str,
    run_at: Optional[datetime],   # None = run immediately / use interval
    interval_sec: Optional[int],  # None = one-shot
    callback,
) -> None:
    try:
        # Wait until scheduled time
        if run_at:
            now = datetime.utcnow()
            wait = (run_at - now).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)

        while True:
            client = await get_client(uid, phone)
            if not client:
                break

            sent, failed = 0, 0
            for gid in targets:
                ok = await _send_to_group(client, gid, message, ghost=False)
                if ok:
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(random.uniform(3, 10))

            await callback(task_id, sent, failed)

            if interval_sec is None:
                break
            await asyncio.sleep(interval_sec)

    except asyncio.CancelledError:
        pass
    finally:
        _scheduled_tasks.pop(task_id, None)
        d = load_data()
        rec = get_user_record(d, uid)
        if task_id in rec.get("scheduled_tasks", {}):
            rec["scheduled_tasks"][task_id]["status"] = "done"
            save_data(d)

def schedule_message(
    uid: int,
    targets: List[int],
    phone: str,
    message: str,
    run_at: Optional[datetime],
    interval_sec: Optional[int],
    callback,
) -> str:
    task_id = f"sc_{uid}_{int(time.time())}"
    coro = _scheduler_loop(uid, task_id, targets, phone, message, run_at, interval_sec, callback)
    task = asyncio.create_task(coro)
    _scheduled_tasks[task_id] = task

    d = load_data()
    rec = get_user_record(d, uid)
    if "scheduled_tasks" not in rec:
        rec["scheduled_tasks"] = {}
    rec["scheduled_tasks"][task_id] = {
        "targets":  targets,
        "phone":    phone,
        "message":  message,
        "run_at":   run_at.isoformat() if run_at else None,
        "interval": interval_sec,
        "status":   "running",
        "created":  datetime.utcnow().isoformat(),
    }
    save_data(d)
    return task_id

def cancel_schedule(task_id: str) -> bool:
    task = _scheduled_tasks.pop(task_id, None)
    if task:
        task.cancel()
        return True
    return False

# ══════════════════════════════════════════════════════════════════
#  SCRAPER ENGINE
# ══════════════════════════════════════════════════════════════════

async def scrape_members(
    uid: int,
    group_id: int,
    limit: int = config.SCRAPER_MAX_MEMBERS,
) -> Tuple[bool, List[Dict], str]:
    """
    Extract members from a group.
    Returns (success, members_list, error_message).
    """
    client = await get_client(uid)
    if not client:
        return False, [], "No active account. Please add an account first."

    members = []
    try:
        entity = await client.get_entity(group_id)
        offset = 0
        batch  = 200

        while len(members) < limit:
            take = min(batch, limit - len(members))
            result = await client(GetParticipantsRequest(
                channel    = entity,
                filter     = ChannelParticipantsSearch(""),
                offset     = offset,
                limit      = take,
                hash       = 0,
            ))
            if not result.users:
                break
            for u in result.users:
                if isinstance(u, User) and not u.bot:
                    members.append({
                        "id":         u.id,
                        "username":   u.username  or "",
                        "first_name": u.first_name or "",
                        "last_name":  u.last_name  or "",
                        "phone":      u.phone      or "",
                    })
            offset += len(result.users)
            await asyncio.sleep(config.SCRAPER_DELAY)

        return True, members, ""
    except errors.ChatAdminRequiredError:
        return False, [], "Admin rights required to scrape members."
    except Exception as exc:
        logger.error("Scraper error: %s", exc)
        return False, [], str(exc)

def members_to_txt(members: List[Dict]) -> str:
    lines = ["ID | Username | First Name | Last Name | Phone"]
    lines.append("-" * 60)
    for m in members:
        lines.append(
            f"{m['id']} | @{m['username']} | {m['first_name']} | {m['last_name']} | {m['phone']}"
        )
    return "\n".join(lines)

def members_to_json(members: List[Dict]) -> str:
    return json.dumps(members, indent=2, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════════
#  GHOST MODE
# ══════════════════════════════════════════════════════════════════

def set_ghost_mode(uid: int, enabled: bool) -> None:
    d = load_data()
    rec = get_user_record(d, uid)
    rec["ghost_mode"] = enabled
    save_data(d)

def get_ghost_mode(uid: int) -> bool:
    d = load_data()
    rec = get_user_record(d, uid)
    return rec.get("ghost_mode", False)

# ══════════════════════════════════════════════════════════════════
#  STATS / ADMIN HELPERS
# ══════════════════════════════════════════════════════════════════

def get_user_stats(uid: int) -> Dict:
    d = load_data()
    rec = get_user_record(d, uid)
    return {
        "accounts":   len(rec["accounts"]),
        "folders":    len(rec["folders"]),
        "broadcasts": len(rec.get("broadcast_tasks", {})),
        "scheduled":  len(rec.get("scheduled_tasks", {})),
        "msgs_sent":  rec["stats"]["messages_sent"],
        "ghost":      rec.get("ghost_mode", False),
        "auto_reply": rec["auto_reply"]["enabled"],
    }

async def disconnect_all() -> None:
    """Gracefully disconnect all Telethon clients on shutdown."""
    for uid_pool in _client_pool.values():
        for cl in uid_pool.values():
            try:
                await cl.disconnect()
            except Exception:
                pass
