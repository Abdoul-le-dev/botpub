import os
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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

NAME, PHONE, JOB = range(3)

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
# HELPER — notification admin centralisée
# ══════════════════════════════════════════════════════════════════════════════

async def notify_admin(bot, title: str, detail: str = ""):
    """Envoie un message d'alerte à TOUS les admins. Ne doit jamais lever d'exception."""
    text = f"⚠️ <b>{title}</b>"
    if detail:
        text += f"\n\n<code>{detail[:3500]}</code>"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            logger.exception(f"[notify_admin] impossible d'envoyer l'alerte à {admin_id}")


async def broadcast_admins(bot, text: str, parse_mode: str = "HTML"):
    """Envoie un message informatif (non-erreur) à tous les admins."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
        except Exception:
            logger.exception(f"[broadcast_admins] échec envoi à {admin_id}")


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA — table `users` réelle : ajout colonne profession + assouplissement NOT NULL
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_users_schema():
    """
    - Ajoute la colonne 'profession' (absente du schéma d'origine, alors que
      l'ancien code appelait save_user(profession=...) -> TypeError silencieux).
    - Ajoute 'last_reminder_at' pour le suivi des relances.
    - Ajoute 'reminder_count' pour l'escalade des relances (10min, 30min, 1h...).
    - Rend 'name' et 'phone' NULL-able pour permettre la sauvegarde progressive
      (on peut avoir le nom sans encore avoir le téléphone).
    Idempotent : peut être exécuté à chaque démarrage sans effet de bord si déjà appliqué.
    Utilise db.get_db() (aiomysql, pool async) — même connexion que le reste du bot.
    """
    ddl_statements = [
        "ALTER TABLE users ADD COLUMN profession VARCHAR(255) NULL",
        "ALTER TABLE users ADD COLUMN last_reminder_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN reminder_count INT NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN name_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN phone_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN profession_at DATETIME NULL",
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


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE — enregistrement progressif directement dans `users`
# (db.get_db() est async, DictCursor -> les lignes sont des dicts, placeholders %s,
#  commit automatique en fin de bloc "async with", rollback auto sur exception)
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_user_row(telegram_id):
    """
    Crée une ligne vide dès l'approbation, même si l'utilisateur ne répond jamais tout de suite.
    Atomique : gère le cas où plusieurs demandes d'adhésion arrivent en parallèle
    pour le même user_id (sinon deux INSERT concurrents -> IntegrityError 1062).
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
            "SELECT name, phone, profession FROM users WHERE telegram_id=%s",
            (telegram_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return row.get("name"), row.get("phone"), row.get("profession")


async def save_registration_field(telegram_id, field: str, value: str) -> bool:
    """
    Sauvegarde immédiate d'un champ dès sa saisie.
    Atomique (upsert). Écrit aussi {field}_at avec NOW() UNIQUEMENT si NULL
    (premier remplissage), pour les stats de temps de réponse.
    Retourne True si la ligne vient de devenir complète (name+phone+profession
    tous remplis et completed_at NULL jusque-là).
    """
    if field not in ("name", "phone", "profession"):
        raise ValueError(f"Champ non autorisé: {field}")
    field_at = f"{field}_at"

    async with sync_get_db() as cur:
        # 1. Upsert atomique avec tracking du premier timestamp
        await cur.execute(
            f"INSERT INTO users (telegram_id, created_at, {field}, {field_at}) "
            f"VALUES (%s, NOW(), %s, NOW()) "
            f"ON DUPLICATE KEY UPDATE "
            f"  {field} = VALUES({field}), "
            f"  {field_at} = COALESCE({field_at}, NOW())",
            (telegram_id, value)
        )

        # 2. Marquer complet si tous les champs sont maintenant remplis
        await cur.execute(
            "UPDATE users SET completed_at = NOW() "
            "WHERE telegram_id = %s "
            "  AND completed_at IS NULL "
            "  AND name IS NOT NULL AND name <> '' "
            "  AND phone IS NOT NULL AND phone <> '' "
            "  AND profession IS NOT NULL AND profession <> ''",
            (telegram_id,)
        )
        just_completed = cur.rowcount == 1

    return just_completed


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
            "OR (profession IS NULL OR profession = '')"
        )
        rows = await cur.fetchall()
    return [r["telegram_id"] for r in rows]


async def get_incomplete_users_full():
    """Comme get_incomplete_telegram_ids mais avec les infos nécessaires à
    l'escalade des relances (created_at, last_reminder_at, reminder_count)."""
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT telegram_id, created_at, last_reminder_at, reminder_count "
            "FROM users WHERE "
            "(name IS NULL OR name = '') "
            "OR (phone IS NULL OR phone = '') "
            "OR (profession IS NULL OR profession = '')"
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
        # Palier suivant à franchir
        next_threshold = ((_last_100_notified // 100) + 1) * 100
        if total >= next_threshold:
            # On rattrape aussi le retard si plusieurs paliers ont été franchis
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
        await notify_admin(bot, "Erreur check_and_notify_milestone", str(e))


async def init_milestone_counter():
    """Au démarrage, aligne _last_100_notified sur le total actuel arrondi à 100
    pour ne pas re-notifier les paliers passés."""
    global _last_100_notified
    try:
        total = await count_total_completed()
        _last_100_notified = (total // 100) * 100
        logger.info(f"[milestone] initialisé à {_last_100_notified} (total={total})")
    except Exception:
        logger.exception("[milestone] échec init")


# ══════════════════════════════════════════════════════════════════════════════
# FORMATION
# ══════════════════════════════════════════════════════════════════════════════
keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "🎓 Accéder à la formation",
            url="https://fdksignal.com/formation/formation-debutant"
        )
    ]
])


