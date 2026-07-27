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
logger = logging.getLogger("fdk_bot")

CANAL_B_ID = int(os.getenv("CANAL_B_ID", "-1002705005402"))
ADMIN_ID   = int(os.getenv("ADMIN_ID", "6992809421"))

NAME, PHONE, JOB = range(3)

CATEGORIE = "FDK CONCEPT CAPITAL W-2"
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
    """Envoie un message d'erreur à l'admin. Ne doit jamais lever d'exception."""
    text = f"⚠️ <b>{title}</b>"
    if detail:
        text += f"\n\n<code>{detail[:3500]}</code>"
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except Exception:
        logger.exception("[notify_admin] impossible d'envoyer l'alerte à l'admin")


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA — table `users` réelle : ajout colonne profession + assouplissement NOT NULL
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_users_schema():
    """
    - Ajoute la colonne 'profession' (absente du schéma d'origine, alors que
      l'ancien code appelait save_user(profession=...) -> TypeError silencieux).
    - Ajoute 'last_reminder_at' pour le suivi des relances.
    - Rend 'name' et 'phone' NULL-able pour permettre la sauvegarde progressive
      (on peut avoir le nom sans encore avoir le téléphone).
    Idempotent : peut être exécuté à chaque démarrage sans effet de bord si déjà appliqué.
    Utilise db.get_db() (aiomysql, pool async) — même connexion que le reste du bot.
    """
    ddl_statements = [
        "ALTER TABLE users ADD COLUMN profession VARCHAR(255) NULL",
        "ALTER TABLE users ADD COLUMN last_reminder_at DATETIME NULL",
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
    """Crée une ligne vide dès l'approbation, même si l'utilisateur ne répond jamais tout de suite."""
    async with sync_get_db() as cur:
        await cur.execute("SELECT id FROM users WHERE telegram_id=%s", (telegram_id,))
        row = await cur.fetchone()
        if not row:
            await cur.execute(
                "INSERT INTO users (telegram_id, created_at) VALUES (%s, NOW())",
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


async def save_registration_field(telegram_id, field: str, value: str):
    """Sauvegarde immédiate d'un champ dès sa saisie (nom, téléphone ou profession)."""
    if field not in ("name", "phone", "profession"):
        raise ValueError(f"Champ non autorisé: {field}")
    async with sync_get_db() as cur:
        await cur.execute("SELECT id FROM users WHERE telegram_id=%s", (telegram_id,))
        row = await cur.fetchone()
        if row:
            await cur.execute(
                f"UPDATE users SET {field}=%s WHERE telegram_id=%s",
                (value, telegram_id)
            )
        else:
            await cur.execute(
                f"INSERT INTO users (telegram_id, created_at, {field}) VALUES (%s, NOW(), %s)",
                (telegram_id, value)
            )


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


async def mark_reminder_sent(telegram_id):
    async with sync_get_db() as cur:
        await cur.execute(
            "UPDATE users SET last_reminder_at=NOW() WHERE telegram_id=%s",
            (telegram_id,)
        )


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
# TUNNEL D'ENREGISTREMENT (ConversationHandler)
# ══════════════════════════════════════════════════════════════════════════════

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.message.text.strip()
    context.user_data["name"] = name

    try:
        await save_registration_field(user_id, "name", name)
    except Exception as e:
        logger.exception(f"[get_name] échec sauvegarde pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde du nom", f"user_id={user_id}\n{e}")

    try:
        await update.message.reply_text(
            "📱 Quel est votre numéro WhatsApp ?\n\n"
            "Exemple : +229 97 00 00 00"
        )
    except Exception as e:
        logger.exception(f"[get_name] échec envoi message pour {user_id}")
        await notify_admin(context.bot, "Échec envoi message (get_name)", f"user_id={user_id}\n{e}")

    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    context.user_data["phone"] = phone

    try:
        await save_registration_field(user_id, "phone", phone)
    except Exception as e:
        logger.exception(f"[get_phone] échec sauvegarde pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde du téléphone", f"user_id={user_id}\n{e}")

    try:
        await update.message.reply_text(
            "💼 Quelle est votre profession ou votre activité ?\n\n"
            "Exemple :\n"
            "Étudiant\n"
            "Commerçant\n"
            "Employé\n"
            "Entrepreneur"
        )
    except Exception as e:
        logger.exception(f"[get_phone] échec envoi message pour {user_id}")
        await notify_admin(context.bot, "Échec envoi message (get_phone)", f"user_id={user_id}\n{e}")

    return JOB


async def get_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    job = update.message.text.strip()
    context.user_data["job"] = job

    try:
        # Écrit directement dans `users` (nom + téléphone déjà écrits aux étapes
        # précédentes). On n'appelle plus save_user() ici : sa signature ne
        # comporte pas de paramètre 'profession', l'appel levait TypeError
        # et faisait échouer silencieusement la sauvegarde finale.
        await save_registration_field(user_id, "profession", job)
    except Exception as e:
        logger.exception(f"[get_job] échec sauvegarde profession pour {user_id}")
        await notify_admin(context.bot, "Échec sauvegarde de la profession", f"user_id={user_id}\n{e}")

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

    # 4. Envoi du message d'accueil / démarrage du tunnel d'enregistrement
    #    ATTENTION : Telegram interdit à un bot d'initier une conversation privée
    #    avec un utilisateur qui n'a jamais démarré /start avec lui. Une demande
    #    d'adhésion à un canal NE GARANTIT PAS cela. C'est la cause la plus probable
    #    d'un enregistrement qui "ne se déclenche jamais" pour certains utilisateurs.
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Félicitations !</b>\n\n"
                "Votre demande a été acceptée et vous êtes <b>éligible</b> pour participer à "
                "<b>FDK CAPITAL CONCEPT</b>.\n\n"
                "📝 Pour valider définitivement votre participation, vous devez maintenant vous enregistrer.\n\n"
                "Commençons.\n\n"
                "👤 Quel est votre <b>nom et prénom</b> ?\n\n"
                "<i>Exemple :</i>\n"
                "Fiacre Kpanou"
            ),
            parse_mode="HTML"
        )
        return NAME

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
            "Il sera relancé automatiquement par le scheduler dès que possible.\n"
            f"{e}"
        )
        return ConversationHandler.END

    except Exception as e:
        logger.exception(f"[join] erreur inattendue envoi message bienvenue à {user_id}")
        await notify_admin(context.bot, "Erreur envoi message de bienvenue", f"user_id={user_id}\n{e}")
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# RELANCE AUTOMATIQUE — toutes les 3h, utilisateurs incomplets
# ══════════════════════════════════════════════════════════════════════════════

