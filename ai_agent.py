"""
ia_agent.py — v4 MySQL
"""

import os
import re
import uuid
import httpx
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ContextTypes
from fastapi import APIRouter, HTTPException, Request

from db import get_db   # ← pool MySQL

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

AGENT_URL     = "http://3.95.218.71/app2"
AGENT_API_KEY = "change_moi_par_un_secret_fort"
AGENT_TIMEOUT = 200

MEDIA_DIR = Path("media")

MARKER_TESTIMONIAL = "[TESTIMONIAL]"
MARKER_NEED_INFO   = re.compile(r"\[NEED_INFO:([^\|]+)\|(.+?)\]")
MARKER_ESCALADE    = re.compile(r"\[ESCALADE:(\w+)\|(.+?)\]")
MARKER_BOT_CMD     = re.compile(r"\[BOT:(/\S+)\s*(.*?)\]")

MSG_SUPPORT     = "📸 Merci pour ton envoi !\n\nNotre équipe de support a bien reçu ton message et te répondra dans la suite de la journée. 🙏"
MSG_TESTIMONIAL = "🙏 Merci beaucoup pour ce témoignage !\n\nC'est exactement ce qui nous motive chaque jour. Continue comme ça, on est fiers de toi ! 🚀"
MSG_ESCALADE    = "Je comprends ta demande, mais cette situation nécessite l'intervention d'un membre de notre équipe.\n\nUn admin va te contacter très prochainement. 🙏"
MSG_FALLBACK    = "Je n'ai pas bien saisi ta demande. Peux-tu me la reformuler différemment ? 😊"
MSG_ERROR       = "⚠️ Une erreur est survenue de mon côté. Réessaie dans un instant ou contacte le support. 🙏"

BOT_WEBHOOK_KEY = os.getenv("BOT_WEBHOOK_KEY", "")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DB
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now().isoformat()


def get_conversation_state(user_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT ia_enabled, is_blocked FROM conversations WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    if row:
        return {"ia_enabled": int(row["ia_enabled"]), "is_blocked": int(row["is_blocked"])}
    return {"ia_enabled": 1, "is_blocked": 0}


def ensure_user(user_id: int, first_name: str = "", username: str = ""):
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT IGNORE INTO users (telegram_id, name, phone, created_at)
                VALUES (?, ?, '0000', NOW())
            """, (user_id, first_name or username or "inconnu"))
    except Exception as e:
        print(f"[ensure_user] {e}")


def ensure_conversation(user_id: int):
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT IGNORE INTO conversations (user_id, created_at, updated_at)
                VALUES (?, NOW(), NOW())
            """, (user_id,))
            conn.execute("""
                UPDATE conversations
                SET last_activity = NOW(), updated_at = NOW(),
                    unread_count  = unread_count + 1
                WHERE user_id = ?
            """, (user_id,))
    except Exception as e:
        print(f"[ensure_conversation] {e}")


def save_message(user_id, message_id, message_text, answer=None,
                 message_type="text", media_url=None, direction="inbound",
                 answered_by=None, requires_admin=0, is_testimonial=0,
                 ia_enabled=0, status="received") -> int:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, answer, created_at,
                 media_url, status, direction, answered_by,
                 message_type, ia_enabled, requires_admin, is_testimonial)
            VALUES (?, ?, ?, ?, NOW(), ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, message_id, message_text or "", answer,
              media_url, status, direction, answered_by,
              message_type, ia_enabled, requires_admin, is_testimonial))
        row_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]
    return row_id


def save_outbound(user_id, text, answered_by="ia", is_testimonial=0, requires_admin=0):
    save_message(user_id=user_id, message_id=None, message_text=text,
                 message_type="text", direction="outbound", answered_by=answered_by,
                 ia_enabled=1, status="sent", is_testimonial=is_testimonial,
                 requires_admin=requires_admin)


def update_inbound(msg_id, answer, answered_by="ia", status="sent",
                   requires_admin=0, is_testimonial=0):
    with get_db() as conn:
        conn.execute("""
            UPDATE messages
            SET answer=?, answered_by=?, status=?, requires_admin=?, is_testimonial=?
            WHERE id=?
        """, (answer, answered_by, status, requires_admin, is_testimonial, msg_id))


# ══════════════════════════════════════════════════════════════════════════════
# PARSING DE LA RÉPONSE AGENT  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