# ══════════════════════════════════════════════════════════════════════════════
# TEXTES & OUTILS RÉUTILISABLES POUR LE TUNNEL D'ENREGISTREMENT
# ══════════════════════════════════════════════════════════════════════════════

# ── Questions réutilisables (centralisées pour reprise / relance) ────────────
NAME_Q  = "👤 Quel est votre <b>nom et prénom</b> ?\n\n<i>Exemple :</i>\nFiacre Kpanou"
PHONE_Q = "📱 Quel est votre numéro WhatsApp ?\n\nExemple : +229 97 00 00 00"
JOB_Q   = ("💼 Quelle est votre profession ou votre activité ?\n\n"
           "Exemple :\nÉtudiant\nCommerçant\nEmployé\nEntrepreneur")

# Rempli une fois NAME/PHONE/JOB connus (voir _init_state_questions plus bas)
_STATE_QUESTION = {}

WELCOME_TEXT = (
    "🎉 <b>Félicitations !</b>\n\n"
    "Votre demande a été acceptée et vous êtes <b>éligible</b> pour participer à "
    "<b>FDK CAPITAL CONCEPT</b>.\n\n"
    "📝 Pour valider définitivement votre participation, vous devez maintenant vous enregistrer.\n\n"
    "Commençons.\n\n" + NAME_Q
)

ALREADY_REGISTERED_TEXT = (
    "✅ <b>Vous êtes déjà inscrit !</b>\n\n"
    "Votre enregistrement est complet et vous êtes bien <b>éligible</b>.\n\n"
    "🎯 Vous figurez officiellement sur la liste des participants de "
    "<b>FDK CAPITAL CONCEPT</b>.\n\n"
    "📅 Chaque samedi, les bénéficiaires sont sélectionnés en direct devant toute la communauté."
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

# Bouton de reprise attaché aux relances
resume_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("▶️ Terminer mon enregistrement",
                          callback_data="resume_registration")]
])


def _init_state_questions():
    _STATE_QUESTION.update({NAME: NAME_Q, PHONE: PHONE_Q, JOB: JOB_Q})


_init_state_questions()


def _missing_field_state(name, phone, profession):
    """Renvoie l'étape (NAME/PHONE/JOB) manquante, ou None si tout est rempli."""
    if not (name and str(name).strip()):
        return NAME
    if not (phone and str(phone).strip()):
        return PHONE
    if not (profession and str(profession).strip()):
        return JOB
    return None


