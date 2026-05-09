"""
ia_agent.py — Handler Telegram, version finale complète.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARCHITECTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bot Telegram → POST /process → Agent IA hébergé → réponse structurée

L'agent hébergé gère :
  - Sélection des prompts selon la complexité du message
  - Appel des fonctions (get_user_trades, get_user_performance, etc.)
  - Historique de conversation
  - Détection escalade [ESCALADE:code|detail]
  - Détection commande bot [BOT:/commande]
  - Détection témoignage [TESTIMONIAL]
  - Demande d'infos supplémentaires [NEED_INFO:champ|question]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FLUX COMPLET — log_unhandled_message
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GARDE-FOUS (avant tout traitement)
  → is_blocked = 1         : silence total, rien enregistrer
  → ia_enabled = 0         : enregistrer inbound, laisser admin répondre

TEXTE PUR
  → POST /process → l'agent répond avec action + marqueurs
      [TESTIMONIAL]          → is_testimonial=1 + MSG_TESTIMONIAL
      [NEED_INFO:x|question] → poser la question au user, attendre réponse
      action=ai_response + relevant=true  → réponse normale
      action=ai_response + relevant=false → MSG_FALLBACK
      action=escalation      → MSG_ESCALADE + requires_admin=1
      action=ignored         → silence
      action=async (202/timeout) → attendre webhook /agent-response
      ok=false               → MSG_ERROR

MÉDIA avec caption
  → POST /process avec le caption comme texte
      [TESTIMONIAL] → enregistrer média + is_testimonial=1 + MSG_TESTIMONIAL
      autre         → enregistrer média + requires_admin=1 + MSG_SUPPORT

MÉDIA sans caption
  → enregistrer + requires_admin=1 + MSG_SUPPORT (support humain)

STICKER / AUTRE
  → enregistrer silencieusement, aucune réponse

TRAÇABILITÉ
  → Message entrant  : 1 ligne inbound dans messages
  → Réponse IA       : 1 ligne outbound dans messages (dashboard chat)
  → Réponse admin    : idem via dashboard

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MARQUEURS QUE LE PROMPT SYSTÈME DOIT CONNAÎTRE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[TESTIMONIAL]
  → Le message est un témoignage positif d'un membre.
  → Placer ce marqueur au début de la réponse.
  → Exemple : "[TESTIMONIAL] Super témoignage !"
  → Le bot détecte le marqueur, flag is_testimonial=1, répond avec MSG_TESTIMONIAL.

[NEED_INFO:champ|question posée à l'utilisateur]
  → L'IA a besoin d'une information pour répondre correctement.
  → Exemple : "[NEED_INFO:capital|Quel est ton capital actuel en $?]"
  → Le bot envoie la question au user et attend sa réponse au prochain message.
  → L'agent gère la suite via l'historique de conversation.

[ESCALADE:code|description]
  → Codes : hors_sujet | sensible | acces_refuse | inconnu
  → Exemple : "[ESCALADE:sensible|Question nécessitant validation humaine]"

[BOT:/commande params]
  → Exemple : "[BOT:/jemenregistre]"
  → Le marqueur est retiré de la réponse avant envoi au user.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VARIABLES .env
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT_URL       = http://44.201.200.160/app2
AGENT_API_KEY   = ta_cle_api
BOT_WEBHOOK_KEY = cle_webhook_agent
"""

import os
import re
import uuid
import sqlite3
import httpx
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ContextTypes
from fastapi import APIRouter, HTTPException, Request

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

#AGENT_URL       = os.getenv("AGENT_URL",       "http://44.201.200.160/app2")
#AGENT_API_KEY   = os.getenv("AGENT_API_KEY",   "")
#BOT_WEBHOOK_KEY = os.getenv("BOT_WEBHOOK_KEY", "")

AGENT_URL      = "http://3.95.218.71/app2"
AGENT_API_KEY  = "change_moi_par_un_secret_fort"
AGENT_TIMEOUT   = 200   # 180s max selon la doc + marge de sécurité

DB_PATH   = "preinscriptions.db"
MEDIA_DIR = Path("media")