def parse_agent_response(result: dict) -> dict:
    action     = result.get("action", "ai_response")
    raw        = result.get("response") or ""
    relevant   = result.get("relevant", True)
    categories = result.get("categories", [])
    bot_cmd    = result.get("bot_command")

    out = {"action": action, "response": raw, "is_testimonial": False,
           "need_info": None, "escalation": None, "bot_command": bot_cmd,
           "relevant": relevant, "categories": categories}

    if not raw:
        return out

    if MARKER_TESTIMONIAL in raw:
        out["is_testimonial"] = True
        out["action"]         = "testimonial"
        out["response"]       = raw.replace(MARKER_TESTIMONIAL, "").strip()
        return out

    need_match = MARKER_NEED_INFO.search(raw)
    if need_match:
        out["action"]    = "need_info"
        out["need_info"] = {"field": need_match.group(1).strip(), "question": need_match.group(2).strip()}
        out["response"]  = need_match.group(2).strip()
        return out

    if action != "escalation":
        esc_match = MARKER_ESCALADE.search(raw)
        if esc_match:
            out["action"]     = "escalation"
            out["response"]   = None
            out["escalation"] = {"code": esc_match.group(1), "detail": esc_match.group(2)}
            return out

    if action == "escalation":
        out["response"]   = None
        out["escalation"] = {"code": "escalation", "detail": raw}
        return out

    bot_match = MARKER_BOT_CMD.search(raw)
    if bot_match and not bot_cmd:
        out["bot_command"] = {"command": bot_match.group(1), "params": bot_match.group(2).strip()}
        out["response"]    = MARKER_BOT_CMD.sub("", raw).strip()

    return out


# ══════════════════════════════════════════════════════════════════════════════
# APPEL AGENT IA
# ══════════════════════════════════════════════════════════════════════════════

