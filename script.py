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
from broadcast.cleanup import register_broadcast_admin_handlers
from database.database import save_user

from db import get_db as sync_get_db, init_pool

from ai_agent import set_bot, log_unhandled_message
from validation_handler import register_validation_handler
from validation_formation import register_formation_handler
from form.form_engine import register_form_handlers, setup_background_worker
from telegram_page.gold.gold_engine import set_bot as set_gold_bot, daily_cramed_check
from telegram_page.gold.error_handler import error_handler

from aiohttp import web
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.signal_broadcast import send_signal
from telegram_page.gold.interactive_tools import register_interactive_handlers
from telegram_page.gold.disclaimer_gate import (
    ensure_schema as ensure_disclaimer_schema,
    weekend_scheduler_loop,
)
import telegram_page.gold.gold_engine as gold_engine_mod

# ══════════════════════════════════════════════════════════════════════════════
# NOUVEAU : moteur de relance externalisé (cf. reminder_engine.py)
# ══════════════════════════════════════════════════════════════════════════════
from reminder_engine import (
    registration_reminder_loop,
    add_to_prospect,
    promote_prospect_to_main_active,
    normalize_phone_text,
    PHONE_FORMAT_HINT,
    PROSPECT_CATEGORY,
    peek_daily_counter,
    get_and_reset_daily_counter,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("fdk_bot")

CANAL_B_ID = int(os.getenv("CANAL_B_ID", "-1002705005402"))

# Liste des admins. ADMIN_ID (singulier) reste défini comme le principal pour
# les modules externes qui n'acceptent qu'un seul ID.
ADMIN_IDS = [6992809421, 571718066]
ADMIN_ID  = ADMIN_IDS[0]

# Ordre du tunnel : LEVEL → PHONE → NAME.
LEVEL, PHONE, NAME = range(3)

# Date de lancement du projet : toutes les statistiques "cumul" sont calculées
# à partir de cette date (rien avant n'est compté).
STATS_START_DATE = "2026-07-27"

token = os.getenv("tokens")

if not token:
    raise RuntimeError(
        "Variable d'environnement 'tokens' manquante : impossible de démarrer le bot."
    )

import asyncio as _asyncio_for_uvloop
import uvloop
_asyncio_for_uvloop.set_event_loop_policy(uvloop.EventLoopPolicy())


# ══════════════════════════════════════════════════════════════════════════════
# LOG D'ERREURS SUR FICHIER + NOTIFS ADMIN
# ══════════════════════════════════════════════════════════════════════════════

ERRORS_LOG_PATH = Path(
    os.getenv("ERRORS_LOG_PATH")
    or (Path(__file__).resolve().parent / "errors.log")
)


def _ensure_errors_log_writable():
    """Fallback vers /tmp si errors.log est indisponible en écriture."""
    global ERRORS_LOG_PATH
    try:
        ERRORS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ERRORS_LOG_PATH.open("a", encoding="utf-8") as f:
            pass
    except (PermissionError, OSError) as e:
        fallback = Path("/tmp") / "fdk_bot_errors.log"
        logger.warning(
            f"[log_error] impossible d'écrire dans {ERRORS_LOG_PATH} ({e}), "
            f"repli sur {fallback}"
        )
        try:
            with fallback.open("a", encoding="utf-8") as f:
                pass
            ERRORS_LOG_PATH = fallback
        except Exception:
            logger.exception("[log_error] même /tmp est indisponible, logs perdus")


_ensure_errors_log_writable()


def log_error(title: str, detail: str = ""):
    """Enregistre une erreur dans errors.log (silencieux, pas de notif Telegram)."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {title}"
        if detail:
            snippet = str(detail)[:4000].replace("\n", " | ")
            line += f" — {snippet}"
        with ERRORS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.exception("[log_error] impossible d'écrire dans errors.log")


async def notify_admin_critical(bot, title: str, detail: str = ""):
    """Erreur critique : envoi immédiat à tous les admins + fichier."""
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
    """Envoie un message informatif (non-erreur) à tous les admins."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
        except Exception:
            logger.exception(f"[broadcast_admins] échec envoi à {admin_id}")


async def send_and_rotate_errors_log(bot):
    """Envoie errors.log aux admins puis rotation. Appelé chaque soir à 20h."""
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
    """Colonnes ajoutées de manière idempotente (1060 = déjà présente)."""
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
                if "1060" in str(e) or "duplicate column" in str(e).lower():
                    continue
                logger.exception(f"[schema] échec: {stmt}")
                log_error("Échec ALTER schema users", f"{stmt} — {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE — enregistrement progressif dans `users`
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_user_row(telegram_id):
    """Crée une ligne vide dès l'approbation (idempotent, gère les demandes ||)."""
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
    Sauvegarde immédiate d'un champ dès la saisie.
    Écrit {field}_at avec NOW() UNIQUEMENT si NULL (premier remplissage).
    Retourne True si la ligne vient de devenir complète, ET dans ce cas
    programme la promotion PROSPECT → FDK CONCEPT CAPITAL LISTE ACTIFS.
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

    # Promotion catégorie : PROSPECT → FDK CONCEPT CAPITAL LISTE ACTIFS
    # (idempotent, safe même si le user n'était pas dans PROSPECT)
    if just_completed:
        asyncio.create_task(promote_prospect_to_main_active(telegram_id))

    return just_completed


async def read_back_field(telegram_id, field: str):
    """Relit un champ pour vérifier qu'il a bien été sauvegardé."""
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


# ══════════════════════════════════════════════════════════════════════════════
# COMPTEUR MILESTONE (100, 200, 300... inscriptions complètes)
# ══════════════════════════════════════════════════════════════════════════════

_last_100_notified = 0


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
LEVEL_CHOICES = [
    ("Débutant",      "🌱 Débutant"),
    ("Intermédiaire", "📈 Intermédiaire"),
    ("Avancé",        "🏆 Avancé"),
]
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
PHONE_Q = (
    "📱 Partagez votre numéro WhatsApp en un clic 👇\n\n"
    "<i>Ou tapez-le directement au format</i> <code>+229 00 00 00 00 00</code>."
)

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

LEVEL_REDIRECT_TEXT = (
    "⚠️ <b>Attention !</b>\n"
    "\n"
    "Il faut cliquer sur <b>votre niveau</b> juste en dessous 👇"
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

resume_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("▶️ Terminer mon enregistrement",
                          callback_data="resume_registration")]
])


