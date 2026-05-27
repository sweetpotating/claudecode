#!/usr/bin/env python3
"""RUN FASTA FATTI — Strava Telegram bot powered by Claude."""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import httpx
import anthropic

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("bot")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
GOALS_FILE = DATA_DIR / "goals.json"
NOTES_FILE = DATA_DIR / "notes.json"
TOKEN_FILE = DATA_DIR / "strava_token.json"

SYSTEM_PROMPT = (
    "You are RUN FASTA FATTI, a friendly and knowledgeable running coach Telegram bot. "
    "You have access to the user's Strava data. Be concise, supportive, and data-driven. "
    "Use metric units (km, min/km). Format responses for Telegram (use markdown sparingly). "
    "Keep responses under 300 words unless detailed analysis is requested. "
    "When analysing data, highlight key insights and actionable advice."
)

# --------------- Storage helpers ---------------

def _load(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text())
    return default if default is not None else {}


def _save(path: Path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


def get_goals(uid: str) -> list[str]:
    return _load(GOALS_FILE, {}).get(uid, [])


def set_goals(uid: str, goals: list[str]):
    all_goals = _load(GOALS_FILE, {})
    all_goals[uid] = goals
    _save(GOALS_FILE, all_goals)


def get_notes(uid: str) -> list[dict]:
    return _load(NOTES_FILE, {}).get(uid, [])


def add_note(uid: str, text: str):
    all_notes = _load(NOTES_FILE, {})
    if uid not in all_notes:
        all_notes[uid] = []
    all_notes[uid].append({"text": text, "ts": datetime.now(timezone.utc).isoformat()})
    all_notes[uid] = all_notes[uid][-50:]
    _save(NOTES_FILE, all_notes)


# --------------- Conversation history ---------------

_history: dict[int, list[dict]] = {}
MAX_HISTORY = 20


def _get_hist(cid: int) -> list[dict]:
    return _history.get(cid, [])


def _add_hist(cid: int, role: str, content: str):
    if cid not in _history:
        _history[cid] = []
    _history[cid].append({"role": role, "content": content})
    if len(_history[cid]) > MAX_HISTORY:
        _history[cid] = _history[cid][-MAX_HISTORY:]


# --------------- Strava API (direct) ---------------

_strava_token: dict = {}

def _get_access_token() -> str:
    global _strava_token
    now = datetime.now(timezone.utc).timestamp()

    if _strava_token.get("access_token") and _strava_token.get("expires_at", 0) > now + 60:
        return _strava_token["access_token"]

    saved = _load(TOKEN_FILE, {})
    if saved.get("access_token") and saved.get("expires_at", 0) > now + 60:
        _strava_token = saved
        return saved["access_token"]

    log.info("Refreshing Strava access token...")
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://www.strava.com/oauth/token",
                data={
                    "client_id": STRAVA_CLIENT_ID,
                    "client_secret": STRAVA_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": STRAVA_REFRESH_TOKEN,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            _strava_token = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", STRAVA_REFRESH_TOKEN),
                "expires_at": data["expires_at"],
            }
            _save(TOKEN_FILE, _strava_token)
            log.info("Strava token refreshed, expires at %s", data["expires_at"])
            return data["access_token"]
    except Exception as e:
        log.error("Strava token refresh failed: %s", e)
        raise


def _strava_get(endpoint: str, params: dict | None = None) -> dict | list:
    token = _get_access_token()
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            f"https://www.strava.com/api/v3{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


def fetch_activities(n: int = 1) -> list[dict]:
    try:
        data = _strava_get("/athlete/activities", {"per_page": n, "page": 1})
        if isinstance(data, list):
            return data
        return [data] if data else []
    except Exception as e:
        log.error("Strava fetch activities failed: %s", e)
        return []


def fetch_activity_detail(activity_id: int) -> dict:
    try:
        return _strava_get(f"/activities/{activity_id}")
    except Exception as e:
        log.error("Strava fetch activity %s failed: %s", activity_id, e)
        return {"error": str(e)}


# --------------- Formatting helpers ---------------

def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "0:00"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_pace(speed_mps: float) -> str:
    if not speed_mps or speed_mps <= 0:
        return "N/A"
    pace_s = 1000 / speed_mps
    m, s = divmod(int(pace_s), 60)
    return f"{m}:{s:02d} /km"


