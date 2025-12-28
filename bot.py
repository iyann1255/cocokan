#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import hashlib
import random
import secrets
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

from dotenv import load_dotenv
from telegram import (
    Update,
    User,
    BotCommand,
    MessageEntity,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("love-match")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("ENV BOT_TOKEN belum diisi (BOT_TOKEN).")

SEED_SECRET = os.getenv("SEED_SECRET", "match-secret").strip()

# Isi pakai custom_emoji_id (angka), contoh: 5260535596941582167
EMOJI_PREMIUM = {
    "love": os.getenv("EMOJI_PREMIUM_LOVE", "").strip(),
    "sparkle": os.getenv("EMOJI_PREMIUM_SPARKLE", "").strip(),
    "kiss": os.getenv("EMOJI_PREMIUM_KISS", "").strip(),
    "laugh": os.getenv("EMOJI_PREMIUM_LAUGH", "").strip(),
    "blush": os.getenv("EMOJI_PREMIUM_BLUSH", "").strip(),
}

# Normalize: custom_emoji_id harus string
for k, v in list(EMOJI_PREMIUM.items()):
    if v:
        EMOJI_PREMIUM[k] = str(v)

MENTION_RE = re.compile(r"@([A-Za-z0-9_]{4,32})")

# =========================
# UTIL
# =========================
def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:64]

def _pair_key(a: str, b: str) -> Tuple[str, str]:
    a, b = a.strip().lower(), b.strip().lower()
    return (a, b) if a <= b else (b, a)

def _stable_int(secret: str, *parts: str) -> int:
    raw = "|".join([p.strip().lower() for p in parts if p is not None]) + "|" + (secret or "")
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(h[:16], 16)

def _pick(rng: random.Random, items: list, k: int = 1) -> list:
    if not items:
        return []
    if k <= 1:
        return [items[rng.randrange(0, len(items))]]
    items2 = items[:]
    rng.shuffle(items2)
    return items2[:k]

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def _display_name(u: User) -> str:
    name = (u.full_name or u.first_name or "Seseorang").strip()
    return name.replace("\n", " ")[:64]

def _extract_two_names(text: str) -> Optional[Tuple[str, str]]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^/\w+(@\w+)?\s*", "", t).strip()

    seps = [" x ", " X ", " vs ", " VS ", " & ", " dan ", " + "]
    for sep in seps:
        if sep in t:
            left, right = t.split(sep, 1)
            left, right = _clean(left), _clean(right)
            if left and right:
                return left, right

    m = MENTION_RE.findall(t)
    if len(m) >= 2:
        return m[0], m[1]
    return None

async def _resolve_target_user(update: Update) -> Optional[User]:
    msg = update.effective_message
    if not msg:
        return None

    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user

    if msg.entities:
        for ent in msg.entities:
            if ent.type == "text_mention" and ent.user:
                return ent.user

    return None

# =========================
# PREMIUM EMOJI (SAFE)
# =========================
def pick_premium_by_score(score: int) -> Tuple[str, str]:
    """
    Return (key, fallback_unicode)
    Lucu untuk rendah, romantis untuk tinggi.
    NOTE: fallback boleh multi-codepoint, karena hanya dipakai saat tanpa entity.
    """
    if score >= 85:
        return "love", "❤️‍🔥"
    if score >= 70:
        return "sparkle", "✨"
    if score >= 55:
        return "blush", "😊"
    return "laugh", "😂"

def with_premium_prefix(prefix_label: str, emoji_key: str, fallback: str) -> Tuple[str, Optional[List[MessageEntity]]]:
    """
    FIX BadRequest UTF-16:
    - gunakan placeholder ASCII 1 char di awal ("*")
    - entity custom_emoji menimpa placeholder tsb: offset=0 length=1
    - kalau emoji_id kosong -> fallback unicode biasa
    """
    emoji_id = (EMOJI_PREMIUM.get(emoji_key) or "").strip()
    if not emoji_id:
        return f"{fallback} {prefix_label}", None

    text = f"* {prefix_label}"
    entities = [
        MessageEntity(
            type="custom_emoji",
            offset=0,
            length=1,  # placeholder 1 char ASCII => aman
            custom_emoji_id=emoji_id,
        )
    ]
    return text, entities

# =========================
# MATCH ENGINE
# =========================
@dataclass
class MatchResult:
    score: int
    label: str
    vibe: str
    reasons: list
    greens: list
    reds: list

