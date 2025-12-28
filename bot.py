import os
import re
import hashlib
import random
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import load_dotenv
from telegram import Update, User
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("ENV BOT_TOKEN belum diisi (BOT_TOKEN).")

# Seed default dari env, bisa dioverride runtime via /setsecret
SEED_SECRET = os.getenv("SEED_SECRET", "match-secret").strip()

MENTION_RE = re.compile(r"@([A-Za-z0-9_]{4,32})")


# ---------- Utilities ----------

def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:64]

def _stable_int(secret: str, *parts: str) -> int:
    raw = "|".join([p.strip().lower() for p in parts if p is not None]) + "|" + (secret or "")
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(h[:16], 16)

def _pair_key(a: str, b: str) -> Tuple[str, str]:
    a, b = a.strip().lower(), b.strip().lower()
    return (a, b) if a <= b else (b, a)

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
    name = name.replace("<", "").replace(">", "")
    return name

def _mention_html(u: User) -> str:
    name = _display_name(u)
    return f"<a href='tg://user?id={u.id}'>{name}</a>"

@dataclass
class MatchResult:
    score: int
    label: str
    vibe: str
    reasons: list
    greens: list
    reds: list

def compute_match(secret: str, name1: str, name2: str) -> MatchResult:
    n1 = _clean(name1)
    n2 = _clean(name2)
    a, b = _pair_key(n1, n2)

    seed = _stable_int(secret, a, b)
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
        vibe = "Kalian tuh kalau chat, universe ikut ngetik."
    elif score >= 75:
        label = "Green Flag Couple"
        vibe = "Nyambungnya enak, drama minim kalau komunikasi jalan."
    elif score >= 60:
        label = "Potential, tapi perlu usaha"
        vibe = "Ada chemistry, tapi jangan males ngobrolin ekspektasi."
    elif score >= 45:
        label = "50:50"
        vibe = "Bisa jadi, bisa bubar. Tergantung kalian—bukan ramalan."
    else:
        label = "Hati-hati"
        vibe = "Bukan nggak bisa, cuma rawan salah paham dan capek sendiri."

    reason_pool = [
        "cara mikir kalian komplementer, bukan tabrakan",
        "satu lebih spontan, satu lebih rapi—kalau saling ngerti jadi kuat",
        "kalian sama-sama bisa jadi tempat pulang (kalau ego nggak menang)",
        "chemistry ada, tinggal konsistenin effort",
        "ritme komunikasi cocok: nggak kebanyakan, nggak kelamaan ngilang",
        "humor kalian sefrekuensi (yang penting jangan saling roasting kebablasan)",
        "dua-duanya perlu clear soal batasan biar aman",
        "kalau ngambek, jangan silent treatment—ini titik rawan kalian",
    ]
    green_pool = [
        "supportive",
        "jujur (kalau berani)",
        "cepat baikan",
        "saling ngingetin tanpa ngegas",
        "nyaman jadi diri sendiri",
        "bisa teamwork",
    ]
    red_pool = [
        "overthinking",
        "gengsi minta maaf",
        "suka asumsi",
        "posesif kalau insecure",
        "baperan pas capek",
        "komunikasi putus-nyambung",
    ]

    reasons = _pick(rng, reason_pool, 3 if score >= 70 else 2)
    greens = _pick(rng, green_pool, 3 if score >= 75 else 2)
    reds = _pick(rng, red_pool, 2 if score >= 60 else 3)

    return MatchResult(score=score, label=label, vibe=vibe, reasons=reasons, greens=greens, reds=reds)

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

def _format_result(title1: str, title2: str, r: MatchResult) -> str:
    greens = ", ".join(r.greens)
    reds = ", ".join(r.reds)
    reasons = "\n".join([f"• {x}" for x in r.reasons])

    bar_len = 10
    filled = round((r.score / 100) * bar_len)
    meter = "█" * filled + "░" * (bar_len - filled)

    return (
        f"<b>💘 Love Match</b>\n"
        f"{title1} <b>×</b> {title2}\n\n"
        f"<b>Skor:</b> <code>{r.score}%</code>  {meter}\n"
        f"<b>Status:</b> {r.label}\n"
        f"<i>{r.vibe}</i>\n\n"
        f"<b>Kenapa bisa gitu?</b>\n{reasons}\n\n"
        f"<b>Green flags:</b> {greens}\n"
        f"<b>Red flags:</b> {reds}\n"
    )

