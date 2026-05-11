# handlers/broadcast.py

import os
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from notifications.scheduler import send_emergency_broadcast
from notifications.db import get_all_enabled_users

OWNER_ID = int(os.getenv("ADMIN_ID", "0"))


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ============================================================================
# /broadcast  — step 1: prompt owner for message
# ============================================================================

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ This command is only available to the bot owner.")
        return

    context.user_data["broadcast_mode"] = True

    await update.message.reply_text(
        "📢 <b>Emergency Broadcast</b>\n\n"
        "Send me the message you want to broadcast to all users.\n\n"
        "Formatting supported:\n"
        "• &lt;b&gt;Bold&lt;/b&gt;\n"
        "• &lt;i&gt;Italic&lt;/i&gt;\n"
        "• &lt;code&gt;Code&lt;/code&gt;\n"
        "• &lt;a href='url'&gt;Links&lt;/a&gt;\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )


# ============================================================================
# broadcast_message_handler — step 2: receives the message text
# Called from global_text_router when broadcast_mode is True
# ============================================================================

async def broadcast_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("broadcast_mode"):
        return

    context.user_data.pop("broadcast_mode", None)
    message = update.message.text

    await update.message.reply_text(
        "📤 <b>Broadcasting message...</b>\n\n"
        "This may take a few minutes depending on the number of users.",
        parse_mode="HTML"
    )

    stats = await send_emergency_broadcast(
        context.application,
        message,
        all_users=True
    )

    if "error" in stats:
        await update.message.reply_text(
            f"❌ <b>Broadcast Failed</b>\n\n"
            f"Error: {stats['error']}",
            parse_mode="HTML"
        )
    else:
        total = stats['sent'] + stats['failed'] + stats['blocked']
        await update.message.reply_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"📊 <b>Statistics:</b>\n"
            f"• ✅ Sent: {stats['sent']}\n"
            f"• ❌ Failed: {stats['failed']}\n"
            f"• 🚫 Blocked: {stats['blocked']}\n\n"
            f"Total users: {total}",
            parse_mode="HTML"
        )


# ============================================================================
# /cancel
# ============================================================================

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("broadcast_mode", None)
    await update.message.reply_text("❌ Broadcast cancelled.")


# ============================================================================
# /broadcast_to USER_ID1 USER_ID2 ... message text
# ============================================================================

async def broadcast_specific(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ This command is only available to the bot owner.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/broadcast_to USER_ID1 USER_ID2 ... Your message</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/broadcast_to 123456789 987654321 Hello users!</code>",
            parse_mode="HTML"
        )
        return

    user_ids = []
    message_parts = []

    for arg in args:
        if arg.isdigit() and not message_parts:
            user_ids.append(int(arg))
        else:
            message_parts.append(arg)

    if not user_ids or not message_parts:
        await update.message.reply_text("❌ Please provide both user IDs and a message.")
        return

    message = " ".join(message_parts)

    await update.message.reply_text(
        f"📤 Broadcasting to {len(user_ids)} specific users..."
    )

    stats = await send_emergency_broadcast(
        context.application,
        message,
        all_users=False,
        user_ids=user_ids
    )

    await update.message.reply_text(
        f"✅ <b>Targeted Broadcast Complete!</b>\n\n"
        f"📊 <b>Statistics:</b>\n"
        f"• ✅ Sent: {stats['sent']}\n"
        f"• ❌ Failed: {stats['failed']}\n"
        f"• 🚫 Blocked: {stats['blocked']}",
        parse_mode="HTML"
    )


# ============================================================================
# /broadcast_preview message text
# ============================================================================

async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ This command is only available to the bot owner.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/broadcast_preview Your message here</code>",
            parse_mode="HTML"
        )
        return

    message = " ".join(context.args)

    await update.message.reply_text(
        "👀 <b>Broadcast Preview</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "This is how users will see your message."
        # No parse_mode — renders the message exactly as users will see it
    )


# ============================================================================
# /admin — dashboard
# ============================================================================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ This command is only available to the bot owner.")
        return

    from notifications.scheduler import get_scheduler_status

    all_users = get_all_enabled_users()  # from notifications.db — correct source
    active_count = len([u for u in all_users if u["is_enabled"] == 1])

    scheduler_status = get_scheduler_status()
    next_run = (
        scheduler_status['jobs'][0]['next_run']
        if scheduler_status.get('jobs')
        else 'N/A'
    )

    await update.message.reply_text(
        f"📊 <b>Bot Admin Dashboard</b>\n\n"
        f"👥 <b>Users:</b>\n"
        f"• Total: {len(all_users)}\n"
        f"• Notifications on: {active_count}\n"
        f"• Notifications off: {len(all_users) - active_count}\n\n"
        f"⚙️ <b>Scheduler:</b>\n"
        f"• Status: {scheduler_status['status']}\n"
        f"• Next run: {next_run}\n\n"
        f"📡 <b>Commands:</b>\n"
        f"• /broadcast — Send to all users\n"
        f"• /broadcast_to — Send to specific users\n"
        f"• /broadcast_preview — Preview message\n"
        f"• /admin — Show this dashboard",
        parse_mode="HTML"
    )


# ============================================================================
# Handler registration
# ============================================================================

def register_broadcast_handlers(app):
    app.add_handler(CommandHandler("broadcast", broadcast_start))
    app.add_handler(CommandHandler("broadcast_to", broadcast_specific))
    app.add_handler(CommandHandler("broadcast_preview", broadcast_preview))
    app.add_handler(CommandHandler("cancel", broadcast_cancel))
    app.add_handler(CommandHandler("admin", admin_stats))
    
