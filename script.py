import os
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatJoinRequestHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)
from dotenv import load_dotenv

from database.database import save_user

from db import get_db as sync_get_db, init_pool

from ai_agent import set_bot, log_unhandled_message
from validation_handler import register_validation_handler
from validation_formation import register_formation_handler
from form.form_engine import register_form_handlers, setup_background_worker
from telegram_page.signal_broadcast import register_signal_handlers
from telegram_page.gold.gold_engine import set_bot as set_gold_bot, daily_cramed_check
from telegram_page.gold.gold_write_queue import start_gold_write_worker
from telegram_page.gold.error_handler import error_handler
from telegram_page.gold.weekly_capital_cache import weekly_capital

from aiohttp import web
from telegram_page.gold.lifecycle import open_new_session, mark_broadcast_done
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.broadcast_send import send_teaser_broadcast
import telegram_page.gold.gold_engine as gold_engine_mod

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("fdk_bot")

CANAL_B_ID = int(os.getenv("CANAL_B_ID", "-1002705005402"))

# Liste des admins. ADMIN_ID (singulier) reste défini comme le principal pour
# les modules externes qui n'acceptent qu'un seul ID (ex: register_form_handlers).
ADMIN_IDS = [6992809421, 571718066]
ADMIN_ID  = ADMIN_IDS[0]

# Ordre du tunnel : LEVEL → PHONE → NAME.
LEVEL, PHONE, NAME = range(3)

CATEGORIE = "FDK CONCEPT CAPITAL LISTE ACTIFS"

# Date de lancement du projet : toutes les statistiques "cumul" sont calculées
# à partir de cette date (rien avant n'est compté).
STATS_START_DATE = "2026-07-27"

token     = os.getenv("tokens")

if not token:
    raise RuntimeError(
        "Variable d'environnement 'tokens' manquante : impossible de démarrer le bot."
    )

import uvloop
uvloop.install()

# ══════════════════════════════════════════════════════════════════════════════
# LOG D'ERREURS SUR FICHIER + NOTIFS ADMIN
# ══════════════════════════════════════════════════════════════════════════════
# Toutes les erreurs "normales" (échec envoi d'un message user, échec sauvegarde
# d'un champ, etc.) sont écrites dans errors.log. Ce fichier est envoyé aux
# admins chaque soir à 20h en pièce jointe, puis rotaté.
#
# Les erreurs CRITIQUES (démarrage, Gold, campagne, HTTP interne) déclenchent
# EN PLUS une notif Telegram immédiate via notify_admin_critical().
# ══════════════════════════════════════════════════════════════════════════════

ERRORS_LOG_PATH = Path(os.getenv("ERRORS_LOG_PATH", "errors.log"))


def log_error(title: str, detail: str = ""):
    """Enregistre une erreur dans errors.log (silencieux, pas de notif Telegram).
    Ne doit JAMAIS lever d'exception, même si le disque est plein."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {title}"
        if detail:
            # On limite la taille d'un enregistrement pour éviter un fichier monstre
            snippet = str(detail)[:4000].replace("\n", " | ")
            line += f" — {snippet}"
        with ERRORS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Impossible d'écrire ? On log dans la sortie standard, on continue.
        logger.exception("[log_error] impossible d'écrire dans errors.log")


async def notify_admin_critical(bot, title: str, detail: str = ""):
    """Erreur critique : envoie IMMÉDIATEMENT à tous les admins ET journalise
    dans le fichier. À utiliser uniquement pour ce qui bloque le business
    (démarrage, Gold, campagne, HTTP interne)."""
    log_error("[CRITIQUE] " + title, detail)
    text = f"🚨 <b>[CRITIQUE] {title}</b>"
    if detail:
        text += f"\n\n<code>{str(detail)[:3500]}</code>"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            logger.exception(f"[notify_admin_critical] impossible d'envoyer à {admin_id}")


async def broadcast_admins(bot, text: str, parse_mode: str = "HTML"):
    """Envoie un message informatif (non-erreur) à tous les admins.
    Ex: bilan quotidien, palier de 100 membres, etc."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
        except Exception:
            logger.exception(f"[broadcast_admins] échec envoi à {admin_id}")