async def _prompt_state(bot, chat_id, state, resume=False):
    """Envoie la question correspondant à l'étape manquante."""
    q = _STATE_QUESTION[state]
    if resume:
        q = "▶️ <b>Reprenons votre enregistrement.</b>\n\n" + q
    await bot.send_message(chat_id=chat_id, text=q, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# TUNNEL D'ENREGISTREMENT (ConversationHandler)
# ══════════════════════════════════════════════════════════════════════════════

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.message.text.strip()
    context.user_data["name"] = name

    just_completed = False
    try:
        just_completed = await save_registration_field(user_id, "name", name)
    except Exception as e:
        logger.exception(f"[get_name] échec sauvegarde pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde du nom", f"user_id={user_id}\n{e}")

    if just_completed:
        asyncio.create_task(check_and_notify_milestone(context.bot))

    try:
        await update.message.reply_text(PHONE_Q, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"[get_name] échec envoi message pour {user_id}")
        await notify_admin(context.bot, "Échec envoi message (get_name)", f"user_id={user_id}\n{e}")

    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    context.user_data["phone"] = phone

    just_completed = False
    try:
        just_completed = await save_registration_field(user_id, "phone", phone)
    except Exception as e:
        logger.exception(f"[get_phone] échec sauvegarde pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde du téléphone", f"user_id={user_id}\n{e}")

    if just_completed:
        asyncio.create_task(check_and_notify_milestone(context.bot))

    try:
        await update.message.reply_text(JOB_Q, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"[get_phone] échec envoi message pour {user_id}")
        await notify_admin(context.bot, "Échec envoi message (get_phone)", f"user_id={user_id}\n{e}")

    return JOB


async def get_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    job = update.message.text.strip()
    context.user_data["job"] = job

    just_completed = False
    try:
        just_completed = await save_registration_field(user_id, "profession", job)
    except Exception as e:
        logger.exception(f"[get_job] échec sauvegarde profession pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde de la profession", f"user_id={user_id}\n{e}")

    if just_completed:
        # Palier "N x 100 membres" éventuel
        asyncio.create_task(check_and_notify_milestone(context.bot))

    try:
        await update.message.reply_text(
            "✅ <b>Vous êtes maintenant enregistré !</b>\n\n"
            "Votre candidature a bien été prise en compte.\n\n"
            "📅 Chaque samedi, les bénéficiaires sont sélectionnés en direct devant toute la communauté.\n\n"
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
        logger.exception(f"[get_job] échec envoi messages finaux pour {user_id}")
        await notify_admin(context.bot, "Échec envoi messages finaux", f"user_id={user_id}\n{e}")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.reply_text("❌ Annulé.")
    except Exception as e:
        logger.exception("[cancel] échec envoi message")
        await notify_admin(context.bot, "Échec envoi message (cancel)", str(e))
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# REPRISE VIA BOUTON — relit la base et repositionne l'étape manquante
# (entry point de la conversation : fonctionne même après un redémarrage du bot)
# ══════════════════════════════════════════════════════════════════════════════

async def resume_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    name = phone = profession = None
    try:
        row = await get_user_row(user_id)
        if row:
            name, phone, profession = row
    except Exception as e:
        logger.exception(f"[resume] échec get_user_row pour {user_id}")
        await notify_admin(context.bot, "Échec get_user_row (resume)", f"user_id={user_id}\n{e}")

    state = _missing_field_state(name, phone, profession)

    if state is None:
        # Déjà complet : on confirme simplement
        try:
            await context.bot.send_message(chat_id=user_id, text=ALREADY_REGISTERED_TEXT,
                                           parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[resume] échec confirmation à {user_id}: {e}")
        return ConversationHandler.END

    # On restaure ce qu'on connaît déjà et on reprend à l'étape manquante
    context.user_data["name"]  = name
    context.user_data["phone"] = phone
    try:
        await _prompt_state(context.bot, user_id, state, resume=True)
    except Exception as e:
        logger.warning(f"[resume] échec prompt à {user_id}: {e}")
        return ConversationHandler.END
    return state


# ══════════════════════════════════════════════════════════════════════════════
# APPROBATION DES DEMANDES D'ADHÉSION — point d'entrée du ConversationHandler
# ══════════════════════════════════════════════════════════════════════════════

async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.chat_join_request.from_user
    user_id = user.id
    chat_id = update.chat_join_request.chat.id

    # 1. Enregistrement immédiat de l'utilisateur (même incomplet), pour qu'il
    #    apparaisse dans la relance même si le message de bienvenue échoue.
    try:
        await ensure_user_row(user_id)
    except Exception as e:
        logger.exception(f"[join] échec ensure_user_row pour {user_id}")
        await notify_admin(context.bot, "Échec création ligne users", f"user_id={user_id}\n{e}")

    # 2. Approbation de la demande
    try:
        await update.chat_join_request.approve()
    except BadRequest as e:
        msg = str(e).lower()
        if "already" in msg or "participant" in msg:
            logger.info(f"[join] {user_id} déjà membre.")
        else:
            logger.exception(f"[join] échec approve() pour {user_id}")
            await notify_admin(context.bot, "Échec approbation demande d'adhésion", f"user_id={user_id}\n{e}")
        return ConversationHandler.END
    except Exception as e:
        logger.exception(f"[join] erreur inattendue approve() pour {user_id}")
        await notify_admin(context.bot, "Erreur inattendue lors de l'approbation", f"user_id={user_id}\n{e}")
        return ConversationHandler.END

    # 3. Ajout à la catégorie
    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(CATEGORIE, [user_id])
    except Exception as e:
        logger.exception(f"[join] categorie error pour {user_id}")
        await notify_admin(context.bot, "Erreur lors de l'ajout à la catégorie", f"user_id={user_id}\n{e}")

    # 4. Vérifier l'état d'enregistrement AVANT d'écrire :
    #    - déjà complet  -> message "déjà inscrit / éligible"
    #    - tout nouveau  -> message de bienvenue (demande le nom)
    #    - incomplet     -> on reprend exactement là où il en était
    #
    #    ATTENTION : Telegram interdit à un bot d'initier une conversation privée
    #    avec un utilisateur qui n'a jamais démarré /start avec lui. Une demande
    #    d'adhésion à un canal NE GARANTIT PAS cela. C'est la cause la plus probable
    #    d'un enregistrement qui "ne se déclenche jamais" pour certains utilisateurs.
    name = phone = profession = None
    try:
        row = await get_user_row(user_id)
        if row:
            name, phone, profession = row
    except Exception as e:
        logger.exception(f"[join] échec get_user_row pour {user_id}")

    state = _missing_field_state(name, phone, profession)

    try:
        if state is None:
            # Déjà inscrit et complet
            await context.bot.send_message(chat_id=user_id,
                                           text=ALREADY_REGISTERED_TEXT, parse_mode="HTML")
            return ConversationHandler.END

        if not name and not phone and not profession:
            # Tout nouveau → message de bienvenue complet (contient la question du nom)
            await context.bot.send_message(chat_id=user_id, text=WELCOME_TEXT, parse_mode="HTML")
            return NAME

        # Incomplet → on reprend exactement là où il en était
        context.user_data["name"]  = name
        context.user_data["phone"] = phone
        await context.bot.send_message(chat_id=user_id, text=RESUME_INTRO_TEXT, parse_mode="HTML")
        await _prompt_state(context.bot, user_id, state)
        return state

    except Forbidden as e:
        # L'utilisateur n'a jamais démarré de conversation privée avec le bot,
        # ou l'a bloqué. Il restera "incomplet" et sera relancé par le scheduler
        # dès qu'il aura interagi avec le bot au moins une fois.
        logger.warning(f"[join] impossible d'écrire en privé à {user_id}: {e}")
        await notify_admin(
            context.bot,
            "Utilisateur non joignable en privé après approbation",
            f"user_id={user_id}\n"
            "L'utilisateur n'a probablement jamais démarré /start avec le bot. "
            "Il sera relancé automatiquement dès que possible.\n"
            f"{e}"
        )
        return ConversationHandler.END

    except Exception as e:
        logger.exception(f"[join] erreur inattendue envoi message d'accueil à {user_id}")
        await notify_admin(context.bot, "Erreur envoi message d'accueil", f"user_id={user_id}\n{e}")
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# RELANCE AUTOMATIQUE — escalade : 10min, 30min, 1h, 2h, 4h, 8h, 16h, puis 24h
# Chaque relance porte un bouton "Terminer mon enregistrement".
# ══════════════════════════════════════════════════════════════════════════════

# Intervalles ENTRE relances (minutes). Une fois la liste épuisée, on reste
# sur le dernier (24h) indéfiniment.
REMINDER_OFFSETS_MINUTES = [10, 30, 60, 120, 240, 480, 960, 1440]


async def registration_reminder_loop(bot):
    CHECK_INTERVAL = 60  # on vérifie chaque minute qui est "dû"

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)

            try:
                rows = await get_incomplete_users_full()
            except Exception as e:
                logger.exception("[reminder] échec récupération des utilisateurs incomplets")
                await notify_admin(bot, "Échec requête utilisateurs incomplets", str(e))
                continue

            now = datetime.now()
            sent, failed = 0, 0

            for r in rows:
                user_id = r["telegram_id"]
                if not user_id:
                    continue

                count = int(r.get("reminder_count") or 0)
                # Étape suivante du barème (plafonnée au dernier = 24h en boucle)
                idx = min(count, len(REMINDER_OFFSETS_MINUTES) - 1)
                offset = timedelta(minutes=REMINDER_OFFSETS_MINUTES[idx])

                # Point de départ : dernière relance, sinon date de création
                last_event = r.get("last_reminder_at") or r.get("created_at")
                if last_event is None:
                    continue
                if now - last_event < offset:
                    continue  # pas encore l'heure de relancer

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
                    # jamais démarré /start ou a bloqué le bot : normal, on ignore
                    failed += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"[reminder] échec envoi à {user_id}: {e}")

            if sent or failed:
                logger.info(f"[reminder] cycle terminé — envoyées={sent} échecs={failed}")

        except Exception as e:
            logger.exception("[reminder] erreur inattendue dans la boucle")
            try:
                await notify_admin(bot, "Erreur inattendue — boucle de relance", str(e))
            except Exception:
                pass


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
        await notify_admin(context.bot, "Erreur /queue_status", str(e))


async def cmd_gold_check(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        rep = await run_full_check()
        await update.message.reply_text(rep.summary())
    except Exception as e:
        logger.exception("[cmd_gold_check] erreur")
        await notify_admin(context.bot, "Erreur /gold_check", str(e))


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
        await notify_admin(context.bot, "Erreur /capital_status", str(e))


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
                await notify_admin(context.bot, "Échec run_campaign (tâche de fond)", str(e))

        asyncio.create_task(_run())
    except Exception as e:
        logger.exception("[cmd_capital_campaign_now] erreur")
        await notify_admin(context.bot, "Erreur /capital_campaign_now", str(e))


async def cmd_incomplete_status(update, context):
    """Nouvelle commande admin : voir combien d'utilisateurs sont incomplets."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        telegram_ids = await get_incomplete_telegram_ids()
        await update.message.reply_text(
            f"📋 Enregistrements incomplets : {len(telegram_ids)}"
        )
    except Exception as e:
        logger.exception("[cmd_incomplete_status] erreur")
        await notify_admin(context.bot, "Erreur /incomplete_status", str(e))


async def cmd_stats_now(update, context):
    """Envoie le bilan complet des inscriptions à la demande (ne reset PAS le compteur)."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        await update.message.reply_text("⏳ Calcul en cours...")
        # On copie la logique de send_daily_stats_report mais sans reset
        s = await compute_daily_stats()
        reminders_today = _daily_reminders_sent
        completion_rate_global = _pct(s["total_completed"], s["total_users"])
        completion_rate_today  = _pct(s["completed_today"], s["new_today"])
        completion_rate_week   = _pct(s["completed_this_week"], s["new_this_week"])
        total_stuck = s["stuck_before_name"] + s["stuck_at_phone"] + s["stuck_at_profession"]

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
            f"• Approbation → nom : <b>{_fmt_duration(s['avg_created_to_name_s'])}</b>\n"
            f"• Nom → Téléphone : <b>{_fmt_duration(s['avg_name_to_phone_s'])}</b>\n"
            f"• Téléphone → Profession : <b>{_fmt_duration(s['avg_phone_to_profession_s'])}</b>\n"
            f"• Durée totale : <b>{_fmt_duration(s['avg_total_completion_s'])}</b>\n\n"
            f"<b>🚨 Points de blocage</b>\n"
            f"• Sans nom : <b>{s['stuck_before_name']}</b> ({_pct(s['stuck_before_name'], s['total_users'])})\n"
            f"• Bloqués téléphone : <b>{s['stuck_at_phone']}</b> ({_pct(s['stuck_at_phone'], s['total_users'])})\n"
            f"• Bloqués profession : <b>{s['stuck_at_profession']}</b> ({_pct(s['stuck_at_profession'], s['total_users'])})\n"
            f"• Total bloqués : <b>{total_stuck}</b>"
        )
        await update.message.reply_text(report, parse_mode="HTML")
    except Exception as e:
        logger.exception("[cmd_stats_now] erreur")
        await notify_admin(context.bot, "Erreur /stats_now", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# TÂCHE — bilan quotidien 20h
# ══════════════════════════════════════════════════════════════════════════════

async def compute_daily_stats() -> dict:
    """
    Rassemble toutes les statistiques d'inscription pour le bilan quotidien.
    Toutes les valeurs sont calculées en SQL pour rester rapides même
    avec plusieurs milliers de lignes.
    """
    stats = {}
    async with sync_get_db() as cur:
        # ─── Volumes du jour ────────────────────────────────────────
        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE DATE(created_at) = CURDATE()"
        )
        stats["new_today"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE DATE(completed_at) = CURDATE()"
        )
        stats["completed_today"] = int((await cur.fetchone())["n"])

        # ─── Volumes de la semaine en cours (semaine ISO, lundi→dimanche) ──
        await cur.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE YEARWEEK(created_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["new_this_week"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE YEARWEEK(completed_at, 3) = YEARWEEK(CURDATE(), 3)"
        )
        stats["completed_this_week"] = int((await cur.fetchone())["n"])

        # ─── Totaux cumulés (à partir de la date de lancement) ──────
        await cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE created_at >= %s",
            (STATS_START_DATE,)
        )
        stats["total_users"] = int((await cur.fetchone())["n"])

        await cur.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE completed_at IS NOT NULL AND created_at >= %s",
            (STATS_START_DATE,)
        )
        stats["total_completed"] = int((await cur.fetchone())["n"])

        # ─── Funnel — où les gens abandonnent (depuis le lancement) ─
        await cur.execute(
            "SELECT "
            "  SUM(CASE WHEN name IS NULL OR name = '' THEN 1 ELSE 0 END)         AS stuck_before_name, "
            "  SUM(CASE WHEN name IS NOT NULL AND name <> '' "
            "           AND (phone IS NULL OR phone = '') THEN 1 ELSE 0 END)       AS stuck_at_phone, "
            "  SUM(CASE WHEN phone IS NOT NULL AND phone <> '' "
            "           AND (profession IS NULL OR profession = '') THEN 1 ELSE 0 END) AS stuck_at_profession "
            "FROM users WHERE created_at >= %s",
            (STATS_START_DATE,)
        )
        row = await cur.fetchone() or {}
        stats["stuck_before_name"]     = int(row.get("stuck_before_name") or 0)
        stats["stuck_at_phone"]        = int(row.get("stuck_at_phone") or 0)
        stats["stuck_at_profession"]   = int(row.get("stuck_at_profession") or 0)

        # ─── Temps de réponse moyens (en secondes) ──────────────────
        # 1er message (nom) après approbation
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, name_at)) AS avg_s "
            "FROM users WHERE name_at IS NOT NULL AND created_at IS NOT NULL "
            "AND name_at >= created_at"
        )
        stats["avg_created_to_name_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # nom -> téléphone
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, name_at, phone_at)) AS avg_s "
            "FROM users WHERE phone_at IS NOT NULL AND name_at IS NOT NULL "
            "AND phone_at >= name_at"
        )
        stats["avg_name_to_phone_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # téléphone -> profession
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, phone_at, profession_at)) AS avg_s "
            "FROM users WHERE profession_at IS NOT NULL AND phone_at IS NOT NULL "
            "AND profession_at >= phone_at"
        )
        stats["avg_phone_to_profession_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # durée totale d'inscription
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, completed_at)) AS avg_s "
            "FROM users WHERE completed_at IS NOT NULL"
        )
        stats["avg_total_completion_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # ─── Réactivité du jour (pour comparaison) ──────────────────
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, name_at)) AS avg_s "
            "FROM users WHERE DATE(name_at) = CURDATE() AND name_at >= created_at"
        )
        stats["avg_created_to_name_today_s"] = float((await cur.fetchone())["avg_s"] or 0)

        # ─── Réactivité de la semaine en cours ──────────────────────
        await cur.execute(
            "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, name_at)) AS avg_s "
            "FROM users WHERE YEARWEEK(name_at, 3) = YEARWEEK(CURDATE(), 3) "
            "AND name_at >= created_at"
        )
        stats["avg_created_to_name_week_s"] = float((await cur.fetchone())["avg_s"] or 0)

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
    """Envoie le bilan complet aux deux admins et remet les compteurs à zéro."""
    global _daily_reminders_sent

    try:
        s = await compute_daily_stats()
    except Exception as e:
        logger.exception("[stats] échec compute_daily_stats")
        await notify_admin(bot, "Échec calcul stats quotidiennes", str(e))
        return

    reminders_today = _daily_reminders_sent
    completion_rate_global = _pct(s["total_completed"], s["total_users"])
    completion_rate_today  = _pct(s["completed_today"], s["new_today"])
    completion_rate_week   = _pct(s["completed_this_week"], s["new_this_week"])

    total_stuck = s["stuck_before_name"] + s["stuck_at_phone"] + s["stuck_at_profession"]
    start_fr = datetime.strptime(STATS_START_DATE, '%Y-%m-%d').strftime('%d/%m/%Y')

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

        f"<b>⏱ Temps de réponse moyens (tous)</b>\n"
        f"• Approbation → 1er message (nom) : <b>{_fmt_duration(s['avg_created_to_name_s'])}</b>\n"
        f"• Nom → Téléphone : <b>{_fmt_duration(s['avg_name_to_phone_s'])}</b>\n"
        f"• Téléphone → Profession : <b>{_fmt_duration(s['avg_phone_to_profession_s'])}</b>\n"
        f"• Durée totale d'inscription : <b>{_fmt_duration(s['avg_total_completion_s'])}</b>\n\n"

        f"<b>⏱ Réactivité</b>\n"
        f"• Approbation → 1er message aujourd'hui : "
        f"<b>{_fmt_duration(s['avg_created_to_name_today_s'])}</b>\n"
        f"• Approbation → 1er message cette semaine : "
        f"<b>{_fmt_duration(s['avg_created_to_name_week_s'])}</b>\n\n"

        f"<b>🚨 Funnel — points de blocage (depuis le lancement)</b>\n"
        f"• N'ont jamais envoyé leur nom : <b>{s['stuck_before_name']}</b> "
        f"({_pct(s['stuck_before_name'], s['total_users'])})\n"
        f"• Bloqués à l'étape téléphone : <b>{s['stuck_at_phone']}</b> "
        f"({_pct(s['stuck_at_phone'], s['total_users'])})\n"
        f"• Bloqués à l'étape profession : <b>{s['stuck_at_profession']}</b> "
        f"({_pct(s['stuck_at_profession'], s['total_users'])})\n"
        f"• Total bloqués : <b>{total_stuck}</b>\n\n"

        f"<i>💡 L'étape avec le plus d'abandons est celle à optimiser en priorité.</i>"
    )

    await broadcast_admins(bot, report)

    # Reset compteur journalier
    _daily_reminders_sent = 0


