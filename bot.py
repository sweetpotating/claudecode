#!/usr/bin/env python3
"""RUN FASTA FATTI — Strava Telegram bot powered by Claude."""

import os
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
    "You are RUN FASTA FATTI, a high-energy running coach bot with big personality. "
    "You're like that friend who's always hyped about running — supportive, a little cheeky, "
    "and genuinely knowledgeable. You celebrate wins (even small ones), give honest feedback "
    "when performance dips, and always end with something actionable.\n\n"
    "Style rules:\n"
    "- Use metric units (km, min/km)\n"
    "- Keep it punchy — short paragraphs, no walls of text\n"
    "- Use occasional running slang (negative split, bonk, easy pace, tempo, fartlek)\n"
    "- Be data-driven but human — numbers matter, but so does how it felt\n"
    "- Max 250 words unless detailed analysis is requested\n"
    "- When giving pace, always format as X:XX /km\n"
    "- Use bold (*text*) for key numbers and insights"
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


def fetch_athlete_stats(athlete_id: int) -> dict:
    try:
        return _strava_get(f"/athletes/{athlete_id}/stats")
    except Exception as e:
        log.error("Strava fetch stats failed: %s", e)
        return {}


def fetch_athlete() -> dict:
    try:
        return _strava_get("/athlete")
    except Exception as e:
        log.error("Strava fetch athlete failed: %s", e)
        return {}


# --------------- Visual formatting helpers ---------------

def progress_bar(value: float, max_val: float, width: int = 10) -> str:
    if max_val <= 0:
        return "░" * width
    ratio = min(value / max_val, 1.0)
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def spark_line(values: list[float]) -> str:
    if not values:
        return ""
    sparks = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    return "".join(sparks[min(int((v - mn) / rng * 7), 7)] for v in values)


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


def pace_zone(speed_mps: float) -> str:
    if not speed_mps or speed_mps <= 0:
        return "⬜ Unknown"
    pace_min_km = (1000 / speed_mps) / 60
    if pace_min_km > 7:
        return "🟢 Recovery"
    if pace_min_km > 6:
        return "🔵 Easy"
    if pace_min_km > 5.2:
        return "🟡 Aerobic"
    if pace_min_km > 4.5:
        return "🟠 Tempo"
    if pace_min_km > 3.8:
        return "🔴 Threshold"
    return "⚫ Sprint"


def fmt_activity(a: dict, detailed: bool = False) -> str:
    name = a.get("name", "Activity")
    atype = a.get("type", a.get("sport_type", ""))
    dist = a.get("distance", 0)
    moving = a.get("moving_time", 0)
    elapsed = a.get("elapsed_time", 0)
    speed = a.get("average_speed", 0)
    max_speed = a.get("max_speed", 0)
    hr = a.get("average_heartrate")
    max_hr = a.get("max_heartrate")
    elev = a.get("total_elevation_gain", 0)
    date = a.get("start_date_local", a.get("start_date", ""))[:10]
    kudos = a.get("kudos_count", 0)
    suffer = a.get("suffer_score")
    calories = a.get("calories")

    type_emoji = {"Run": "🏃", "Ride": "🚴", "Swim": "🏊", "Walk": "🚶",
                  "Hike": "🥾", "Workout": "💪", "WeightTraining": "🏋️",
                  "Yoga": "🧘", "VirtualRun": "🏃"}.get(atype, "🏅")

    lines = [f"{type_emoji} *{name}*"]
    lines.append(f"📅 {date}  •  {atype}")
    lines.append("")
    lines.append(f"📏 *{fmt_dist(dist)}*  •  ⏱ *{fmt_duration(moving)}*")
    lines.append(f"⚡ *{fmt_pace(speed)}*  •  {pace_zone(speed)}")

    if detailed:
        if max_speed:
            lines.append(f"🏎 Max pace: *{fmt_pace(max_speed)}*")
        if elapsed and moving:
            rest_pct = ((elapsed - moving) / elapsed) * 100 if elapsed > 0 else 0
            lines.append(f"⏸ Stopped: {fmt_duration(elapsed - moving)} ({rest_pct:.0f}%)")

    if hr:
        hr_bar = progress_bar(hr, 200, 8)
        lines.append(f"❤️ *{hr:.0f}* bpm {hr_bar}")
        if max_hr:
            lines.append(f"   Max: *{max_hr:.0f}* bpm")

    if elev > 0:
        lines.append(f"⛰ Elevation: *{elev:.0f}m*")

    if detailed:
        if suffer:
            lines.append(f"😤 Suffer score: *{suffer}*")
        if calories:
            lines.append(f"🔥 Calories: *{calories:.0f}*")

    if kudos:
        lines.append(f"👏 {kudos} kudos")

    return "\n".join(lines)


