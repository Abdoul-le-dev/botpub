"""
form_engine.py — Moteur d'exécution des formulaires dynamiques via Telegram.

Gère :
  - L'envoi question par question (tous types : QCM, texte, note, NPS, media…)
  - La collecte des réponses et leur stockage
  - Le scoring quiz (manuel + comparaison simple)
  - Les actions post-soumission (catégorie, message, notif admin)
  - La logique conditionnelle SI/ALORS

Intégration dans script.py :
  from form_engine import register_form_handlers
  register_form_handlers(app, bot, ADMIN_ID)
"""

import asyncio
import json
import sqlite3
import re

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import (
    Application, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters, ContextTypes,
)

from form.form import (
    get_form_by_command, get_form_by_id,
    get_or_create_session, advance_session, complete_session,
    save_response, save_submission, get_session,
)

DB_PATH = "preinscriptions.db"

# État unique pour le ConversationHandler
FORM_STEP = 200


# ════════════════════════════════════════════════════════════════════════════
# HELPERS DB (lecture user)
# ════════════════════════════════════════════════════════════════════════════

def _get_prenom(telegram_id: int) -> str:
    try:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT name FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        c.close()
        if row and row["prenom"]:
            p = row["prenom"].strip()
            if 1 <= len(p) <= 20:
                return p
    except Exception:
        pass
    return "toi"


def _inject_vars(text: str, telegram_id: int, score: int = 0, total: int = 0) -> str:
    """Remplace +prenom, +score, +total, +date dans les messages."""
    from datetime import date
    prenom = _get_prenom(telegram_id)
    return (
        text
        .replace("+prenom", prenom)
        .replace("+score",  str(score))
        .replace("+total",  str(total))
        .replace("+date",   date.today().strftime("%d/%m/%Y"))
    )


# ════════════════════════════════════════════════════════════════════════════
# ENVOI D'UN CHAMP (question)
# ════════════════════════════════════════════════════════════════════════════

async def _send_field(bot, chat_id: int, field: dict, step: int, total_steps: int):
    """Envoie la question correspondant au champ field."""
    ftype = field.get("type", "text")
    label = field.get("label") or "Réponds à cette question :"
    progress = f"[{step}/{total_steps}] " if total_steps > 1 else ""
    text = f"{progress}{label}"

    # Types à boutons inline (InlineKeyboardMarkup)
    if ftype == "qcm":
        opts = field.get("opts", [])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(o["t"], callback_data=f"fopt_{o['t'][:40]}")]
            for o in opts
        ])
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype == "multi":
        # Multi-sélection : on stocke les sélections dans user_data, bouton Valider séparé
        opts = field.get("opts", [])
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"☐ {o['t']}", callback_data=f"fmul_{i}_{o['t'][:35]}")]
             for i, o in enumerate(opts)]
            + [[InlineKeyboardButton("✅ Valider ma sélection", callback_data="fmul_validate")]]
        )
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype == "oui_non":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Oui", callback_data="fopt_Oui"),
            InlineKeyboardButton("❌ Non", callback_data="fopt_Non"),
        ]])
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype == "note5":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⭐{i}", callback_data=f"fopt_{i}")
            for i in range(1, 6)
        ]])
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype == "nps":
        rows = [
            [InlineKeyboardButton(str(i), callback_data=f"fopt_{i}") for i in range(0, 6)],
            [InlineKeyboardButton(str(i), callback_data=f"fopt_{i}") for i in range(6, 11)],
        ]
        await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(rows))

    elif ftype == "contact":
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Partager mon numéro", request_contact=True)]],
            one_time_keyboard=True, resize_keyboard=True
        )
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype in ("photo", "video", "audio", "document"):
        hints = {
            "photo":    "📸 Envoie ta photo.",
            "video":    "🎬 Envoie ta vidéo (max 20 Mo).",
            "audio":    "🎙️ Envoie un message vocal.",
            "document": "📄 Envoie ton document (PDF, ZIP…).",
        }
        skip_btn = None
        if not field.get("required", True):
            skip_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("Passer →", callback_data="fopt__skip")
            ]])
        await bot.send_message(chat_id, f"{text}\n\n{hints[ftype]}", reply_markup=skip_btn)

    elif ftype == "info":
        # Juste un message informatif, pas de réponse attendue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continuer →", callback_data="fopt__info")]])
        await bot.send_message(chat_id, text, reply_markup=kb)

    else:
        # text, long, email, number → saisie libre
        await bot.send_message(chat_id, text, reply_markup=ReplyKeyboardRemove())