async def send_and_rotate_errors_log(bot):
    """Envoie errors.log aux admins en pièce jointe, puis le renomme pour
    repartir sur un fichier vide. Appelé chaque soir dans le bilan 20h."""
    try:
        if not ERRORS_LOG_PATH.exists() or ERRORS_LOG_PATH.stat().st_size == 0:
            logger.info("[errors_log] aucun log à envoyer aujourd'hui")
            return

        caption = f"📄 Journal d'erreurs — {datetime.now().strftime('%d/%m/%Y')}"
        for admin_id in ADMIN_IDS:
            try:
                with ERRORS_LOG_PATH.open("rb") as f:
                    await bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        filename=f"errors_{datetime.now().strftime('%Y%m%d')}.log",
                        caption=caption,
                    )
            except Exception:
                logger.exception(f"[errors_log] échec envoi à {admin_id}")

        # Rotation : renomme le fichier courant, on repart sur un vide
        try:
            archive = ERRORS_LOG_PATH.with_suffix(
                f".{datetime.now().strftime('%Y%m%d')}.log"
            )
            ERRORS_LOG_PATH.rename(archive)
        except Exception:
            logger.exception("[errors_log] échec rotation")
    except Exception:
        logger.exception("[errors_log] erreur inattendue send_and_rotate")


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA — table `users`
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_users_schema():
    """
    - Utilise la colonne `level` existante (VARCHAR 100) pour stocker
      Débutant / Intermédiaire / Avancé (en texte lisible).
    - Ajoute `level_at` pour tracker quand l'utilisateur a choisi son niveau
      (utilisé par les stats de temps de réponse).
    - Ajoute `last_reminder_at`, `reminder_count`, `name_at`, `phone_at`,
      `completed_at` pour le suivi de l'inscription.
    - Rend `name` et `phone` NULL-able (sauvegarde progressive : on peut avoir
      le niveau et le numéro avant le nom, dans le nouvel ordre).
    Idempotent grâce au filtre "duplicate column" ci-dessous.
    """
    ddl_statements = [
        "ALTER TABLE users ADD COLUMN level_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN last_reminder_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN reminder_count INT NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN name_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN phone_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN completed_at DATETIME NULL",
        "ALTER TABLE users MODIFY COLUMN name VARCHAR(255) NULL",
        "ALTER TABLE users MODIFY COLUMN phone VARCHAR(50) NULL",
    ]
    async with sync_get_db() as cur:
        for stmt in ddl_statements:
            try:
                await cur.execute(stmt)
            except Exception as e:
                # 1060 = Duplicate column name (MySQL) -> déjà appliqué, on ignore
                if "1060" in str(e) or "duplicate column" in str(e).lower():
                    continue
                logger.exception(f"[schema] échec: {stmt}")
                log_error("Échec ALTER schema users", f"{stmt} — {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE — enregistrement progressif directement dans `users`
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_user_row(telegram_id):
    """
    Crée une ligne vide dès l'approbation, même si l'utilisateur ne répond jamais.
    Atomique (ON DUPLICATE KEY) pour gérer les demandes parallèles.
    """
    async with sync_get_db() as cur:
        await cur.execute(
            "INSERT INTO users (telegram_id, created_at) VALUES (%s, NOW()) "
            "ON DUPLICATE KEY UPDATE telegram_id = telegram_id",
            (telegram_id,)
        )


async def get_user_row(telegram_id):
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT name, phone, level FROM users WHERE telegram_id=%s",
            (telegram_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return row.get("name"), row.get("phone"), row.get("level")


async def save_registration_field(telegram_id, field: str, value: str) -> bool:
    """
    Sauvegarde immédiate d'un champ dès sa saisie.
    Écrit aussi {field}_at avec NOW() UNIQUEMENT si NULL (premier remplissage).
    Retourne True si la ligne vient de devenir complète (les 3 champs
    remplis et completed_at NULL jusque-là).
    """
    if field not in ("name", "phone", "level"):
        raise ValueError(f"Champ non autorisé: {field}")
    field_at = f"{field}_at"

    async with sync_get_db() as cur:
        await cur.execute(
            f"INSERT INTO users (telegram_id, created_at, {field}, {field_at}) "
            f"VALUES (%s, NOW(), %s, NOW()) AS new_row "
            f"ON DUPLICATE KEY UPDATE "
            f"  {field} = new_row.{field}, "
            f"  {field_at} = COALESCE(users.{field_at}, NOW())",
            (telegram_id, value)
        )

        await cur.execute(
            "UPDATE users SET completed_at = NOW() "
            "WHERE telegram_id = %s "
            "  AND completed_at IS NULL "
            "  AND name IS NOT NULL AND name <> '' "
            "  AND phone IS NOT NULL AND phone <> '' "
            "  AND level IS NOT NULL AND level <> ''",
            (telegram_id,)
        )
        just_completed = cur.rowcount == 1

    return just_completed


async def read_back_field(telegram_id, field: str):
    """Relit un champ pour vérifier qu'il a bien été sauvegardé.
    Utilisé après un save_registration_field critique (téléphone)."""
    if field not in ("name", "phone", "level"):
        raise ValueError(f"Champ non autorisé: {field}")
    async with sync_get_db() as cur:
        await cur.execute(
            f"SELECT {field} AS v FROM users WHERE telegram_id=%s",
            (telegram_id,)
        )
        row = await cur.fetchone()
    return row.get("v") if row else None


async def count_total_completed() -> int:
    async with sync_get_db() as cur:
        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE completed_at IS NOT NULL")
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def get_incomplete_telegram_ids():
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT telegram_id FROM users WHERE "
            "(name IS NULL OR name = '') "
            "OR (phone IS NULL OR phone = '') "
            "OR (level IS NULL OR level = '')"
        )
        rows = await cur.fetchall()
    return [r["telegram_id"] for r in rows]


async def get_incomplete_users_full():
    """Utilisateurs incomplets créés depuis le lancement, avec toutes les
    infos nécessaires au calcul de la relance (cas A vs cas B)."""
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT telegram_id, created_at, last_reminder_at, reminder_count, "
            "  name, phone, level "
            "FROM users WHERE created_at >= %s AND ("
            "  (name IS NULL OR name = '') "
            "  OR (phone IS NULL OR phone = '') "
            "  OR (level IS NULL OR level = '')"
            ")",
            (STATS_START_DATE,)
        )
        return await cur.fetchall()


async def mark_reminder_sent(telegram_id):
    async with sync_get_db() as cur:
        await cur.execute(
            "UPDATE users SET last_reminder_at=NOW(), reminder_count=reminder_count+1 "
            "WHERE telegram_id=%s",
            (telegram_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# COMPTEURS EN MÉMOIRE — remis à zéro par le bilan quotidien 20h
# ══════════════════════════════════════════════════════════════════════════════

_daily_reminders_sent = 0     # nombre de relances envoyées depuis dernier reset
_last_100_notified    = 0     # dernier palier "N complets" déjà notifié


async def bump_reminder_counter():
    global _daily_reminders_sent
    _daily_reminders_sent += 1


async def check_and_notify_milestone(bot):
    """Notifie les admins tous les 100 nouveaux enregistrements complets."""
    global _last_100_notified
    try:
        total = await count_total_completed()
        next_threshold = ((_last_100_notified // 100) + 1) * 100
        if total >= next_threshold:
            reached = (total // 100) * 100
            if reached > _last_100_notified and reached > 0:
                _last_100_notified = reached
                await broadcast_admins(
                    bot,
                    f"🎉 <b>Palier atteint : {reached} membres enregistrés</b>\n\n"
                    f"Total actuel : <b>{total}</b> inscriptions complètes."
                )
    except Exception as e:
        logger.exception("[milestone] erreur")
        log_error("check_and_notify_milestone", str(e))


async def init_milestone_counter():
    global _last_100_notified
    try:
        total = await count_total_completed()
        _last_100_notified = (total // 100) * 100
        logger.info(f"[milestone] initialisé à {_last_100_notified} (total={total})")
    except Exception:
        logger.exception("[milestone] échec init")


# ══════════════════════════════════════════════════════════════════════════════
# TEXTES & CLAVIERS DU TUNNEL
# ══════════════════════════════════════════════════════════════════════════════

# Formation (message final)
keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎓 Accéder à la formation",
                          url="https://fdksignal.com/formation/formation-debutant")]
])

# ── Étape 1 : Niveau ────────────────────────────────────────────────────────
# On stocke le libellé texte en base (pas un slug).
LEVEL_CHOICES = [
    ("Débutant",      "🌱 Débutant"),
    ("Intermédiaire", "📈 Intermédiaire"),
    ("Avancé",        "🏆 Avancé"),
]
# callback_data limité à 64 octets → on transmet l'index, pas le libellé accentué
level_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton(display, callback_data=f"level:{i}")]
    for i, (_, display) in enumerate(LEVEL_CHOICES)
])
LEVEL_Q = "📊 Quel est votre <b>niveau</b> en trading ?\n\nChoisissez ci-dessous 👇"

# ── Étape 2 : Contact (bouton natif Telegram) ───────────────────────────────
phone_share_keyboard = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Partager mon numéro", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)
PHONE_Q = "📱 Partagez votre numéro WhatsApp en un clic 👇"

# ── Étape 3 : Nom ───────────────────────────────────────────────────────────
NAME_Q = (
    "⚠️ <b>Attention !</b>\n"
    "\n"
    "Nous y sommes presque 😊\n"
    "\n"
    "Il ne reste plus qu'une dernière étape pour finaliser votre inscription.\n"
    "\n"
    "👤 Envoyez simplement votre <b>nom et votre prénom</b>.\n"
    "\n"
    "<i>Exemple :</i>\n"
    "Fiacre Kpanou\n"
    "\n"
    "✅ Dès que vous les envoyez, votre inscription sera finalisée."
)

