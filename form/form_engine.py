"""
form_engine.py — Moteur d'exécution des formulaires dynamiques via Telegram.
Les fichiers médias (photo, video, audio, document) sont téléchargés en local
dans /media/forms/ et le chemin relatif est stocké en base — même pattern que le chat.
"""

import asyncio
import json
import sqlite3
import re
import os
import uuid
from pathlib import Path

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

DB_PATH    = "preinscriptions.db"
MEDIA_DIR  = Path("media/forms")          # servi par StaticFiles /media
MEDIA_URL  = "/media/forms"               # préfixe URL côté front

FORM_STEP  = 200

conn = sqlite3.connect(DB_PATH)
# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _get_prenom(telegram_id: int) -> str:
    try:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT name FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        c.close()
        if row and row["name"]:
            p = row["name"].strip()
            if 1 <= len(p) <= 20:
                return p
    except Exception:
        pass
    return "l'ami"


def _inject_vars(text: str, telegram_id: int, score: int = 0, total: int = 0) -> str:
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
# TÉLÉCHARGEMENT LOCAL DU FICHIER TELEGRAM
# Retourne le chemin relatif "/media/forms/xxxx.jpg" ou None si erreur
# ════════════════════════════════════════════════════════════════════════════

async def _download_media(bot, file_id: str, field_type: str) -> str | None:
    """
    Télécharge un fichier depuis Telegram et le sauvegarde en local.
    Retourne le chemin URL relatif (/media/forms/filename.ext) ou None.
    """
    EXT_MAP = {
        "photo":    "jpg",
        "video":    "mp4",
        "audio":    "ogg",    # voice note Telegram = ogg
        "document": "bin",    # extension overridée depuis file_path Telegram
    }

    try:
        # Créer le répertoire si absent
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)

        tg_file   = await bot.get_file(file_id)
        file_path = tg_file.file_path or ""

        # Détecter l'extension réelle depuis file_path Telegram
        if "." in file_path:
            ext = file_path.rsplit(".", 1)[-1].lower()
        else:
            ext = EXT_MAP.get(field_type, "bin")

        filename  = f"{uuid.uuid4().hex}.{ext}"
        dest      = MEDIA_DIR / filename

        await tg_file.download_to_drive(str(dest))

        return f"{MEDIA_URL}/{filename}"

    except Exception as e:
        print(f"[form_engine] download_media {file_id}: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# ENVOI D'UN CHAMP (question)
# ════════════════════════════════════════════════════════════════════════════

async def _send_field(bot, chat_id: int, field: dict, step: int, total_steps: int, progress: bool):
    ftype = field.get("type", "text")
    label = field.get("label") or "Réponds à cette question :"
    
    
    progress = f"[{step}/{total_steps}] " if total_steps > 1 and progress else ""
    text = f"{progress}{label}"

    if ftype == "qcm":
        opts = field.get("opts", [])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(o["t"], callback_data=f"fopt_{o['t'][:40]}")]
            for o in opts
        ])
        await bot.send_message(chat_id, text, reply_markup=kb)

    elif ftype == "multi":
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
            "photo":    "📸 ",
            "video":    "🎬 (max 20 Mo).",
            "audio":    "🎙️",
            "document": "📄",
        }
        skip_btn = None
        if not field.get("required", True):
            skip_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("Passer →", callback_data="fopt__skip")
            ]])
        await bot.send_message(chat_id, f"{text}\n\n{hints[ftype]}", reply_markup=skip_btn)

    elif ftype == "info":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continuer →", callback_data="fopt__info")]])
        await bot.send_message(chat_id, text, reply_markup=kb)

    else:
        await bot.send_message(chat_id, text, reply_markup=ReplyKeyboardRemove())


# ════════════════════════════════════════════════════════════════════════════
# ÉVALUATION QUIZ
# ════════════════════════════════════════════════════════════════════════════