def _missing_field_state(name, phone, level):
    """Renvoie l'étape manquante (LEVEL/PHONE/NAME) ou None si tout OK."""
    if not (level and str(level).strip()):
        return LEVEL
    if not (phone and str(phone).strip()):
        return PHONE
    if not (name and str(name).strip()):
        return NAME
    return None


async def _prompt_state(bot, chat_id, state, resume=False):
    """Envoie la question de l'étape manquante. Peut lever Forbidden."""
    prefix = "▶️ <b>Reprenons votre enregistrement.</b>\n\n" if resume else ""

    if state == LEVEL:
        await bot.send_message(chat_id=chat_id, text=prefix + LEVEL_Q,
                               parse_mode="HTML", reply_markup=level_keyboard)
    elif state == PHONE:
        await bot.send_message(chat_id=chat_id, text=prefix + PHONE_Q,
                               parse_mode="HTML", reply_markup=phone_share_keyboard)
    elif state == NAME:
        await bot.send_message(chat_id=chat_id, text=prefix + NAME_Q,
                               parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# TUNNEL D'ENREGISTREMENT
# ══════════════════════════════════════════════════════════════════════════════

async def get_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 1 : clic sur un bouton de niveau."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        idx = int(query.data.split(":", 1)[-1])
        value, display = LEVEL_CHOICES[idx]
    except (ValueError, IndexError):
        log_error("callback_data level invalide", f"user_id={user_id} data={query.data}")
        return LEVEL

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


async def _save_phone_and_advance(update, context, phone: str, source: str):
    """Logique commune aux deux entrées (contact + texte)."""
    user_id = update.effective_user.id

    context.user_data["phone"] = phone

    # Sauvegarde + vérification par relecture immédiate
    just_completed = False
    saved_ok = False
    try:
        just_completed = await save_registration_field(user_id, "phone", phone)
        readback = await read_back_field(user_id, "phone")
        saved_ok = bool(readback and str(readback).strip())
        if not saved_ok:
            await notify_admin_critical(
                context.bot,
                "Numéro non persisté après save",
                f"user_id={user_id} phone_reçu={phone!r} phone_relu={readback!r}"
            )
    except Exception as e:
        logger.exception(f"[_save_phone] échec sauvegarde pour {user_id}")
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
        logger.exception(f"[_save_phone] échec envoi message pour {user_id}")
        log_error("Échec envoi message (_save_phone)", f"user_id={user_id} — {e}")

    return NAME


async def get_phone_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 2a : contact partagé via le bouton natif Telegram."""
    user_id = update.effective_user.id

    if update.message.contact is None:
        log_error("get_phone_contact sans contact", f"user_id={user_id}")
        try:
            await update.message.reply_text(
                PHONE_Q, parse_mode="HTML", reply_markup=phone_share_keyboard,
            )
        except Exception:
            pass
        return PHONE

    phone = update.message.contact.phone_number
    if not phone:
        log_error("Numéro vide reçu (contact)", f"user_id={user_id}")
        try:
            await update.message.reply_text(
                PHONE_Q, parse_mode="HTML", reply_markup=phone_share_keyboard,
            )
        except Exception:
            pass
        return PHONE

    return await _save_phone_and_advance(update, context, phone, source="contact")


async def get_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Étape 2b : texte libre. Accepté si ça ressemble à un numéro,
    sinon on renvoie le format attendu."""
    user_id = update.effective_user.id
    raw = update.message.text or ""

    normalized = normalize_phone_text(raw)
    if not normalized:
        try:
            await update.message.reply_text(
                PHONE_FORMAT_HINT, parse_mode="HTML",
                reply_markup=phone_share_keyboard,
            )
        except Exception as e:
            logger.exception("[get_phone_text] échec envoi hint")
            log_error("Échec envoi format hint", f"user_id={user_id} — {e}")
        return PHONE

    return await _save_phone_and_advance(update, context, normalized, source="text")


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
    """Texte reçu à l'étape LEVEL au lieu d'un clic → on redemande le clic."""
    try:
        await update.message.reply_text(
            LEVEL_REDIRECT_TEXT, parse_mode="HTML", reply_markup=level_keyboard,
        )
    except Exception as e:
        logger.exception("[level_redirect] échec envoi")
        log_error("Échec envoi level_redirect",
                  f"user_id={update.effective_user.id} — {e}")
    return LEVEL


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.reply_text("❌ Annulé.", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.exception("[cancel] échec envoi message")
        log_error("Échec envoi message (cancel)", str(e))
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# REPRISE VIA BOUTON DE RELANCE
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
# APPROBATION DES DEMANDES D'ADHÉSION (canal principal uniquement)
# ══════════════════════════════════════════════════════════════════════════════

async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Flow :
      1. Créer la ligne DB
      2. Approuver la demande
      3. Essayer d'envoyer le message d'accueil au user
      4. SEULEMENT si l'envoi réussit → ajouter à la catégorie PROSPECT
         (si l'envoi échoue, le user ne sera jamais relancé — cohérent
          avec le fait qu'on ne peut pas lui parler)
    """
    user = update.chat_join_request.from_user
    user_id = user.id
    chat = update.chat_join_request.chat
    logger.info(
        f"[join] request user={user_id} chat_id={chat.id} title={chat.title!r}"
    )

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

    # 3. Lire l'état actuel pour choisir le bon message
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
            await context.bot.send_message(chat_id=user_id, text=WELCOME_TEXT,
                                           parse_mode="HTML", reply_markup=level_keyboard)
            sent_ok = True
            return_state = LEVEL

        else:
            context.user_data["name"]  = name
            context.user_data["phone"] = phone
            context.user_data["level"] = level
            await context.bot.send_message(chat_id=user_id, text=RESUME_INTRO_TEXT,
                                           parse_mode="HTML")
            await _prompt_state(context.bot, user_id, state)
            sent_ok = True
            return_state = state

    except Forbidden as e:
        # User jamais démarré /start ou bot bloqué → PAS d'ajout catégorie
        logger.warning(f"[join] impossible d'écrire en privé à {user_id}: {e}")
        log_error("Utilisateur non joignable en privé après approbation",
                  f"user_id={user_id} — {e}")
        return ConversationHandler.END

    except Exception as e:
        logger.exception(f"[join] erreur inattendue envoi message d'accueil à {user_id}")
        log_error("Erreur envoi message d'accueil", f"user_id={user_id} — {e}")
        return ConversationHandler.END

    # 5. Envoi OK → ajout à la catégorie PROSPECT (donc relançable)
    #    NB : si le user complète immédiatement son formulaire, la promotion
    #    PROSPECT → FDK CONCEPT CAPITAL LISTE ACTIFS sera faite automatiquement
    #    par save_registration_field().
    if sent_ok:
        try:
            await add_to_prospect(user_id)
        except Exception as e:
            logger.exception(f"[join] add_to_prospect error pour {user_id}")
            log_error("Erreur lors de l'ajout à PROSPECT",
                      f"user_id={user_id} — {e}")

    return return_state


# ══════════════════════════════════════════════════════════════════════════════
# GOLD v8 — signal brut + disclaimer hebdo + outils à la demande
#
# ATTENTION — DÉPENDANCES NON REVUES :
# lifecycle.py, session_registry.py et tp_notifier.py n'ont pas été
# fournis pour ce refactor. gold_engine.watch_gold_price() (conservé
# tel quel) s'appuie dessus pour détecter TP/SL et fermer la session
# (session_registry.current() + lifecycle.close_session()). Comme le
# nouvel endpoint interne n'appelle plus lifecycle.open_new_session(),
# session_registry restera vide et ces fermetures automatiques ne se
# déclencheront plus. Il faut revoir ces 3 fichiers pour rebrancher
# correctement le suivi TP/SL sur le nouveau flux (voir échange avec
# l'utilisateur — question posée en fin de réponse).
# ══════════════════════════════════════════════════════════════════════════════
from telegram_page.gold.lifecycle import register_buffer
from telegram_page.gold.gold_buffer import gold_buffer


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDES ADMIN
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_queue_status(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        s = gold_buffer.status()
        await update.message.reply_text(
            f"📊 Buffer Gold\n"
            f"Attaché à : {s['attached']}\n"
            f"En attente : {s['pending']} "
            f"(entries {s['entries']} / steps {s['steps']} / events {s['events']})\n"
            f"Agg dirty : {s['dirty_agg']}\n"
            f"Worker actif : {'✅' if s['worker_running'] else '❌'}"
        )
    except Exception as e:
        logger.exception("[cmd_queue_status] erreur")
        log_error("Erreur /queue_status", str(e))


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
    """Bilan complet des inscriptions à la demande (ne reset PAS le compteur)."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        await update.message.reply_text("⏳ Calcul en cours...")
        s = await compute_daily_stats()
        reminders_today = peek_daily_counter()
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
# STATS QUOTIDIENNES
# ══════════════════════════════════════════════════════════════════════════════

async def compute_daily_stats() -> dict:
    stats = {}
    async with sync_get_db() as cur:
        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE DATE(created_at) = CURDATE()")
        stats["new_today"] = int((await cur.fetchone())["n"])

        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE DATE(completed_at) = CURDATE()")
        stats["completed_today"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE YEARWEEK(created_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["new_this_week"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE YEARWEEK(completed_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["completed_this_week"] = int((await cur.fetchone())["n"])

        await cur.execute("SELECT COUNT(*) AS n FROM users WHERE created_at >= %s",
                          (STATS_START_DATE,))
        stats["total_users"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE completed_at IS NOT NULL AND created_at >= %s",
            (STATS_START_DATE,)
        )
        stats["total_completed"] = int((await cur.fetchone())["n"])

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
    """Bilan complet aux admins + reset du compteur de relances."""
    try:
        s = await compute_daily_stats()
    except Exception as e:
        logger.exception("[stats] échec compute_daily_stats")
        await notify_admin_critical(bot, "Échec calcul stats quotidiennes", str(e))
        return

    reminders_today = get_and_reset_daily_counter()
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


async def schedule_daily_check(bot):
    while True:
        try:
            now    = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            # 1. Bilan Gold (comptes simulation en danger — l'alerte par
            # membre réel a été retirée : gold_member_entries n'est plus
            # alimentée depuis le passage au signal brut)
            try:
                results      = await daily_cramed_check()
                total_danger = sum(r.get("total_danger", 0) for r in results)
                if total_danger > 0:
                    await broadcast_admins(
                        bot,
                        f"📋 <b>Bilan Gold — {datetime.now().strftime('%d/%m/%Y')}</b>\n\n"
                        f"Comptes simulation en danger : <b>{total_danger}</b>\n"
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

            # 3. Envoi + rotation errors.log
            try:
                await send_and_rotate_errors_log(bot)
            except Exception as e:
                logger.exception("[daily_check] erreur envoi errors.log")

        except Exception as e:
            logger.exception("[daily_check] erreur")
            log_error("Erreur bilan quotidien (daily_check)", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SERVEUR HTTP INTERNE — déclenche l'envoi d'un signal depuis l'API (autre process)
# ══════════════════════════════════════════════════════════════════════════════

async def _internal_open_gold(request: web.Request) -> web.Response:
    """
    Endpoint appelé par le process API après création d'une session
    (gold_engine.create_gold_session côté API). Il ne fait plus que
    déclencher l'envoi brut du signal — plus de session_registry, plus
    de version, plus de teaser/disclaimer par clic.
    """
    try:
        data = await request.json()
        sid = int(data["session_id"])
        category = data.get("category")
    except Exception as e:
        logger.exception("[internal] payload invalide")
        return web.json_response({"ok": False, "error": f"bad_payload: {e}"}, status=400)

    if not gold_engine_mod._bot:
        return web.json_response({"ok": False, "error": "bot_unavailable"}, status=503)

    async def _run_send():
        try:
            report = await send_signal(gold_engine_mod._bot, sid, category=category)
            logger.info(f"[internal] signal envoyé sid={sid}: {report}")
        except Exception as e:
            logger.exception(f"[internal] envoi signal échoué sid={sid}")
            await notify_admin_critical(gold_engine_mod._bot,
                "Échec envoi signal (interne)", f"sid={sid}\n{e}")

    asyncio.create_task(_run_send())

    return web.json_response({"ok": True, "session_id": sid, "status": "started"})


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
    app = (Application.builder()
           .token(token)
           .concurrent_updates(512)
           .read_timeout(30).write_timeout(30)
           .build())

    async def _post_init(application):
        try:
            await init_pool()
            print("[main] Pool OK ✓")

            await ensure_users_schema()
            print("[main] Schéma users (level_at, name/phone nullable, reminders) OK ✓")

            await ensure_disclaimer_schema()
            print("[main] Schéma disclaimer hebdo OK ✓")

            try:
                from engagement import ensure_engagement_schema
                await ensure_engagement_schema()
                print("[main] Schéma engagement (vote, motivation_at, vote_at) OK ✓")
            except ImportError:
                pass
            except Exception as e:
                logger.exception("[post_init] échec ensure_engagement_schema")
                log_error("Échec ensure_engagement_schema", str(e))

            await setup_background_worker(application)
            asyncio.create_task(schedule_daily_check(application.bot))

            gold_buffer.start(application.bot)
            register_buffer(gold_buffer)

            asyncio.create_task(weekend_scheduler_loop(application.bot))

            # Boucle de relance — nouvelle logique (reminder_engine.py)
            asyncio.create_task(
                registration_reminder_loop(
                    application.bot,
                    notify_admin_critical=notify_admin_critical,
                )
            )

            await init_milestone_counter()
            await _start_internal_http_server()

            print("[main] Gold v8 initialisé ✓")
        except Exception as e:
            logger.exception("[post_init] échec initialisation")
            await notify_admin_critical(application.bot,
                "Échec initialisation du bot (post_init)", str(e))
            raise

    app.post_init = _post_init

    # ── Tunnel d'enregistrement ────────────────────────────────────────────
    # Ordre : LEVEL (boutons inline) → PHONE (contact OU texte) → NAME (texte).
    resume_entry = CallbackQueryHandler(resume_registration, pattern="^resume_registration$")
    from engagement import register_engagement_handlers

    registration_conv = ConversationHandler(
        entry_points=[
            ChatJoinRequestHandler(approve_join_request),
            resume_entry,
        ],
        states={
            LEVEL: [
                CallbackQueryHandler(get_level, pattern=r"^level:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, level_redirect),
                resume_entry,
            ],
            PHONE: [
                # Contact partagé via bouton natif
                MessageHandler(filters.CONTACT, get_phone_contact),
                # Texte : accepté si ça ressemble à un numéro, sinon hint
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_text),
                resume_entry,
            ],
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name), resume_entry],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )
    app.add_handler(registration_conv)

    register_engagement_handlers(app)
    register_validation_handler(app)
    register_formation_handler(app)
    register_form_handlers(app, app.bot, ADMIN_ID)

    register_interactive_handlers(app)

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
    app.add_handler(CommandHandler("incomplete_status", cmd_incomplete_status))
    app.add_handler(CommandHandler("stats_now", cmd_stats_now))
    app.add_handler(CommandHandler("errors_now", cmd_errors_now))

    register_broadcast_admin_handlers(app)

    app.add_error_handler(error_handler)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")

    _loop = _asyncio_for_uvloop.new_event_loop()
    _asyncio_for_uvloop.set_event_loop(_loop)

    app.run_polling(poll_interval=1)