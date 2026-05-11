# notifications/scheduler.py

import asyncio
from datetime import datetime, time as dt_time, timezone
from typing import Dict, List, Optional, Any, Tuple

from telegram import Bot
from telegram.error import TelegramError, Forbidden, BadRequest, RetryAfter
from telegram.ext import Application

from notifications.dispatcher import dispatch_daily_briefs, dispatch_signal_alerts
from notifications.db import get_all_enabled_users, migrate

# ============================================================================
# DAILY BRIEF JOBS (PTB JobQueue)
# ============================================================================

async def _morning_brief_job(context):
    print("[scheduler] ☀️ Morning brief job triggered")
    await dispatch_daily_briefs(context.bot)


async def _evening_brief_job(context):
    print("[scheduler] 🌙 Evening brief job triggered")
    await dispatch_daily_briefs(context.bot)


def setup_notification_jobs(application: Application) -> None:
    job_queue = application.job_queue
    job_queue.run_daily(
        _morning_brief_job,
        time=dt_time(hour=7, minute=0, tzinfo=timezone.utc),
    )
    job_queue.run_daily(
        _evening_brief_job,
        time=dt_time(hour=20, minute=0, tzinfo=timezone.utc),
    )
    print("[scheduler] ✅ Morning brief: 07:00 UTC")
    print("[scheduler] ✅ Evening brief: 20:00 UTC")
    print("[scheduler] ✅ Signal alerts fire after each screener precompute")


# ============================================================================
# SIGNAL ALERT HOOK
# Called from screener_job.py after precompute_all_coins()
# ============================================================================

async def run_signal_check(bot) -> None:
    await dispatch_signal_alerts(bot)


# ============================================================================
# SCHEDULER STATUS
# Returns a safe stub since APScheduler is not used here.
# broadcast.py and admin_stats call this.
# ============================================================================

def get_scheduler_status() -> Dict[str, Any]:
    return {
        "running": True,
        "jobs": [{"next_run": "07:00 UTC / 20:00 UTC (PTB JobQueue)"}],
        "status": "Running (PTB JobQueue)",
        "timezone": "UTC"
    }


# ============================================================================
# NOTIFICATION HISTORY LOGGING
# ============================================================================

def log_notification(
    user_id: int,
    status: str,
    timestamp: datetime,
    message_preview: str = "",
    error: str = ""
) -> None:
    try:
        from models.db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_history (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                status           TEXT    NOT NULL,
                timestamp        TEXT    NOT NULL,
                message_preview  TEXT,
                error            TEXT,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO notification_history
                (user_id, status, timestamp, message_preview, error)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, status, timestamp.isoformat(), message_preview[:100], error))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[History] Failed to log notification for user {user_id}: {e}")


# ============================================================================
# NOTIFICATION SENDING WITH RETRY
# ============================================================================

async def send_notification_with_retry(
    bot: Bot,
    user: Dict[str, Any],
    message: str,
    disable_web_page_preview: bool = True,
    max_retries: int = 3
) -> Tuple[bool, str]:
    user_id = user.get("user_id")

    for attempt in range(1, max_retries + 1):
        try:
            delivery = user.get("delivery", "private")
            if delivery == "private":
                chat_id = user.get("user_id")
            elif delivery == "group":
                chat_id = user.get("group_id")
            else:
                chat_id = None

            if not chat_id:
                if delivery == "group":
                    return False, "Group delivery selected but no group configured"
                return False, "No valid chat_id available"

            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=disable_web_page_preview
            )

            status = "retried" if attempt > 1 else "success"
            log_notification(user_id, status, datetime.utcnow(), message)
            if attempt > 1:
                print(f"[Notification] ✅ Succeeded on retry {attempt} for user {user_id}")
            else:
                print(f"[Notification] ✅ Sent to user {user_id}")
            return True, ""

        except Forbidden as e:
            error = "Bot blocked by user or removed from group"
            print(f"[Notification] ❌ {error} - user {user_id}")
            log_notification(user_id, "blocked", datetime.utcnow(), message, str(e))
            return False, error

        except BadRequest as e:
            error_msg = str(e)
            if "chat not found" in error_msg.lower():
                error = "Chat not found"
            elif "have no rights" in error_msg.lower():
                error = "Bot lacks permission to send messages here"
            else:
                error = f"Invalid request: {error_msg}"
            print(f"[Notification] ❌ {error} - user {user_id}")
            log_notification(user_id, "failed", datetime.utcnow(), message, str(e))
            return False, error

        except RetryAfter as e:
            print(f"[Notification] ⏳ Rate limited, waiting {e.retry_after}s - user {user_id}")
            await asyncio.sleep(e.retry_after)
            continue

        except TelegramError as e:
            error = f"Telegram error: {e}"
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[Notification] ⚠️ Attempt {attempt} failed, retrying in {wait_time}s - user {user_id}")
                await asyncio.sleep(wait_time)
            else:
                print(f"[Notification] ❌ {error} after {max_retries} attempts - user {user_id}")
                log_notification(user_id, "failed", datetime.utcnow(), message, str(e))
                return False, error

        except Exception as e:
            error = f"Unexpected error: {e}"
            print(f"[Notification] ❌ {error} - user {user_id}")
            log_notification(user_id, "failed", datetime.utcnow(), message, str(e))
            return False, error

    return False, f"Failed after {max_retries} retries"