# ── Marqueurs retournés par l'agent dans la réponse ───────────────────────────
MARKER_TESTIMONIAL = "[TESTIMONIAL]"
MARKER_NEED_INFO   = re.compile(r"\[NEED_INFO:([^\|]+)\|(.+?)\]")
MARKER_ESCALADE    = re.compile(r"\[ESCALADE:(\w+)\|(.+?)\]")
MARKER_BOT_CMD     = re.compile(r"\[BOT:(/\S+)\s*(.*?)\]")

# ── Messages fixes envoyés au user ────────────────────────────────────────────
MSG_SUPPORT = (
    "📸 Merci pour ton envoi !\n\n"
    "Notre équipe de support a bien reçu ton message et te répondra "
    "dans la suite de la journée. 🙏"
)

MSG_TESTIMONIAL = (
    "🙏 Merci beaucoup pour ce témoignage !\n\n"
    "C'est exactement ce qui nous motive chaque jour. "
    "Continue comme ça, on est fiers de toi ! 🚀"
)

MSG_ESCALADE = (
    "Je comprends ta demande, mais cette situation nécessite "
    "l'intervention d'un membre de notre équipe.\n\n"
    "Un admin va te contacter très prochainement. 🙏"
)

MSG_FALLBACK = (
    "Je n'ai pas bien saisi ta demande. "
    "Peux-tu me la reformuler différemment ? 😊"
)

MSG_ERROR = (
    "⚠️ Une erreur est survenue de mon côté. "
    "Réessaie dans un instant ou contacte le support. 🙏"
)

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DB
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def get_conversation_state(user_id: int) -> dict:
    """
    Retourne ia_enabled et is_blocked pour ce user.
    Défaut : ia_enabled=1, is_blocked=0 (conversation pas encore créée).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT ia_enabled, is_blocked FROM conversations WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    finally:
        conn.close()

    if row:
        return {
            "ia_enabled": int(row["ia_enabled"]),
            "is_blocked": int(row["is_blocked"]),
        }
    return {"ia_enabled": 1, "is_blocked": 0}


def ensure_conversation(user_id: int):
    """Crée la conversation si elle n'existe pas, incrémente unread_count."""
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO conversations (user_id, created_at, updated_at)
            VALUES (?, ?, ?)
        """, (user_id, _now(), _now()))
        conn.execute("""
            UPDATE conversations
            SET last_activity = ?, updated_at = ?,
                unread_count  = unread_count + 1
            WHERE user_id = ?
        """, (_now(), _now(), user_id))
        conn.commit()
    except Exception as e:
        print(f"[ensure_conversation] {e}")
    finally:
        conn.close()


def save_message(
    user_id:        int,
    message_id:     int | None,
    message_text:   str | None,
    answer:         str | None  = None,
    message_type:   str         = "text",
    media_url:      str | None  = None,
    direction:      str         = "inbound",
    answered_by:    str | None  = None,
    requires_admin: int         = 0,
    is_testimonial: int         = 0,
    ia_enabled:     int         = 0,
    status:         str         = "received",
) -> int:
    """Enregistre un message (entrant ou sortant) et retourne son id."""
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, answer, created_at,
                 media_url, status, direction, answered_by,
                 message_type, ia_enabled, requires_admin, is_testimonial)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, message_id, message_text or "", answer,
            _now(), media_url, status, direction, answered_by,
            message_type, ia_enabled, requires_admin, is_testimonial,
        ))
        row_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return row_id


def save_outbound(
    user_id:        int,
    text:           str,
    answered_by:    str = "ia",
    is_testimonial: int = 0,
    requires_admin: int = 0,
):
    """
    Enregistre la réponse IA comme message OUTBOUND séparé.
    Nécessaire pour que le dashboard chat affiche le fil complet
    (message entrant + réponse sortante).
    """
    save_message(
        user_id=user_id,
        message_id=None,
        message_text=text,
        message_type="text",
        direction="outbound",
        answered_by=answered_by,
        ia_enabled=1,
        status="sent",
        is_testimonial=is_testimonial,
        requires_admin=requires_admin,
    )


def update_inbound(
    msg_id:         int,
    answer:         str,
    answered_by:    str = "ia",
    status:         str = "sent",
    requires_admin: int = 0,
    is_testimonial: int = 0,
):
    """Met à jour le champ answer du message inbound (référence croisée)."""
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE messages
            SET answer = ?, answered_by = ?, status = ?,
                requires_admin = ?, is_testimonial = ?
            WHERE id = ?
        """, (answer, answered_by, status, requires_admin, is_testimonial, msg_id))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PARSING DE LA RÉPONSE AGENT