def _evaluate_answer(field: dict, raw_answer: str) -> tuple[bool | None, int, str]:
    if not field.get("quiz"):
        return None, 0, ""

    ftype   = field.get("type", "text")
    pts_ok  = int(field.get("pts", 10))

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
        feedback = ("✅ Correct !" if is_correct else "❌ Incorrect.") + (f"\n{expl}" if expl else "")
        if not is_correct and correct_opt:
            feedback += f"\n→ Réponse attendue : {correct_opt['t']}"
        return bool(is_correct), points, feedback

    if ftype == "multi":
        selected     = [s.strip() for s in raw_answer.split(",")]
        correct_opts = {o["t"] for o in field.get("opts", []) if o.get("c")}
        pts_per      = int(field.get("pts", 5))
        earned       = sum(pts_per for s in selected if s in correct_opts)
        is_correct   = set(selected) == correct_opts
        expl         = field.get("expl", "")
        feedback     = ("✅ Parfait !" if is_correct else f"⚠️ Partiel ({earned} pts)") + (f"\n{expl}" if expl else "")
        return is_correct, earned, feedback

    if ftype in ("text", "long", "email", "number"):
        expected = (field.get("correctAnswer") or "").strip().lower()
        if not expected:
            return None, 0, ""
        is_correct = expected in raw_answer.lower() or raw_answer.lower() in expected
        points     = pts_ok if is_correct else 0
        expl       = field.get("expl", "")
        feedback   = ("✅ Correct !" if is_correct else f"❌ Incorrect. Attendu : {field.get('correctAnswer', '')}") + (f"\n{expl}" if expl else "")
        return bool(is_correct), points, feedback

    return None, 0, ""


# ════════════════════════════════════════════════════════════════════════════
# LOGIQUE CONDITIONNELLE
# ════════════════════════════════════════════════════════════════════════════

def _eval_conditions(conditions: list, responses_so_far: dict) -> list[str]:
    actions = []
    for rule in conditions:
        if_clause   = rule.get("if", {})
        then_clause = rule.get("then", {})
        field_label = if_clause.get("field", "")
        op          = if_clause.get("op", "=")
        cond_val    = str(if_clause.get("value", "")).lower()
        actual_val  = str(responses_so_far.get(field_label, "")).lower()

        match = False
        if op == "=":        match = actual_val == cond_val
        elif op == "≠":      match = actual_val != cond_val
        elif op == "contient": match = cond_val in actual_val

        if match:
            actions.append(then_clause)
    return actions


# ════════════════════════════════════════════════════════════════════════════
# ACTIONS POST-SOUMISSION
# ════════════════════════════════════════════════════════════════════════════

async def _run_actions(bot, telegram_id: int, actions: list, context_vars: dict):
    done = []
    for action in actions:
        atype = action.get("type", "")
        value = str(action.get("value", ""))
        try:
            if atype == "Ajouter catégorie":
                from database.database import add_categorie
                await add_categorie(telegram_id, value)
                done.append(f"categorie:{value}")

            elif atype == "Envoyer message":
                msg = _inject_vars(value, telegram_id,
                                   score=context_vars.get("score", 0),
                                   total=context_vars.get("total", 0))
                await bot.send_message(telegram_id, msg)
                done.append("message_sent")

            elif atype == "Notifier admin":
                admin_id = context_vars.get("admin_id")
                if admin_id:
                    prenom = _get_prenom(telegram_id)
                    await bot.send_message(
                        admin_id,
                        f"📋 Nouveau formulaire soumis\nUtilisateur : {prenom} ({telegram_id})\n{value}"
                    )
                done.append("admin_notified")

        except Exception as e:
            print(f"[form_engine] Action '{atype}' échouée pour {telegram_id}: {e}")
    return done


# ════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE D'UN FORMULAIRE
# ════════════════════════════════════════════════════════════════════════════

