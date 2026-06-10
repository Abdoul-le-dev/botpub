import os
import asyncio
from datetime import datetime

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
from form.form_engine import register_form_handlers, setup_background_worker
from telegram_page.signal_broadcast import register_signal_handlers
from telegram_page.gold.gold_engine import set_bot as set_gold_bot, daily_cramed_check
from telegram_page.gold.gold_broadcast import register_gold_handlers

load_dotenv()

ADMIN_ID   = 571718066
CANAL_B_ID = -1002705005402
CATEGORIE  = "USER_PUB_1_NON_ACHAT"
token      = os.getenv("tokens")


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
            target = target.replace(day=target.day + 1)
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

async def _post_init(application):
    print("[main] init_pool...")
    await init_pool()
    print("[main] Pool OK ✓")
    await setup_background_worker(application)
    asyncio.create_task(schedule_daily_check(application.bot))

if __name__ == "__main__":
    app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()

    app.post_init = _post_init

    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    register_validation_handler(app)
    register_form_handlers(app, app.bot, ADMIN_ID)
    register_gold_handlers(app)
    register_signal_handlers(app)
    app.add_handler(MessageHandler(filters.TEXT, log_unhandled_message), group=99)

    set_bot(app.bot)
    set_gold_bot(app.bot)

    print("running...")
    app.run_polling(poll_interval=1)  