# ══════════════════════════════════════════════════════════════════════════════

def parse_agent_response(result: dict) -> dict:
    """
    Analyse la réponse complète de l'agent et retourne un dict normalisé.

    Détecte dans result["response"] :
      [TESTIMONIAL]
      [NEED_INFO:champ|question]
      [ESCALADE:code|detail]    (doublon sécurité si action != "escalation")
      [BOT:/commande params]

    Retourne :
    {
      "action":        str,         # action finale normalisée
      "response":      str | None,  # texte propre à envoyer au user
      "is_testimonial": bool,
      "need_info":     {"field": str, "question": str} | None,
      "escalation":    {"code": str, "detail": str} | None,
      "bot_command":   {"command": str, "params": str} | None,
      "relevant":      bool,
      "categories":    list,
    }
    """
    action     = result.get("action", "ai_response")
    raw        = result.get("response") or ""
    relevant   = result.get("relevant", True)
    categories = result.get("categories", [])
    bot_cmd    = result.get("bot_command")  # déjà parsé par l'agent

    out = {
        "action":         action,
        "response":       raw,
        "is_testimonial": False,
        "need_info":      None,
        "escalation":     None,
        "bot_command":    bot_cmd,
        "relevant":       relevant,
        "categories":     categories,
    }

    if not raw:
        return out

    # ── [TESTIMONIAL] ─────────────────────────────────────────────────────────
    if MARKER_TESTIMONIAL in raw:
        out["is_testimonial"] = True
        out["action"]         = "testimonial"
        # Retirer le marqueur du texte
        out["response"] = raw.replace(MARKER_TESTIMONIAL, "").strip()
        return out

    # ── [NEED_INFO:champ|question] ────────────────────────────────────────────
    need_match = MARKER_NEED_INFO.search(raw)
    if need_match:
        out["action"]    = "need_info"
        out["need_info"] = {
            "field":    need_match.group(1).strip(),
            "question": need_match.group(2).strip(),
        }
        # La réponse = uniquement la question posée à l'user
        out["response"] = need_match.group(2).strip()
        return out

    # ── [ESCALADE:code|detail] ────────────────────────────────────────────────
    # (normalement déjà géré par l'agent via action="escalation",
    #  mais on double-vérifie au cas où il est dans le texte)
    if action != "escalation":
        esc_match = MARKER_ESCALADE.search(raw)
        if esc_match:
            out["action"]     = "escalation"
            out["response"]   = None
            out["escalation"] = {
                "code":   esc_match.group(1),
                "detail": esc_match.group(2),
            }
            return out

    if action == "escalation":
        out["response"]   = None
        out["escalation"] = {"code": "escalation", "detail": raw}
        return out

    # ── [BOT:/commande] ───────────────────────────────────────────────────────
    # L'agent le retourne déjà dans bot_command, mais on parse aussi
    # au cas où il serait dans le texte brut
    bot_match = MARKER_BOT_CMD.search(raw)
    if bot_match and not bot_cmd:
        out["bot_command"] = {
            "command": bot_match.group(1),
            "params":  bot_match.group(2).strip(),
        }
        out["response"] = MARKER_BOT_CMD.sub("", raw).strip()

    return out


# ══════════════════════════════════════════════════════════════════════════════
# APPEL AGENT IA HÉBERGÉ
# ══════════════════════════════════════════════════════════════════════════════