async def schedule_daily_check(bot):
    while True:
        try:
            now    = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            # ── 1. Bilan Gold (existant)
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
                await notify_admin(bot, "Erreur bilan Gold quotidien", str(e))

            # ── 2. Bilan inscriptions (nouveau)
            try:
                await send_daily_stats_report(bot)
            except Exception as e:
                logger.exception("[daily_check] erreur bilan inscriptions")
                await notify_admin(bot, "Erreur bilan inscriptions quotidien", str(e))

        except Exception as e:
            logger.exception("[daily_check] erreur")
            try:
                await notify_admin(bot, "Erreur bilan quotidien (daily_check)", str(e))
            except Exception:
                pass


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
            await notify_admin(gold_engine_mod._bot, "Échec open_new_session", f"sid={sid}\n{e}")
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
                await notify_admin(gold_engine_mod._bot, "Échec broadcast v7", f"sid={sid}\n{e}")

        asyncio.create_task(_run_broadcast())
        bstatus = "started"
    else:
        try:
            mark_broadcast_done(snap.session_id, snap.version)
        except Exception as e:
            logger.exception(f"[internal] mark_broadcast_done failed sid={sid}")
            if gold_engine_mod._bot:
                await notify_admin(gold_engine_mod._bot, "Échec mark_broadcast_done", f"sid={sid}\n{e}")
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(init_pool())
    print("[main] Pool OK ✓")

    loop.run_until_complete(ensure_users_schema())
    print("[main] Schéma users (profession, name/phone nullable, reminder_count) OK ✓")

    loop.run_until_complete(ensure_capital_schema())
    loop.run_until_complete(ensure_campaign_schema())
    print("[main] Schémas v7 OK ✓")

    app = (Application.builder()
           .token(token)
           .concurrent_updates(512)
           .read_timeout(30).write_timeout(30)
           .build())

    async def _post_init(application):
        try:
            await setup_background_worker(application)
            asyncio.create_task(schedule_daily_check(application.bot))

            start_gold_write_worker(application.bot)

            gold_buffer.start(application.bot)
            register_buffer(gold_buffer)

            asyncio.create_task(weekly_scheduler_loop(application.bot))
            asyncio.create_task(registration_reminder_loop(application.bot))

            # Aligne le compteur "palier 100" sur l'état actuel de la base
            # pour ne pas re-notifier les paliers historiques au redémarrage
            await init_milestone_counter()

            await _start_internal_http_server()

            print("[main] Gold v7.1 initialisé ✓")
        except Exception as e:
            logger.exception("[post_init] échec initialisation")
            await notify_admin(application.bot, "Échec initialisation du bot (post_init)", str(e))
            raise

    app.post_init = _post_init

    # ── Tunnel d'enregistrement : déclenché par la demande d'adhésion OU par le
    #    bouton "Terminer mon enregistrement" (relance / reprise après restart),
    #    poursuivi par les réponses privées de l'utilisateur.
    resume_entry = CallbackQueryHandler(resume_registration, pattern="^resume_registration$")

    registration_conv = ConversationHandler(
        entry_points=[
            ChatJoinRequestHandler(approve_join_request),
            resume_entry,
        ],
        states={
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name),  resume_entry],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone), resume_entry],
            JOB:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_job),   resume_entry],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,   # la demande arrive sur le canal, les réponses en privé
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

    app.add_error_handler(error_handler)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=2)