def fmt_dist(meters: float) -> str:
    return f"{meters / 1000:.2f} km"


def fmt_activity(a: dict) -> str:
    name = a.get("name", "Activity")
    atype = a.get("type", a.get("sport_type", ""))
    dist = a.get("distance", 0)
    moving = a.get("moving_time", 0)
    speed = a.get("average_speed", 0)
    hr = a.get("average_heartrate")
    elev = a.get("total_elevation_gain", 0)
    date = a.get("start_date_local", a.get("start_date", ""))[:10]

    lines = [f"*{name}*  ({atype})"]
    if date:
        lines[0] += f"  —  {date}"
    lines.append(f"Distance: {fmt_dist(dist)}  |  Time: {fmt_duration(moving)}")
    lines.append(f"Pace: {fmt_pace(speed)}  |  Elevation: {elev:.0f}m")
    if hr:
        lines.append(f"Avg HR: {hr:.0f} bpm")
    return "\n".join(lines)


# --------------- Claude AI helper ---------------

def ask_claude(prompt: str, chat_id: int | None = None, extra_system: str = "") -> str:
    system = SYSTEM_PROMPT
    if extra_system:
        system += "\n\n" + extra_system

    uid = str(chat_id) if chat_id else "0"
    goals = get_goals(uid)
    notes = get_notes(uid)
    if goals:
        system += f"\n\nUser's goals: {', '.join(goals)}"
    if notes:
        recent = notes[-5:]
        system += "\n\nRecent notes: " + "; ".join(n["text"] for n in recent)

    messages = []
    if chat_id:
        messages = list(_get_hist(chat_id))
    messages.append({"role": "user", "content": prompt})

    try:
        resp = claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
        )
        text = resp.content[0].text
        if chat_id:
            _add_hist(chat_id, "user", prompt)
            _add_hist(chat_id, "assistant", text)
        return text
    except Exception as e:
        log.error("Claude error: %s", e)
        return f"AI error: {e}"


# --------------- Telegram handlers ---------------

async def _reply(update: Update, text: str):
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(
        update,
        "Hey! I'm *RUN FASTA FATTI*\n\n"
        "I've got your Strava data and I'm ready to help.\n\n"
        "*Quick lookup*\n"
        "/latest — latest activity\n"
        "/last5 — last 5 activities\n\n"
        "*Summaries*\n"
        "/week — weekly training summary\n"
        "/month — monthly training summary\n\n"
        "*Analysis*\n"
        "/run\\_analysis — analyse your latest run\n"
        "/hr\\_analysis — pace vs heart rate\n"
        "/fatigue — fatigue / overtraining signals\n"
        "/compare — latest run vs similar past runs\n\n"
        "*Coaching*\n"
        "/plan — 7-day training plan\n"
        "/hyrox — HYROX-specific advice\n"
        "/raceprep — race-week guidance\n\n"
        "*Tracking*\n"
        "/goals — list goals (or /goals add <text>, /goals clear)\n"
        "/log\\_note <text> — log a note\n"
        "/reset — clear conversation history\n\n"
        "Or just chat — I have your Strava data in context.",
    )


async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Running diagnostics...")
    lines = ["*Strava Direct API*"]
    try:
        token = _get_access_token()
        lines.append(f"Token: `{token[:8]}...` (valid)")
    except Exception as e:
        lines.append(f"Token error: `{str(e)[:200]}`")
        await _reply(update, "\n".join(lines))
        return

    try:
        activities = fetch_activities(1)
        if activities:
            a = activities[0]
            lines.append(f"Latest: *{a.get('name', '?')}*")
            lines.append(f"Type: {a.get('type', '?')}  |  Date: {a.get('start_date_local', '?')[:10]}")
            lines.append(f"Distance: {fmt_dist(a.get('distance', 0))}")
            lines.append("\nStrava connection is working!")
        else:
            lines.append("No activities found (empty response)")
    except Exception as e:
        lines.append(f"Fetch error: `{str(e)[:200]}`")

    await _reply(update, "\n".join(lines))


async def cmd_latest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Fetching your latest activity...")
    activities = fetch_activities(1)
    if not activities:
        await _reply(update, "No activities found. Check /debug for connection status.")
        return
    await _reply(update, fmt_activity(activities[0]))