# ════════════════════════════════════════════════════════════════════════════
# ÉVALUATION QUIZ
# ════════════════════════════════════════════════════════════════════════════

def _evaluate_answer(field: dict, raw_answer: str) -> tuple[bool | None, int, str]:
    """
    Retourne (is_correct, points, feedback).
    is_correct = None si le champ n'est pas en mode quiz.
    """
    if not field.get("quiz"):
        return None, 0, ""

    ftype = field.get("type", "text")
    pts_ok = int(field.get("pts", 10))

    # QCM / oui_non : comparer avec la bonne option
    if ftype in ("qcm", "oui_non"):
        correct_opt = next((o for o in field.get("opts", []) if o.get("c")), None)
        if ftype == "oui_non":
            correct_val = (field.get("correctAnswer") or "").lower()
            answer_val  = raw_answer.lower()
            is_correct  = answer_val in correct_val or correct_val in answer_val
        else:
            is_correct = correct_opt and correct_opt["t"].lower() == raw_answer.lower()

        points   = pts_ok if is_correct else 0
        expl     = field.get("expl", "")
        feedback = ("✅ Correct !" if is_correct else f"❌ Incorrect.") + (f"\n{expl}" if expl else "")
        if not is_correct and correct_opt:
            feedback += f"\n→ Réponse attendue : {correct_opt['t']}"
        return bool(is_correct), points, feedback

    # Multi : compter les bonnes options cochées
    if ftype == "multi":
        selected = [s.strip() for s in raw_answer.split(",")]
        correct_opts = {o["t"] for o in field.get("opts", []) if o.get("c")}
        pts_per = int(field.get("pts", 5))
        earned = sum(pts_per for s in selected if s in correct_opts)
        is_correct = set(selected) == correct_opts
        expl = field.get("expl", "")
        feedback = ("✅ Parfait !" if is_correct else f"⚠️ Partiel ({earned} pts)") + (f"\n{expl}" if expl else "")
        return is_correct, earned, feedback

    # Text / number : comparaison exacte (insensible à la casse)
    if ftype in ("text", "long", "email", "number"):
        expected = (field.get("correctAnswer") or "").strip().lower()
        if not expected:
            return None, 0, ""  # pas de réponse configurée = pas de correction
        is_correct = expected in raw_answer.lower() or raw_answer.lower() in expected
        points = pts_ok if is_correct else 0
        expl = field.get("expl", "")
        feedback = ("✅ Correct !" if is_correct else f"❌ Incorrect. Attendu : {field.get('correctAnswer', '')}") + (f"\n{expl}" if expl else "")
        return bool(is_correct), points, feedback

    return None, 0, ""


# ════════════════════════════════════════════════════════════════════════════
# LOGIQUE CONDITIONNELLE
# ════════════════════════════════════════════════════════════════════════════

def _eval_conditions(conditions: list, responses_so_far: dict) -> list[str]:
    """
    Évalue les règles SI/ALORS et retourne la liste des actions à déclencher.
    responses_so_far = {field_label: raw_value}
    """
    actions = []
    for rule in conditions:
        # rule = { "if": {field, op, value}, "then": {action, param} }
        if_clause  = rule.get("if", {})
        then_clause = rule.get("then", {})
        field_label = if_clause.get("field", "")
        op          = if_clause.get("op", "=")
        cond_val    = str(if_clause.get("value", "")).lower()
        actual_val  = str(responses_so_far.get(field_label, "")).lower()

        match = False
        if op == "=":
            match = actual_val == cond_val
        elif op == "≠":
            match = actual_val != cond_val
        elif op == "contient":
            match = cond_val in actual_val

        if match:
            actions.append(then_clause)

    return actions