# ── Messages de redirection si l'utilisateur envoie du texte au mauvais moment
LEVEL_REDIRECT_TEXT = (
    "⚠️ <b>Attention !</b>\n"
    "\n"
    "Il faut cliquer sur <b>votre niveau</b> juste en dessous 👇"
)
PHONE_REDIRECT_TEXT = (
    "⚠️ <b>Attention !</b>\n"
    "\n"
    "Il faut cliquer sur <b>« 📱 Partager mon numéro »</b> juste au niveau de votre clavier 👇"
)

WELCOME_TEXT = (
    "🎉 <b>Félicitations !</b>\n\n"
    "Votre demande a été acceptée et vous êtes <b>éligible</b> pour participer à "
    "<b>FDK CAPITAL CONCEPT</b>.\n\n"
    "📝 Pour valider définitivement votre participation, commençons.\n\n" + LEVEL_Q
)

ALREADY_REGISTERED_TEXT = (
    "✅ <b>Vous êtes déjà inscrit !</b>\n\n"
    "Votre enregistrement est complet et vous êtes bien <b>éligible</b>.\n\n"
    "🎯 Vous figurez officiellement sur la liste des participants de "
    "<b>FDK CAPITAL CONCEPT</b>.\n\n"
    "📅 Chaque samedi, les gagnants sont sélectionnés en direct devant toute la communauté."
)

RESUME_INTRO_TEXT = (
    "👋 <b>Content de vous revoir !</b>\n\n"
    "Il vous reste juste quelques informations à compléter pour finaliser votre "
    "inscription à <b>FDK CAPITAL CONCEPT</b>."
)

REMINDER_TEXT = (
    "⏳ <b>Votre inscription n'est pas terminée</b>\n\n"
    "Il vous manque encore quelques informations pour valider votre participation à "
    "<b>FDK CAPITAL CONCEPT</b>.\n\n"
    "👇 Cliquez sur le bouton pour reprendre là où vous vous êtes arrêté."
)

resume_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("▶️ Terminer mon enregistrement",
                          callback_data="resume_registration")]
])


def _missing_field_state(name, phone, level):
    """Renvoie l'étape (LEVEL/PHONE/NAME) manquante dans l'ordre du nouveau tunnel,
    ou None si tout est rempli."""
    if not (level and str(level).strip()):
        return LEVEL
    if not (phone and str(phone).strip()):
        return PHONE
    if not (name and str(name).strip()):
        return NAME
    return None