async def cmd_last5(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Fetching your last 5 activities...")
    activities = fetch_activities(5)
    if not activities:
        await _reply(update, "No activities found. Check /debug for connection status.")
        return
    text = "\n\n".join(fmt_activity(a) for a in activities)
    await _reply(update, text)


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Pulling this week's data...")
    activities = fetch_activities(20)
    if not activities:
        await _reply(update, "No activities found.")
        return
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    weekly = []
    for a in activities:
        ds = a.get("start_date", "")
        try:
            dt = datetime.fromisoformat(ds.replace("Z", "+00:00"))
            if dt >= week_ago:
                weekly.append(a)
        except (ValueError, TypeError):
            continue
    if not weekly:
        await _reply(update, "No activities in the last 7 days.")
        return
    data_str = json.dumps(weekly, default=str)
    reply = ask_claude(
        f"Here are my activities from the past 7 days. Give me a weekly training summary with "
        f"total distance, time, number of sessions, avg pace, and key observations:\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, reply)


async def cmd_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Pulling this month's data...")
    activities = fetch_activities(60)
    if not activities:
        await _reply(update, "No activities found.")
        return
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    monthly = []
    for a in activities:
        ds = a.get("start_date", "")
        try:
            dt = datetime.fromisoformat(ds.replace("Z", "+00:00"))
            if dt >= month_ago:
                monthly.append(a)
        except (ValueError, TypeError):
            continue
    if not monthly:
        await _reply(update, "No activities in the last 30 days.")
        return
    data_str = json.dumps(monthly, default=str)
    reply = ask_claude(
        f"Here are my activities from the past 30 days. Give me a monthly training summary with "
        f"total distance, time, sessions per week, progression, and key observations:\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, reply)


async def cmd_run_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Analysing your latest run...")
    activities = fetch_activities(1)
    if not activities:
        await _reply(update, "No activities found.")
        return
    a = activities[0]
    detail = fetch_activity_detail(a.get("id")) if a.get("id") else a
    data_str = json.dumps(detail, default=str)
    reply = ask_claude(
        f"Analyse this run in detail — pace breakdown, effort level, what went well, "
        f"what to improve:\n\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an expert running coach analysing a training session.",
    )
    await _reply(update, reply)


async def cmd_hr_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Analysing pace vs heart rate...")
    activities = fetch_activities(10)
    if not activities:
        await _reply(update, "No activities found.")
        return
    runs = [a for a in activities if a.get("type", "").lower() in ("run", "virtualrun")]
    if not runs:
        runs = activities[:5]
    data_str = json.dumps(runs, default=str)
    reply = ask_claude(
        f"Analyse the relationship between pace and heart rate across these recent activities. "
        f"Look for cardiac drift, efficiency trends, and aerobic fitness indicators:\n\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are a sports physiologist specialising in heart rate training.",
    )
    await _reply(update, reply)


async def cmd_fatigue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Checking for fatigue signals...")
    activities = fetch_activities(14)
    if not activities:
        await _reply(update, "No activities found.")
        return
    uid = str(update.effective_chat.id)
    notes = get_notes(uid)
    data_str = json.dumps(activities, default=str)
    notes_str = json.dumps(notes[-10:], default=str) if notes else "No notes logged."
    reply = ask_claude(
        f"Check these recent activities and my notes for overtraining or fatigue signals. "
        f"Look at frequency, pace trends, heart rate trends, and my subjective notes.\n\n"
        f"Activities:\n{data_str}\n\nNotes:\n{notes_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are a sports scientist specialising in recovery and load management.",
    )
    await _reply(update, reply)


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Comparing your latest run to past runs...")
    activities = fetch_activities(30)
    if not activities:
        await _reply(update, "No activities found.")
        return
    latest = activities[0]
    latest_type = latest.get("type", "Run").lower()
    similar = [a for a in activities[1:] if a.get("type", "").lower() == latest_type]
    if not similar:
        similar = activities[1:6]
    data = {"latest": latest, "past_similar": similar[:5]}
    data_str = json.dumps(data, default=str)
    reply = ask_claude(
        f"Compare my latest run to these similar past runs. Highlight improvements, "
        f"regressions, and trends:\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, reply)


async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Building your 7-day plan...")
    activities = fetch_activities(14)
    uid = str(update.effective_chat.id)
    goals = get_goals(uid)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    goals_str = ", ".join(goals) if goals else "No specific goals set."
    reply = ask_claude(
        f"Based on my recent training and goals, create a 7-day training plan. "
        f"Include easy runs, workouts, rest days, and cross-training as appropriate.\n\n"
        f"Recent activities:\n{data_str}\n\nGoals: {goals_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an expert running coach creating a personalised training plan.",
    )
    await _reply(update, reply)


async def cmd_hyrox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Preparing HYROX advice...")
    activities = fetch_activities(14)
    uid = str(update.effective_chat.id)
    goals = get_goals(uid)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    reply = ask_claude(
        f"Based on my recent training, give me HYROX-specific training advice. "
        f"Cover running between stations, functional exercises, pacing strategy, "
        f"and what to focus on this week.\n\nRecent activities:\n{data_str}\n\n"
        f"Goals: {', '.join(goals) if goals else 'General HYROX prep'}",
        chat_id=update.effective_chat.id,
        extra_system="You are a HYROX training specialist and running coach.",
    )
    await _reply(update, reply)


async def cmd_raceprep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Preparing race-week guidance...")
    activities = fetch_activities(14)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    reply = ask_claude(
        f"I have a race coming up. Based on my recent training, give me race-week guidance: "
        f"taper advice, nutrition, sleep, warm-up, pacing strategy, mental prep.\n\n"
        f"Recent activities:\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an experienced race coach helping an athlete peak for race day.",
    )
    await _reply(update, reply)


async def cmd_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""

    if arg.lower().startswith("add "):
        goal = arg[4:].strip()
        if goal:
            goals = get_goals(uid)
            goals.append(goal)
            set_goals(uid, goals)
            await _reply(update, f"Goal added: *{goal}*")
        else:
            await _reply(update, "Usage: /goals add <your goal>")
    elif arg.lower() == "clear":
        set_goals(uid, [])
        await _reply(update, "All goals cleared.")
    else:
        goals = get_goals(uid)
        if goals:
            numbered = "\n".join(f"{i+1}. {g}" for i, g in enumerate(goals))
            await _reply(update, f"*Your goals:*\n{numbered}")
        else:
            await _reply(update, "No goals set. Use /goals add <text> to add one.")


async def cmd_log_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    note = parts[1] if len(parts) > 1 else ""
    if not note:
        await _reply(update, "Usage: /log\\_note <your note>\nExample: /log\\_note legs felt heavy today")
        return
    add_note(uid, note)
    await _reply(update, f"Noted: _{note}_")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_chat.id)
    await _reply(update, "Conversation history cleared. Fresh start!")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    activities = fetch_activities(5)
    context_str = ""
    if activities:
        context_str = f"\n\nMy recent Strava activities:\n{json.dumps(activities, default=str)}"
    reply = ask_claude(
        text + context_str,
        chat_id=update.effective_chat.id,
    )
    await _reply(update, reply)


# --------------- Main ---------------

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Show help"),
        BotCommand("latest", "Latest activity"),
        BotCommand("last5", "Last 5 activities"),
        BotCommand("week", "Weekly summary"),
        BotCommand("month", "Monthly summary"),
        BotCommand("run_analysis", "Analyse latest run"),
        BotCommand("hr_analysis", "Pace vs heart rate"),
        BotCommand("fatigue", "Fatigue signals"),
        BotCommand("compare", "Compare latest vs past"),
        BotCommand("plan", "7-day training plan"),
        BotCommand("hyrox", "HYROX advice"),
        BotCommand("raceprep", "Race-week guidance"),
        BotCommand("goals", "List / add / clear goals"),
        BotCommand("log_note", "Log a note"),
        BotCommand("reset", "Clear chat history"),
    ])
    log.info("Bot commands registered. RUN FASTA FATTI is live!")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("last5", cmd_last5))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("run_analysis", cmd_run_analysis))
    app.add_handler(CommandHandler("hr_analysis", cmd_hr_analysis))
    app.add_handler(CommandHandler("fatigue", cmd_fatigue))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("hyrox", cmd_hyrox))
    app.add_handler(CommandHandler("raceprep", cmd_raceprep))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("log_note", cmd_log_note))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Starting RUN FASTA FATTI...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