# ════════════════════════════════════════════════════════════════════════════
# ACTIONS POST-SOUMISSION
# ════════════════════════════════════════════════════════════════════════════

async def _run_actions(bot, telegram_id: int, actions: list, context_vars: dict):
    """Exécute les actions post-soumission définies dans le formulaire."""
    done = []
    for action in actions:
        atype = action.get("type", "")
        value = str(action.get("value", ""))

        try:
            if atype == "Ajouter catégorie":
                # Réutilise ta fonction add_categorie existante
                from database.database import add_categorie
                await add_categorie(telegram_id, value)
                done.append(f"categorie:{value}")

            elif atype == "Envoyer message":
                msg = _inject_vars(value, telegram_id,
                                   score=context_vars.get("score", 0),
                                   total=context_vars.get("total", 0))
                await bot.send_message(telegram_id, msg)
                done.append(f"message_sent")

            elif atype == "Notifier admin":
                admin_id = context_vars.get("admin_id")
                if admin_id:
                    prenom = _get_prenom(telegram_id)
                    msg = f"📋 Nouveau formulaire soumis\nUtilisateur : {prenom} ({telegram_id})\n{value}"
                    await bot.send_message(admin_id, msg)
                done.append("admin_notified")

            elif atype == "Broadcast":
                # Planifier ou déléguer — ici on log simplement
                done.append(f"broadcast_queued:{value}")

        except Exception as e:
            print(f"[form_engine] Action '{atype}' échouée pour {telegram_id}: {e}")

    return done


# ════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE D'UN FORMULAIRE (commande)
# ════════════════════════════════════════════════════════════════════════════