async def _form_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    command = "/" + update.message.text.strip().lstrip("/").split()[0]

    form = get_form_by_command(command)

    if not form:
        await update.message.reply_text("...")#form non disponible
        return ConversationHandler.END
    
    options = form.get("options", [])
    form_completed = await has_completed_form(conn,form["id"],user_id)
    print(form_completed)
    print(options['one_per_user'])
    print(options['one_per_user'])
    if options['one_per_user'] :
        if form_completed :
            await update.message.reply_text("Form remplis")#form non disponible
            return ConversationHandler.END

    session = get_or_create_session(form["id"], user_id)

    context.user_data["form_id"]    = form["id"]
    context.user_data["session_id"] = session["id"]
    context.user_data["step"]       = session["step_index"]
    context.user_data["progress"]    = options["progress"]
    context.user_data["multi_sel"]  = []
    context.user_data["responses"]  = {}

    fields = form.get("fields", [])
    
    if not fields:
        await update.message.reply_text("Ce formulaire est vide.")
        return ConversationHandler.END

    if form.get("intro"):
        intro = _inject_vars(form["intro"], user_id)
        await update.message.reply_text(intro)
        await asyncio.sleep(0.5)

    step = session["step_index"]
    if step >= len(fields):
        await update.message.reply_text("Tu as déjà complété ce formulaire.")
        return ConversationHandler.END

    await _send_field(context.bot, user_id, fields[step], step + 1, len(fields),options["progress"])
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION TEXTE / CONTACT
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    progress = context.user_data.get("progress")
    step       = context.user_data.get("step", 0)

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field      = fields[step]
    raw_answer = update.message.text.strip() if update.message.text else ""

    if update.message.contact:
        raw_answer = update.message.contact.phone_number

    return await _process_answer(
        update, context, form, fields, field, step,
        session_id, form_id, user_id, raw_answer, progress
    )


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION BOUTON
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id    = query.from_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)
    progress   = context.user_data.get("progress")

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field = fields[step]
    data  = query.data

    if data.startswith("fmul_") and data != "fmul_validate":
        parts = data.split("_", 2)
        idx   = int(parts[1])
        sel   = context.user_data.setdefault("multi_sel", [])
        opts  = field.get("opts", [])
        if idx in sel: sel.remove(idx)
        else:          sel.append(idx)
        new_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                ("✅ " if i in sel else "☐ ") + o["t"],
                callback_data=f"fmul_{i}_{o['t'][:35]}"
            )] for i, o in enumerate(opts)]
            + [[InlineKeyboardButton("✅ Valider ma sélection", callback_data="fmul_validate")]]
        )
        try: await query.edit_message_reply_markup(new_kb)
        except Exception: pass
        return FORM_STEP

    if data == "fmul_validate":
        sel        = context.user_data.get("multi_sel", [])
        opts       = field.get("opts", [])
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
        session_id, form_id, user_id, raw_answer, is_callback=True, progress= progress
    )


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION MÉDIAS — TÉLÉCHARGEMENT LOCAL
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)
    progress = context.user_data.get("progress")

    if not form_id:
        return ConversationHandler.END

    form   = get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field      = fields[step]
    ftype      = field.get("type")
    file_id    = None

    # Extraire le file_id selon le type de message
    try:
        if ftype == "photo" and update.message.photo:
            file_id = update.message.photo[-1].file_id   # meilleure qualité
        elif ftype == "video" and update.message.video:
            file_id = update.message.video.file_id
        elif ftype == "audio" and update.message.voice:
            file_id = update.message.voice.file_id
        elif ftype == "audio" and update.message.audio:
            file_id = update.message.audio.file_id
        elif ftype == "document" and update.message.document:
            file_id = update.message.document.file_id
    except Exception:
        pass

    if not file_id:
        await update.message.reply_text("❌ Fichier non reconnu, réessaie.")
        return FORM_STEP

    # ── Télécharger en local et stocker le chemin URL ──────────────────
    local_url = await _download_media(context.bot, file_id, ftype)

    print(local_url)

    if local_url:
        raw_answer = local_url          # ex: "/media/forms/abc123.jpg"
    else:
        # Fallback : stocker le file_id si le téléchargement échoue
        raw_answer = file_id
        await update.message.reply_text("⚠️ Fichier reçu mais non téléchargé localement.")

    return await _process_answer(
        update, context, form, fields, field, step,
        session_id, form_id, user_id, raw_answer, progress
    )


# ════════════════════════════════════════════════════════════════════════════
# TRAITEMENT COMMUN D'UNE RÉPONSE
# ════════════════════════════════════════════════════════════════════════════

async def has_completed_form(conn, form_id: int, telegram_id: int) -> bool:
    """
    Vérifie si un utilisateur a complété avec succès un formulaire entier.
    Retourne True si toutes les réponses sont présentes et valides.
    """
    

    # 1. Récupérer les champs requis du formulaire
    form = conn.execute(
        "SELECT fields FROM forms WHERE id = ?", (form_id,)
    ).fetchone()

    if not form:
        print("1")
        return False

    try:
        fields = json.loads(form[0])
    except json.JSONDecodeError:
        print("2")
        return False

    # Filtrer uniquement les champs obligatoires (non optionnels)
    required_fields = [
        f for f in fields
        if not f.get("optional", False) and f.get("id")
    ]

    if not required_fields:
        print("3")
        return False  # Formulaire sans champs = pas valide

    required_ids = {str(f["id"]) for f in required_fields}

    # 2. Récupérer les réponses de cet utilisateur pour ce formulaire
    responses = conn.execute("""
        SELECT field_id, value
        FROM form_responses
        WHERE form_id = ? AND telegram_id = ?
    """, (form_id, telegram_id)).fetchall()

    if not responses:
        print("4")
        return False

    # 3. Vérifier que tous les champs requis ont une réponse non vide
    answered_ids = {
        str(r[0]) for r in responses
        if r[1] is not None and str(r[1]).strip() != ""
    }
    print(answered_ids)

    return required_ids.issubset(answered_ids)