async def call_agent(user_id, chat_id, text, message_id,
                     first_name="", username="", chat_type="private") -> dict:
    payload = {"chat_id": chat_id, "user_id": user_id, "text": text,
               "username": username, "first_name": first_name,
               "message_id": message_id, "chat_type": chat_type, "message_thread_id": None}
    headers = {"Content-Type": "application/json", "X-API-Key": AGENT_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
            r = await client.post(f"{AGENT_URL}/process", headers=headers, json=payload)
            if r.status_code == 202:
                return {"ok": True, "action": "async", "response": None}
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        return {"ok": True, "action": "async", "response": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT MÉDIA
# ══════════════════════════════════════════════════════════════════════════════

async def _download_media(bot, file_id: str, ext: str) -> str | None:
    try:
        MEDIA_DIR.mkdir(exist_ok=True)
        path = MEDIA_DIR / f"{uuid.uuid4()}{ext}"
        f    = await bot.get_file(file_id)
        await f.download_to_drive(str(path))
        return str(path)
    except Exception as e:
        print(f"[_download_media] {e}")
        return None


def _detect_media_type(msg) -> tuple[str, str, str | None]:
    if msg.photo:     return "image",    msg.photo[-1].file_id, ".jpg"
    if msg.video:     return "video",    msg.video.file_id,     ".mp4"
    if msg.voice:     return "voice",    msg.voice.file_id,     ".ogg"
    if msg.audio:
        ext = Path(msg.audio.file_name or "").suffix.lower() or ".mp3"
        return "audio", msg.audio.file_id, ext
    if msg.document:
        fname = msg.document.file_name or ""; ext = Path(fname).suffix.lower() or ".bin"
        mime  = msg.document.mime_type or ""
        if "pdf" in mime:                               mtype = "pdf"
        elif "word" in mime or ext in (".doc",".docx"): mtype = "word"
        elif "excel" in mime or ext in (".xls",".xlsx"): mtype = "excel"
        elif "powerpoint" in mime or ext in (".ppt",".pptx"): mtype = "powerpoint"
        else:                                           mtype = "document"
        return mtype, msg.document.file_id, ext
    if msg.sticker:   return "sticker", None, None
    return "other", None, None


# ══════════════════════════════════════════════════════════════════════════════
# EXÉCUTION COMMANDE BOT
# ══════════════════════════════════════════════════════════════════════════════

async def execute_bot_command(bot_command, user_id, chat_id, bot):
    command = bot_command.get("command", "")
    params  = bot_command.get("params", "")
    print(f"[BOT_CMD] {command} '{params}' — user {user_id}")


# ══════════════════════════════════════════════════════════════════════════════
# TRAITEMENT TEXTE VIA AGENT
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_text_via_agent(user_id, chat_id, message_id, text,
                                  first_name, username, chat_type,
                                  msg_db_id, bot, is_media_caption=False) -> dict:
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    raw_result = await call_agent(user_id=user_id, chat_id=chat_id, text=text,
                                   message_id=message_id, first_name=first_name,
                                   username=username, chat_type=chat_type)

    ok     = raw_result.get("ok", False)
    action = raw_result.get("action")

    if not ok:
        update_inbound(msg_db_id, MSG_ERROR, "error", "error")
        save_outbound(user_id, MSG_ERROR, "error")
        await bot.send_message(chat_id=chat_id, text=MSG_ERROR)
        return {"action": "error"}

    if action == "async":
        update_inbound(msg_db_id, "", "ia_async", "pending")
        return {"action": "async"}

    if action == "ignored":
        update_inbound(msg_db_id, "", "ia", "ignored")
        return {"action": "ignored"}

    parsed    = parse_agent_response(raw_result)
    p_action  = parsed["action"]
    p_response = parsed["response"]
    relevant  = parsed["relevant"]

    if p_action == "escalation":
        update_inbound(msg_db_id, MSG_ESCALADE, "ia_escalade", "sent", requires_admin=1)
        save_outbound(user_id, MSG_ESCALADE, "ia_escalade", requires_admin=1)
        await bot.send_message(chat_id=chat_id, text=MSG_ESCALADE)
        return parsed

    if p_action == "testimonial":
        update_inbound(msg_db_id, MSG_TESTIMONIAL, "ia", "sent", is_testimonial=1)
        save_outbound(user_id, MSG_TESTIMONIAL, "ia", is_testimonial=1)
        await bot.send_message(chat_id=chat_id, text=MSG_TESTIMONIAL)
        return parsed

    if p_action == "need_info":
        question = parsed["need_info"]["question"]
        update_inbound(msg_db_id, question, "ia", "sent")
        save_outbound(user_id, question, "ia")
        await bot.send_message(chat_id=chat_id, text=question)
        return parsed

    if not relevant:
        update_inbound(msg_db_id, MSG_FALLBACK, "ia", "sent")
        save_outbound(user_id, MSG_FALLBACK, "ia")
        await bot.send_message(chat_id=chat_id, text=MSG_FALLBACK)
        return parsed

    if p_response:
        update_inbound(msg_db_id, p_response, "ia", "sent")
        save_outbound(user_id, p_response, "ia")
        await bot.send_message(chat_id=chat_id, text=p_response, reply_to_message_id=message_id)
        if parsed.get("bot_command"):
            await execute_bot_command(parsed["bot_command"], user_id, chat_id, bot)
    else:
        update_inbound(msg_db_id, MSG_ERROR, "error", "error")
        save_outbound(user_id, MSG_ERROR, "error")
        await bot.send_message(chat_id=chat_id, text=MSG_ERROR)

    return parsed


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def log_unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; msg = update.message
    if not user or not msg: return

    user_id    = user.id; chat_id = msg.chat.id; message_id = msg.message_id
    first_name = user.first_name or ""; username = user.username or ""
    chat_type  = msg.chat.type or "private"
    text       = msg.text or None; caption = msg.caption or None

    ensure_user(user_id, first_name, username)
    conv = get_conversation_state(user_id)

    if conv["is_blocked"]:
        return

    ensure_conversation(user_id)

    if not conv["ia_enabled"]:
        save_message(user_id=user_id, message_id=message_id,
                     message_text=text or caption or f"[{_detect_media_type(msg)[0]}]",
                     message_type="text" if text else _detect_media_type(msg)[0],
                     direction="inbound", ia_enabled=0, requires_admin=1, status="received")
        return

    # CAS 1 — TEXTE PUR
    if msg.text and not any([msg.photo, msg.video, msg.document, msg.audio, msg.voice, msg.sticker]):
        msg_db_id = save_message(user_id=user_id, message_id=message_id, message_text=text,
                                  message_type="text", direction="inbound", ia_enabled=1)
        return
        await _handle_text_via_agent(
            user_id=user_id, chat_id=chat_id, message_id=message_id, text=text,
            first_name=first_name, username=username, chat_type=chat_type,
            msg_db_id=msg_db_id, bot=context.bot)
        return

    # CAS 2 — STICKER
    if msg.sticker:
        save_message(user_id=user_id, message_id=message_id, message_text="[sticker]",
                     message_type="sticker", direction="inbound", ia_enabled=0)
        return

    # CAS 3 — MÉDIA
    mtype, file_id, ext = _detect_media_type(msg)
    media_url = None
    if file_id and ext:
        media_url = await _download_media(context.bot, file_id, ext)

    if caption:
        msg_db_id = save_message(user_id=user_id, message_id=message_id, message_text=caption,
                                  message_type=mtype, media_url=media_url,
                                  direction="inbound", ia_enabled=1)
        parsed = await _handle_text_via_agent(
            user_id=user_id, chat_id=chat_id, message_id=message_id, text=caption,
            first_name=first_name, username=username, chat_type=chat_type,
            msg_db_id=msg_db_id, bot=context.bot, is_media_caption=True)

        if parsed.get("is_testimonial") or parsed.get("action") == "testimonial":
            with get_db() as conn:
                conn.execute("UPDATE messages SET is_testimonial=1 WHERE id=?", (msg_db_id,))

        if parsed.get("action") not in ("testimonial", "ai_response", "need_info"):
            with get_db() as conn:
                conn.execute("UPDATE messages SET requires_admin=1 WHERE id=?", (msg_db_id,))
        return

    save_message(user_id=user_id, message_id=message_id, message_text=f"[{mtype}]",
                 answer=MSG_SUPPORT, message_type=mtype, media_url=media_url,
                 direction="inbound", answered_by="ia", requires_admin=1,
                 ia_enabled=0, status="sent")
    save_outbound(user_id, MSG_SUPPORT, "ia")
    await msg.reply_text(MSG_SUPPORT)


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK AGENT → BOT
# ══════════════════════════════════════════════════════════════════════════════

agent_response_router = APIRouter()
_bot_instance = None


def set_bot(bot):
    global _bot_instance
    _bot_instance = bot


@agent_response_router.post("/agent-response")
async def agent_response_webhook(request: Request):
    if BOT_WEBHOOK_KEY:
        if request.headers.get("X-API-Key", "") != BOT_WEBHOOK_KEY:
            raise HTTPException(status_code=401, detail="Clé invalide")

    body       = await request.json()
    chat_id    = body.get("chat_id"); user_id = body.get("user_id")
    message_id = body.get("message_id"); response = body.get("response")
    relevant   = body.get("relevant", True)

    if not chat_id or not _bot_instance:
        return {"ok": False, "reason": "bot non initialisé ou chat_id manquant"}

    parsed = parse_agent_response(body)
    text_to_send = None

    if parsed["action"] == "escalation" or response is None:
        with get_db() as conn:
            conn.execute("""
                UPDATE messages SET requires_admin=1, answered_by='ia_escalade', status='sent'
                WHERE user_id=? AND message_id=? AND status='pending'
            """, (user_id, message_id))
        return {"ok": True}

    elif parsed["action"] == "testimonial":
        text_to_send = MSG_TESTIMONIAL
        save_outbound(user_id, MSG_TESTIMONIAL, "ia", is_testimonial=1)
    elif parsed["action"] == "need_info":
        text_to_send = parsed["need_info"]["question"]
        save_outbound(user_id, text_to_send, "ia")
    elif not relevant:
        text_to_send = MSG_FALLBACK
        save_outbound(user_id, MSG_FALLBACK, "ia")
    elif parsed["response"]:
        text_to_send = parsed["response"]
        save_outbound(user_id, text_to_send, "ia")

    if text_to_send:
        try:
            await _bot_instance.send_message(chat_id=chat_id, text=text_to_send,
                                              reply_to_message_id=message_id)
            with get_db() as conn:
                conn.execute("""
                    UPDATE messages SET answer=?, answered_by='ia', status='sent', is_testimonial=?
                    WHERE user_id=? AND message_id=? AND status='pending'
                """, (text_to_send, 1 if parsed["action"]=="testimonial" else 0, user_id, message_id))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if parsed.get("bot_command") and _bot_instance:
        await execute_bot_command(parsed["bot_command"], user_id, chat_id, _bot_instance)

    return {"ok": True}