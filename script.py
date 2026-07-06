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
from telegram_page.gold.gold_broadcast import register_gold_handlers
from telegram_page.gold.gold_write_queue import start_gold_write_worker, get_queue_status
from telegram_page.gold.error_handler import error_handler

load_dotenv()
CANAL_B_ID = -1002705005402
ADMIN_ID   = 571718066

CATEGORIE  = "USER_PUB_1_NON_ACHAT"
token      = os.getenv("tokens")

import uvloop
uvloop.install()

from telegram_page.gold.gold_cache import signal_cache
from telegram_page.gold.gold_state import user_state
from telegram_page.gold.gold_buffer import gold_buffer
from telegram_page.gold.gold_broadcast import register_gold_handlers





async def cmd_queue_status(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    s = await gold_buffer.status()
    await update.message.reply_text(
        f"📊 Buffer Gold\nEn attente : {s['pending']} "
        f"(entries {s['entries']} / steps {s['steps']} / events {s['events']})\n"
        f"Flusher actif : {'✅' if s['worker_running'] else '❌'}"
    )

# ── save_user_default async ───────────────────────────────────────────────────
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
            target = timedelta(days=1)
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


    

if __name__ == "__main__":
    # 1. Créer un loop persistant
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. Init le pool dans ce loop
    loop.run_until_complete(init_pool())
    print("[main] Pool OK ✓")

    # 3. Construire l'app PTB
    app = (Application.builder()
       .token(token)
       .concurrent_updates(512)      
       .read_timeout(30).write_timeout(30)
       .build())
   # app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()

    async def _post_init(application):
        await setup_background_worker(application)
        asyncio.create_task(schedule_daily_check(application.bot))

        # v6 : cache + état + flusher
        await signal_cache.reload()
        session = signal_cache.get_session()
        if session:
            await user_state.restore(session["id"])
        gold_buffer.start(application.bot)
        print("[main] Cache Gold chargé, flusher démarré.")

    app.post_init = _post_init

    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    register_validation_handler(app)
    register_formation_handler(app)
    register_form_handlers(app, app.bot, ADMIN_ID)
    register_gold_handlers(app)
    register_signal_handlers(app)
    app.add_handler(MessageHandler(filters.TEXT, log_unhandled_message), group=99)
    app.add_handler(CommandHandler("queue_status", cmd_queue_status))

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=1)  # récupère le loop existant