async def _form_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler déclenché par la commande /xxx correspondant à un formulaire."""
    user_id  = update.effective_user.id
    command  = "/" + update.message.text.strip().lstrip("/").split()[0]

    form = get_form_by_command(command)
    if not form:
        await update.message.reply_text("❌ Ce formulaire n'est pas disponible.")
        return ConversationHandler.END

    session = get_or_create_session(form["id"], user_id)

    # Stockage du contexte dans user_data
    context.user_data["form_id"]      = form["id"]
    context.user_data["session_id"]   = session["id"]
    context.user_data["step"]         = session["step_index"]
    context.user_data["multi_sel"]    = []       # pour les QCM multi
    context.user_data["responses"]    = {}       # label → valeur pour la logique cond.

    fields = form.get("fields", [])
    if not fields:
        await update.message.reply_text("Ce formulaire est vide.")
        return ConversationHandler.END

    # Message d'intro
    if form.get("intro"):
        intro = _inject_vars(form["intro"], user_id)
        await update.message.reply_text(intro)
        await asyncio.sleep(0.5)

    # Envoyer la 1ère question (ou reprendre à l'étape en cours)
    step = session["step_index"]
    if step >= len(fields):
        await update.message.reply_text("Tu as déjà complété ce formulaire.")
        return ConversationHandler.END

    await _send_field(
        context.bot, user_id,
        fields[step], step + 1, len(fields)
    )
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION D'UNE RÉPONSE TEXTE
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])

    if step >= len(fields):
        return ConversationHandler.END

    field      = fields[step]
    raw_answer = update.message.text.strip()

    # Contact partagé via bouton natif
    if update.message.contact:
        raw_answer = update.message.contact.phone_number

    return await _process_answer(update, context, form, fields, field, step, session_id, form_id, user_id, raw_answer)


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION D'UNE RÉPONSE BOUTON (callback)
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field = fields[step]
    data  = query.data

    # ── Gestion multi-sélection ──────────────────────────────────
    if data.startswith("fmul_") and data != "fmul_validate":
        parts = data.split("_", 2)
        idx   = int(parts[1])
        label = parts[2] if len(parts) > 2 else ""
        sel   = context.user_data.setdefault("multi_sel", [])
        opts  = field.get("opts", [])

        if idx in sel:
            sel.remove(idx)
        else:
            sel.append(idx)

        # Mettre à jour les boutons visuellement (✓ / ☐)
        new_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                ("✅ " if i in sel else "☐ ") + o["t"],
                callback_data=f"fmul_{i}_{o['t'][:35]}"
            )] for i, o in enumerate(opts)]
            + [[InlineKeyboardButton("✅ Valider ma sélection", callback_data="fmul_validate")]]
        )
        try:
            await query.edit_message_reply_markup(new_kb)
        except Exception:
            pass
        return FORM_STEP

    if data == "fmul_validate":
        sel   = context.user_data.get("multi_sel", [])
        opts  = field.get("opts", [])
        raw_answer = ", ".join(opts[i]["t"] for i in sorted(sel) if i < len(opts)) or "—"
        context.user_data["multi_sel"] = []
    elif data == "fopt__skip":
        raw_answer = "__skip__"
    elif data == "fopt__info":
        raw_answer = "__info__"
    elif data.startswith("fopt_"):
        raw_answer = data[5:]
    else:
        return FORM_STEP

    return await _process_answer(
        update, context, form, fields, field, step,
        session_id, form_id, user_id, raw_answer,
        is_callback=True
    )


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION MÉDIAS
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field = fields[step]
    ftype = field.get("type")

    # Extraire le file_id selon le type
    raw_answer = "__media__"
    try:
        if ftype == "photo" and update.message.photo:
            raw_answer = update.message.photo[-1].file_id
        elif ftype == "video" and update.message.video:
            raw_answer = update.message.video.file_id
        elif ftype == "audio" and update.message.voice:
            raw_answer = update.message.voice.file_id
        elif ftype == "document" and update.message.document:
            raw_answer = update.message.document.file_id
    except Exception:
        pass

    return await _process_answer(
        update, context, form, fields, field, step,
        session_id, form_id, user_id, raw_answer
    )


# ════════════════════════════════════════════════════════════════════════════
# TRAITEMENT COMMUN D'UNE RÉPONSE
# ════════════════════════════════════════════════════════════════════════════

async def _process_answer(
    update, context,
    form, fields, field, step,
    session_id, form_id, user_id,
    raw_answer: str,
    is_callback: bool = False,
):
    bot = context.bot

    # Évaluation quiz
    is_correct, points, feedback = _evaluate_answer(field, raw_answer)

    # Stocker la réponse (sauf skip/info)
    if raw_answer not in ("__skip__", "__info__"):
        save_response(
            session_id, form_id, user_id,
            field_id=field.get("id", step),
            field_type=field.get("type", "text"),
            value=raw_answer,
            is_correct=is_correct,
            points=points,
        )
        context.user_data["responses"][field.get("label", "")] = raw_answer

    # Envoyer le feedback quiz
    if feedback:
        send_fn = bot.send_message
        await send_fn(user_id, feedback)
        await asyncio.sleep(0.4)

    # Évaluer conditions
    cond_actions = _eval_conditions(
        form.get("conditions", []),
        context.user_data.get("responses", {})
    )

    next_step = step + 1

    # Avancer la session
    advance_session(session_id, next_step, add_score=points)

    # ── Fin du formulaire ───────────────────────────────────────
    if next_step >= len(fields):
        return await _finish_form(update, context, form, session_id, form_id, user_id, cond_actions)

    # ── Champ suivant ──────────────────────────────────────────
    context.user_data["step"] = next_step
    await _send_field(bot, user_id, fields[next_step], next_step + 1, len(fields))
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# FIN DU FORMULAIRE
# ════════════════════════════════════════════════════════════════════════════

async def _finish_form(update, context, form, session_id, form_id, user_id, extra_cond_actions):
    bot = context.bot

    complete_session(session_id)
    session = get_session(session_id)
    score   = session["score"] if session else 0

    # Score max depuis quiz_config
    qcfg      = form.get("quiz_config", {})
    score_max = int(qcfg.get("max", 0))

    # Actions à exécuter : formulaire + conditions
    form_actions = form.get("actions", [])
    all_actions  = form_actions + extra_cond_actions

    # Enregistrer la soumission
    admin_id = context.bot_data.get("admin_id")
    ctx_vars = {"score": score, "total": score_max, "admin_id": admin_id}
    done = await _run_actions(bot, user_id, all_actions, ctx_vars)

    save_submission(session_id, form_id, user_id, done)

    # Message de fin
    if form.get("outro"):
        outro = _inject_vars(form["outro"], user_id, score=score, total=score_max)
        await bot.send_message(user_id, outro, reply_markup=ReplyKeyboardRemove())

    return ConversationHandler.END


async def _form_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = context.user_data.get("session_id")
    if session_id:
        from forms_db import abandon_session
        abandon_session(session_id)
    context.user_data.clear()
    await update.message.reply_text("Formulaire annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════════════════
# ENVOI DIRECT (depuis l'API ou le scheduler)
# ════════════════════════════════════════════════════════════════════════════

async def send_form_to_user(bot, telegram_id: int, form_id: int, app: Application = None):
    """
    Lance un formulaire pour un utilisateur donné sans commande Telegram.
    Utilisé par le scheduler ou l'API broadcast.
    Crée une session et envoie le message d'intro + 1ère question.
    Note : les réponses arrivent via les handlers ConversationHandler.
    """
    form = get_form_by_id(form_id)
    if not form:
        print(f"[form_engine] Formulaire {form_id} introuvable.")
        return

    fields = form.get("fields", [])
    if not fields:
        return

    session = get_or_create_session(form_id, telegram_id)

    if form.get("intro"):
        intro = _inject_vars(form["intro"], telegram_id)
        await bot.send_message(telegram_id, intro)
        await asyncio.sleep(0.4)

    await _send_field(bot, telegram_id, fields[0], 1, len(fields))
    print(f"[form_engine] Formulaire {form['name']} envoyé à {telegram_id}")


async def broadcast_form(bot, form_id: int, user_ids: list[int], admin_id: int = None):
    """Diffuse un formulaire à une liste d'utilisateurs."""
    sent = errors = 0
    for uid in user_ids:
        try:
            await send_form_to_user(bot, uid, form_id)
            sent += 1
        except Exception as e:
            errors += 1
            print(f"[form_engine] broadcast uid={uid}: {e}")
        await asyncio.sleep(0.15)

    if admin_id:
        await bot.send_message(
            admin_id,
            f"📋 Diffusion formulaire terminée\nEnvoyés : {sent} | Erreurs : {errors}"
        )