def fmt_activity_compact(a: dict, idx: int) -> str:
    name = a.get("name", "Activity")
    dist = a.get("distance", 0)
    speed = a.get("average_speed", 0)
    date = a.get("start_date_local", a.get("start_date", ""))[:10]
    atype = a.get("type", "")
    type_emoji = {"Run": "🏃", "Ride": "🚴", "Swim": "🏊", "Walk": "🚶",
                  "Hike": "🥾", "Workout": "💪"}.get(atype, "🏅")
    return f"{idx}. {type_emoji} *{name}*\n   {date} • {fmt_dist(dist)} • {fmt_pace(speed)}"


def fmt_summary_stats(activities: list[dict]) -> str:
    if not activities:
        return "No activities to summarise."

    total_dist = sum(a.get("distance", 0) for a in activities)
    total_time = sum(a.get("moving_time", 0) for a in activities)
    total_elev = sum(a.get("total_elevation_gain", 0) for a in activities)
    runs = [a for a in activities if a.get("type", "").lower() in ("run", "virtualrun")]
    paces = [a["average_speed"] for a in runs if a.get("average_speed")]
    hrs = [a["average_heartrate"] for a in activities if a.get("average_heartrate")]
    dists = [a.get("distance", 0) / 1000 for a in activities]

    lines = [
        "📊 *Summary Stats*",
        "",
        f"🔢 Sessions: *{len(activities)}*",
        f"📏 Total distance: *{fmt_dist(total_dist)}*",
        f"⏱ Total time: *{fmt_duration(total_time)}*",
        f"⛰ Total elevation: *{total_elev:.0f}m*",
    ]

    if paces:
        avg_pace = sum(paces) / len(paces)
        lines.append(f"⚡ Avg pace: *{fmt_pace(avg_pace)}*")

    if hrs:
        avg_hr = sum(hrs) / len(hrs)
        lines.append(f"❤️ Avg HR: *{avg_hr:.0f}* bpm")

    if len(dists) > 1:
        lines.append(f"\n📈 Distance trend: {spark_line(dists)}")

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


# --------------- Inline keyboard helpers ---------------

def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏃 Latest", callback_data="cmd_latest"),
            InlineKeyboardButton("📋 Last 5", callback_data="cmd_last5"),
        ],
        [
            InlineKeyboardButton("📅 Week", callback_data="cmd_week"),
            InlineKeyboardButton("📆 Month", callback_data="cmd_month"),
        ],
        [
            InlineKeyboardButton("🔬 Analyse Run", callback_data="cmd_run_analysis"),
            InlineKeyboardButton("❤️ HR Analysis", callback_data="cmd_hr_analysis"),
        ],
        [
            InlineKeyboardButton("😴 Fatigue", callback_data="cmd_fatigue"),
            InlineKeyboardButton("📊 Compare", callback_data="cmd_compare"),
        ],
        [
            InlineKeyboardButton("📝 Plan", callback_data="cmd_plan"),
            InlineKeyboardButton("🏋️ HYROX", callback_data="cmd_hyrox"),
        ],
        [
            InlineKeyboardButton("🏁 Race Prep", callback_data="cmd_raceprep"),
            InlineKeyboardButton("🏆 PRs", callback_data="cmd_pr"),
        ],
        [
            InlineKeyboardButton("🔥 Streak", callback_data="cmd_streak"),
            InlineKeyboardButton("🎯 Goals", callback_data="cmd_goals"),
        ],
    ])


