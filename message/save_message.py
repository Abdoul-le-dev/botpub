from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
from telegram import Update
import os
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH   = "preinscriptions.db"
MEDIA_DIR = Path("media")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ════════════════════════════════════════════════════════════════════════
# SAVE MESSAGE — insertion en base
# ════════════════════════════════════════════════════════════════════════
def save_message(
    user_id:      int,
    message_id:   int,
    message_text: str  = None,
    answer:       str  = None,
    message_type: str  = "text",
    media_url:    str  = None,
    direction:    str  = "inbound",
    answered_by:  str  = None,
    requires_admin: int  = 0,      # ← ajouter
    is_testimonial: int  = 0,      
):
    """
    Insère un message dans la table messages.
    Le trigger trg_upsert_conv met à jour conversations automatiquement.
    """
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, answer,
                 message_type, media_url, direction, answered_by,requires_admin,is_testimonial
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?,?,?, 'received', ?)
        """, (
            user_id,
            message_id,
            message_text or "",   # NOT NULL — chaîne vide si pas de texte
            answer or "",
            message_type,
            media_url,
            direction,
            answered_by,
            requires_admin,
            is_testimonial,
            datetime.now().isoformat(),
        ))
        conn.commit()
    finally:
        conn.close()
 
 
# ════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT MÉDIA DEPUIS TELEGRAM
# ════════════════════════════════════════════════════════════════════════
 
async def _download_media(bot, file_id: str, extension: str) -> str | None:
    """
    Télécharge un fichier depuis Telegram et le stocke dans /media/.
    Retourne le chemin local /media/uuid.ext ou None si échec.
    """
    try:
        import uuid
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
 
        tg_file  = await bot.get_file(file_id)
        fname    = f"{uuid.uuid4()}{extension}"
        dest     = MEDIA_DIR / fname
 
        await tg_file.download_to_drive(str(dest))
        return f"/media/{fname}"
 
    except Exception as e:
        print(f"⚠️ Erreur téléchargement média : {e}")
        return None
 
 
# ════════════════════════════════════════════════════════════════════════
# HANDLER PRINCIPAL — tous les messages entrants
# ════════════════════════════════════════════════════════════════════════
def ensure_user_and_conversation(user_id: int):
    """
    Garantit qu'un user et sa conversation existent en base.
    Appelée à chaque message entrant avant save_message().
 
    - Si le user n'existe pas dans users
      → INSERT avec telegram_id uniquement (name="" phone="" en attendant le profil)
    - Si la conversation n'existe pas dans conversations
      → INSERT avec ia_enabled=1 par défaut
    - Si tout existe déjà → ne rien faire
    """
    now  = datetime.now().isoformat()
    conn = get_conn()
    try:
        # ── User ──────────────────────────────────────────────────────────
        existing = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
 
        if not existing:
            # On ne connaît que le telegram_id
            # name et phone sont NOT NULL → valeurs vides en attendant le profil
            conn.execute("""
                INSERT INTO users (name, phone, country, created_at, telegram_id)
                VALUES (?, ?, ?, ?, ?)
            """, ("", "", "", now, user_id))
            print(f"✓ Nouveau user enregistré : telegram_id={user_id}")
 
        # ── Conversation ──────────────────────────────────────────────────
        conv = conn.execute(
            "SELECT id FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
 
        if not conv:
            conn.execute("""
                INSERT INTO conversations (user_id, ia_enabled, unread_count, created_at, updated_at)
                VALUES (?, 1, 0, ?, ?)
            """, (user_id, now, now))
            print(f"✓ Nouvelle conversation créée : telegram_id={user_id}")
 
        conn.commit()
    finally:
        conn.close()
 
async def log_unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message
 
    if not user or not msg:
        return
 
    user_id    = user.id
    message_id = msg.message_id
    text       = msg.text or msg.caption or None  # caption = texte sur une photo/vidéo
    media_url  = None
    message_type = "text"
 
    # ── Image ────────────────────────────────────────────────────────────
    if msg.photo:
        message_type = "image"
        # Prendre la photo en meilleure résolution (dernière dans la liste)
        file_id  = msg.photo[-1].file_id
        media_url = await _download_media(context.bot, file_id, ".jpg")
 
    # ── Vidéo ────────────────────────────────────────────────────────────
    elif msg.video:
        message_type = "video"
        media_url = await _download_media(context.bot, msg.video.file_id, ".mp4")
 
    # ── Document (PDF, Word, Excel, etc.) ────────────────────────────────
    elif msg.document:
        # Déterminer l'extension depuis le nom original
        fname = msg.document.file_name or ""
        ext   = Path(fname).suffix.lower() if fname else ".bin"
 
        # Mapper le mime_type vers notre message_type
        mime = msg.document.mime_type or ""
        if mime == "application/pdf":
            message_type = "pdf"
        elif "word" in mime or ext in (".doc", ".docx"):
            message_type = "word"
        elif "excel" in mime or "spreadsheet" in mime or ext in (".xls", ".xlsx"):
            message_type = "excel"
        elif "powerpoint" in mime or "presentation" in mime or ext in (".ppt", ".pptx"):
            message_type = "powerpoint"
        elif mime == "text/plain" or ext == ".txt":
            message_type = "text_file"
        elif mime in ("application/zip", "application/x-rar-compressed") or ext in (".zip", ".rar"):
            message_type = "archive"
        else:
            message_type = "document"
 
        media_url = await _download_media(context.bot, msg.document.file_id, ext or ".bin")
 
    # ── Audio ─────────────────────────────────────────────────────────────
    elif msg.audio:
        message_type = "audio"
        ext = Path(msg.audio.file_name or "").suffix.lower() or ".mp3"
        media_url = await _download_media(context.bot, msg.audio.file_id, ext)
 
    # ── Note vocale ───────────────────────────────────────────────────────
    elif msg.voice:
        message_type = "voice"
        media_url = await _download_media(context.bot, msg.voice.file_id, ".ogg")
 
    # ── Sticker ───────────────────────────────────────────────────────────
    elif msg.sticker:
        message_type = "sticker"
        # Pas de téléchargement — juste noter le type
 
    # ── Texte pur ─────────────────────────────────────────────────────────
    elif msg.text:
        message_type = "text"
 
    # ── Autres ────────────────────────────────────────────────────────────
    else:
        message_type = "other"
 
    # ── Log si média non téléchargé ───────────────────────────────────────
    if message_type not in ("text", "sticker", "other") and media_url is None:
        print(f"⚠️ Média {message_type} non téléchargé pour user {user_id}")

    ensure_user_and_conversation(user_id)    
 
    # ── Enregistrement en base ────────────────────────────────────────────
    save_message(
        user_id      = user_id,
        message_id   = message_id,
        message_text = text,
        answer       = None,
        message_type = message_type,
        media_url    = media_url,
        direction    = "inbound",
        answered_by  = None,
        requires_admin=1,
        is_testimonial=1
    )
 
    print(f"✓ Message {message_type} enregistré — user {user_id}")
 
 

