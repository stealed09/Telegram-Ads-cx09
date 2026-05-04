"""
╔══════════════════════════════════════════════════════════════════╗
║         TeleAutoBot — Main Bot Handler (Aiogram 3.x)             ║
║                                                                  ║
║  Run:  python main.py                                            ║
║  Requirements:  pip install -r requirements.txt                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

# ── Aiogram 3 ───────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ── Local modules ───────────────────────────────────────────────
import config
import core

# ────────────────────────────────────────────────────────────────
#  LOGGING
# ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ────────────────────────────────────────────────────────────────
#  BOT & DISPATCHER
# ────────────────────────────────────────────────────────────────
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp  = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ════════════════════════════════════════════════════════════════
#  FSM STATES
# ════════════════════════════════════════════════════════════════
class LoginState(StatesGroup):
    waiting_api_id   = State()
    waiting_api_hash = State()
    waiting_phone    = State()
    waiting_code     = State()
    waiting_password = State()   # 2FA

class BroadcastState(StatesGroup):
    choose_folder  = State()
    choose_account = State()
    enter_message  = State()
    enter_interval = State()

class AutoReplyState(StatesGroup):
    choose_mode    = State()
    enter_keyword  = State()
    enter_reply    = State()
    enter_default  = State()

class SchedulerState(StatesGroup):
    enter_targets  = State()   # comma-separated group IDs or folder name
    choose_account = State()
    enter_message  = State()
    enter_datetime = State()   # "YYYY-MM-DD HH:MM" or interval in seconds
    enter_interval = State()

class ScraperState(StatesGroup):
    enter_group_id = State()
    choose_format  = State()

class FolderState(StatesGroup):
    enter_name     = State()
    select_groups  = State()
    confirm_save   = State()
    delete_confirm = State()

class AdminState(StatesGroup):
    broadcast_msg  = State()
    ban_uid        = State()
    unban_uid      = State()

# ════════════════════════════════════════════════════════════════
#  KEYBOARD BUILDERS
# ════════════════════════════════════════════════════════════════

def main_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📥 Import Groups",   callback_data="import_groups")
    b.button(text="📂 Folder Manager",  callback_data="folder_menu")
    b.button(text="📤 Broadcast",       callback_data="broadcast_menu")
    b.button(text="🤖 Auto Reply",      callback_data="autoreply_menu")
    b.button(text="⏰ Scheduler",       callback_data="scheduler_menu")
    b.button(text="🔍 Scraper",         callback_data="scraper_menu")
    b.button(text="🔐 Accounts",        callback_data="accounts_menu")
    b.button(text="👻 Ghost Mode",      callback_data="ghost_toggle")
    b.button(text="📊 My Stats",        callback_data="my_stats")
    b.button(text="⚙️ Settings",        callback_data="settings_menu")
    b.adjust(2, 2, 2, 2, 2)
    return b.as_markup()

def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 View Users",       callback_data="admin_users")
    b.button(text="📢 Broadcast All",    callback_data="admin_broadcast")
    b.button(text="🚫 Ban User",         callback_data="admin_ban")
    b.button(text="✅ Unban User",       callback_data="admin_unban")
    b.button(text="📋 Active Sessions",  callback_data="admin_sessions")
    b.button(text="🔙 Main Menu",        callback_data="main_menu")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

def back_kb(cb: str = "main_menu") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Back", callback_data=cb)
    return b.as_markup()

def paginate_groups(
    groups: List[Dict],
    page: int,
    selected: List[int],
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    """Build a paginated, selectable group list."""
    b = InlineKeyboardBuilder()
    start = page * page_size
    end   = start + page_size
    for g in groups[start:end]:
        mark = "✅ " if g["id"] in selected else ""
        b.button(
            text=f"{mark}{g['title'][:28]} ({g.get('members',0)})",
            callback_data=f"sel_group:{g['id']}:{page}",
        )
    b.adjust(1)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"grp_page:{page-1}"))
    if end < len(groups):
        nav.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"grp_page:{page+1}"))
    if nav:
        b.row(*nav)

    b.row(
        InlineKeyboardButton(text="✅ Confirm Selection", callback_data="grp_confirm"),
        InlineKeyboardButton(text="🔙 Cancel",           callback_data="main_menu"),
    )
    return b.as_markup()

# ════════════════════════════════════════════════════════════════
#  DECORATORS / GUARDS
# ════════════════════════════════════════════════════════════════

def approved_only(func):
    """Guard: user must be approved."""
    async def wrapper(event, *args, **kwargs):
        uid = event.from_user.id if hasattr(event, "from_user") else 0
        if core.is_banned(uid):
            txt = config.MSG_BANNED
        elif not core.is_approved(uid):
            txt = config.MSG_NOT_APPROVED
        else:
            return await func(event, *args, **kwargs)
        if isinstance(event, Message):
            await event.answer(txt)
        elif isinstance(event, CallbackQuery):
            await event.answer(txt, show_alert=True)
    return wrapper

def admin_only(func):
    """Guard: user must be the admin."""
    async def wrapper(event, *args, **kwargs):
        uid = event.from_user.id if hasattr(event, "from_user") else 0
        if uid != config.ADMIN_ID:
            txt = "🚫 Admin only command."
            if isinstance(event, Message):
                await event.answer(txt)
            elif isinstance(event, CallbackQuery):
                await event.answer(txt, show_alert=True)
            return
        return await func(event, *args, **kwargs)
    return wrapper

# ════════════════════════════════════════════════════════════════
#  /start   — Registration & welcome
# ════════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid      = message.from_user.id
    username = message.from_user.username or "N/A"
    full     = message.from_user.full_name

    if uid == config.ADMIN_ID:
        core.approve_user(uid)   # Auto-approve admin
        await message.answer(
            f"👑 <b>Welcome, Admin!</b>\n\n"
            f"Hello <b>{full}</b> — you have full access.\n\n"
            f"Use /menu for the main menu or /admin for the admin panel.",
        )
        return

    status = core.request_access(uid)

    if status == "already_approved":
        await message.answer(
            f"✅ <b>Welcome back, {full}!</b>\n\nUse /menu to access all features."
        )
    elif status == "banned":
        await message.answer(config.MSG_BANNED)
    elif status == "already_pending":
        await message.answer(config.MSG_PENDING)
    elif status == "pending_added":
        await message.answer(config.MSG_PENDING)
        # Notify admin
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=f"approve:{uid}")
        kb.button(text="❌ Reject",  callback_data=f"reject:{uid}")
        kb.adjust(2)
        await bot.send_message(
            config.ADMIN_ID,
            f"🔔 <b>New Access Request</b>\n\n"
            f"👤 Name: <b>{full}</b>\n"
            f"🆔 User ID: <code>{uid}</code>\n"
            f"📛 Username: @{username}",
            reply_markup=kb.as_markup(),
        )

# ════════════════════════════════════════════════════════════════
#  /menu
# ════════════════════════════════════════════════════════════════

@router.message(Command("menu"))
@approved_only
async def cmd_menu(message: Message) -> None:
    await message.answer(
        "🏠 <b>Main Menu</b>\n\nChoose a feature:",
        reply_markup=main_menu_kb(),
    )

@router.callback_query(F.data == "main_menu")
@approved_only
async def cb_main_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "🏠 <b>Main Menu</b>\n\nChoose a feature:",
        reply_markup=main_menu_kb(),
    )

# ════════════════════════════════════════════════════════════════
#  ADMIN APPROVAL CALLBACKS
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("approve:"))
@admin_only
async def cb_approve(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[1])
    core.approve_user(uid)
    await call.message.edit_text(
        f"✅ User <code>{uid}</code> <b>approved</b>.",
        reply_markup=None,
    )
    await bot.send_message(uid, config.MSG_APPROVED)
    await call.answer("User approved!")

@router.callback_query(F.data.startswith("reject:"))
@admin_only
async def cb_reject(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[1])
    core.reject_user(uid)
    await call.message.edit_text(
        f"❌ User <code>{uid}</code> <b>rejected</b>.",
        reply_markup=None,
    )
    await bot.send_message(uid, config.MSG_REJECTED)
    await call.answer("User rejected!")

# ════════════════════════════════════════════════════════════════
#  ADMIN PANEL  /admin
# ════════════════════════════════════════════════════════════════

@router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message) -> None:
    await message.answer("👑 <b>Admin Panel</b>", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "admin_users")
@admin_only
async def cb_admin_users(call: CallbackQuery) -> None:
    info = core.get_all_users()
    text = (
        f"👥 <b>User Overview</b>\n\n"
        f"✅ Approved : {len(info['approved'])}\n"
        f"⏳ Pending  : {len(info['pending'])}\n"
        f"🚫 Banned   : {len(info['banned'])}\n"
        f"📊 Total    : {info['total']}\n\n"
        f"<b>Approved IDs:</b> {info['approved']}\n"
        f"<b>Pending IDs:</b>  {info['pending']}\n"
        f"<b>Banned IDs:</b>   {info['banned']}"
    )
    await call.message.edit_text(text, reply_markup=admin_menu_kb())

@router.callback_query(F.data == "admin_sessions")
@admin_only
async def cb_admin_sessions(call: CallbackQuery) -> None:
    d = core.load_data()
    lines = ["📋 <b>Active Sessions</b>\n"]
    for uid_str, rec in d["users"].items():
        accs = list(rec["accounts"].keys())
        if accs:
            lines.append(f"👤 UID <code>{uid_str}</code> → {len(accs)} account(s): {accs}")
    if len(lines) == 1:
        lines.append("No active sessions found.")
    await call.message.edit_text("\n".join(lines), reply_markup=admin_menu_kb())

@router.callback_query(F.data == "admin_broadcast")
@admin_only
async def cb_admin_broadcast_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.broadcast_msg)
    await call.message.edit_text(
        "📢 <b>Admin Broadcast</b>\n\nEnter the message to send to ALL approved users:",
        reply_markup=back_kb("admin_menu") if False else None,
    )

@router.message(AdminState.broadcast_msg)
@admin_only
async def admin_do_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    d = core.load_data()
    sent = failed = 0
    for uid in d["approved_users"]:
        try:
            await bot.send_message(uid, f"📢 <b>Admin Broadcast</b>\n\n{message.text}")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)
    await message.answer(
        f"📢 Broadcast complete!\n✅ Sent: {sent} | ❌ Failed: {failed}",
        reply_markup=admin_menu_kb(),
    )

@router.callback_query(F.data == "admin_ban")
@admin_only
async def cb_admin_ban(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.ban_uid)
    await call.message.edit_text("🚫 Enter the <b>User ID</b> to ban:")

@router.message(AdminState.ban_uid)
@admin_only
async def admin_do_ban(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        uid = int(message.text.strip())
        core.ban_user(uid)
        await message.answer(f"🚫 User <code>{uid}</code> banned.", reply_markup=admin_menu_kb())
        await bot.send_message(uid, config.MSG_BANNED)
    except ValueError:
        await message.answer("❌ Invalid user ID.", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "admin_unban")
@admin_only
async def cb_admin_unban(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.unban_uid)
    await call.message.edit_text("✅ Enter the <b>User ID</b> to unban:")

@router.message(AdminState.unban_uid)
@admin_only
async def admin_do_unban(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        uid = int(message.text.strip())
        core.unban_user(uid)
        await message.answer(f"✅ User <code>{uid}</code> unbanned.", reply_markup=admin_menu_kb())
    except ValueError:
        await message.answer("❌ Invalid user ID.", reply_markup=admin_menu_kb())

# ════════════════════════════════════════════════════════════════
#  ACCOUNT MANAGEMENT
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "accounts_menu")
@approved_only
async def cb_accounts_menu(call: CallbackQuery) -> None:
    uid     = call.from_user.id
    accounts = core.list_accounts(uid)
    b = InlineKeyboardBuilder()
    for acc in accounts:
        me   = acc["me"]
        mark = "🟢" if acc["active"] else "⚪"
        b.button(
            text=f"{mark} {me['first_name']} ({acc['phone']})",
            callback_data=f"switch_acc:{acc['phone']}",
        )
    b.button(text="➕ Add Account",    callback_data="add_account")
    b.button(text="🗑 Remove Account", callback_data="remove_acc_menu")
    b.button(text="🔙 Main Menu",      callback_data="main_menu")
    b.adjust(1)
    text = f"🔐 <b>Accounts</b> ({len(accounts)} linked)\n\n" \
           "🟢 = active | Tap to switch"
    await call.message.edit_text(text, reply_markup=b.as_markup())

@router.callback_query(F.data == "add_account")
@approved_only
async def cb_add_account(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LoginState.waiting_api_id)
    await call.message.edit_text(
        "🔐 <b>Add Telegram Account</b>\n\n"
        "Step 1/3: Enter your <b>API ID</b>\n"
        "Get it from https://my.telegram.org"
    )

@router.message(LoginState.waiting_api_id)
@approved_only
async def login_api_id(message: Message, state: FSMContext) -> None:
    try:
        api_id = int(message.text.strip())
        await state.update_data(api_id=api_id)
        await state.set_state(LoginState.waiting_api_hash)
        await message.answer("Step 2/3: Enter your <b>API HASH</b>:")
    except ValueError:
        await message.answer("❌ API ID must be a number. Try again:")

@router.message(LoginState.waiting_api_hash)
@approved_only
async def login_api_hash(message: Message, state: FSMContext) -> None:
    api_hash = message.text.strip()
    await state.update_data(api_hash=api_hash)
    await state.set_state(LoginState.waiting_phone)
    await message.answer("Step 3/3: Enter your <b>phone number</b> (international format, e.g. +1234567890):")

@router.message(LoginState.waiting_phone)
@approved_only
async def login_phone(message: Message, state: FSMContext) -> None:
    uid   = message.from_user.id
    data  = await state.get_data()
    phone = message.text.strip()
    await message.answer(f"📲 Sending OTP to <code>{phone}</code>…")
    try:
        await core.start_login(uid, data["api_id"], data["api_hash"], phone)
        await state.update_data(phone=phone)
        await state.set_state(LoginState.waiting_code)
        await message.answer(
            "✉️ OTP sent! Enter the code you received.\n"
            "<i>(Add spaces between digits to avoid Telegram flagging: e.g. <b>1 2 3 4 5</b>)</i>"
        )
    except Exception as exc:
        await state.clear()
        await message.answer(f"❌ Failed to send OTP: {exc}\n\nTry /addaccount again.")

@router.message(LoginState.waiting_code)
@approved_only
async def login_code(message: Message, state: FSMContext) -> None:
    uid  = message.from_user.id
    code = message.text.strip().replace(" ", "")
    success, msg = await core.complete_login(uid, code)
    if msg == "2FA_REQUIRED":
        await state.set_state(LoginState.waiting_password)
        await message.answer("🔒 Two-Factor Authentication required. Enter your <b>2FA password</b>:")
    elif success:
        await state.clear()
        await message.answer(msg, reply_markup=main_menu_kb())
    else:
        await state.clear()
        await message.answer(f"❌ {msg}")

@router.message(LoginState.waiting_password)
@approved_only
async def login_password(message: Message, state: FSMContext) -> None:
    uid      = message.from_user.id
    data     = await state.get_data()
    password = message.text.strip()
    success, msg = await core.complete_login(uid, data.get("code", ""), password)
    await state.clear()
    if success:
        await message.answer(msg, reply_markup=main_menu_kb())
    else:
        await message.answer(f"❌ {msg}")

@router.callback_query(F.data.startswith("switch_acc:"))
@approved_only
async def cb_switch_acc(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    phone = call.data.split(":", 1)[1]
    core.switch_account(uid, phone)
    await call.answer(f"✅ Switched to {phone}")
    await cb_accounts_menu(call)

@router.callback_query(F.data == "remove_acc_menu")
@approved_only
async def cb_remove_acc_menu(call: CallbackQuery) -> None:
    uid      = call.from_user.id
    accounts = core.list_accounts(uid)
    if not accounts:
        await call.answer("No accounts to remove.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    for acc in accounts:
        b.button(
            text=f"🗑 {acc['me']['first_name']} ({acc['phone']})",
            callback_data=f"del_acc:{acc['phone']}",
        )
    b.button(text="🔙 Back", callback_data="accounts_menu")
    b.adjust(1)
    await call.message.edit_text("🗑 <b>Select account to remove:</b>", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("del_acc:"))
@approved_only
async def cb_del_acc(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    phone = call.data.split(":", 1)[1]
    core.remove_account(uid, phone)
    await call.answer(f"Account {phone} removed.")
    await cb_accounts_menu(call)

# ════════════════════════════════════════════════════════════════
#  GROUP IMPORT
# ════════════════════════════════════════════════════════════════

# Temp storage for import flow: {uid: {groups, selected, page}}
_import_state: Dict[int, Dict] = {}

@router.callback_query(F.data == "import_groups")
@approved_only
async def cb_import_groups(call: CallbackQuery) -> None:
    uid = call.from_user.id
    await call.message.edit_text("⏳ <b>Fetching your groups & channels…</b>")
    groups = await core.import_groups(uid)
    if not groups:
        await call.message.edit_text(
            "❌ No groups found or no active account.\n"
            "Please add an account first.",
            reply_markup=back_kb(),
        )
        return
    _import_state[uid] = {"groups": groups, "selected": [], "page": 0}
    await call.message.edit_text(
        f"📥 <b>Found {len(groups)} groups/channels</b>\n\nSelect the ones to import:",
        reply_markup=paginate_groups(groups, 0, []),
    )

@router.callback_query(F.data.startswith("sel_group:"))
@approved_only
async def cb_sel_group(call: CallbackQuery) -> None:
    uid  = call.from_user.id
    parts = call.data.split(":")
    gid   = int(parts[1])
    page  = int(parts[2])
    state = _import_state.get(uid, {})
    sel   = state.get("selected", [])
    if gid in sel:
        sel.remove(gid)
    else:
        sel.append(gid)
    _import_state[uid]["selected"] = sel
    groups = state["groups"]
    await call.message.edit_reply_markup(
        reply_markup=paginate_groups(groups, page, sel)
    )
    await call.answer(f"{'✅ Selected' if gid in sel else '⬜ Deselected'}")

@router.callback_query(F.data.startswith("grp_page:"))
@approved_only
async def cb_grp_page(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    page  = int(call.data.split(":")[1])
    state = _import_state.get(uid, {})
    groups = state.get("groups", [])
    sel    = state.get("selected", [])
    _import_state[uid]["page"] = page
    await call.message.edit_reply_markup(
        reply_markup=paginate_groups(groups, page, sel)
    )

@router.callback_query(F.data == "grp_confirm")
@approved_only
async def cb_grp_confirm(call: CallbackQuery, state: FSMContext) -> None:
    uid   = call.from_user.id
    imp   = _import_state.get(uid, {})
    sel   = imp.get("selected", [])
    groups= imp.get("groups", [])
    if not sel:
        await call.answer("Select at least one group first!", show_alert=True)
        return
    selected_groups = [g for g in groups if g["id"] in sel]
    await state.update_data(selected_groups=selected_groups)
    await state.set_state(FolderState.enter_name)
    await call.message.edit_text(
        f"✅ <b>{len(sel)} groups selected.</b>\n\n"
        "Enter a <b>folder name</b> to save them to:"
    )

@router.message(FolderState.enter_name)
@approved_only
async def folder_name_entered(message: Message, state: FSMContext) -> None:
    uid         = message.from_user.id
    folder_name = message.text.strip()
    data        = await state.get_data()
    groups      = data.get("selected_groups", [])
    await state.clear()

    core.create_folder(uid, folder_name)
    added = core.add_groups_to_folder(uid, folder_name, groups)
    _import_state.pop(uid, None)

    await message.answer(
        f"📂 <b>Folder '{folder_name}' saved!</b>\n"
        f"✅ {added} groups added.",
        reply_markup=main_menu_kb(),
    )

# ════════════════════════════════════════════════════════════════
#  FOLDER MANAGER
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "folder_menu")
@approved_only
async def cb_folder_menu(call: CallbackQuery) -> None:
    uid     = call.from_user.id
    folders = core.list_folders(uid)
    b = InlineKeyboardBuilder()
    for fname, grps in folders.items():
        b.button(
            text=f"📂 {fname} ({len(grps)} groups)",
            callback_data=f"view_folder:{fname}",
        )
    b.button(text="➕ Create Folder", callback_data="create_folder_prompt")
    b.button(text="🔙 Main Menu",     callback_data="main_menu")
    b.adjust(1)
    await call.message.edit_text(
        f"📂 <b>Folder Manager</b>\n\n"
        f"You have <b>{len(folders)}</b> folder(s):",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data.startswith("view_folder:"))
@approved_only
async def cb_view_folder(call: CallbackQuery) -> None:
    uid    = call.from_user.id
    fname  = call.data.split(":", 1)[1]
    folders= core.list_folders(uid)
    grps   = folders.get(fname, [])
    lines  = [f"📂 <b>{fname}</b> — {len(grps)} group(s)\n"]
    for g in grps:
        lines.append(f"• {g['title']} (<code>{g['id']}</code>)")
    b = InlineKeyboardBuilder()
    b.button(text=f"🗑 Delete '{fname}'",  callback_data=f"del_folder:{fname}")
    b.button(text="🔙 Folders",            callback_data="folder_menu")
    b.adjust(1)
    await call.message.edit_text("\n".join(lines), reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("del_folder:"))
@approved_only
async def cb_del_folder(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    fname = call.data.split(":", 1)[1]
    core.delete_folder(uid, fname)
    await call.answer(f"Folder '{fname}' deleted.")
    await cb_folder_menu(call)

@router.callback_query(F.data == "create_folder_prompt")
@approved_only
async def cb_create_folder_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(selected_groups=[])
    await state.set_state(FolderState.enter_name)
    await call.message.edit_text("📂 Enter a name for the new folder:")

# ════════════════════════════════════════════════════════════════
#  BROADCAST
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "broadcast_menu")
@approved_only
async def cb_broadcast_menu(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    tasks = core.list_broadcasts(uid)
    b = InlineKeyboardBuilder()
    b.button(text="🚀 New Broadcast",      callback_data="new_broadcast")
    for tid, meta in list(tasks.items())[-5:]:   # show last 5
        status = meta["status"]
        icon   = "🟢" if status == "running" else "🔴"
        b.button(
            text=f"{icon} {meta['folder']} ({status})",
            callback_data=f"manage_bc:{tid}",
        )
    b.button(text="🔙 Main Menu", callback_data="main_menu")
    b.adjust(1)
    await call.message.edit_text(
        "📤 <b>Broadcast Manager</b>",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data == "new_broadcast")
@approved_only
async def cb_new_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    uid     = call.from_user.id
    folders = core.list_folders(uid)
    if not folders:
        await call.answer("No folders found. Import groups first.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    for fname in folders:
        b.button(text=f"📂 {fname}", callback_data=f"bc_folder:{fname}")
    b.button(text="🔙 Cancel", callback_data="broadcast_menu")
    b.adjust(1)
    await state.set_state(BroadcastState.choose_folder)
    await call.message.edit_text("📂 Select folder to broadcast to:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("bc_folder:"), BroadcastState.choose_folder)
@approved_only
async def cb_bc_folder(call: CallbackQuery, state: FSMContext) -> None:
    uid    = call.from_user.id
    folder = call.data.split(":", 1)[1]
    await state.update_data(folder=folder)
    accounts = core.list_accounts(uid)
    if not accounts:
        await call.answer("No accounts. Add one first.", show_alert=True)
        await state.clear()
        return
    b = InlineKeyboardBuilder()
    for acc in accounts:
        mark = "🟢" if acc["active"] else ""
        b.button(
            text=f"{mark} {acc['me']['first_name']} ({acc['phone']})",
            callback_data=f"bc_acc:{acc['phone']}",
        )
    b.adjust(1)
    await state.set_state(BroadcastState.choose_account)
    await call.message.edit_text("🔐 Select account to send from:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("bc_acc:"), BroadcastState.choose_account)
@approved_only
async def cb_bc_account(call: CallbackQuery, state: FSMContext) -> None:
    phone = call.data.split(":", 1)[1]
    await state.update_data(phone=phone)
    await state.set_state(BroadcastState.enter_message)
    await call.message.edit_text("✏️ Enter the <b>message</b> to broadcast (HTML supported):")

@router.message(BroadcastState.enter_message)
@approved_only
async def bc_message(message: Message, state: FSMContext) -> None:
    await state.update_data(bc_message=message.text)
    await state.set_state(BroadcastState.enter_interval)
    await message.answer(
        "⏱ Enter <b>interval</b> in seconds between full cycles.\n"
        "Enter <b>0</b> for one-shot (send once and stop):"
    )

@router.message(BroadcastState.enter_interval)
@approved_only
async def bc_interval(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    try:
        interval = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Enter a valid number.")
        return

    data      = await state.get_data()
    one_shot  = (interval == 0)
    interval  = max(interval, 60) if not one_shot else 0

    async def broadcast_callback(task_id, sent, failed, done):
        status = "✅ Done" if done else "📤 Running"
        try:
            await bot.send_message(
                uid,
                f"📤 <b>Broadcast Update</b>\n"
                f"Task: <code>{task_id}</code>\n"
                f"Status: {status}\n"
                f"✅ Sent: {sent} | ❌ Failed: {failed}",
            )
        except Exception:
            pass

    task_id = core.start_broadcast(
        uid       = uid,
        folder    = data["folder"],
        phone     = data["phone"],
        message   = data["bc_message"],
        interval  = interval,
        one_shot  = one_shot,
        callback  = broadcast_callback,
    )
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="⏹ Stop", callback_data=f"stop_bc:{task_id}")
    b.button(text="🔙 Menu", callback_data="broadcast_menu")
    b.adjust(2)
    await message.answer(
        f"🚀 <b>Broadcast started!</b>\n"
        f"Task ID: <code>{task_id}</code>\n"
        f"Folder: {data['folder']}\n"
        f"Mode: {'One-shot' if one_shot else f'Every {interval}s'}",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data.startswith("stop_bc:"))
@approved_only
async def cb_stop_bc(call: CallbackQuery) -> None:
    uid     = call.from_user.id
    task_id = call.data.split(":", 1)[1]
    stopped = core.stop_broadcast(uid, task_id)
    await call.answer("⏹ Broadcast stopped!" if stopped else "Task not found.")
    await cb_broadcast_menu(call)

@router.callback_query(F.data.startswith("manage_bc:"))
@approved_only
async def cb_manage_bc(call: CallbackQuery) -> None:
    uid     = call.from_user.id
    task_id = call.data.split(":", 1)[1]
    tasks   = core.list_broadcasts(uid)
    meta    = tasks.get(task_id, {})
    b = InlineKeyboardBuilder()
    if meta.get("status") == "running":
        b.button(text="⏹ Stop",  callback_data=f"stop_bc:{task_id}")
    b.button(text="🔙 Back", callback_data="broadcast_menu")
    b.adjust(1)
    await call.message.edit_text(
        f"📤 <b>Broadcast Details</b>\n\n"
        f"ID: <code>{task_id}</code>\n"
        f"Folder: {meta.get('folder','?')}\n"
        f"Status: {meta.get('status','?')}\n"
        f"Started: {meta.get('started','?')}\n"
        f"Message:\n<i>{meta.get('message','?')[:200]}</i>",
        reply_markup=b.as_markup(),
    )

# ════════════════════════════════════════════════════════════════
#  AUTO-REPLY
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "autoreply_menu")
@approved_only
async def cb_autoreply_menu(call: CallbackQuery) -> None:
    uid  = call.from_user.id
    cfg  = core.get_auto_reply_config(uid)
    on   = cfg["enabled"]
    mode = cfg["mode"]
    kws  = cfg["keywords"]
    b = InlineKeyboardBuilder()
    b.button(
        text=f"{'🔴 Disable' if on else '🟢 Enable'} Auto-Reply",
        callback_data="ar_toggle",
    )
    b.button(text="📝 Set Mode",      callback_data="ar_mode")
    b.button(text="➕ Add Keyword",   callback_data="ar_add_kw")
    b.button(text="📋 View Keywords", callback_data="ar_list_kw")
    b.button(text="🔙 Main Menu",     callback_data="main_menu")
    b.adjust(1)
    await call.message.edit_text(
        f"🤖 <b>Auto-Reply</b>\n\n"
        f"Status: {'🟢 On' if on else '🔴 Off'}\n"
        f"Mode: <b>{mode}</b>\n"
        f"Keywords: {len(kws)}",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data == "ar_toggle")
@approved_only
async def cb_ar_toggle(call: CallbackQuery) -> None:
    uid  = call.from_user.id
    cfg  = core.get_auto_reply_config(uid)
    new  = not cfg["enabled"]
    core.set_auto_reply(uid, new)
    if new:
        await core.enable_auto_reply_for_account(uid)
    await call.answer(f"Auto-Reply {'enabled' if new else 'disabled'}!")
    await cb_autoreply_menu(call)

@router.callback_query(F.data == "ar_mode")
@approved_only
async def cb_ar_mode(call: CallbackQuery) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="🔑 Keyword Mode",  callback_data="ar_set_mode:keyword")
    b.button(text="💬 Reply All",     callback_data="ar_set_mode:all")
    b.button(text="🔙 Back",          callback_data="autoreply_menu")
    b.adjust(1)
    await call.message.edit_text("🤖 Choose auto-reply mode:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("ar_set_mode:"))
@approved_only
async def cb_ar_set_mode(call: CallbackQuery, state: FSMContext) -> None:
    uid  = call.from_user.id
    mode = call.data.split(":")[1]
    core.set_auto_reply(uid, True, mode=mode)
    if mode == "all":
        await state.set_state(AutoReplyState.enter_default)
        await call.message.edit_text("Enter the <b>default reply message</b> for all incoming messages:")
    else:
        await call.answer(f"Mode set to {mode}!")
        await cb_autoreply_menu(call)

@router.message(AutoReplyState.enter_default)
@approved_only
async def ar_enter_default(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    core.set_auto_reply(uid, True, mode="all", default_reply=message.text)
    await state.clear()
    await message.answer("✅ Default reply set!", reply_markup=main_menu_kb())

@router.callback_query(F.data == "ar_add_kw")
@approved_only
async def cb_ar_add_kw(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoReplyState.enter_keyword)
    await call.message.edit_text("Enter the <b>keyword</b> to trigger auto-reply:")

@router.message(AutoReplyState.enter_keyword)
@approved_only
async def ar_keyword(message: Message, state: FSMContext) -> None:
    await state.update_data(ar_keyword=message.text.strip())
    await state.set_state(AutoReplyState.enter_reply)
    await message.answer(f"Enter the <b>reply</b> for keyword <code>{message.text.strip()}</code>:")

@router.message(AutoReplyState.enter_reply)
@approved_only
async def ar_reply(message: Message, state: FSMContext) -> None:
    uid  = message.from_user.id
    data = await state.get_data()
    kw   = data["ar_keyword"]
    cfg  = core.get_auto_reply_config(uid)
    kws  = cfg.get("keywords", {})
    kws[kw] = message.text
    core.set_auto_reply(uid, cfg["enabled"], keywords=kws)
    await state.clear()
    await message.answer(f"✅ Keyword <code>{kw}</code> → <i>{message.text}</i> saved!", reply_markup=main_menu_kb())

@router.callback_query(F.data == "ar_list_kw")
@approved_only
async def cb_ar_list_kw(call: CallbackQuery) -> None:
    uid = call.from_user.id
    cfg = core.get_auto_reply_config(uid)
    kws = cfg.get("keywords", {})
    lines = ["📋 <b>Keywords & Replies</b>\n"]
    if not kws:
        lines.append("No keywords set yet.")
    for k, v in kws.items():
        lines.append(f"🔑 <code>{k}</code> → <i>{v}</i>")
    await call.message.edit_text("\n".join(lines), reply_markup=back_kb("autoreply_menu"))

# ════════════════════════════════════════════════════════════════
#  SCHEDULER
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "scheduler_menu")
@approved_only
async def cb_scheduler_menu(call: CallbackQuery) -> None:
    uid = call.from_user.id
    d   = core.load_data()
    rec = core.get_user_record(d, uid)
    tasks = rec.get("scheduled_tasks", {})
    b = InlineKeyboardBuilder()
    b.button(text="⏰ New Schedule", callback_data="new_schedule")
    for tid, meta in list(tasks.items())[-5:]:
        st = meta.get("status", "?")
        b.button(text=f"{'🟢' if st=='running' else '🔴'} Task {tid[-6:]}",
                 callback_data=f"sch_detail:{tid}")
    b.button(text="🔙 Main Menu", callback_data="main_menu")
    b.adjust(1)
    await call.message.edit_text(
        f"⏰ <b>Scheduler</b> — {len(tasks)} task(s)",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data == "new_schedule")
@approved_only
async def cb_new_schedule(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SchedulerState.enter_targets)
    await call.message.edit_text(
        "⏰ <b>New Schedule</b>\n\n"
        "Enter <b>target group IDs</b> (comma-separated)\n"
        "OR enter a <b>folder name</b> to target all groups in it:"
    )

@router.message(SchedulerState.enter_targets)
@approved_only
async def sch_targets(message: Message, state: FSMContext) -> None:
    uid  = message.from_user.id
    text = message.text.strip()
    # Check if folder name
    folders = core.list_folders(uid)
    if text in folders:
        targets = [g["id"] for g in folders[text]]
        await state.update_data(targets=targets, target_label=text)
    else:
        try:
            targets = [int(x.strip()) for x in text.split(",")]
            await state.update_data(targets=targets, target_label=text)
        except ValueError:
            await message.answer("❌ Invalid input. Enter group IDs or folder name.")
            return
    await state.set_state(SchedulerState.choose_account)
    accounts = core.list_accounts(uid)
    b = InlineKeyboardBuilder()
    for acc in accounts:
        b.button(
            text=f"{acc['me']['first_name']} ({acc['phone']})",
            callback_data=f"sch_acc:{acc['phone']}",
        )
    b.adjust(1)
    await message.answer("🔐 Select account:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("sch_acc:"), SchedulerState.choose_account)
@approved_only
async def sch_account(call: CallbackQuery, state: FSMContext) -> None:
    phone = call.data.split(":", 1)[1]
    await state.update_data(sch_phone=phone)
    await state.set_state(SchedulerState.enter_message)
    await call.message.edit_text("✏️ Enter the <b>message</b> to schedule:")

@router.message(SchedulerState.enter_message)
@approved_only
async def sch_message(message: Message, state: FSMContext) -> None:
    await state.update_data(sch_message=message.text)
    await state.set_state(SchedulerState.enter_datetime)
    await message.answer(
        "📅 When to send?\n\n"
        "• Enter <b>date-time</b>: <code>YYYY-MM-DD HH:MM</code> (UTC)\n"
        "• Or enter <b>now</b> to send immediately\n"
    )

@router.message(SchedulerState.enter_datetime)
@approved_only
async def sch_datetime(message: Message, state: FSMContext) -> None:
    text = message.text.strip().lower()
    if text == "now":
        run_at = None
    else:
        try:
            run_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer("❌ Invalid format. Use YYYY-MM-DD HH:MM or 'now'.")
            return
    await state.update_data(run_at=run_at)
    await state.set_state(SchedulerState.enter_interval)
    await message.answer(
        "🔁 Repeat interval in seconds (enter <b>0</b> for one-time):"
    )

@router.message(SchedulerState.enter_interval)
@approved_only
async def sch_interval(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    try:
        interval = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Enter a valid number.")
        return

    data = await state.get_data()
    await state.clear()

    async def sch_callback(task_id, sent, failed):
        try:
            await bot.send_message(
                uid,
                f"⏰ <b>Scheduled Task Complete</b>\n"
                f"ID: <code>{task_id}</code>\n"
                f"✅ Sent: {sent} | ❌ Failed: {failed}",
            )
        except Exception:
            pass

    task_id = core.schedule_message(
        uid         = uid,
        targets     = data["targets"],
        phone       = data["sch_phone"],
        message     = data["sch_message"],
        run_at      = data.get("run_at"),
        interval_sec= interval if interval > 0 else None,
        callback    = sch_callback,
    )
    await message.answer(
        f"⏰ <b>Scheduled!</b>\n"
        f"Task ID: <code>{task_id}</code>\n"
        f"Targets: {len(data['targets'])} group(s)\n"
        f"Run at: {data.get('run_at') or 'Now'}\n"
        f"Repeat: {'Every ' + str(interval) + 's' if interval else 'Once'}",
        reply_markup=main_menu_kb(),
    )

# ════════════════════════════════════════════════════════════════
#  SCRAPER
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "scraper_menu")
@approved_only
async def cb_scraper_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ScraperState.enter_group_id)
    await call.message.edit_text(
        "🔍 <b>Member Scraper</b>\n\n"
        "Enter the <b>Group ID</b> to scrape members from:\n"
        "<i>(You must be a member of the group)</i>"
    )

@router.message(ScraperState.enter_group_id)
@approved_only
async def scraper_group_id(message: Message, state: FSMContext) -> None:
    try:
        gid = int(message.text.strip().lstrip("-"))
        # Most channel/group IDs need -100 prefix for supergroups
        if gid > 0:
            gid = -gid
        await state.update_data(scrape_gid=gid)
        await state.set_state(ScraperState.choose_format)
        b = InlineKeyboardBuilder()
        b.button(text="📄 TXT",  callback_data="scrape_fmt:txt")
        b.button(text="📋 JSON", callback_data="scrape_fmt:json")
        b.adjust(2)
        await message.answer("Choose export format:", reply_markup=b.as_markup())
    except ValueError:
        await message.answer("❌ Invalid group ID. Enter a numeric ID.")

@router.callback_query(F.data.startswith("scrape_fmt:"), ScraperState.choose_format)
@approved_only
async def scraper_format(call: CallbackQuery, state: FSMContext) -> None:
    uid  = call.from_user.id
    fmt  = call.data.split(":")[1]
    data = await state.get_data()
    gid  = data["scrape_gid"]
    await state.clear()
    await call.message.edit_text(
        f"⏳ Scraping members from group <code>{gid}</code>…\n"
        f"This may take a moment, please wait."
    )
    success, members, err = await core.scrape_members(uid, gid)
    if not success:
        await call.message.edit_text(
            f"❌ Scrape failed: {err}",
            reply_markup=back_kb(),
        )
        return

    count = len(members)
    if fmt == "txt":
        content = core.members_to_txt(members)
        filename = f"members_{abs(gid)}.txt"
        file_bytes = BytesIO(content.encode("utf-8"))
    else:
        content = core.members_to_json(members)
        filename = f"members_{abs(gid)}.json"
        file_bytes = BytesIO(content.encode("utf-8"))

    file_bytes.name = filename
    await call.message.edit_text(
        f"✅ Scraped <b>{count}</b> members from group <code>{gid}</code>"
    )
    from aiogram.types import BufferedInputFile
    await bot.send_document(
        uid,
        document=BufferedInputFile(file_bytes.getvalue(), filename=filename),
        caption=f"📊 {count} members exported as {fmt.upper()}",
    )

# ════════════════════════════════════════════════════════════════
#  GHOST MODE
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "ghost_toggle")
@approved_only
async def cb_ghost_toggle(call: CallbackQuery) -> None:
    uid    = call.from_user.id
    status = core.get_ghost_mode(uid)
    core.set_ghost_mode(uid, not status)
    new = not status
    await call.answer(f"Ghost Mode {'🟢 ON' if new else '🔴 OFF'}")
    await call.message.edit_text(
        f"👻 <b>Ghost Mode</b>\n\n"
        f"Status: {'🟢 Enabled' if new else '🔴 Disabled'}\n\n"
        f"<i>When enabled: no typing indicators, random delays, human-like behaviour.</i>",
        reply_markup=back_kb(),
    )

# ════════════════════════════════════════════════════════════════
#  STATS
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "my_stats")
@approved_only
async def cb_my_stats(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    stats = core.get_user_stats(uid)
    await call.message.edit_text(
        f"📊 <b>Your Stats</b>\n\n"
        f"🔐 Accounts: {stats['accounts']}\n"
        f"📂 Folders: {stats['folders']}\n"
        f"📤 Broadcasts: {stats['broadcasts']}\n"
        f"⏰ Scheduled Tasks: {stats['scheduled']}\n"
        f"💬 Messages Sent: {stats['msgs_sent']}\n"
        f"👻 Ghost Mode: {'🟢 On' if stats['ghost'] else '🔴 Off'}\n"
        f"🤖 Auto-Reply: {'🟢 On' if stats['auto_reply'] else '🔴 Off'}",
        reply_markup=back_kb(),
    )

# ════════════════════════════════════════════════════════════════
#  SETTINGS
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "settings_menu")
@approved_only
async def cb_settings_menu(call: CallbackQuery) -> None:
    uid   = call.from_user.id
    ghost = core.get_ghost_mode(uid)
    ar    = core.get_auto_reply_config(uid)
    b = InlineKeyboardBuilder()
    b.button(
        text=f"{'🔴 Disable' if ghost else '🟢 Enable'} Ghost Mode",
        callback_data="ghost_toggle",
    )
    b.button(
        text=f"{'🔴 Disable' if ar['enabled'] else '🟢 Enable'} Auto-Reply",
        callback_data="ar_toggle",
    )
    b.button(text="🔙 Main Menu", callback_data="main_menu")
    b.adjust(1)
    await call.message.edit_text(
        "⚙️ <b>Settings</b>\n\n"
        f"Ghost Mode: {'🟢 On' if ghost else '🔴 Off'}\n"
        f"Auto-Reply: {'🟢 On' if ar['enabled'] else '🔴 Off'}",
        reply_markup=b.as_markup(),
    )

# ════════════════════════════════════════════════════════════════
#  ADMIN MENU CALLBACK
# ════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin_menu")
@admin_only
async def cb_admin_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("👑 <b>Admin Panel</b>", reply_markup=admin_menu_kb())

# ════════════════════════════════════════════════════════════════
#  COMMAND SHORTCUTS
# ════════════════════════════════════════════════════════════════

@router.message(Command("addaccount"))
@approved_only
async def cmd_addaccount(message: Message, state: FSMContext) -> None:
    await state.set_state(LoginState.waiting_api_id)
    await message.answer(
        "🔐 <b>Add Telegram Account</b>\n\n"
        "Step 1/3: Enter your <b>API ID</b>\n"
        "<i>Get it from https://my.telegram.org</i>"
    )

@router.message(Command("accounts"))
@approved_only
async def cmd_accounts(message: Message) -> None:
    uid      = message.from_user.id
    accounts = core.list_accounts(uid)
    if not accounts:
        await message.answer("No accounts linked. Use /addaccount to add one.")
        return
    b = InlineKeyboardBuilder()
    for acc in accounts:
        me   = acc["me"]
        mark = "🟢" if acc["active"] else "⚪"
        b.button(
            text=f"{mark} {me['first_name']} ({acc['phone']})",
            callback_data=f"switch_acc:{acc['phone']}",
        )
    b.button(text="➕ Add Account", callback_data="add_account")
    b.adjust(1)
    await message.answer("🔐 <b>Your Accounts</b>", reply_markup=b.as_markup())

@router.message(Command("admin"))
@admin_only
async def cmd_admin_slash(message: Message) -> None:
    await message.answer("👑 <b>Admin Panel</b>", reply_markup=admin_menu_kb())

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📖 <b>TeleAutoBot Help</b>\n\n"
        "/start — Register / request access\n"
        "/menu — Main menu\n"
        "/addaccount — Link a Telegram account\n"
        "/accounts — Manage accounts\n"
        "/admin — Admin panel (admin only)\n"
        "/help — This help message\n\n"
        "<b>Features:</b>\n"
        "• 📥 Import your groups/channels\n"
        "• 📂 Organise into folders\n"
        "• 📤 Broadcast messages\n"
        "• 🤖 Auto-reply with keywords\n"
        "• ⏰ Schedule messages\n"
        "• 🔍 Scrape group members\n"
        "• 👻 Ghost mode\n"
    )

# ════════════════════════════════════════════════════════════════
#  STARTUP / SHUTDOWN
# ════════════════════════════════════════════════════════════════

async def on_startup(bot: Bot) -> None:
    logger.info("TeleAutoBot starting up…")
    await bot.set_my_commands([
        BotCommand(command="start",      description="Register / Request access"),
        BotCommand(command="menu",       description="Main menu"),
        BotCommand(command="addaccount", description="Add a Telegram account"),
        BotCommand(command="accounts",   description="Manage accounts"),
        BotCommand(command="admin",      description="Admin panel"),
        BotCommand(command="help",       description="Help"),
    ])
    logger.info("Bot commands set. Bot is ready!")

async def on_shutdown(bot: Bot) -> None:
    logger.info("TeleAutoBot shutting down — disconnecting clients…")
    await core.disconnect_all()

async def main() -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    logger.info("Starting polling…")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