# ════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT DES HANDLERS
# ════════════════════════════════════════════════════════════════════════════

def register_form_handlers(app: Application, bot, admin_id: int):
    """
    À appeler dans script.py avant app.run_polling().

    Enregistre un ConversationHandler générique qui intercepte toutes
    les commandes /xxx correspondant à un formulaire actif en base.

    Usage dans script.py :
        from form_engine import register_form_handlers
        register_form_handlers(app, bot, ADMIN_ID)
    """
    app.bot_data["admin_id"] = admin_id

    conv = ConversationHandler(
        entry_points=[
            # Intercepte toutes les commandes et vérifie si c'est un formulaire
            MessageHandler(filters.COMMAND, _form_start),
        ],
        states={
            FORM_STEP: [
                # Boutons inline (QCM, note, NPS, oui/non, multi, skip, info)
                CallbackQueryHandler(_form_receive_callback, pattern=r"^(fopt_|fmul_)"),
                # Contact partagé
                MessageHandler(filters.CONTACT, _form_receive_text),
                # Médias
                MessageHandler(filters.PHOTO,    _form_receive_media),
                MessageHandler(filters.VIDEO,    _form_receive_media),
                MessageHandler(filters.VOICE,    _form_receive_media),
                MessageHandler(filters.Document.ALL, _form_receive_media),
                # Réponse texte libre (doit être en dernier)
                MessageHandler(filters.TEXT & ~filters.COMMAND, _form_receive_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", _form_cancel)],
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )

    app.add_handler(conv, group=1)  # group=1 pour ne pas conflictuer avec tes handlers existants
    print("[form_engine] Handlers formulaires enregistrés.")