async def call_agent(
    user_id:    int,
    chat_id:    int,
    text:       str,
    message_id: int,
    first_name: str = "",
    username:   str = "",
    chat_type:  str = "private",
) -> dict:
    """
    POST /process sur l'agent IA hébergé.

    Retourne le JSON brut de l'agent, ou :
      {"ok": True,  "action": "async"}  → 202 ou timeout, webhook prendra le relais
      {"ok": False, "error": "..."}     → erreur réseau/agent
    """
    payload = {
        "chat_id":           chat_id,
        "user_id":           user_id,
        "text":              text,
        "username":          username,
        "first_name":        first_name,
        "message_id":        message_id,
        "chat_type":         chat_type,
        "message_thread_id": None,
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-Key":    AGENT_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
            r = await client.post(
                f"{AGENT_URL}/process",
                headers=headers,
                json=payload,
            )
            if r.status_code == 202:
                return {"ok": True, "action": "async", "response": None}
            r.raise_for_status()
            return r.json()

    except httpx.TimeoutException:
        print(f"[call_agent] Timeout — user {user_id} → webhook prendra le relais")
        return {"ok": True, "action": "async", "response": None}

    except Exception as e:
        print(f"[call_agent] Erreur : {e}")
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT MÉDIA
# ══════════════════════════════════════════════════════════════════════════════

async def _download_media(bot, file_id: str, ext: str) -> str | None:
    """Télécharge un fichier Telegram dans /media/ et retourne le chemin local."""
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
    """
    Retourne (message_type, file_id, ext) pour un message média.
    file_id = None si pas de fichier téléchargeable (sticker, other).
    """
    if msg.photo:
        return "image", msg.photo[-1].file_id, ".jpg"
    if msg.video:
        return "video", msg.video.file_id, ".mp4"
    if msg.voice:
        return "voice", msg.voice.file_id, ".ogg"
    if msg.audio:
        ext = Path(msg.audio.file_name or "").suffix.lower() or ".mp3"
        return "audio", msg.audio.file_id, ext
    if msg.document:
        fname = msg.document.file_name or ""
        ext   = Path(fname).suffix.lower() or ".bin"
        mime  = msg.document.mime_type or ""
        if "pdf" in mime:                              mtype = "pdf"
        elif "word" in mime or ext in (".doc",".docx"): mtype = "word"
        elif "excel" in mime or ext in (".xls",".xlsx"): mtype = "excel"
        elif "powerpoint" in mime or ext in (".ppt",".pptx"): mtype = "powerpoint"
        else:                                          mtype = "document"
        return mtype, msg.document.file_id, ext
    if msg.sticker:
        return "sticker", None, None
    return "other", None, None


# ══════════════════════════════════════════════════════════════════════════════
# EXÉCUTION COMMANDE BOT
# ══════════════════════════════════════════════════════════════════════════════

async def execute_bot_command(
    bot_command: dict,
    user_id:     int,
    chat_id:     int,
    bot,
):
    """
    Exécute une commande bot retournée par l'agent.
    bot_command = {"command": "/jemenregistre", "params": ""}

    Ajoute ici tes handlers selon les commandes disponibles.
    """
    command = bot_command.get("command", "")
    params  = bot_command.get("params", "")
    print(f"[BOT_CMD] {command} '{params}' — user {user_id}")

    # ── Branche tes commandes ici ──────────────────────────────────────────
    # if command == "/jemenregistre":
    #     await enregistrer_utilisateur(user_id, chat_id, bot)
    # elif command == "/mespaiements":
    #     await afficher_paiements(user_id, chat_id, bot)
    # elif command == "/monprofil":
    #     await afficher_profil(user_id, chat_id, bot)
    # elif command == "/aide":
    #     await afficher_aide(chat_id, bot)


# ══════════════════════════════════════════════════════════════════════════════
# TRAITEMENT D'UN TEXTE VERS L'AGENT (réutilisé texte pur + caption)
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_text_via_agent(
    user_id:     int,
    chat_id:     int,
    message_id:  int,
    text:        str,
    first_name:  str,
    username:    str,
    chat_type:   str,
    msg_db_id:   int,        # id du message inbound déjà enregistré
    bot,
    is_media_caption: bool = False,  # True si on traite un caption de média
) -> dict:
    """
    Appelle l'agent, parse la réponse, gère tous les cas,
    envoie la réponse au user, sauvegarde outbound.

    Retourne le dict parsé pour que l'appelant puisse agir
    (ex : savoir si c'est un témoignage pour ajuster le média).
    """
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    raw_result = await call_agent(
        user_id=user_id, chat_id=chat_id, text=text,
        message_id=message_id, first_name=first_name,
        username=username, chat_type=chat_type,
    )

    ok     = raw_result.get("ok", False)
    action = raw_result.get("action")

    # ── Erreur agent ──────────────────────────────────────────────────────────
    if not ok:
        update_inbound(msg_db_id, MSG_ERROR, "error", "error")
        save_outbound(user_id, MSG_ERROR, "error")
        await bot.send_message(chat_id=chat_id, text=MSG_ERROR)
        print(f"✗ Erreur agent — user {user_id} | {raw_result.get('error')}")
        return {"action": "error"}

    # ── Réponse asynchrone (202 / timeout) ────────────────────────────────────
    if action == "async":
        update_inbound(msg_db_id, "", "ia_async", "pending")
        print(f"⏳ Async — user {user_id} (webhook prendra le relais)")
        return {"action": "async"}

    # ── Utilisateur ignoré (banni/muet côté agent) ───────────────────────────
    if action == "ignored":
        update_inbound(msg_db_id, "", "ia", "ignored")
        print(f"🔇 Ignoré par l'agent — user {user_id}")
        return {"action": "ignored"}

    # ── Parser la réponse ─────────────────────────────────────────────────────
    parsed = parse_agent_response(raw_result)
    p_action  = parsed["action"]
    p_response = parsed["response"]
    relevant   = parsed["relevant"]

    # ── Escalade ──────────────────────────────────────────────────────────────
    if p_action == "escalation":
        update_inbound(msg_db_id, MSG_ESCALADE, "ia_escalade", "sent", requires_admin=1)
        save_outbound(user_id, MSG_ESCALADE, "ia_escalade", requires_admin=1)
        await bot.send_message(chat_id=chat_id, text=MSG_ESCALADE)
        print(f"⚠ Escalade — user {user_id} | {parsed.get('escalation')}")
        return parsed

    # ── Témoignage ────────────────────────────────────────────────────────────
    if p_action == "testimonial":
        update_inbound(msg_db_id, MSG_TESTIMONIAL, "ia", "sent", is_testimonial=1)
        save_outbound(user_id, MSG_TESTIMONIAL, "ia", is_testimonial=1)
        await bot.send_message(chat_id=chat_id, text=MSG_TESTIMONIAL)
        print(f"🏆 Témoignage — user {user_id}")
        return parsed

    # ── L'agent a besoin d'une info supplémentaire ────────────────────────────
    if p_action == "need_info":
        question = parsed["need_info"]["question"]
        field    = parsed["need_info"]["field"]
        update_inbound(msg_db_id, question, "ia", "sent")
        save_outbound(user_id, question, "ia")
        await bot.send_message(chat_id=chat_id, text=question)
        print(f"❓ Need info '{field}' — user {user_id}")
        # L'agent garde le contexte : la prochaine réponse du user sera
        # traitée via POST /process avec l'historique complet,
        # l'agent saura qu'il attendait cette info.
        return parsed

    # ── Réponse non pertinente (relevant=false) ───────────────────────────────
    if not relevant:
        update_inbound(msg_db_id, MSG_FALLBACK, "ia", "sent")
        save_outbound(user_id, MSG_FALLBACK, "ia")
        await bot.send_message(chat_id=chat_id, text=MSG_FALLBACK)
        print(f"⚠ Réponse non pertinente — user {user_id}")
        return parsed

    # ── Réponse normale ───────────────────────────────────────────────────────
    if p_response:
        update_inbound(msg_db_id, p_response, "ia", "sent")
        save_outbound(user_id, p_response, "ia")
        await bot.send_message(
            chat_id=chat_id,
            text=p_response,
            reply_to_message_id=message_id,
        )

        # Exécuter commande bot si présente
        if parsed.get("bot_command"):
            await execute_bot_command(parsed["bot_command"], user_id, chat_id, bot)

        print(
            f"✓ Réponse IA — user {user_id} | "
            f"relevant={relevant} | categories={parsed.get('categories')}"
        )
    else:
        # Réponse vide inattendue
        update_inbound(msg_db_id, MSG_ERROR, "error", "error")
        save_outbound(user_id, MSG_ERROR, "error")
        await bot.send_message(chat_id=chat_id, text=MSG_ERROR)

    return parsed


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def log_unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler principal branché sur filters.ALL.
    app.add_handler(MessageHandler(filters.ALL, log_unhandled_message))
    """
    user = update.effective_user
    msg  = update.message

    if not user or not msg:
        return

    user_id    = user.id
    chat_id    = msg.chat.id
    message_id = msg.message_id
    first_name = user.first_name or ""
    username   = user.username   or ""
    chat_type  = msg.chat.type   or "private"
    text       = msg.text        or None
    caption    = msg.caption     or None

    # ══════════════════════════════════════════════════════════════════════
    # GARDE-FOUS — vérifier état de la conversation avant tout
    # ══════════════════════════════════════════════════════════════════════
    conv = get_conversation_state(user_id)

    # User bloqué → silence total (même pas enregistrer)
    if conv["is_blocked"]:
        print(f"🚫 User bloqué — user {user_id} | message ignoré")
        return

    # Créer/mettre à jour la conversation
    ensure_conversation(user_id)

    # IA désactivée par l'admin → enregistrer inbound, ne pas appeler l'agent
    if not conv["ia_enabled"]:
        save_message(
            user_id=user_id,
            message_id=message_id,
            message_text=text or caption or f"[{_detect_media_type(msg)[0]}]",
            message_type="text" if text else _detect_media_type(msg)[0],
            direction="inbound",
            ia_enabled=0,
            requires_admin=1,
            status="received",
        )
        print(f"🔕 IA désactivée pour user {user_id} — admin doit répondre")
        return

    # ══════════════════════════════════════════════════════════════════════
    # CAS 1 — TEXTE PUR (aucun média attaché)
    # ══════════════════════════════════════════════════════════════════════
    if msg.text and not any([
        msg.photo, msg.video, msg.document,
        msg.audio, msg.voice, msg.sticker,
    ]):
        msg_db_id = save_message(
            user_id=user_id,
            message_id=message_id,
            message_text=text,
            message_type="text",
            direction="inbound",
            ia_enabled=1,
        )

        await _handle_text_via_agent(
            user_id=user_id, chat_id=chat_id, message_id=message_id,
            text=text, first_name=first_name, username=username,
            chat_type=chat_type, msg_db_id=msg_db_id, bot=context.bot,
        )
        return

    # ══════════════════════════════════════════════════════════════════════
    # CAS 2 — STICKER → silencieux
    # ══════════════════════════════════════════════════════════════════════
    if msg.sticker:
        save_message(
            user_id=user_id, message_id=message_id,
            message_text="[sticker]", message_type="sticker",
            direction="inbound", ia_enabled=0,
        )
        print(f"✓ Sticker silencieux — user {user_id}")
        return

    # ══════════════════════════════════════════════════════════════════════
    # CAS 3 — MÉDIA (photo, vidéo, audio, voice, document)
    # ══════════════════════════════════════════════════════════════════════
    mtype, file_id, ext = _detect_media_type(msg)

    # Télécharger le fichier
    media_url = None
    if file_id and ext:
        media_url = await _download_media(context.bot, file_id, ext)

    # ── MÉDIA AVEC CAPTION → analyser le caption via l'agent ─────────────
    if caption:
        # Enregistrer le message entrant (média + caption)
        msg_db_id = save_message(
            user_id=user_id,
            message_id=message_id,
            message_text=caption,
            message_type=mtype,
            media_url=media_url,
            direction="inbound",
            ia_enabled=1,
        )

        # Passer le caption à l'agent (qui détectera témoignage ou autre)
        parsed = await _handle_text_via_agent(
            user_id=user_id, chat_id=chat_id, message_id=message_id,
            text=caption, first_name=first_name, username=username,
            chat_type=chat_type, msg_db_id=msg_db_id, bot=context.bot,
            is_media_caption=True,
        )

        # Si c'est un témoignage, mettre à jour le flag sur le message inbound
        if parsed.get("is_testimonial") or parsed.get("action") == "testimonial":
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE messages SET is_testimonial = 1 WHERE id = ?",
                    (msg_db_id,)
                )
                conn.commit()
            finally:
                conn.close()

        # Si ce n'est pas un témoignage et pas une réponse IA normale,
        # notifier également le support (média reçu)
        if parsed.get("action") not in ("testimonial", "ai_response", "need_info"):
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE messages SET requires_admin = 1 WHERE id = ?",
                    (msg_db_id,)
                )
                conn.commit()
            finally:
                conn.close()

        print(f"✓ Média+caption {mtype} traité — user {user_id} | action={parsed.get('action')}")
        return

    # ── MÉDIA SANS CAPTION → support humain direct ────────────────────────
    save_message(
        user_id=user_id,
        message_id=message_id,
        message_text=f"[{mtype}]",
        answer=MSG_SUPPORT,
        message_type=mtype,
        media_url=media_url,
        direction="inbound",
        answered_by="ia",
        requires_admin=1,
        ia_enabled=0,
        status="sent",
    )
    save_outbound(user_id, MSG_SUPPORT, "ia")
    await msg.reply_text(MSG_SUPPORT)
    print(f"✓ Média {mtype} sans caption → support — user {user_id}")


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK AGENT → BOT (réponse asynchrone)
# ══════════════════════════════════════════════════════════════════════════════
# Déclarer dans ton api.py :
#   from ia_agent import agent_response_router, set_bot
#   app.include_router(agent_response_router)
#   set_bot(bot_instance)
#
# Puis déclarer l'URL auprès de l'agent :
#   POST http://44.201.200.160/app2/telegram/webhook
#   { "url": "https://ton-bot.com/agent-response" }
# ══════════════════════════════════════════════════════════════════════════════

agent_response_router = APIRouter()
_bot_instance = None


def set_bot(bot):
    """Injecte l'instance bot Telegram (appeler depuis api.py)."""
    global _bot_instance
    _bot_instance = bot


@agent_response_router.post("/agent-response")
async def agent_response_webhook(request: Request):
    """
    Appelé par l'agent quand la réponse IA est prête (cas async 202/timeout).

    Body :
    {
      "chat_id":    int,
      "user_id":    int,
      "message_id": int,
      "response":   str | null,     null = escalade → ne rien envoyer
      "bot_command": dict | null,
      "relevant":   bool,
      "categories": list
    }
    """
    # Validation de la clé
    if BOT_WEBHOOK_KEY:
        if request.headers.get("X-API-Key", "") != BOT_WEBHOOK_KEY:
            raise HTTPException(status_code=401, detail="Clé invalide")

    body       = await request.json()
    chat_id    = body.get("chat_id")
    user_id    = body.get("user_id")
    message_id = body.get("message_id")
    response   = body.get("response")   # None si escalade
    relevant   = body.get("relevant", True)
    bot_cmd    = body.get("bot_command")

    if not chat_id or not _bot_instance:
        return {"ok": False, "reason": "bot non initialisé ou chat_id manquant"}

    # ── Parser la réponse async exactement comme en sync ──────────────────────
    parsed = parse_agent_response(body)

    text_to_send = None

    if parsed["action"] == "escalation" or response is None:
        # Escalade → ne rien envoyer, mettre à jour DB
        conn = get_conn()
        try:
            conn.execute("""
                UPDATE messages
                SET requires_admin = 1, answered_by = 'ia_escalade', status = 'sent'
                WHERE user_id = ? AND message_id = ? AND status = 'pending'
            """, (user_id, message_id))
            conn.commit()
        finally:
            conn.close()
        print(f"⚠ Escalade async — user {user_id}")
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

    # Envoyer le message au user
    if text_to_send:
        try:
            await _bot_instance.send_message(
                chat_id=chat_id,
                text=text_to_send,
                reply_to_message_id=message_id,
            )
            # Mettre à jour le message pending en DB
            conn = get_conn()
            try:
                conn.execute("""
                    UPDATE messages
                    SET answer = ?, answered_by = 'ia', status = 'sent',
                        is_testimonial = ?
                    WHERE user_id = ? AND message_id = ? AND status = 'pending'
                """, (
                    text_to_send,
                    1 if parsed["action"] == "testimonial" else 0,
                    user_id, message_id,
                ))
                conn.commit()
            finally:
                conn.close()

            print(f"✓ Réponse async envoyée — user {user_id} | action={parsed['action']}")

        except Exception as e:
            print(f"[agent_response_webhook] Erreur envoi : {e}")
            return {"ok": False, "error": str(e)}

    # Exécuter commande bot si présente
    if parsed.get("bot_command") and _bot_instance:
        await execute_bot_command(
            parsed["bot_command"], user_id, chat_id, _bot_instance
        )

    return {"ok": True}