async def registration_reminder_loop(bot):
    INTERVAL_SECONDS = 3 * 60 * 60  # 3h

    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)

            try:
                telegram_ids = await get_incomplete_telegram_ids()
            except Exception as e:
                logger.exception("[reminder] échec récupération des utilisateurs incomplets")
                await notify_admin(bot, "Échec requête utilisateurs incomplets", str(e))
                continue

            sent, failed = 0, 0
            for user_id in telegram_ids:
                if not user_id:
                    continue
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            "👋 <b>Rappel important</b>\n\n"
                            f"Vous n'avez pas terminé votre enregistrement pour "
                            f"<b>{CATEGORIE}</b>.\n\n"
                            "Merci de reprendre la conversation et de compléter les informations "
                            "demandées (nom, numéro WhatsApp, profession) pour valider "
                            "définitivement votre participation."
                        ),
                        parse_mode="HTML",
                    )
                    sent += 1
                    await mark_reminder_sent(user_id)
                    await asyncio.sleep(0.05)  # anti flood
                except Forbidden:
                    # utilisateur n'a jamais démarré le bot ou l'a bloqué : normal, on ignore
                    failed += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"[reminder] échec envoi à {user_id}: {e}")

            if telegram_ids:
                await notify_admin(
                    bot,
                    "📋 Rapport de relance d'enregistrement",
                    f"Utilisateurs incomplets: {len(telegram_ids)}\nRelances envoyées: {sent}\nÉchecs: {failed}",
                )

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
    if update.effective_user.id != ADMIN_ID:
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
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        rep = await run_full_check()
        await update.message.reply_text(rep.summary())
    except Exception as e:
        logger.exception("[cmd_gold_check] erreur")
        await notify_admin(context.bot, "Erreur /gold_check", str(e))


async def cmd_capital_status(update, context):
    if update.effective_user.id != ADMIN_ID:
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
    if update.effective_user.id != ADMIN_ID:
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
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        telegram_ids = await get_incomplete_telegram_ids()
        await update.message.reply_text(
            f"📋 Enregistrements incomplets : {len(telegram_ids)}"
        )
    except Exception as e:
        logger.exception("[cmd_incomplete_status] erreur")
        await notify_admin(context.bot, "Erreur /incomplete_status", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# TÂCHE — bilan quotidien 20h
# ══════════════════════════════════════════════════════════════════════════════

async def schedule_daily_check(bot):
    while True:
        try:
            now    = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            results      = await daily_cramed_check()
            total_danger = sum(r.get("total_danger", 0) for r in results)
            if total_danger > 0:
                await bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"📋 *Bilan fin de journée — {datetime.now().strftime('%d/%m/%Y')}*\n\n"
                        f"Comptes en danger : *{total_danger}*\n"
                        f"Sessions surveillées : {len(results)}\n\n"
                        f"_Consultez le dashboard pour le détail._"
                    ),
                    parse_mode="Markdown"
                )
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
    print("[main] Schéma users (profession, name/phone nullable) OK ✓")

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

            await _start_internal_http_server()

            print("[main] Gold v7.1 initialisé ✓")
        except Exception as e:
            logger.exception("[post_init] échec initialisation")
            await notify_admin(application.bot, "Échec initialisation du bot (post_init)", str(e))
            raise

    app.post_init = _post_init

    # ── Tunnel d'enregistrement : déclenché par la demande d'adhésion,
    #    poursuivi par les réponses privées de l'utilisateur.
    registration_conv = ConversationHandler(
        entry_points=[ChatJoinRequestHandler(approve_join_request)],
        states={
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            JOB:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_job)],
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

    app.add_error_handler(error_handler)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=2)