def compute_match(secret: str, name1: str, name2: str, nonce: int = 0) -> MatchResult:
    n1 = _clean(name1)
    n2 = _clean(name2)
    a, b = _pair_key(n1, n2)

    seed = _stable_int(secret, a, b, str(int(nonce)))
    rng = random.Random(seed)

    base = rng.randint(35, 92)

    def vowel_ratio(s: str) -> float:
        v = sum(1 for ch in s.lower() if ch in "aiueo")
        return v / max(1, len(s))

    def similarity_bonus(x: str, y: str) -> int:
        sx, sy = set(x.lower()), set(y.lower())
        overlap = len(sx & sy)
        return _clamp(overlap - 6, -3, 8)

    bonus = similarity_bonus(a, b)

    vr = abs(vowel_ratio(a) - vowel_ratio(b))
    if vr < 0.05:
        bonus += 5
    elif vr < 0.12:
        bonus += 2
    else:
        bonus -= 1

    if rng.random() < 0.12:
        bonus += rng.randint(2, 7)
    if rng.random() < 0.10:
        bonus -= rng.randint(1, 5)

    score = _clamp(base + bonus, 1, 100)

    if score >= 90:
        label = "Soulmate Mode"
        vibe = "Kalian kalau chat, universe ikut ngetik."
    elif score >= 75:
        label = "Green Flag Couple"
        vibe = "Nyambungnya enak, asal komunikasi jalan."
    elif score >= 60:
        label = "Potential (butuh effort)"
        vibe = "Chemistry ada, tinggal konsistenin usaha."
    elif score >= 45:
        label = "50:50"
        vibe = "Bisa jadi, bisa bubar. Jangan mager komunikasi."
    else:
        label = "Hati-hati"
        vibe = "Bukan nggak bisa, cuma rawan salah paham."

    reason_pool = [
        "cara mikir kalian komplementer, bukan tabrakan",
        "satu spontan, satu rapi—kalau saling ngerti jadi kuat",
        "chemistry ada, tinggal konsistenin effort",
        "ritme komunikasi cocok: nggak kebanyakan, nggak kelamaan ngilang",
        "humor kalian sefrekuensi (asal roastingnya nggak keterlaluan)",
        "perlu clear soal batasan biar aman",
        "kalau ngambek, jangan silent treatment—ini titik rawan",
    ]
    green_pool = ["supportive", "cepat baikan", "nyaman jadi diri sendiri", "bisa teamwork", "careful tapi tulus", "bikin tenang"]
    red_pool = ["overthinking", "gengsi minta maaf", "suka asumsi", "baper pas capek", "komunikasi putus-nyambung", "mendadak ngilang"]

    reasons = _pick(rng, reason_pool, 3 if score >= 70 else 2)
    greens = _pick(rng, green_pool, 3 if score >= 75 else 2)
    reds = _pick(rng, red_pool, 2 if score >= 60 else 3)

    return MatchResult(score=score, label=label, vibe=vibe, reasons=reasons, greens=greens, reds=reds)

def _meter(score: int) -> str:
    bar_len = 10
    filled = round((score / 100) * bar_len)
    return "█" * filled + "░" * (bar_len - filled)

def build_result_text(name1: str, name2: str, r: MatchResult) -> str:
    reasons = "\n".join([f"• {x}" for x in r.reasons])
    greens = ", ".join(r.greens)
    reds = ", ".join(r.reds)

    return (
        f"{name1} × {name2}\n\n"
        f"Skor: {r.score}%  {_meter(r.score)}\n"
        f"Status: {r.label}\n"
        f"Vibe: {r.vibe}\n\n"
        f"Kenapa:\n{reasons}\n\n"
        f"Green flags: {greens}\n"
        f"Red flags: {reds}\n"
    )

# =========================
# REROLL SESSIONS
# =========================
def _get_sessions(app: Application) -> Dict[str, Dict[str, Any]]:
    return app.bot_data.setdefault("reroll_sessions", {})

def _make_reroll_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Coba lagi 💞", callback_data=f"reroll:{token}")]])

# =========================
# HELP TEXT
# =========================
def help_text() -> str:
    return (
        "Perintah Bot Kecocokan Cinta\n\n"
        "• /match  (reply orangnya) atau /match @username\n"
        "• /ship Nama1 x Nama2\n"
        "• /compat Nama1 & Nama2\n"
        "• /ping\n"
        "• /about\n"
        "• /setsecret kata_rahasia\n\n"
        "Tips: reply pesan orangnya terus /match biar paling enak."
    )

# =========================
# HANDLERS
# =========================
async def _post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "Mulai & cara pakai"),
        BotCommand("help", "Panduan lengkap"),
        BotCommand("cmds", "List perintah singkat"),
        BotCommand("match", "Cocokin kamu vs orang (reply/@user)"),
        BotCommand("ship", "Cocokin dua nama (A x B)"),
        BotCommand("compat", "Alias /ship"),
        BotCommand("ping", "Cek bot hidup"),
        BotCommand("about", "Info bot"),
        BotCommand("setsecret", "Ubah secret hasil (opsional)"),
    ]
    await app.bot.set_my_commands(commands)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled exception", exc_info=context.error)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(help_text())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(help_text())

