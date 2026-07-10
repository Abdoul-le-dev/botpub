import os
import asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatJoinRequestHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)
from dotenv import load_dotenv

from db import get_db, init_pool

from ai_agent import set_bot, log_unhandled_message
from validation_handler import register_validation_handler
from validation_formation import register_formation_handler
from form.form_engine import register_form_handlers, setup_background_worker
from telegram_page.signal_broadcast import register_signal_handlers
from telegram_page.gold.gold_engine import set_bot as set_gold_bot, daily_cramed_check
from telegram_page.gold.gold_write_queue import start_gold_write_worker
from telegram_page.gold.error_handler import error_handler
from telegram_page.gold.weekly_capital_cache import weekly_capital
from collections import defaultdict
import asyncio



load_dotenv()
CANAL_B_ID = -1002705005402
ADMIN_ID   = 571718066

CATEGORIE  = "USER_PUB_1_NON_ACHAT"
token      = os.getenv("tokens")

import uvloop
uvloop.install()

# ══════════════════════════════════════════════════════════════════════════════
# GOLD V7.1 — Architecture refondue
# ══════════════════════════════════════════════════════════════════════════════
# Les handlers Gold v6 (signal_cache/user_state/gold_buffer) sont DÉSACTIVÉS.
# La v7.1 les remplace intégralement :
#   - session unique + versionnée (session_registry)
#   - snapshot immutable du trade actif (session_snapshot)
#   - état isolé par (session_id, user_id) (state_v7)
#   - buffer batch attaché à la session (buffer_v7)
#   - callback guard systématique (callback_guard)
#   - workflow simplifié : ouvrir = accepter (broadcast_v7)
#   - Weekly Capital Cache 7 jours (weekly_capital_cache)
#   - campagne hebdo progressive (capital_campaign)
#
# Les fichiers v6 restent importables tant que les routes API n'ont pas migré,
# mais leurs workers/handlers ne s'enregistrent plus.
from telegram_page.gold.gold_broadcast import (
    
    register_gold_handlers_v7,
   
)
from telegram_page.gold.consistency import run_full_check
from telegram_page.gold.lifecycle import  register_buffer
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
    """Status du buffer v7 (remplace l'ancien qui pointait vers gold_buffer v6)."""
    if update.effective_user.id != ADMIN_ID:
        return
    s = gold_buffer.status()
    await update.message.reply_text(
        f"📊 Buffer Gold v7\n"
        f"Attaché à : {s['attached']}\n"
        f"En attente : {s['pending']} "
        f"(entries {s['entries']} / steps {s['steps']} / events {s['events']})\n"
        f"Agg dirty : {s['dirty_agg']}\n"
        f"Worker actif : {'✅' if s['worker_running'] else '❌'}"
    )


async def cmd_gold_check(update, context):
    """Vérifie la cohérence RAM / Snapshot / State / Buffer / SQL."""
    if update.effective_user.id != ADMIN_ID:
        return
    rep = await run_full_check()
    await update.message.reply_text(rep.summary())


async def cmd_capital_status(update, context):
    """État du Weekly Capital Cache."""
    if update.effective_user.id != ADMIN_ID:
        return
    s = weekly_capital.status()
    await update.message.reply_text(
        f"💼 Weekly Capital Cache\n"
        f"Total RAM : {s['total_ram']}\n"
        f"Actifs : {s['active']}\n"
        f"Expirés (pas encore purgés) : {s['expired_stale']}\n"
        f"TTL : {s['ttl_days']} jours"
    )


async def cmd_capital_campaign_now(update, context):
    """Lance manuellement une campagne capital (test)."""
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔄 Campagne lancée en tâche de fond...")
    asyncio.create_task(run_campaign(context.bot, CampaignConfig()))


# ══════════════════════════════════════════════════════════════════════════════
# save_user_default async
# ══════════════════════════════════════════════════════════════════════════════

async def save_user_default(user_id):
    async with get_db() as cur:
        await cur.execute(
            "INSERT IGNORE INTO usersdefault (user_id, created_at) VALUES (%s, NOW())",
            (str(user_id),)
        )


def build_answer_keyboards():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Je m'enregistre", callback_data="enregistre")
    ]])