def after_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔬 Analyse this", callback_data="cmd_run_analysis"),
            InlineKeyboardButton("📊 Compare", callback_data="cmd_compare"),
        ],
        [
            InlineKeyboardButton("📋 More activities", callback_data="cmd_last5"),
            InlineKeyboardButton("📖 Menu", callback_data="cmd_help"),
        ],
    ])


# --------------- Telegram handlers ---------------

async def _reply(update: Update, text: str, keyboard=None):
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏃 *RUN FASTA FATTI*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Your AI running coach with Strava superpowers.\n\n"
        "I can pull your runs, analyse your pace and heart rate, "
        "spot fatigue, build training plans, and more.\n\n"
        "Tap a button below or type a command. "
        "You can also just *chat* — ask me anything about your training!\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔍 *Quick Lookup*\n"
        "  /latest • /last5\n\n"
        "📊 *Summaries*\n"
        "  /week • /month\n\n"
        "🔬 *Analysis*\n"
        "  /run\\_analysis • /hr\\_analysis\n"
        "  /fatigue • /compare\n\n"
        "🧠 *Coaching*\n"
        "  /plan • /hyrox • /raceprep\n\n"
        "🏆 *Tracking*\n"
        "  /pr • /streak\n"
        "  /goals • /log\\_note • /reset\n"
    )
    await _reply(update, text, help_keyboard())


async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "🔧 Running diagnostics...")
    lines = ["*Strava Direct API*"]
    try:
        token = _get_access_token()
        lines.append(f"✅ Token: `{token[:8]}...`")
    except Exception as e:
        lines.append(f"❌ Token error: `{str(e)[:200]}`")
        await _reply(update, "\n".join(lines))
        return

    try:
        activities = fetch_activities(1)
        if activities:
            a = activities[0]
            lines.append(f"✅ Latest: *{a.get('name', '?')}*")
            lines.append(f"   {a.get('start_date_local', '?')[:10]} • {fmt_dist(a.get('distance', 0))}")
            lines.append("\n🟢 Strava connection is working!")
        else:
            lines.append("⚠️ No activities found")
    except Exception as e:
        lines.append(f"❌ Fetch error: `{str(e)[:200]}`")

    await _reply(update, "\n".join(lines))


async def cmd_latest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(1)
    if not activities:
        await _reply(update, "😕 No activities found. Check /debug for connection status.")
        return
    text = fmt_activity(activities[0], detailed=True)
    await _reply(update, text, after_action_keyboard())