async def cmds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Commands:\n"
        "/match (reply) | /match @user\n"
        "/ship A x B | /compat A & B\n"
        "/ping | /about | /help | /setsecret"
    )

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("pong")

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "About:\n"
        "Bot kecocokan cinta (hiburan) + tombol reroll.\n"
        "Emoji premium aktif kalau EMOJI_PREMIUM_* diisi di .env."
    )

async def setsecret_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global SEED_SECRET
    msg = update.effective_message
    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        await msg.reply_text("Pakai: /setsecret kata_rahasia")
        return
    SEED_SECRET = _clean(arg)
    await msg.reply_text(f"OK. Secret diganti jadi: {SEED_SECRET}\nCatatan: kalau bot restart, secret balik ke .env.")

async def match_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    me = update.effective_user
    if not msg or not me:
        return

    target = await _resolve_target_user(update)
    typed = msg.text or ""
    m = MENTION_RE.search(typed)
    typed_username = m.group(1) if m else None

    if target is None and not typed_username:
        await msg.reply_text("Cara pakai:\n1) reply orangnya lalu /match\n2) atau /match @username\n3) atau /ship A x B")
        return

    name1 = _display_name(me)
    name2 = _display_name(target) if target else f"@{typed_username}"

    token = secrets.token_urlsafe(8)
    sessions = _get_sessions(context.application)
    sessions[token] = {"name1": name1, "name2": name2, "nonce": 0}

    r = compute_match(SEED_SECRET, name1, name2, nonce=0)

    key, fallback = pick_premium_by_score(r.score)
    prefix, entities = with_premium_prefix("Love Match", key, fallback)

    text = prefix + "\n\n" + build_result_text(name1, name2, r)

    await msg.reply_text(
        text,
        entities=entities,
        reply_markup=_make_reroll_keyboard(token),
        disable_web_page_preview=True,
    )

async def ship_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    pair = _extract_two_names(msg.text or "")
    if not pair:
        await msg.reply_text("Format:\n/ship Nama1 x Nama2\n/compat Nama1 & Nama2")
        return

    a, b = pair

    token = secrets.token_urlsafe(8)
    sessions = _get_sessions(context.application)
    sessions[token] = {"name1": a, "name2": b, "nonce": 0}

    r = compute_match(SEED_SECRET, a, b, nonce=0)

    key, fallback = pick_premium_by_score(r.score)
    prefix, entities = with_premium_prefix("Love Match", key, fallback)

    text = prefix + "\n\n" + build_result_text(a, b, r)

    await msg.reply_text(
        text,
        entities=entities,
        reply_markup=_make_reroll_keyboard(token),
        disable_web_page_preview=True,
    )

async def compat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ship_cmd(update, context)

async def reroll_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()

    m = re.match(r"^reroll:(.+)$", q.data)
    if not m:
        return
    token = m.group(1)

    sessions = _get_sessions(context.application)
    sess = sessions.get(token)
    if not sess:
        await q.edit_message_text("Session expired. Ketik /match atau /ship lagi ya.")
        return

    sess["nonce"] = int(sess.get("nonce", 0)) + 1
    name1 = sess["name1"]
    name2 = sess["name2"]

    r = compute_match(SEED_SECRET, name1, name2, nonce=sess["nonce"])

    key, fallback = pick_premium_by_score(r.score)
    prefix, entities = with_premium_prefix("Love Match", key, fallback)

    text = prefix + "\n\n" + build_result_text(name1, name2, r)

    await q.edit_message_text(
        text,
        entities=entities,
        reply_markup=_make_reroll_keyboard(token),
        disable_web_page_preview=True,
    )

async def text_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return
    t = msg.text.lower()
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
        if ("cocok" in t or "kecocokan" in t or "jodoh" in t) and random.random() < 0.05:
            await msg.reply_text("Coba /match (reply orangnya) atau /ship Nama1 x Nama2")

# =========================
# MAIN
# =========================
def main() -> None:
    app: Application = ApplicationBuilder().token(BOT_TOKEN).post_init(_post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cmds", cmds_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("setsecret", setsecret_cmd))
    app.add_handler(CommandHandler("match", match_cmd))
    app.add_handler(CommandHandler("ship", ship_cmd))
    app.add_handler(CommandHandler("compat", compat_cmd))

    # Inline button
    app.add_handler(CallbackQueryHandler(reroll_cb, pattern=r"^reroll:"))

    # Hints
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_hint))

    # Error handler
    app.add_error_handler(on_error)

    log.info("LoveMatch bot running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