async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.chat_join_request.from_user
    user_id   = user.id
    user_name = update.effective_user.first_name or "CONFERENCE 1"
    chat_id   = update.chat_join_request.chat.id

    await save_user_default(user_id)

    try:
        await update.chat_join_request.approve()
    except BadRequest as e:
        if "User_already_participant" in str(e):
            print("Déjà membre.")
            return

    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(CATEGORIE, [user_id])
    except Exception as e:
        print(f"[validation] categorie error: {e}")

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "Bonjour l'ami 👋\n\n"
            "Je suis <b>Fiacre KPANOU</b>, j'échange directement avec toi via mon assistant bot.\n\n"
            "J'ai remarqué que tu n'as pas encore profité de l'offre disponible sur la plateforme, mais ce n'est absolument pas grave. "
            "Je salue d'ailleurs ton initiative d'avoir rejoint mon canal 🙌\n\n"
            "D'autres opportunités arrivent très bientôt. "
            "J'organise régulièrement des <b>webinaires</b> où je t'initie pas à pas aux marchés financiers :\n\n"
            "📊 Comment aborder les marchés avec méthode\n"
            "🏆 Les résultats concrets de mes apprenants\n"
            "💡 Des success stories qui vont t'inspirer et te donner envie de te lancer\n\n"
            "Clique ici pour t'enregistrer en avant-première : /je_menregistre_en_avant_premiere_pour_la_prochaine_masterclass\n\n"
            "Reste connecté et bien branché 🔥\n"
            "Je t'enverrai toutes les informations importantes directement via mon assistant.\n\n"
            "Merci l'ami 🤝"
        ),
        parse_mode="HTML"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END


async def schedule_daily_check(bot):
    while True:
        now    = datetime.now()
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
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
            print(f"[daily_check] Erreur: {e}")

_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

def get_user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks[user_id]
# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. Loop persistant
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. Pool DB
    loop.run_until_complete(init_pool())
    print("[main] Pool OK ✓")

    # 3. Schémas v7 (idempotent — safe à ré-exécuter à chaque boot)
    loop.run_until_complete(ensure_capital_schema())
    loop.run_until_complete(ensure_campaign_schema())
    print("[main] Schémas v7 OK ✓")
    
    # 4. App PTB
    app = (Application.builder()
           .token(token)
           .concurrent_updates(512)
           .read_timeout(30).write_timeout(30)
           .build())

    async def _post_init(application):
        await setup_background_worker(application)
        asyncio.create_task(schedule_daily_check(application.bot))

        # Worker de l'ancienne queue — toujours nécessaire tant que
        # les routes API (POST /gold/sessions/{id}/confirm, etc.)
        # continuent à l'utiliser.
        start_gold_write_worker(application.bot)
        #gold_buffer.start(app.bot)

        # ── V7.1 : buffer + capital cache + scheduler campagne ────────
        gold_buffer.start(application.bot)
        register_buffer(gold_buffer)
        # PAS d'auto-attach : les sessions s'ouvrent explicitement via
        # lifecycle.open_new_session() depuis la route POST /gold/sessions.

        # Scheduler campagne hebdo (samedi 10h locale par défaut)
        asyncio.create_task(weekly_scheduler_loop(application.bot))

        print("[main] Gold v7.1 initialisé ✓")

    app.post_init = _post_init

    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    register_validation_handler(app)
    register_formation_handler(app)
    register_form_handlers(app, app.bot, ADMIN_ID)

    # ── V7.1 : nouveaux handlers Gold (remplace register_gold_handlers v6)
    register_gold_handlers_v7(app)

    register_signal_handlers(app)

    # Log des messages non gérés — priorité minimale
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND
            & filters.UpdateType.MESSAGE
            & filters.ChatType.PRIVATE,
            log_unhandled_message,
        ),
        group=99,
    )

    # ── Commandes admin
    app.add_handler(CommandHandler("queue_status", cmd_queue_status))
    app.add_handler(CommandHandler("gold_check", cmd_gold_check))
    app.add_handler(CommandHandler("capital_status", cmd_capital_status))
    app.add_handler(CommandHandler("capital_campaign_now", cmd_capital_campaign_now))

    app.add_error_handler(error_handler)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=2)