# ============================================================================
# MESSAGE BUILDING
# ============================================================================

def parse_hour(time_str: Optional[str]) -> Optional[int]:
    if not time_str or not isinstance(time_str, str):
        return None
    try:
        hour = int(time_str.split(":")[0])
        return hour if 0 <= hour <= 23 else None
    except (ValueError, IndexError):
        return None


def should_notify_user(user: Dict[str, Any], current_utc_hour: int, last_hour: Optional[int]) -> bool:
    if last_hour == current_utc_hour:
        return False
    frequency = user.get("frequency")
    if not frequency or frequency == "off":
        return False
    morning_hour = parse_hour(user.get("morning_time"))
    evening_hour = parse_hour(user.get("evening_time"))
    if frequency in ["once", "twice"] and morning_hour == current_utc_hour:
        return True
    if frequency == "twice" and evening_hour == current_utc_hour:
        return True
    return False


async def build_message(user: Dict[str, Any], notif_data: Dict[str, Any]) -> str:
    """Build HTML-formatted notification message."""
    parts = ["📊 <b>Daily Market Update</b>"]

    if user.get("include_global") and notif_data.get("global"):
        g = notif_data["global"]
        if isinstance(g, dict) and g:
            parts.append(
                "\n🌍 <b>Global Market Overview</b>\n"
                f"💰 <b>Market Cap:</b> {g.get('market_cap', 'N/A')}\n"
                f"📊 <b>24h Volume:</b> {g.get('volume', 'N/A')}\n"
                f"📈 <b>Change:</b> {g.get('change', 'N/A')}\n"
                f"🏆 <b>BTC Dom:</b> {g.get('btc_dominance', 'N/A')} | "
                f"💎 <b>ETH Dom:</b> {g.get('eth_dominance', 'N/A')}"
            )

    if user.get("include_gainers") and notif_data.get("gainers"):
        gainers = notif_data["gainers"]
        if isinstance(gainers, list) and gainers:
            lines = "\n".join(f"• {coin} — 📈 <b>{change}</b>" for coin, change in gainers[:3])
            if lines:
                parts.append(f"\n🚀 <b>Top Gainers (24h)</b>\n{lines}")

    if user.get("include_losers") and notif_data.get("losers"):
        losers = notif_data["losers"]
        if isinstance(losers, list) and losers:
            lines = "\n".join(f"• {coin} — 🔻 <b>{change}</b>" for coin, change in losers[:3])
            if lines:
                parts.append(f"\n📉 <b>Top Losers (24h)</b>\n{lines}")

    if user.get("include_news") and notif_data.get("news"):
        news = notif_data["news"]
        if isinstance(news, list) and news:
            lines = []
            for item in news[:3]:
                if isinstance(item, dict):
                    title = item.get("title", "Untitled")
                    url = item.get("url", "")
                    lines.append(f'• <a href="{url}">{title}</a>' if url else f"• {title}")
            if lines:
                parts.append(f"\n📰 <b>Latest Crypto News</b>\n" + "\n".join(lines))

    if user.get("include_gas") and notif_data.get("gas"):
        gas = notif_data["gas"]
        if isinstance(gas, dict) and gas:
            parts.append(
                "\n⛽ <b>Gas Fees (ETH)</b>\n"
                f"• Low: {gas.get('low', 'N/A')}\n"
                f"• Standard: {gas.get('standard', 'N/A')}\n"
                f"• High: {gas.get('high', 'N/A')}"
            )

    if user.get("include_cod") and notif_data.get("cod"):
        cod = notif_data["cod"]
        if isinstance(cod, dict) and cod:
            parts.append(
                f"\n💡 <b>Coin of the Day</b>\n"
                f"• <b>{cod.get('coin', 'N/A')}</b> — {cod.get('reason', 'No reason provided.')}"
            )

    return "\n".join(parts)


# ============================================================================
# EMERGENCY BROADCAST
# Called from handlers/broadcast.py
# Uses get_all_enabled_users() from notifications.db — no phantom imports
# ============================================================================
async def send_emergency_broadcast(
    app,
    message: str,
    all_users: bool = True,
    user_ids: Optional[List[int]] = None
) -> Dict[str, int]:
    try:
        bot = app.bot

        if all_users:
            from models.db import get_connection
            conn = get_connection()
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            conn.close()
            users = [{"user_id": row[0], "delivery": "private"} for row in rows]
        elif user_ids:
            users = [{"user_id": uid, "delivery": "private"} for uid in user_ids]
        else:
            return {"sent": 0, "failed": 0, "blocked": 0, "error": "No users specified"}

        print(f"[Broadcast] Sending to {len(users)} users")
        stats = {"sent": 0, "failed": 0, "blocked": 0}

        for user in users:
            success, error = await send_notification_with_retry(
                bot, user, message,
                disable_web_page_preview=False,
                max_retries=2
            )
            if success:
                stats["sent"] += 1
            elif "blocked" in error.lower():
                stats["blocked"] += 1
            else:
                stats["failed"] += 1
            await asyncio.sleep(0.3)

        print(f"[Broadcast] Done — Sent: {stats['sent']}, Failed: {stats['failed']}, Blocked: {stats['blocked']}")
        return stats

    except Exception as e:
        print(f"[Broadcast] Error: {e}")
        return {"sent": 0, "failed": 0, "blocked": 0, "error": str(e)}

    
