from telegram import Update
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
from models.user import set_user_plan
from config import ADMIN_ID
from .upgrade import calculate_new_expiry  
from models.db import get_connection

async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 Admin only.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/setplan &lt;user_id&gt; &lt;plan&gt;</code>\n\n"
            "<b>Plans:</b>\n"
            "• <code>pro_monthly</code> — 30 days\n"
            "• <code>pro_yearly</code> — 365 days\n"
            "• <code>pro_lifetime</code> — Forever\n"
            "• <code>free</code> — Remove Pro access\n\n"
            "<b>Examples:</b>\n"
            "• <code>/setplan 123456 pro_yearly</code>\n"
            "• <code>/setplan 123456 free</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    
    plan_type = context.args[1].lower()
    
    valid_plans = ["pro_monthly", "pro_yearly", "pro_lifetime", "free"]
    if plan_type not in valid_plans:
        await update.message.reply_text(
            f"❌ Invalid plan.\n\n"
            f"<b>Valid options:</b>\n" + "\n".join([f"• <code>{p}</code>" for p in valid_plans]),
            parse_mode="HTML"
        )
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT plan, expiry_date 
        FROM users 
        WHERE user_id = ?
    """, (target_user_id,))
    
    row = cursor.fetchone()
    current_plan = row[0].lower() if row and row[0] else "free"
    current_expiry = row[1] if row and row[1] else None
    
    current_expiry_dt = None
    remaining_days = 0
    if current_expiry:
        try:
            if isinstance(current_expiry, str):
                current_expiry_dt = datetime.fromisoformat(current_expiry)
            elif isinstance(current_expiry, datetime):
                current_expiry_dt = current_expiry
            
            if current_expiry_dt and current_expiry_dt > datetime.utcnow():
                remaining_days = (current_expiry_dt - datetime.utcnow()).days
        except Exception as e:
            print(f"⚠️ Error parsing expiry: {e}")
    
    if current_plan == plan_type and remaining_days > 0:
        await update.message.reply_text(
            f"⚠️ <b>User already has {plan_type}</b>\n\n"
            f"📅 Current expiry: <code>{current_expiry_dt.strftime('%Y-%m-%d')}</code> ({remaining_days} days left)\n\n"
            f"❌ <b>Not adding duplicate plan</b>\n\n"
            f"💡 <b>Options:</b>\n"
            f"• To extend: Wait until plan expires, then add new plan\n"
            f"• To change: Use different plan type (e.g., upgrade monthly → yearly)\n"
            f"• To reset: Set to <code>free</code> first, then add new plan",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    base_plan = plan_type.replace("pro_", "") if plan_type != "free" else "free"
    
    if base_plan in ["monthly", "yearly", "lifetime"]:
        new_expiry = calculate_new_expiry(target_user_id, base_plan, stack=True)
    else:
        new_expiry = None
    
    if new_expiry:
        cursor.execute("""
            INSERT INTO users (user_id, plan, expiry_date)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan = excluded.plan,
                expiry_date = excluded.expiry_date
        """, (target_user_id, plan_type, new_expiry.isoformat()))
    else:
        cursor.execute("""
            INSERT INTO users (user_id, plan, expiry_date)
            VALUES (?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                plan = excluded.plan,
                expiry_date = NULL
        """, (target_user_id, plan_type))
    
    conn.commit()
    conn.close()

    # ═══════════════════════════════════════════════════════════════════════
    # NOTIFY USER
    # ═══════════════════════════════════════════════════════════════════════

    try:
        if plan_type == "pro_lifetime":
            user_msg = (
                f"🎉 <b>You're now a Lifetime Pro member!</b>\n\n"
                f"♾️ Your access never expires.\n"
                f"👑 Every Pro feature is unlocked — enjoy.\n\n"
                f"Use /menu to explore everything available to you."
            )
        elif plan_type == "free":
            user_msg = (
                f"ℹ️ <b>Your Pro access has been updated.</b>\n\n"
                f"Your account has been moved to the Free plan.\n\n"
                f"Use /upgrade to resubscribe anytime."
            )
        else:
            expiry_str = new_expiry.strftime("%B %d, %Y")
            total_days = (new_expiry - datetime.utcnow()).days
            plan_label = "Monthly" if "monthly" in plan_type else "Yearly"
            user_msg = (
                f"🎉 <b>You're now a Pro member!</b>\n\n"
                f"✅ <b>Plan:</b> {plan_label}\n"
                f"📅 <b>Expires:</b> {expiry_str} ({total_days} days)\n\n"
                f"All Pro features are now unlocked.\n"
                f"Use /menu to explore everything available to you."
            )

        await context.bot.send_message(
            chat_id=target_user_id,
            text=user_msg,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Could not notify user {target_user_id}: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # ADMIN CONFIRMATION
    # ═══════════════════════════════════════════════════════════════════════

    if new_expiry:
        expiry_str = new_expiry.strftime("%Y-%m-%d")
        total_days = (new_expiry - datetime.utcnow()).days
        
        upgrade_msg = f"✅ <b>User {target_user_id} upgraded to {plan_type}</b>\n\n"
        
        if current_plan != "free" and remaining_days > 0:
            upgrade_msg += (
                f"<b>Previous plan:</b> {current_plan} ({remaining_days} days left)\n"
                f"<b>New plan:</b> {plan_type} ({total_days} days total)\n\n"
                f"✨ <b>Preserved {remaining_days} remaining days!</b>\n"
                f"📅 New expiry: <code>{expiry_str}</code>\n\n"
                f"📨 User has been notified."
            )
        else:
            upgrade_msg += (
                f"📅 Expires: <code>{expiry_str}</code> ({total_days} days)\n"
                f"🆕 Started fresh (no previous plan)\n\n"
                f"📨 User has been notified."
            )
        
        await update.message.reply_text(upgrade_msg, parse_mode="HTML")
    else:
        if plan_type == "pro_lifetime":
            await update.message.reply_text(
                f"✅ <b>User {target_user_id} set to Lifetime Pro</b>\n\n"
                f"♾️ Never expires\n"
                f"👑 Full access forever\n\n"
                f"📨 User has been notified.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"✅ <b>User {target_user_id} set to Free</b>\n\n"
                f"❌ Pro access removed\n\n"
                f"📨 User has been notified.",
                parse_mode="HTML"
            )

    
from telegram import Update
from telegram.ext import ContextTypes
from models.db import get_connection
import asyncio

PROLIST_TIMEOUT = 120  # seconds (2 minutes)

async def pro_user_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Admins only.")
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, username, plan, expiry_date 
        FROM users
        WHERE plan LIKE 'pro%'
        ORDER BY expiry_date IS NULL DESC, expiry_date ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        response = await update.message.reply_text("📭 No Pro users found.")
        # Delete response message after timeout
        asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, response.message_id, PROLIST_TIMEOUT))
        return

    monthly_count = yearly_count = lifetime_count = 0
    msg = "*📋 Current Pro Users:*\n\n"

    for uid, username, plan, expiry in rows:
        name = f"@{username}" if username else f"`{uid}`"
        plan_name = plan.replace("pro_", "").capitalize()
        expiry_display = expiry if expiry else "♾️ Lifetime"

        msg += f"• {name} — *{plan_name}* — {expiry_display}\n"

        if uid == ADMIN_ID:
            continue

        if "month" in plan.lower():
            monthly_count += 1
        elif "year" in plan.lower():
            yearly_count += 1
        elif "life" in plan.lower():
            lifetime_count += 1

    revenue = (
        f"\n\n💰 *Expected Revenue Summary (Auto-expires):*\n"
        f"🗓️ Monthly ({monthly_count}): ${monthly_count * 7.99}\n"
        f"📅 Yearly ({yearly_count}): ${yearly_count * 59}\n"
        f"♾️ Lifetime ({lifetime_count}): ${lifetime_count * 149}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 *Total:* ${(monthly_count*7.99)+(yearly_count*59)+(lifetime_count*149)}"
    )

    sent = await update.message.reply_text(msg + revenue, parse_mode="Markdown")
    
    # Delete the results after 2 minutes
    asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, sent.message_id, PROLIST_TIMEOUT))
    
    
async def delete_message_after_delay(context, chat_id, message_id, delay=PROLIST_TIMEOUT):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id, message_id)
    except:
        pass