async def _prompt_state(bot, chat_id, state, resume=False):
    """Envoie la question correspondant à l'étape manquante, avec le bon clavier.
    Peut lever telegram.error.Forbidden si l'utilisateur n'a pas démarré /start."""
    prefix = "▶️ <b>Reprenons votre enregistrement.</b>\n\n" if resume else ""

    if state == LEVEL:
        await bot.send_message(chat_id=chat_id, text=prefix + LEVEL_Q,
                               parse_mode="HTML", reply_markup=level_keyboard)
    elif state == PHONE:
        await bot.send_message(chat_id=chat_id, text=prefix + PHONE_Q,
                               parse_mode="HTML", reply_markup=phone_share_keyboard)
    elif state == NAME:
        await bot.send_message(chat_id=chat_id, text=prefix + NAME_Q, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# TUNNEL D'ENREGISTREMENT
# ══════════════════════════════════════════════════════════════════════════════

async def get_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 1 : clic sur un bouton de niveau."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # callback_data = "level:<index>"
    try:
        idx = int(query.data.split(":", 1)[-1])
        value, display = LEVEL_CHOICES[idx]
    except (ValueError, IndexError):
        log_error("callback_data level invalide", f"user_id={user_id} data={query.data}")
        return LEVEL  # on reste sur l'étape, l'utilisateur peut recliquer

    context.user_data["level"] = value

    just_completed = False
    try:
        just_completed = await save_registration_field(user_id, "level", value)
    except Exception as e:
        logger.exception(f"[get_level] échec sauvegarde pour {user_id}")
        log_error("Échec sauvegarde du niveau", f"user_id={user_id} — {e}")

    if just_completed:
        asyncio.create_task(check_and_notify_milestone(context.bot))

    try:
        try:
            await query.edit_message_text(
                f"📊 Niveau sélectionné : <b>{display}</b>", parse_mode="HTML"
            )
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=user_id, text=PHONE_Q,
            parse_mode="HTML", reply_markup=phone_share_keyboard,
        )
    except Exception as e:
        logger.exception(f"[get_level] échec envoi message pour {user_id}")
        log_error("Échec envoi message (get_level)", f"user_id={user_id} — {e}")

    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 2 : réception UNIQUEMENT du contact (bouton natif).
    Le texte libre est traité par phone_redirect() qui redirige vers le bouton."""
    user_id = update.effective_user.id

    # À ce stade on est certain d'avoir un contact (filters.CONTACT sur le handler)
    if update.message.contact is None:
        # Filet de sécurité : ne devrait jamais arriver
        log_error("get_phone appelé sans contact", f"user_id={user_id}")
        try:
            await update.message.reply_text(
                PHONE_REDIRECT_TEXT, parse_mode="HTML",
                reply_markup=phone_share_keyboard,
            )
        except Exception:
            pass
        return PHONE

    phone = update.message.contact.phone_number
    source = "contact"

    if not phone:
        log_error("Numéro vide reçu", f"user_id={user_id} source={source}")
        try:
            await update.message.reply_text(
                PHONE_REDIRECT_TEXT, parse_mode="HTML",
                reply_markup=phone_share_keyboard,
            )
        except Exception:
            pass
        return PHONE

    context.user_data["phone"] = phone

    # Sauvegarde + vérification par relecture immédiate — on veut être SÛR
    # que le numéro est bien en base avant de continuer.
    just_completed = False
    saved_ok = False
    try:
        just_completed = await save_registration_field(user_id, "phone", phone)
        # Relecture de contrôle
        readback = await read_back_field(user_id, "phone")
        saved_ok = bool(readback and str(readback).strip())
        if not saved_ok:
            await notify_admin_critical(
                context.bot,
                "Numéro non persisté après save",
                f"user_id={user_id} phone_reçu={phone!r} phone_relu={readback!r}"
            )
    except Exception as e:
        logger.exception(f"[get_phone] échec sauvegarde pour {user_id}")
        await notify_admin_critical(
            context.bot, "Échec sauvegarde du téléphone",
            f"user_id={user_id} phone={phone!r} — {e}"
        )

    if just_completed:
        asyncio.create_task(check_and_notify_milestone(context.bot))

    logger.info(f"[phone] user_id={user_id} source={source} saved={saved_ok}")

    try:
        await update.message.reply_text(
            "✅ Numéro enregistré.", reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(NAME_Q, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"[get_phone] échec envoi message pour {user_id}")
        log_error("Échec envoi message (get_phone)", f"user_id={user_id} — {e}")

    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 3 : nom et prénom (texte libre)."""
    user_id = update.effective_user.id
    name = update.message.text.strip()
    context.user_data["name"] = name

    just_completed = False
    try:
        just_completed = await save_registration_field(user_id, "name", name)
    except Exception as e:
        logger.exception(f"[get_name] échec sauvegarde pour {user_id}")
        log_error("Échec sauvegarde du nom", f"user_id={user_id} — {e}")

    if just_completed:
        asyncio.create_task(check_and_notify_milestone(context.bot))

    try:
        await update.message.reply_text(
            "✅ <b>Vous êtes maintenant enregistré !</b>\n\n"
            "Votre candidature a bien été prise en compte.\n\n"
            "📅 Chaque samedi, les gagnants sont sélectionnés en direct devant toute la communauté.\n\n"
            "🎯 Vous faites désormais officiellement partie des participants.",
            parse_mode="HTML"
        )
        await update.message.reply_text(
            "🎓 <b>Accès à votre formation</b>\n\n"
            "Vous pouvez dès maintenant commencer votre formation.\n\n"
            "Elle vous accompagnera étape par étape afin d'être prêt lorsque vous serez sélectionné.\n\n"
            "👇 Cliquez sur le bouton ci-dessous.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.exception(f"[get_name] échec envoi messages finaux pour {user_id}")
        log_error("Échec envoi messages finaux", f"user_id={user_id} — {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def level_redirect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """L'utilisateur envoie du texte à l'étape LEVEL au lieu de cliquer un bouton :
    on lui redemande gentiment de cliquer, avec les boutons réattachés."""
    try:
        await update.message.reply_text(
            LEVEL_REDIRECT_TEXT, parse_mode="HTML", reply_markup=level_keyboard,
        )
    except Exception as e:
        logger.exception("[level_redirect] échec envoi")
        log_error("Échec envoi level_redirect",
                  f"user_id={update.effective_user.id} — {e}")
    return LEVEL


async def phone_redirect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """L'utilisateur envoie du texte à l'étape PHONE au lieu de partager son contact :
    on lui redemande gentiment d'appuyer sur le bouton du clavier."""
    try:
        await update.message.reply_text(
            PHONE_REDIRECT_TEXT, parse_mode="HTML", reply_markup=phone_share_keyboard,
        )
    except Exception as e:
        logger.exception("[phone_redirect] échec envoi")
        log_error("Échec envoi phone_redirect",
                  f"user_id={update.effective_user.id} — {e}")
    return PHONE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.reply_text("❌ Annulé.", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.exception("[cancel] échec envoi message")
        log_error("Échec envoi message (cancel)", str(e))
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# REPRISE VIA BOUTON — relit la base et repositionne l'étape manquante
# ══════════════════════════════════════════════════════════════════════════════

async def resume_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    name = phone = level = None
    try:
        row = await get_user_row(user_id)
        if row:
            name, phone, level = row
    except Exception as e:
        logger.exception(f"[resume] échec get_user_row pour {user_id}")
        log_error("Échec get_user_row (resume)", f"user_id={user_id} — {e}")

    state = _missing_field_state(name, phone, level)

    if state is None:
        try:
            await context.bot.send_message(chat_id=user_id, text=ALREADY_REGISTERED_TEXT,
                                           parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[resume] échec confirmation à {user_id}: {e}")
        return ConversationHandler.END

    context.user_data["name"]  = name
    context.user_data["phone"] = phone
    context.user_data["level"] = level
    try:
        await _prompt_state(context.bot, user_id, state, resume=True)
    except Exception as e:
        logger.warning(f"[resume] échec prompt à {user_id}: {e}")
        return ConversationHandler.END
    return state


# ══════════════════════════════════════════════════════════════════════════════
# APPROBATION DES DEMANDES D'ADHÉSION
# ══════════════════════════════════════════════════════════════════════════════

async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Nouveau flow :
      1. Créer la ligne DB
      2. Approuver la demande
      3. ESSAYER d'envoyer le message d'accueil au user
      4. SEULEMENT SI l'envoi réussit → ajouter à la catégorie
         (si l'envoi échoue, l'utilisateur ne sera jamais relancé — cohérent
          avec le fait qu'on ne peut pas lui parler)
    """
    user    = update.chat_join_request.from_user
    user_id = user.id

    # 1. Ligne DB
    try:
        await ensure_user_row(user_id)
    except Exception as e:
        logger.exception(f"[join] échec ensure_user_row pour {user_id}")
        log_error("Échec création ligne users", f"user_id={user_id} — {e}")

    # 2. Approbation
    try:
        await update.chat_join_request.approve()
    except BadRequest as e:
        msg = str(e).lower()
        if "already" in msg or "participant" in msg:
            logger.info(f"[join] {user_id} déjà membre.")
        else:
            logger.exception(f"[join] échec approve() pour {user_id}")
            log_error("Échec approbation demande d'adhésion", f"user_id={user_id} — {e}")
        return ConversationHandler.END
    except Exception as e:
        logger.exception(f"[join] erreur inattendue approve() pour {user_id}")
        log_error("Erreur inattendue lors de l'approbation", f"user_id={user_id} — {e}")
        return ConversationHandler.END

    # 3. Lire l'état actuel de l'utilisateur pour choisir le bon message
    name = phone = level = None
    try:
        row = await get_user_row(user_id)
        if row:
            name, phone, level = row
    except Exception as e:
        logger.exception(f"[join] échec get_user_row pour {user_id}")
        log_error("Échec get_user_row (join)", f"user_id={user_id} — {e}")

    state = _missing_field_state(name, phone, level)

    # 4. Essayer d'envoyer le message d'accueil
    sent_ok = False
    return_state = ConversationHandler.END

    try:
        if state is None:
            await context.bot.send_message(chat_id=user_id,
                                           text=ALREADY_REGISTERED_TEXT, parse_mode="HTML")
            sent_ok = True
            return_state = ConversationHandler.END

        elif not name and not phone and not level:
            # Tout nouveau → message de bienvenue qui contient déjà les boutons de niveau
            await context.bot.send_message(chat_id=user_id, text=WELCOME_TEXT,
                                           parse_mode="HTML", reply_markup=level_keyboard)
            sent_ok = True
            return_state = LEVEL

        else:
            # Incomplet → on reprend exactement là où il en était
            context.user_data["name"]  = name
            context.user_data["phone"] = phone
            context.user_data["level"] = level
            await context.bot.send_message(chat_id=user_id, text=RESUME_INTRO_TEXT,
                                           parse_mode="HTML")
            await _prompt_state(context.bot, user_id, state)
            sent_ok = True
            return_state = state

    except Forbidden as e:
        # L'utilisateur n'a jamais démarré /start avec le bot, ou l'a bloqué.
        # Conformément à la règle : on NE l'ajoute PAS à la catégorie
        # (sinon il figurerait dans les relances alors qu'on ne peut pas lui parler).
        logger.warning(f"[join] impossible d'écrire en privé à {user_id}: {e}")
        log_error("Utilisateur non joignable en privé après approbation",
                  f"user_id={user_id} — {e}")
        return ConversationHandler.END

    except Exception as e:
        logger.exception(f"[join] erreur inattendue envoi message d'accueil à {user_id}")
        log_error("Erreur envoi message d'accueil", f"user_id={user_id} — {e}")
        return ConversationHandler.END

    # 5. Envoi OK → on l'ajoute à la catégorie (donc relançable)
    if sent_ok:
        try:
            from telegram_page.categorie import add_members_to_category
            await add_members_to_category(CATEGORIE, [user_id])
        except Exception as e:
            logger.exception(f"[join] categorie error pour {user_id}")
            log_error("Erreur lors de l'ajout à la catégorie",
                      f"user_id={user_id} — {e}")

    return return_state


# ══════════════════════════════════════════════════════════════════════════════
# RELANCE AUTOMATIQUE
# ══════════════════════════════════════════════════════════════════════════════
#
# Deux cas :
#
# Cas A — l'utilisateur n'a RIEN commencé (name, phone, level tous vides) :
#   - relance 1 à T+10min  (reminder_count = 0)
#   - relance 2 à T+30min  (reminder_count = 1)
#   - au-delà : STOP
#
# Cas B — l'utilisateur a commencé mais pas fini (au moins 1 champ rempli) :
#   - relance toutes les 24h depuis le dernier événement
#   - 3 relances maximum, puis STOP
#
# Filtre : on ne relance QUE les utilisateurs présents dans CATEGORIE.
# ══════════════════════════════════════════════════════════════════════════════

CAS_A_OFFSETS_MINUTES = [10, 30]   # relances 1 et 2 depuis created_at
CAS_A_MAX_REMINDERS   = 2

CAS_B_INTERVAL_HOURS  = 24
CAS_B_MAX_REMINDERS   = 3


def _is_case_a(row) -> bool:
    """Rien de rempli du tout."""
    name  = (row.get("name")  or "").strip()
    phone = (row.get("phone") or "").strip()
    level = (row.get("level") or "").strip()
    return not name and not phone and not level


async def _fetch_category_member_ids(name_categorie: str) -> set:
    """Récupère tous les telegram_id de la catégorie via l'API existante.
    Un seul appel par cycle de relance (limit très haut)."""
    try:
        from telegram_page.categorie import get_category_members
        # Limite très haute pour tout récupérer en une fois. Si un jour la
        # catégorie dépasse ce seuil, il faudra paginer.
        result = await get_category_members(name_categorie, {"limit": 1000000, "offset": 0})
        members = result.get("members", []) if isinstance(result, dict) else []
        return {int(m["telegram_id"]) for m in members if m.get("telegram_id") is not None}
    except Exception as e:
        logger.exception("[reminder] échec récupération des membres de la catégorie")
        log_error("Échec get_category_members", f"cat={name_categorie} — {e}")
        return set()


async def registration_reminder_loop(bot):
    CHECK_INTERVAL = 60  # une passe par minute

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)

            try:
                rows = await get_incomplete_users_full()
            except Exception as e:
                logger.exception("[reminder] échec récupération des utilisateurs incomplets")
                log_error("Échec requête utilisateurs incomplets", str(e))
                continue

            if not rows:
                continue

            # Filtre catégorie : on ne relance que ceux qui y sont
            eligible_ids = await _fetch_category_member_ids(CATEGORIE)
            if not eligible_ids:
                # Soit personne dans la catégorie, soit erreur déjà loguée : on saute
                continue

            now = datetime.now()
            sent, failed = 0, 0

            for r in rows:
                user_id = r.get("telegram_id")
                if not user_id or int(user_id) not in eligible_ids:
                    continue

                count = int(r.get("reminder_count") or 0)
                created_at = r.get("created_at")
                last_event = r.get("last_reminder_at") or created_at
                if last_event is None:
                    continue

                # Détermine si on doit relancer maintenant, selon le cas
                if _is_case_a(r):
                    # Cas A : 2 relances max, à 10min et 30min DEPUIS created_at
                    if count >= CAS_A_MAX_REMINDERS:
                        continue
                    if created_at is None:
                        continue
                    target_offset = timedelta(minutes=CAS_A_OFFSETS_MINUTES[count])
                    if now - created_at < target_offset:
                        continue
                else:
                    # Cas B : 3 relances max, 24h entre chaque
                    if count >= CAS_B_MAX_REMINDERS:
                        continue
                    if now - last_event < timedelta(hours=CAS_B_INTERVAL_HOURS):
                        continue

                # Envoi
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=REMINDER_TEXT,
                        parse_mode="HTML",
                        reply_markup=resume_keyboard,
                    )
                    sent += 1
                    await mark_reminder_sent(user_id)
                    await bump_reminder_counter()
                    await asyncio.sleep(0.05)  # anti-flood
                except Forbidden:
                    # Utilisateur a bloqué le bot : normal, on ignore silencieusement
                    failed += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"[reminder] échec envoi à {user_id}: {e}")
                    log_error("Échec envoi relance", f"user_id={user_id} — {e}")

            if sent or failed:
                logger.info(f"[reminder] cycle — envoyées={sent} échecs={failed}")

        except Exception as e:
            logger.exception("[reminder] erreur inattendue dans la boucle")
            log_error("Erreur inattendue — boucle de relance", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# GOLD V7.1
# ══════════════════════════════════════════════════════════════════════════════
from telegram_page.gold.gold_broadcast import register_gold_handlers_v7
from telegram_page.gold.consistency import run_full_check
from telegram_page.gold.lifecycle import register_buffer
from telegram_page.gold.gold_buffer import gold_buffer

from telegram_page.gold.weekly_capital_cache import ensure_schema as ensure_capital_schema
from telegram_page.gold.capital_campaign import (
    ensure_schema as ensure_campaign_schema,
    weekly_scheduler_loop,
    run_campaign,
    CampaignConfig,
)


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDES ADMIN
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_queue_status(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        s = gold_buffer.status()
        await update.message.reply_text(
            f"📊 Buffer Gold v7\n"
            f"Attaché à : {s['attached']}\n"
            f"En attente : {s['pending']} "
            f"(entries {s['entries']} / steps {s['steps']} / events {s['events']})\n"
            f"Agg dirty : {s['dirty_agg']}\n"
            f"Worker actif : {'✅' if s['worker_running'] else '❌'}"
        )
    except Exception as e:
        logger.exception("[cmd_queue_status] erreur")
        log_error("Erreur /queue_status", str(e))


async def cmd_gold_check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        rep = await run_full_check()
        await update.message.reply_text(rep.summary())
    except Exception as e:
        logger.exception("[cmd_gold_check] erreur")
        log_error("Erreur /gold_check", str(e))


async def cmd_capital_status(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        s = weekly_capital.status()
        await update.message.reply_text(
            f"💼 Weekly Capital Cache\n"
            f"Total RAM : {s['total_ram']}\n"
            f"Actifs : {s['active']}\n"
            f"Expirés (pas encore purgés) : {s['expired_stale']}\n"
            f"TTL : {s['ttl_days']} jours"
        )
    except Exception as e:
        logger.exception("[cmd_capital_status] erreur")
        log_error("Erreur /capital_status", str(e))


async def cmd_capital_campaign_now(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        await update.message.reply_text("🔄 Campagne lancée en tâche de fond...")

        async def _run():
            try:
                await run_campaign(context.bot, CampaignConfig())
            except Exception as e:
                logger.exception("[cmd_capital_campaign_now] échec run_campaign")
                await notify_admin_critical(context.bot,
                    "Échec run_campaign (tâche de fond)", str(e))

        asyncio.create_task(_run())
    except Exception as e:
        logger.exception("[cmd_capital_campaign_now] erreur")
        log_error("Erreur /capital_campaign_now", str(e))


async def cmd_incomplete_status(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        telegram_ids = await get_incomplete_telegram_ids()
        await update.message.reply_text(
            f"📋 Enregistrements incomplets : {len(telegram_ids)}"
        )
    except Exception as e:
        logger.exception("[cmd_incomplete_status] erreur")
        log_error("Erreur /incomplete_status", str(e))


async def cmd_stats_now(update, context):
    """Envoie le bilan complet des inscriptions à la demande (ne reset PAS le compteur)."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        await update.message.reply_text("⏳ Calcul en cours...")
        s = await compute_daily_stats()
        reminders_today = _daily_reminders_sent
        completion_rate_global = _pct(s["total_completed"], s["total_users"])
        completion_rate_today  = _pct(s["completed_today"], s["new_today"])
        completion_rate_week   = _pct(s["completed_this_week"], s["new_this_week"])
        total_stuck = s["stuck_before_level"] + s["stuck_at_phone"] + s["stuck_at_name"]

        report = (
            f"📊 <b>Stats à l'instant — {datetime.now().strftime('%d/%m/%Y %H:%M')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>🆕 Aujourd'hui</b>\n"
            f"• Nouvelles demandes approuvées : <b>{s['new_today']}</b>\n"
            f"• Nouvelles inscriptions complètes : <b>{s['completed_today']}</b>\n"
            f"• Taux de complétion du jour : <b>{completion_rate_today}</b>\n"
            f"• Relances envoyées : <b>{reminders_today}</b>\n\n"
            f"<b>📆 Cette semaine</b>\n"
            f"• Nouvelles demandes approuvées : <b>{s['new_this_week']}</b>\n"
            f"• Nouvelles inscriptions complètes : <b>{s['completed_this_week']}</b>\n"
            f"• Taux de complétion de la semaine : <b>{completion_rate_week}</b>\n\n"
            f"<b>🌍 Cumul (depuis le {datetime.strptime(STATS_START_DATE, '%Y-%m-%d').strftime('%d/%m/%Y')})</b>\n"
            f"• Total utilisateurs approuvés : <b>{s['total_users']}</b>\n"
            f"• Total inscriptions complètes : <b>{s['total_completed']}</b>\n"
            f"• Taux de complétion global : <b>{completion_rate_global}</b>\n\n"
            f"<b>⏱ Temps de réponse moyens</b>\n"
            f"• Approbation → niveau : <b>{_fmt_duration(s['avg_created_to_level_s'])}</b>\n"
            f"• Niveau → Téléphone : <b>{_fmt_duration(s['avg_level_to_phone_s'])}</b>\n"
            f"• Téléphone → Nom : <b>{_fmt_duration(s['avg_phone_to_name_s'])}</b>\n"
            f"• Durée totale : <b>{_fmt_duration(s['avg_total_completion_s'])}</b>\n\n"
            f"<b>🚨 Points de blocage</b>\n"
            f"• Sans niveau : <b>{s['stuck_before_level']}</b> ({_pct(s['stuck_before_level'], s['total_users'])})\n"
            f"• Bloqués téléphone : <b>{s['stuck_at_phone']}</b> ({_pct(s['stuck_at_phone'], s['total_users'])})\n"
            f"• Bloqués nom : <b>{s['stuck_at_name']}</b> ({_pct(s['stuck_at_name'], s['total_users'])})\n"
            f"• Total bloqués : <b>{total_stuck}</b>"
        )
        await update.message.reply_text(report, parse_mode="HTML")
    except Exception as e:
        logger.exception("[cmd_stats_now] erreur")
        log_error("Erreur /stats_now", str(e))


async def cmd_errors_now(update, context):
    """Force l'envoi immédiat du fichier d'erreurs actuel (sans rotation)."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        if not ERRORS_LOG_PATH.exists() or ERRORS_LOG_PATH.stat().st_size == 0:
            await update.message.reply_text("Aucune erreur enregistrée pour le moment.")
            return
        with ERRORS_LOG_PATH.open("rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=f,
                filename=f"errors_current.log",
                caption=f"📄 Erreurs en cours — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            )
    except Exception as e:
        logger.exception("[cmd_errors_now] erreur")
        log_error("Erreur /errors_now", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# STATS QUOTIDIENNES — funnel adapté au nouvel ordre (level → phone → name)
# ══════════════════════════════════════════════════════════════════════════════

async def compute_daily_stats() -> dict:
    stats = {}
    async with sync_get_db() as cur:
        # Volumes du jour
        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE DATE(created_at) = CURDATE()")
        stats["new_today"] = int((await cur.fetchone())["n"])

        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE DATE(completed_at) = CURDATE()")
        stats["completed_today"] = int((await cur.fetchone())["n"])

        # Semaine ISO
        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE YEARWEEK(created_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["new_this_week"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE YEARWEEK(completed_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["completed_this_week"] = int((await cur.fetchone())["n"])

        # Cumuls depuis lancement
        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE created_at >= %s",
                          (STATS_START_DATE,))
        stats["total_users"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE completed_at IS NOT NULL AND created_at >= %s",
            (STATS_START_DATE,)
        )
        stats["total_completed"] = int((await cur.fetchone())["n"])

        # Funnel — nouvel ordre level → phone → name
        await cur.execute(
            "SELECT "
            "  SUM(CASE WHEN level IS NULL OR level = '' THEN 1 ELSE 0 END) AS stuck_before_level, "
            "  SUM(CASE WHEN level IS NOT NULL AND level <> '' "
            "           AND (phone IS NULL OR phone = '') THEN 1 ELSE 0 END) AS stuck_at_phone, "
            "  SUM(CASE WHEN phone IS NOT NULL AND phone <> '' "
            "           AND (name IS NULL OR name = '') THEN 1 ELSE 0 END) AS stuck_at_name "
            "FROM users WHERE created_at >= %s",
            (STATS_START_DATE,)
        )
        row = await cur.fetchone() or {}
        stats["stuck_before_level"] = int(row.get("stuck_before_level") or 0)
        stats["stuck_at_phone"]     = int(row.get("stuck_at_phone") or 0)
        stats["stuck_at_name"]      = int(row.get("stuck_at_name") or 0)

        # Temps de réponse moyens (nouvel ordre)
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, level_at)) AS avg_s "
            "FROM users WHERE level_at IS NOT NULL AND created_at IS NOT NULL "
            "AND level_at >= created_at"
        )
        stats["avg_created_to_level_s"] = float((await cur.fetchone())["avg_s"] or 0)

        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, level_at, phone_at)) AS avg_s "
            "FROM users WHERE phone_at IS NOT NULL AND level_at IS NOT NULL "
            "AND phone_at >= level_at"
        )
        stats["avg_level_to_phone_s"] = float((await cur.fetchone())["avg_s"] or 0)

        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, phone_at, name_at)) AS avg_s "
            "FROM users WHERE name_at IS NOT NULL AND phone_at IS NOT NULL "
            "AND name_at >= phone_at"
        )
        stats["avg_phone_to_name_s"] = float((await cur.fetchone())["avg_s"] or 0)

        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, completed_at)) AS avg_s "
            "FROM users WHERE completed_at IS NOT NULL"
        )
        stats["avg_total_completion_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # Réactivité
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, level_at)) AS avg_s "
            "FROM users WHERE DATE(level_at) = CURDATE() AND level_at >= created_at"
        )
        stats["avg_created_to_level_today_s"] = float((await cur.fetchone())["avg_s"] or 0)

        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, level_at)) AS avg_s "
            "FROM users WHERE YEARWEEK(level_at, 3) = YEARWEEK(CURDATE(), 3) "
            "AND level_at >= created_at"
        )
        stats["avg_created_to_level_week_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # Répartition par niveau (bonus utile pour un bilan)
        await cur.execute(
            "SELECT level, COUNT(*) AS n FROM users "
            "WHERE completed_at IS NOT NULL AND created_at >= %s "
            "GROUP BY level ORDER BY n DESC",
            (STATS_START_DATE,)
        )
        stats["by_level"] = [
            {"level": r["level"], "n": int(r["n"])}
            for r in await cur.fetchall()
        ]

    return stats


def _fmt_duration(seconds: float) -> str:
    if not seconds or seconds <= 0:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}min {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}min"


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    return f"{(part * 100 / whole):.1f}%"


async def send_daily_stats_report(bot):
    """Envoie le bilan complet aux admins et remet les compteurs à zéro."""
    global _daily_reminders_sent

    try:
        s = await compute_daily_stats()
    except Exception as e:
        logger.exception("[stats] échec compute_daily_stats")
        await notify_admin_critical(bot, "Échec calcul stats quotidiennes", str(e))
        return

    reminders_today = _daily_reminders_sent
    completion_rate_global = _pct(s["total_completed"], s["total_users"])
    completion_rate_today  = _pct(s["completed_today"], s["new_today"])
    completion_rate_week   = _pct(s["completed_this_week"], s["new_this_week"])

    total_stuck = s["stuck_before_level"] + s["stuck_at_phone"] + s["stuck_at_name"]
    start_fr = datetime.strptime(STATS_START_DATE, '%Y-%m-%d').strftime('%d/%m/%Y')

    by_level_txt = ""
    if s.get("by_level"):
        lines = [f"• {r['level']} : <b>{r['n']}</b>" for r in s["by_level"]]
        by_level_txt = "<b>🎯 Répartition par niveau (complets)</b>\n" + "\n".join(lines) + "\n\n"

    report = (
        f"📊 <b>Bilan quotidien — {datetime.now().strftime('%d/%m/%Y')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"<b>🆕 Aujourd'hui</b>\n"
        f"• Nouvelles demandes approuvées : <b>{s['new_today']}</b>\n"
        f"• Nouvelles inscriptions complètes : <b>{s['completed_today']}</b>\n"
        f"• Taux de complétion du jour : <b>{completion_rate_today}</b>\n"
        f"• Relances envoyées : <b>{reminders_today}</b>\n\n"

        f"<b>📆 Cette semaine</b>\n"
        f"• Nouvelles demandes approuvées : <b>{s['new_this_week']}</b>\n"
        f"• Nouvelles inscriptions complètes : <b>{s['completed_this_week']}</b>\n"
        f"• Taux de complétion de la semaine : <b>{completion_rate_week}</b>\n\n"

        f"<b>🌍 Cumul (depuis le {start_fr})</b>\n"
        f"• Total utilisateurs approuvés : <b>{s['total_users']}</b>\n"
        f"• Total inscriptions complètes : <b>{s['total_completed']}</b>\n"
        f"• Taux de complétion global : <b>{completion_rate_global}</b>\n\n"

        f"{by_level_txt}"

        f"<b>⏱ Temps de réponse moyens (tous)</b>\n"
        f"• Approbation → niveau : <b>{_fmt_duration(s['avg_created_to_level_s'])}</b>\n"
        f"• Niveau → Téléphone : <b>{_fmt_duration(s['avg_level_to_phone_s'])}</b>\n"
        f"• Téléphone → Nom : <b>{_fmt_duration(s['avg_phone_to_name_s'])}</b>\n"
        f"• Durée totale d'inscription : <b>{_fmt_duration(s['avg_total_completion_s'])}</b>\n\n"

        f"<b>⏱ Réactivité</b>\n"
        f"• Approbation → niveau aujourd'hui : "
        f"<b>{_fmt_duration(s['avg_created_to_level_today_s'])}</b>\n"
        f"• Approbation → niveau cette semaine : "
        f"<b>{_fmt_duration(s['avg_created_to_level_week_s'])}</b>\n\n"

        f"<b>🚨 Funnel — points de blocage (depuis le lancement)</b>\n"
        f"• N'ont jamais choisi de niveau : <b>{s['stuck_before_level']}</b> "
        f"({_pct(s['stuck_before_level'], s['total_users'])})\n"
        f"• Bloqués à l'étape téléphone : <b>{s['stuck_at_phone']}</b> "
        f"({_pct(s['stuck_at_phone'], s['total_users'])})\n"
        f"• Bloqués à l'étape nom : <b>{s['stuck_at_name']}</b> "
        f"({_pct(s['stuck_at_name'], s['total_users'])})\n"
        f"• Total bloqués : <b>{total_stuck}</b>\n\n"

        f"<i>💡 L'étape avec le plus d'abandons est celle à optimiser en priorité.</i>"
    )

    await broadcast_admins(bot, report)
    _daily_reminders_sent = 0


async def schedule_daily_check(bot):
    while True:
        try:
            now    = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            # 1. Bilan Gold
            try:
                results      = await daily_cramed_check()
                total_danger = sum(r.get("total_danger", 0) for r in results)
                if total_danger > 0:
                    await broadcast_admins(
                        bot,
                        f"📋 <b>Bilan Gold — {datetime.now().strftime('%d/%m/%Y')}</b>\n\n"
                        f"Comptes en danger : <b>{total_danger}</b>\n"
                        f"Sessions surveillées : {len(results)}\n\n"
                        f"<i>Consultez le dashboard pour le détail.</i>"
                    )
            except Exception as e:
                logger.exception("[daily_check] erreur bilan Gold")
                log_error("Erreur bilan Gold quotidien", str(e))

            # 2. Bilan inscriptions
            try:
                await send_daily_stats_report(bot)
            except Exception as e:
                logger.exception("[daily_check] erreur bilan inscriptions")
                log_error("Erreur bilan inscriptions quotidien", str(e))

            # 3. Envoi + rotation du fichier d'erreurs
            try:
                await send_and_rotate_errors_log(bot)
            except Exception as e:
                logger.exception("[daily_check] erreur envoi errors.log")

        except Exception as e:
            logger.exception("[daily_check] erreur")
            log_error("Erreur bilan quotidien (daily_check)", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SERVEUR HTTP INTERNE — reçoit les ordres Gold depuis l'API (autre process)
# ══════════════════════════════════════════════════════════════════════════════

async def _internal_open_gold(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        sid = int(data["session_id"])
        category = data.get("category")
        send_teaser = bool(data.get("send_teaser", True))
    except Exception as e:
        logger.exception("[internal] payload invalide")
        return web.json_response({"ok": False, "error": f"bad_payload: {e}"}, status=400)

    try:
        snap = await open_new_session(sid, mode="replace")
    except Exception as e:
        logger.exception(f"[internal] open_new_session failed sid={sid}")
        if gold_engine_mod._bot:
            await notify_admin_critical(gold_engine_mod._bot,
                "Échec open_new_session", f"sid={sid}\n{e}")
        return web.json_response({"ok": False, "error": f"open_failed: {e}"}, status=500)

    if send_teaser and gold_engine_mod._bot:
        async def _run_broadcast():
            try:
                report = await send_teaser_broadcast(
                    bot=gold_engine_mod._bot,
                    snap=snap,
                    category=category,
                )
                logger.info(f"[internal] broadcast v7 terminé sid={sid}: {report}")
                mark_broadcast_done(snap.session_id, snap.version)
            except Exception as e:
                logger.exception(f"[internal] broadcast v7 failed sid={sid}")
                await notify_admin_critical(gold_engine_mod._bot,
                    "Échec broadcast v7", f"sid={sid}\n{e}")

        asyncio.create_task(_run_broadcast())
        bstatus = "started"
    else:
        try:
            mark_broadcast_done(snap.session_id, snap.version)
        except Exception as e:
            logger.exception(f"[internal] mark_broadcast_done failed sid={sid}")
            if gold_engine_mod._bot:
                await notify_admin_critical(gold_engine_mod._bot,
                    "Échec mark_broadcast_done", f"sid={sid}\n{e}")
        bstatus = "skipped" if not send_teaser else "bot_unavailable_but_active"

    return web.json_response({
        "ok": True,
        "session_id": snap.session_id,
        "version": snap.version,
        "broadcast_status": bstatus,
    })


async def _start_internal_http_server():
    try:
        server_app = web.Application()
        server_app.router.add_post("/internal/gold/open", _internal_open_gold)
        runner = web.AppRunner(server_app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 9100)
        await site.start()
        logger.info("[internal] HTTP server listening on 127.0.0.1:9100")
        print("[internal] HTTP server listening on 127.0.0.1:9100 ✓")
    except Exception as e:
        logger.exception("[internal] échec démarrage serveur HTTP interne")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # NOTE : on ne crée PLUS de boucle asyncio manuellement ici.
    # app.run_polling() en crée une propre. Avec uvloop, mélanger les deux
    # provoque "RuntimeError: There is no current event loop in thread"
    # au moment du démarrage.
    # Toutes les initialisations async (pool, schémas) sont désormais faites
    # dans _post_init, qui tourne dans la même boucle que le polling.

    app = (Application.builder()
           .token(token)
           .concurrent_updates(512)
           .read_timeout(30).write_timeout(30)
           .build())

    async def _post_init(application):
        try:
            # ── Init base de données (avant tout le reste) ─────────────
            await init_pool()
            print("[main] Pool OK ✓")

            await ensure_users_schema()
            print("[main] Schéma users (level_at, name/phone nullable, reminders) OK ✓")

            await ensure_capital_schema()
            await ensure_campaign_schema()
            print("[main] Schémas v7 OK ✓")

            # ── Reste des inits ────────────────────────────────────────
            await setup_background_worker(application)
            asyncio.create_task(schedule_daily_check(application.bot))

            start_gold_write_worker(application.bot)

            gold_buffer.start(application.bot)
            register_buffer(gold_buffer)

            asyncio.create_task(weekly_scheduler_loop(application.bot))

            # Boucle de relance ACTIVÉE (nouvelle logique cas A / cas B)
            asyncio.create_task(registration_reminder_loop(application.bot))

            await init_milestone_counter()
            await _start_internal_http_server()

            print("[main] Gold v7.1 initialisé ✓")
        except Exception as e:
            logger.exception("[post_init] échec initialisation")
            await notify_admin_critical(application.bot,
                "Échec initialisation du bot (post_init)", str(e))
            raise

    app.post_init = _post_init

    # ── Tunnel d'enregistrement ────────────────────────────────────────────
    # Ordre : LEVEL (boutons inline) → PHONE (contact) → NAME (texte).
    # Entrées : demande d'adhésion approuvée, OU bouton "Terminer" d'une relance.
    resume_entry = CallbackQueryHandler(resume_registration, pattern="^resume_registration$")

    registration_conv = ConversationHandler(
        entry_points=[
            ChatJoinRequestHandler(approve_join_request),
            resume_entry,
        ],
        states={
            LEVEL: [
                CallbackQueryHandler(get_level, pattern=r"^level:\d+$"),
                # Texte reçu à cette étape → on redirige vers les boutons
                MessageHandler(filters.TEXT & ~filters.COMMAND, level_redirect),
                resume_entry,
            ],
            PHONE: [
                # Seul le contact partagé fait avancer
                MessageHandler(filters.CONTACT, get_phone),
                # Tout texte → redirection vers le bouton "Partager mon numéro"
                MessageHandler(filters.TEXT & ~filters.COMMAND, phone_redirect),
                resume_entry,
            ],
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name), resume_entry],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )
    app.add_handler(registration_conv)

    register_validation_handler(app)
    register_formation_handler(app)
    register_form_handlers(app, app.bot, ADMIN_ID)

    register_gold_handlers_v7(app)
    register_signal_handlers(app)

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND
            & filters.UpdateType.MESSAGE
            & filters.ChatType.PRIVATE,
            log_unhandled_message,
        ),
        group=99,
    )

    app.add_handler(CommandHandler("queue_status", cmd_queue_status))
    app.add_handler(CommandHandler("gold_check", cmd_gold_check))
    app.add_handler(CommandHandler("capital_status", cmd_capital_status))
    app.add_handler(CommandHandler("capital_campaign_now", cmd_capital_campaign_now))
    app.add_handler(CommandHandler("incomplete_status", cmd_incomplete_status))
    app.add_handler(CommandHandler("stats_now", cmd_stats_now))
    app.add_handler(CommandHandler("errors_now", cmd_errors_now))

    app.add_error_handler(error_handler)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=2)