async def cmd_last5(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(5)
    if not activities:
        await _reply(update, "😕 No activities found.")
        return
    lines = ["📋 *Recent Activities*\n"]
    for i, a in enumerate(activities, 1):
        lines.append(fmt_activity_compact(a, i))
    lines.append(f"\n{fmt_summary_stats(activities)}")
    await _reply(update, "\n".join(lines))


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
        await _reply(update, "No activities in the last 7 days. Rest week? 😴")
        return

    stats = fmt_summary_stats(weekly)
    data_str = json.dumps(weekly, default=str)
    ai_reply = ask_claude(
        f"Here are my activities from the past 7 days. Give me a weekly training summary. "
        f"Comment on volume, intensity, balance, and what to focus on next.\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, f"📅 *This Week*\n\n{stats}\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

    stats = fmt_summary_stats(monthly)
    data_str = json.dumps(monthly, default=str)
    ai_reply = ask_claude(
        f"Here are my activities from the past 30 days. Give me a monthly training summary "
        f"covering volume progression, consistency, pace trends, and recommendations.\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, f"📆 *This Month*\n\n{stats}\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_run_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(1)
    if not activities:
        await _reply(update, "No activities found.")
        return
    a = activities[0]
    detail = fetch_activity_detail(a.get("id")) if a.get("id") else a
    header = fmt_activity(a, detailed=True)
    data_str = json.dumps(detail, default=str)
    ai_reply = ask_claude(
        f"Deep-analyse this run. Cover: effort level, pace consistency, "
        f"heart rate response, what went well, what to improve, and a rating out of 10.\n\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an expert running coach. Give a structured analysis with a rating.",
    )
    await _reply(update, f"🔬 *Run Analysis*\n\n{header}\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_hr_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(10)
    if not activities:
        await _reply(update, "No activities found.")
        return
    runs = [a for a in activities if a.get("type", "").lower() in ("run", "virtualrun")]
    if not runs:
        runs = activities[:5]

    hr_data = []
    for a in runs:
        if a.get("average_heartrate") and a.get("average_speed"):
            hr_data.append(f"  {a.get('name', '?')}: {fmt_pace(a['average_speed'])} @ {a['average_heartrate']:.0f}bpm")

    header = "❤️ *Pace vs Heart Rate*\n"
    if hr_data:
        header += "\n".join(hr_data)

    data_str = json.dumps(runs, default=str)
    ai_reply = ask_claude(
        f"Analyse pace vs heart rate across these runs. "
        f"Look for cardiac drift, aerobic efficiency, decoupling, and fitness trends.\n\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are a sports physiologist. Focus on HR zones, efficiency factor, and aerobic development.",
    )
    await _reply(update, f"{header}\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_fatigue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(14)
    if not activities:
        await _reply(update, "No activities found.")
        return
    uid = str(update.effective_chat.id)
    notes = get_notes(uid)

    dists = [a.get("distance", 0) / 1000 for a in activities]
    header = f"😴 *Fatigue Check*\n\n📈 Load trend: {spark_line(dists)}"

    data_str = json.dumps(activities, default=str)
    notes_str = json.dumps(notes[-10:], default=str) if notes else "No notes logged."
    ai_reply = ask_claude(
        f"Check for overtraining/fatigue signals. Analyse: training load ramp rate, "
        f"pace vs HR decoupling, rest days, and my subjective notes.\n\n"
        f"Activities:\n{data_str}\n\nNotes:\n{notes_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are a sports scientist. Give a fatigue risk rating (Low/Medium/High) and explain why.",
    )
    await _reply(update, f"{header}\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(30)
    if not activities:
        await _reply(update, "No activities found.")
        return
    latest = activities[0]
    latest_type = latest.get("type", "Run").lower()
    similar = [a for a in activities[1:] if a.get("type", "").lower() == latest_type]
    if not similar:
        similar = activities[1:6]

    header_lines = [
        "📊 *Run Comparison*\n",
        f"Latest: *{latest.get('name', '?')}*",
        f"  {fmt_dist(latest.get('distance', 0))} • {fmt_pace(latest.get('average_speed', 0))}",
        f"\nComparing against {len(similar[:5])} similar runs...",
    ]

    data = {"latest": latest, "past_similar": similar[:5]}
    data_str = json.dumps(data, default=str)
    ai_reply = ask_claude(
        f"Compare my latest run to these past runs. Use a table or structured format. "
        f"Highlight improvements and regressions in pace, HR, and distance.\n\n{data_str}",
        chat_id=update.effective_chat.id,
    )
    await _reply(update, "\n".join(header_lines) + f"\n\n━━━━━━━━━━━━━━━━━━\n\n{ai_reply}")


async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(14)
    uid = str(update.effective_chat.id)
    goals = get_goals(uid)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    goals_str = ", ".join(goals) if goals else "No specific goals set."
    ai_reply = ask_claude(
        f"Create a 7-day training plan based on my recent training and goals. "
        f"Format as a day-by-day schedule with session type, target distance/time, "
        f"pace zone, and brief notes. Use emoji for each day.\n\n"
        f"Recent activities:\n{data_str}\n\nGoals: {goals_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an expert running coach. Create a balanced, progressive plan.",
    )
    await _reply(update, f"📝 *7-Day Training Plan*\n\n{ai_reply}")


async def cmd_hyrox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(14)
    uid = str(update.effective_chat.id)
    goals = get_goals(uid)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    ai_reply = ask_claude(
        f"Give HYROX-specific advice based on my training. Cover: running between stations, "
        f"functional workout prep, pacing strategy, and this week's focus.\n\n"
        f"Activities:\n{data_str}\nGoals: {', '.join(goals) if goals else 'General HYROX prep'}",
        chat_id=update.effective_chat.id,
        extra_system="You are a HYROX training specialist. Be specific about station strategy.",
    )
    await _reply(update, f"🏋️ *HYROX Advice*\n\n{ai_reply}")


async def cmd_raceprep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(14)
    data_str = json.dumps(activities, default=str) if activities else "No recent activities."
    ai_reply = ask_claude(
        f"I have a race coming up. Give race-week guidance covering: "
        f"taper, nutrition, sleep, warm-up, pacing strategy, and mental prep. "
        f"Format as a countdown (7 days to 0).\n\n"
        f"Recent activities:\n{data_str}",
        chat_id=update.effective_chat.id,
        extra_system="You are an experienced race coach. Be specific and practical.",
    )
    await _reply(update, f"🏁 *Race Prep*\n\n{ai_reply}")


async def cmd_pr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(50)
    if not activities:
        await _reply(update, "No activities found.")
        return
    runs = [a for a in activities if a.get("type", "").lower() in ("run", "virtualrun")]
    if not runs:
        await _reply(update, "No runs found.")
        return

    fastest = max(runs, key=lambda a: a.get("average_speed", 0))
    longest = max(runs, key=lambda a: a.get("distance", 0))
    most_elev = max(runs, key=lambda a: a.get("total_elevation_gain", 0))

    lines = [
        "🏆 *Personal Records* (last 50 activities)\n",
        f"⚡ *Fastest pace*",
        f"   {fastest.get('name', '?')} — *{fmt_pace(fastest.get('average_speed', 0))}*",
        f"   {fastest.get('start_date_local', '')[:10]} • {fmt_dist(fastest.get('distance', 0))}\n",
        f"📏 *Longest run*",
        f"   {longest.get('name', '?')} — *{fmt_dist(longest.get('distance', 0))}*",
        f"   {longest.get('start_date_local', '')[:10]} • {fmt_duration(longest.get('moving_time', 0))}\n",
        f"⛰ *Most elevation*",
        f"   {most_elev.get('name', '?')} — *{most_elev.get('total_elevation_gain', 0):.0f}m*",
        f"   {most_elev.get('start_date_local', '')[:10]} • {fmt_dist(most_elev.get('distance', 0))}",
    ]
    await _reply(update, "\n".join(lines))


async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    activities = fetch_activities(30)
    if not activities:
        await _reply(update, "No activities found.")
        return

    dates = set()
    for a in activities:
        ds = a.get("start_date_local", a.get("start_date", ""))[:10]
        if ds:
            dates.add(ds)

    today = datetime.now(timezone.utc).date()
    streak = 0
    d = today
    while str(d) in dates:
        streak += 1
        d -= timedelta(days=1)
    if streak == 0 and str(today - timedelta(days=1)) in dates:
        d = today - timedelta(days=1)
        while str(d) in dates:
            streak += 1
            d -= timedelta(days=1)

    total_days = len(dates)
    last_7 = sum(1 for ds in dates
                 if (today - datetime.strptime(ds, "%Y-%m-%d").date()).days < 7)

    fire = "🔥" * min(streak, 10) if streak > 0 else "💤"
    lines = [
        f"🔥 *Activity Streak*\n",
        f"{fire}",
        f"Current streak: *{streak} day{'s' if streak != 1 else ''}*\n",
        f"📅 Active days (last 30): *{total_days}*",
        f"📅 Active days (last 7): *{last_7}*",
    ]

    week_bar = progress_bar(last_7, 7, 7)
    lines.append(f"\nThis week: {week_bar} {last_7}/7 days")

    await _reply(update, "\n".join(lines))


async def cmd_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.strip() if update.message else ""
    parts = text.split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""

    if arg.lower().startswith("add "):
        goal = arg[4:].strip()
        if goal:
            goals = get_goals(uid)
            goals.append(goal)
            set_goals(uid, goals)
            await _reply(update, f"🎯 Goal added: *{goal}*")
        else:
            await _reply(update, "Usage: /goals add <your goal>")
    elif arg.lower() == "clear":
        set_goals(uid, [])
        await _reply(update, "🗑 All goals cleared.")
    else:
        goals = get_goals(uid)
        if goals:
            numbered = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(goals))
            await _reply(update, f"🎯 *Your Goals*\n\n{numbered}\n\n_/goals add <text> or /goals clear_")
        else:
            await _reply(update, "🎯 No goals set yet.\n\nUse /goals add <text> to add one.\nExample: /goals add Sub-25 min 5K")


async def cmd_log_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    note = parts[1] if len(parts) > 1 else ""
    if not note:
        await _reply(update, "📝 Usage: /log\\_note <your note>\nExample: /log\\_note legs felt heavy, slept poorly")
        return
    add_note(uid, note)
    await _reply(update, f"📝 Logged: _{note}_\n\nThis will be factored into your fatigue and plan analysis.")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_chat.id)
    await _reply(update, "🔄 Conversation history cleared. Fresh start!")


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


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data

    handlers = {
        "cmd_latest": cmd_latest,
        "cmd_last5": cmd_last5,
        "cmd_week": cmd_week,
        "cmd_month": cmd_month,
        "cmd_run_analysis": cmd_run_analysis,
        "cmd_hr_analysis": cmd_hr_analysis,
        "cmd_fatigue": cmd_fatigue,
        "cmd_compare": cmd_compare,
        "cmd_plan": cmd_plan,
        "cmd_hyrox": cmd_hyrox,
        "cmd_raceprep": cmd_raceprep,
        "cmd_pr": cmd_pr,
        "cmd_streak": cmd_streak,
        "cmd_goals": cmd_goals,
        "cmd_help": cmd_start,
    }

    handler = handlers.get(cmd)
    if handler:
        await handler(update, ctx)


# --------------- Main ---------------

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Show help & menu"),
        BotCommand("latest", "🏃 Latest activity"),
        BotCommand("last5", "📋 Last 5 activities"),
        BotCommand("week", "📅 Weekly summary"),
        BotCommand("month", "📆 Monthly summary"),
        BotCommand("run_analysis", "🔬 Analyse latest run"),
        BotCommand("hr_analysis", "❤️ Pace vs heart rate"),
        BotCommand("fatigue", "😴 Fatigue check"),
        BotCommand("compare", "📊 Compare runs"),
        BotCommand("plan", "📝 7-day plan"),
        BotCommand("hyrox", "🏋️ HYROX advice"),
        BotCommand("raceprep", "🏁 Race-week prep"),
        BotCommand("pr", "🏆 Personal records"),
        BotCommand("streak", "🔥 Activity streak"),
        BotCommand("goals", "🎯 Goals"),
        BotCommand("log_note", "📝 Log a note"),
        BotCommand("reset", "🔄 Clear history"),
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
    app.add_handler(CommandHandler("pr", cmd_pr))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("log_note", cmd_log_note))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Starting RUN FASTA FATTI...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