async def _process_answer(
    update, context,
    form, fields, field, step,
    session_id, form_id, user_id,
    raw_answer: str,
    is_callback: bool = False,
    progress :bool = False,
    
):
    bot = context.bot

    is_correct, points, feedback = _evaluate_answer(field, raw_answer)

    if raw_answer not in ("__skip__", "__info__"):
        save_response(
            session_id, form_id, user_id,
            field_id   = field.get("id", step),
            field_type = field.get("type", "text"),
            field_label= field.get("label", ""),
            value      = raw_answer,
            is_correct = is_correct,
            points     = points,
        )
        context.user_data["responses"][field.get("label", "")] = raw_answer

    if feedback:
        await bot.send_message(user_id, feedback)
        await asyncio.sleep(0.4)

    cond_actions = _eval_conditions(
        form.get("conditions", []),
        context.user_data.get("responses", {})
    )

    next_step = step + 1
    advance_session(session_id, next_step, add_score=points)

    if next_step >= len(fields):
        return await _finish_form(update, context, form, session_id, form_id, user_id, cond_actions)

    context.user_data["step"] = next_step
    await _send_field(bot, user_id, fields[next_step], next_step + 1, len(fields),progress)
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# FIN DU FORMULAIRE
# ════════════════════════════════════════════════════════════════════════════

async def _finish_form(update, context, form, session_id, form_id, user_id, extra_cond_actions):
    bot = context.bot

    complete_session(session_id)
    link_id = context.user_data.get("pending_link_id") if context else None
    if link_id:
        from telegram_page.start_handler import record_form_completion
        await record_form_completion(bot, user_id, link_id)
        context.user_data.pop("pending_link_id", None)

    session   = get_session(session_id)
    score     = session["score"] if session else 0
    qcfg      = form.get("quiz_config", {})
    score_max = int(qcfg.get("max", 0))

    form_actions = form.get("actions", [])
    all_actions  = form_actions + extra_cond_actions

    admin_id = context.bot_data.get("admin_id")
    ctx_vars = {"score": score, "total": score_max, "admin_id": admin_id}
    done     = await _run_actions(bot, user_id, all_actions, ctx_vars)

    save_submission(session_id, form_id, user_id, done)



    if form.get("outro"):
        outro = _inject_vars(form["outro"], user_id, score=score, total=score_max)
        await bot.send_message(user_id, outro, reply_markup=ReplyKeyboardRemove())

    return ConversationHandler.END


async def _form_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = context.user_data.get("session_id")
    if session_id:
        from form.form import abandon_session
        abandon_session(session_id)
    context.user_data.clear()
    await update.message.reply_text("Formulaire annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════════════════
# ENVOI DIRECT (scheduler / broadcast)
# ════════════════════════════════════════════════════════════════════════════
async def send_form_to_user(bot, telegram_id: int, form_id: int, app: Application = None, context=None):
    form = get_form_by_id(form_id)
    if not form:
        return
    fields = form.get("fields", [])
    if not fields:
        return
    
    options = form.get("options", {}) or {}
    session = get_or_create_session(form_id, telegram_id)
    
    if form.get("intro"):
        intro = _inject_vars(form["intro"], telegram_id)
        await bot.send_message(telegram_id, intro)
        await asyncio.sleep(0.4)

    # Remplir user_data si context disponible
    if context:
        context.user_data["form_id"]    = form_id
        context.user_data["session_id"] = session["id"]
        context.user_data["step"]       = session["step_index"]
        context.user_data["progress"]   = options.get("progress", False)
        context.user_data["multi_sel"]  = []
        context.user_data["responses"]  = {}

    await _send_field(bot, telegram_id, fields[0], 1, len(fields), options.get("progress", False))


async def broadcast_form(bot, form_id: int, user_ids: list[int], admin_id: int = None):
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
            f"📋 Diffusion terminée\nEnvoyés : {sent} | Erreurs : {errors}"
        )


# ════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT DES HANDLERS
# ════════════════════════════════════════════════════════════════════════════

def register_form_handlers(app: Application, bot, admin_id: int):
    app.bot_data["admin_id"] = admin_id

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.COMMAND, _form_start)],
        states={
            FORM_STEP: [
                CallbackQueryHandler(_form_receive_callback, pattern=r"^(fopt_|fmul_)"),
                MessageHandler(filters.CONTACT, _form_receive_text),
                MessageHandler(filters.PHOTO,   _form_receive_media),
                MessageHandler(filters.VIDEO,   _form_receive_media),
                MessageHandler(filters.VOICE,   _form_receive_media),
                MessageHandler(filters.AUDIO,   _form_receive_media),
                MessageHandler(filters.Document.ALL, _form_receive_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _form_receive_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", _form_cancel)],
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )

    app.add_handler(conv, group=1)
    print("[form_engine] Handlers formulaires enregistrés.")