def _help_text() -> str:
    return (
        "<b>Perintah Bot Kecocokan Cinta</b>\n\n"
        "• <code>/match</code> — reply orangnya, terus /match\n"
        "• <code>/match @username</code> — match kamu vs username\n"
        "• <code>/ship Asep x Iyann</code> — match dua nama\n"
        "• <code>/compat Asep & Iyann</code> — sama kayak /ship\n"
        "• <code>/cmds</code> — list singkat\n"
        "• <code>/ping</code> — cek bot hidup\n"
        "• <code>/about</code> — info bot\n"
        "• <code>/setsecret teks</code> — ganti “versi hasil” (opsional)\n\n"
        "Catatan: ini hiburan. Jangan dipakai buat sidang skripsi."
    )


# ---------- Commands ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_help_text(), parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_help_text(), parse_mode=ParseMode.HTML)

async def cmds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>Commands</b>\n"
        "• /match (reply) | /match @user\n"
        "• /ship A x B | /compat A & B\n"
        "• /ping | /about | /help\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("pong")

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>About</b>\n"
        "Bot kecocokan cinta deterministik (hasil stabil untuk pasangan yang sama).\n"
        "Kamu bisa ubah “versi hasil” pakai /setsecret."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def setsecret_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global SEED_SECRET
    msg = update.effective_message
    if not msg:
        return

    arg = (context.args[0] if context.args else "").strip()
    if not arg:
        await msg.reply_text("Pakai: <code>/setsecret kata_rahasia</code>", parse_mode=ParseMode.HTML)
        return

    # Simpen hanya di memory runtime (restart bot = balik ke env)
    SEED_SECRET = _clean(arg)
    await msg.reply_text(f"OK. Secret diganti jadi: <code>{SEED_SECRET}</code>", parse_mode=ParseMode.HTML)

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
        await msg.reply_text(
            "Cara pakenya:\n"
            "• reply orangnya lalu <code>/match</code>\n"
            "• atau <code>/match @username</code>\n"
            "• atau <code>/ship Nama1 x Nama2</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    name1 = _display_name(me)
    title1 = _mention_html(me)

    if target:
        name2 = _display_name(target)
        title2 = _mention_html(target)
    else:
        name2 = typed_username
        safe = typed_username.replace("<", "").replace(">", "")
        title2 = f"@{safe}"

    r = compute_match(SEED_SECRET, name1, name2)
    await msg.reply_text(_format_result(title1, title2, r), parse_mode=ParseMode.HTML)

async def ship_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    pair = _extract_two_names(msg.text or "")
    if not pair:
        await msg.reply_text(
            "Format:\n"
            "• <code>/ship Asep x Iyann</code>\n"
            "• <code>/compat Asep & Iyann</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    a, b = pair
    r = compute_match(SEED_SECRET, a, b)
    title1 = a.replace("<", "").replace(">", "")
    title2 = b.replace("<", "").replace(">", "")
    await msg.reply_text(_format_result(title1, title2, r), parse_mode=ParseMode.HTML)

async def compat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ship_cmd(update, context)

async def text_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return
    t = msg.text.lower()
    if "kecocokan" in t or "cocok" in t or "ship" in t:
        if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            if random.random() < 0.06:
                await msg.reply_text("Coba /match (reply orangnya) atau /ship Nama1 x Nama2.")

def main() -> None:
    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cmds", cmds_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("setsecret", setsecret_cmd))

    app.add_handler(CommandHandler("match", match_cmd))
    app.add_handler(CommandHandler("ship", ship_cmd))
    app.add_handler(CommandHandler("compat", compat_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_hint))

    print("LoveMatch bot running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
