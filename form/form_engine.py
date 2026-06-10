"""
form_engine.py — v5 MySQL async
Moteur d'exécution des formulaires dynamiques via Telegram.
"""

import asyncio
import json
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

from db import get_db

MEDIA_DIR = Path("media/forms")
MEDIA_URL = "/media/forms"
FORM_STEP = 200


# ════════════════════════════════════════════════════════════════════════════
# QUEUE ASYNCIO
# ════════════════════════════════════════════════════════════════════════════

_task_queue: asyncio.Queue = None


async def _background_worker():
    global _task_queue
    while True:
        try:
            coro = await _task_queue.get()
            try:
                await coro
            except Exception as e:
                print(f"[worker] Erreur tâche: {e}")
            finally:
                _task_queue.task_done()
        except Exception as e:
            print(f"[worker] Erreur queue: {e}")
            await asyncio.sleep(1)


async def setup_background_worker(app=None):
    global _task_queue
    _task_queue = asyncio.Queue(maxsize=200)
    asyncio.create_task(_background_worker())
    print("[form_engine] Worker de fond démarré.")


def enqueue(coro):
    if _task_queue is None:
        return
    try:
        _task_queue.put_nowait(coro)
    except asyncio.QueueFull:
        print("[form_engine] ⚠️ Queue pleine, tâche ignorée.")


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

async def _get_prenom(telegram_id: int) -> str:
    try:
        async with get_db() as cur:
            await cur.execute(
                "SELECT name FROM users WHERE telegram_id = %s", (telegram_id,)
            )
            row = await cur.fetchone()
            if row and row["name"]:
                p = row["name"].strip()
                if 1 <= len(p) <= 20:
                    return p
    except Exception:
        pass
    return "l'ami"


def _inject_vars(text: str, telegram_id: int, score: int = 0, total: int = 0, prenom: str = "l'ami") -> str:
    from datetime import date
    return (text
            .replace("+prenom", prenom)
            .replace("+score",  str(score))
            .replace("+total",  str(total))
            .replace("+date",   date.today().strftime("%d/%m/%Y")))


# ════════════════════════════════════════════════════════════════════════════
# VÉRIFICATION FORMULAIRE COMPLÉTÉ
# ════════════════════════════════════════════════════════════════════════════

async def has_completed_form(form_id: int, telegram_id: int) -> bool:
    try:
        async with get_db() as cur:
            await cur.execute("SELECT fields FROM forms WHERE id = %s", (form_id,))
            form = await cur.fetchone()
            if not form:
                return False
            fields = json.loads(form["fields"])
            required_fields = [f for f in fields if not f.get("optional", False) and f.get("id")]
            if not required_fields:
                return False
            required_ids = {str(f["id"]) for f in required_fields}

            await cur.execute("""
                SELECT field_id, value FROM form_responses
                WHERE form_id = %s AND telegram_id = %s
            """, (form_id, telegram_id))
            responses = await cur.fetchall()

            if not responses:
                return False
            answered_ids = {str(r["field_id"]) for r in responses
                            if r["value"] is not None and str(r["value"]).strip() != ""}
            return required_ids.issubset(answered_ids)
    except Exception as e:
        print(f"[has_completed_form] Erreur: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT MÉDIA
# ════════════════════════════════════════════════════════════════════════════

async def _download_media(bot, file_id: str, field_type: str) -> str | None:
    EXT_MAP = {"photo": "jpg", "video": "mp4", "audio": "ogg", "document": "bin"}
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        tg_file   = await bot.get_file(file_id)
        file_path = tg_file.file_path or ""
        ext       = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else EXT_MAP.get(field_type, "bin")
        filename  = f"{uuid.uuid4().hex}.{ext}"
        dest      = MEDIA_DIR / filename
        await tg_file.download_to_drive(str(dest))
        return f"{MEDIA_URL}/{filename}"
    except Exception as e:
        print(f"[form_engine] download_media {file_id}: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# ENVOI D'UN CHAMP
# ════════════════════════════════════════════════════════════════════════════

async def _send_field(bot, chat_id: int, field: dict, step: int, total_steps: int, progress: bool):
    ftype  = field.get("type", "text")
    label  = field.get("label") or "Réponds à cette question :"
    prefix = f"[{step}/{total_steps}] " if total_steps > 1 and progress else ""
    text   = f"{prefix}{label}"

    if ftype == "qcm":
        opts = field.get("opts", [])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(o["t"], callback_data=f"fopt_{o['t'][:40]}")] for o in opts])
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ftype == "multi":
        opts = field.get("opts", [])
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"☐ {o['t']}", callback_data=f"fmul_{i}_{o['t'][:35]}")] for i, o in enumerate(opts)]
            + [[InlineKeyboardButton("✅ Valider ma sélection", callback_data="fmul_validate")]])
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ftype == "oui_non":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Oui", callback_data="fopt_Oui"),
                                     InlineKeyboardButton("❌ Non", callback_data="fopt_Non")]])
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ftype == "note5":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"⭐{i}", callback_data=f"fopt_{i}") for i in range(1, 6)]])
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ftype == "nps":
        rows = [[InlineKeyboardButton(str(i), callback_data=f"fopt_{i}") for i in range(0, 6)],
                [InlineKeyboardButton(str(i), callback_data=f"fopt_{i}") for i in range(6, 11)]]
        await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(rows))
    elif ftype == "contact":
        kb = ReplyKeyboardMarkup([[KeyboardButton("📱 Partager mon numéro", request_contact=True)]],
                                  one_time_keyboard=True, resize_keyboard=True)
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ftype in ("photo", "video", "audio", "document"):
        hints = {"photo": "📸", "video": "🎬 (max 20 Mo).", "audio": "🎙️", "document": "📄"}
        skip_btn = None
        if not field.get("required", True):
            skip_btn = InlineKeyboardMarkup([[InlineKeyboardButton("Passer →", callback_data="fopt__skip")]])
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
    ftype  = field.get("type", "text")
    pts_ok = int(field.get("pts", 10))

    if ftype in ("qcm", "oui_non"):
        correct_opt = next((o for o in field.get("opts", []) if o.get("c")), None)
        if ftype == "oui_non":
            correct_val = (field.get("correctAnswer") or "").lower()
            is_correct  = raw_answer.lower() in correct_val or correct_val in raw_answer.lower()
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

def _eval_conditions(conditions: list, responses_so_far: dict) -> list:
    actions = []
    for rule in conditions:
        if_clause   = rule.get("if", {})
        then_clause = rule.get("then", {})
        field_label = if_clause.get("field", "")
        op          = if_clause.get("op", "=")
        cond_val    = str(if_clause.get("value", "")).lower()
        actual_val  = str(responses_so_far.get(field_label, "")).lower()
        match = False
        if op == "=":          match = actual_val == cond_val
        elif op == "≠":        match = actual_val != cond_val
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
                from telegram_page.categorie import add_members_to_category
                await add_members_to_category(value, [telegram_id])
                done.append(f"categorie:{value}")

            elif atype == "Envoyer message":
                prenom = await _get_prenom(telegram_id)
                msg = _inject_vars(value, telegram_id,
                                   score=context_vars.get("score", 0),
                                   total=context_vars.get("total", 0),
                                   prenom=prenom)
                await bot.send_message(telegram_id, msg)
                done.append("message_sent")

            elif atype == "Notifier admin":
                admin_id = context_vars.get("admin_id")
                if admin_id:
                    prenom = await _get_prenom(telegram_id)
                    await bot.send_message(
                        admin_id,
                        f"📋 Nouveau formulaire soumis\nUtilisateur : {prenom} ({telegram_id})\n{value}"
                    )
                done.append("admin_notified")

        except Exception as e:
            print(f"[form_engine] Action '{atype}' échouée pour {telegram_id}: {e}")
    return done


# ════════════════════════════════════════════════════════════════════════════
# RELANCE FORMULAIRES INCOMPLETS
# ════════════════════════════════════════════════════════════════════════════

async def relancer_formulaires_incomplets(bot, form_id: int = None, admin_id: int = None):
    async with get_db() as cur:
        if form_id:
            await cur.execute("""
                SELECT id, telegram_id, form_id, step_index FROM form_sessions
                WHERE status = 'in_progress' AND form_id = %s
            """, (form_id,))
        else:
            await cur.execute("""
                SELECT id, telegram_id, form_id, step_index FROM form_sessions
                WHERE status = 'in_progress'
            """)
        sessions = await cur.fetchall()

    if not sessions:
        if admin_id:
            await bot.send_message(admin_id, "✅ Aucune session incomplète à relancer.")
        return

    async def _send_relance(session):
        sid = session["id"]; telegram_id = session["telegram_id"]
        fid = session["form_id"]; step_index = session["step_index"]
        if telegram_id <= 0:
            return
        try:
            form = await get_form_by_id(fid)
            if not form: return
            fields  = form.get("fields", [])
            title   = form.get("name", "le formulaire")
            command = form.get("command", "")
            q_actuelle = min(step_index + 1, len(fields))
            if command:
                texte = (f"👋 Tu n'as pas encore terminé {title}.\n\n"
                         f"Tu en es à la question {q_actuelle}/{len(fields)}.\n\n"
                         f"Clique sur la commande ci-dessous pour reprendre 👇\n\n{command}")
            else:
                texte = (f"👋 Tu n'as pas encore terminé {title}.\n\n"
                         f"Tu en es à la question {q_actuelle}/{len(fields)}.\n\n"
                         f"Contacte-nous pour reprendre le formulaire. 🙏")
            await bot.send_message(telegram_id, texte)
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[relancer] Erreur user {telegram_id}: {e}")

    sent = 0
    for session in sessions:
        enqueue(_send_relance(session))
        sent += 1

    print(f"[relancer] {sent} relances mises en queue.")
    if admin_id:
        await bot.send_message(admin_id, f"📋 {sent} relances mises en queue.")


# ════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE D'UN FORMULAIRE
# ════════════════════════════════════════════════════════════════════════════

async def _form_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user        = update.effective_user
    user_id     = user.id
    text        = update.message.text.strip()
    command     = "/" + text.lstrip("/").split()[0]
    args        = text.split()[1:] if len(text.split()) > 1 else []
    start_param = args[0] if args and command == "/start" else None

    if command == "/start":
        if start_param == "relancer12345678":
            ADMIN_ID = 571718066
            await relancer_formulaires_incomplets(context.bot, form_id=17, admin_id=ADMIN_ID)

        from telegram_page.start_handler import process_start_link
        form_id = await process_start_link(update, context, user_id, user.first_name, start_param)

        if form_id == "__validation__":
            return FORM_STEP
        if not form_id:
            return ConversationHandler.END
        form = await get_form_by_id(form_id)
    else:
        form = await get_form_by_command(command)

    if not form:
        return ConversationHandler.END

    options        = form.get("options", {}) or {}
    form_completed = await has_completed_form(form["id"], user_id)

    if options.get("one_per_user") and form_completed:
        await update.message.reply_text(
            "✅ Vous avez déjà complété ce formulaire.\n\n"
            "Notre équipe a bien reçu vos informations. "
            "Si vous avez des questions, n'hésitez pas à nous contacter ici."
        )
        return ConversationHandler.END

    session = await get_or_create_session(form["id"], user_id)
    context.user_data.update({
        "form_id": form["id"], "session_id": session["id"],
        "step": session["step_index"], "progress": options.get("progress", False),
        "multi_sel": [], "responses": {},
    })

    fields = form.get("fields", [])
    if not fields:
        return ConversationHandler.END

    if form.get("intro"):
        prenom = await _get_prenom(user_id)
        await update.message.reply_text(_inject_vars(form["intro"], user_id, prenom=prenom))
        await asyncio.sleep(0.5)

    step = session["step_index"]
    if step >= len(fields):
        await update.message.reply_text("Tu as déjà complété ce formulaire.")
        return ConversationHandler.END

    await _send_field(context.bot, user_id, fields[step], step + 1, len(fields), options.get("progress", False))
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION TEXTE / CONTACT
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    progress   = context.user_data.get("progress", False)
    step       = context.user_data.get("step", 0)

    if context.user_data.get("in_validation"):
        return FORM_STEP

    if not form_id:
        async with get_db() as cur:
            await cur.execute("""
                SELECT id, form_id, step_index FROM form_sessions
                WHERE telegram_id = %s AND status = 'in_progress'
                ORDER BY updated_at DESC LIMIT 1
            """, (user_id,))
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text("⚠️ Aucun formulaire en cours.")
            return ConversationHandler.END
        session_id = row["id"]; form_id = row["form_id"]; step = row["step_index"]
        context.user_data.update({"form_id": form_id, "session_id": session_id,
                                   "step": step, "multi_sel": [], "responses": {}})

    form   = await get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field      = fields[step]
    raw_answer = update.message.text.strip() if update.message.text else ""
    if update.message.contact:
        raw_answer = update.message.contact.phone_number

    return await _process_answer(update, context, form, fields, field, step,
                                  session_id, form_id, user_id, raw_answer, progress=progress)


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
    progress   = context.user_data.get("progress", False)

    if not form_id:
        async with get_db() as cur:
            await cur.execute("""
                SELECT id, form_id, step_index FROM form_sessions
                WHERE telegram_id = %s AND status = 'in_progress'
                ORDER BY updated_at DESC LIMIT 1
            """, (user_id,))
            row = await cur.fetchone()
        if not row:
            await query.message.reply_text("⚠️ Aucun formulaire en cours.")
            return ConversationHandler.END
        session_id = row["id"]; form_id = row["form_id"]; step = row["step_index"]
        context.user_data.update({"form_id": form_id, "session_id": session_id,
                                   "step": step, "multi_sel": [], "responses": {}})

    form   = await  get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field = fields[step]
    data  = query.data

    if data.startswith("fmul_") and data != "fmul_validate":
        parts = data.split("_", 2); idx = int(parts[1])
        sel   = context.user_data.setdefault("multi_sel", [])
        opts  = field.get("opts", [])
        if idx in sel: sel.remove(idx)
        else:          sel.append(idx)
        new_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(("✅ " if i in sel else "☐ ") + o["t"],
                                    callback_data=f"fmul_{i}_{o['t'][:35]}")] for i, o in enumerate(opts)]
            + [[InlineKeyboardButton("✅ Valider ma sélection", callback_data="fmul_validate")]])
        try:
            await query.edit_message_reply_markup(new_kb)
        except Exception:
            pass
        return FORM_STEP

    if data == "fmul_validate":
        sel = context.user_data.get("multi_sel", [])
        opts = field.get("opts", [])
        raw_answer = ", ".join(opts[i]["t"] for i in sorted(sel) if i < len(opts)) or "—"
        context.user_data["multi_sel"] = []
    elif data == "fopt__skip":     raw_answer = "__skip__"
    elif data == "fopt__info":     raw_answer = "__info__"
    elif data.startswith("fopt_"): raw_answer = data[5:]
    else: return FORM_STEP

    return await _process_answer(update, context, form, fields, field, step,
                                  session_id, form_id, user_id, raw_answer,
                                  is_callback=True, progress=progress)


# ════════════════════════════════════════════════════════════════════════════
# RÉCEPTION MÉDIAS
# ════════════════════════════════════════════════════════════════════════════

async def _form_receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = update.effective_user.id
    form_id    = context.user_data.get("form_id")
    session_id = context.user_data.get("session_id")
    step       = context.user_data.get("step", 0)
    progress   = context.user_data.get("progress", False)

    if not form_id:
        return ConversationHandler.END

    form   = await get_form_by_id(form_id)
    fields = form.get("fields", [])
    if step >= len(fields):
        return ConversationHandler.END

    field = fields[step]; ftype = field.get("type"); file_id = None
    try:
        if ftype == "photo"    and update.message.photo:      file_id = update.message.photo[-1].file_id
        elif ftype == "video"  and update.message.video:      file_id = update.message.video.file_id
        elif ftype == "audio"  and update.message.voice:      file_id = update.message.voice.file_id
        elif ftype == "audio"  and update.message.audio:      file_id = update.message.audio.file_id
        elif ftype == "document" and update.message.document: file_id = update.message.document.file_id
    except Exception:
        pass

    if not file_id:
        await update.message.reply_text("❌ Fichier non reconnu, réessaie.")
        return FORM_STEP

    await update.message.reply_text("⏳ Fichier reçu, traitement en cours...")
    local_url  = await _download_media(context.bot, file_id, ftype)
    raw_answer = local_url if local_url else file_id

    return await _process_answer(update, context, form, fields, field, step,
                                  session_id, form_id, user_id, raw_answer, progress=progress)


# ════════════════════════════════════════════════════════════════════════════
# TRAITEMENT COMMUN D'UNE RÉPONSE
# ════════════════════════════════════════════════════════════════════════════

async def _process_answer(update, context, form, fields, field, step,
                           session_id, form_id, user_id, raw_answer: str,
                           is_callback: bool = False, progress: bool = False):
    bot = context.bot
    is_correct, points, feedback = _evaluate_answer(field, raw_answer)

    if raw_answer not in ("__skip__", "__info__"):
        await save_response(session_id, form_id, user_id,
                      field_id=field.get("id", step), field_type=field.get("type", "text"),
                      field_label=field.get("label", ""), value=raw_answer,
                      is_correct=is_correct, points=points)
        context.user_data["responses"][field.get("label", "")] = raw_answer

    if feedback:
        await bot.send_message(user_id, feedback)
        await asyncio.sleep(0.4)

    cond_actions = _eval_conditions(form.get("conditions", []), context.user_data.get("responses", {}))
    next_step    = step + 1
    await advance_session(session_id, next_step, add_score=points)

    if next_step >= len(fields):
        async def _finish_bg():
            await _finish_form(update, context, form, session_id, form_id, user_id, cond_actions)
        enqueue(_finish_bg())
        return ConversationHandler.END

    context.user_data["step"] = next_step
    await _send_field(bot, user_id, fields[next_step], next_step + 1, len(fields), progress)
    return FORM_STEP


# ════════════════════════════════════════════════════════════════════════════
# FIN DU FORMULAIRE
# ════════════════════════════════════════════════════════════════════════════

async def _finish_form(update, context, form, session_id, form_id, user_id, extra_cond_actions):
    bot = context.bot
    await complete_session(session_id)

    link_id = context.user_data.get("pending_link_id") if context else None
    if link_id:
        from telegram_page.start_handler import record_form_completion
        await record_form_completion(bot, user_id, link_id)
        context.user_data.pop("pending_link_id", None)

    session   = await get_session(session_id)
    score     = session["score"] if session else 0
    qcfg      = form.get("quiz_config", {})
    score_max = int(qcfg.get("max", 0))

    all_actions = form.get("actions", []) + extra_cond_actions
    admin_id    = context.bot_data.get("admin_id") if context else None
    ctx_vars    = {"score": score, "total": score_max, "admin_id": admin_id}

    done = await _run_actions(bot, user_id, all_actions, ctx_vars)
    await save_submission(session_id, form_id, user_id, done)

    if form.get("outro"):
        prenom = await _get_prenom(user_id)
        outro  = _inject_vars(form["outro"], user_id, score=score, total=score_max, prenom=prenom)
        await bot.send_message(user_id, outro, reply_markup=ReplyKeyboardRemove())

    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════════════════
# FALLBACK / CANCEL / TIMEOUT
# ════════════════════════════════════════════════════════════════════════════

async def _form_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("form_id"):
        return ConversationHandler.END
    await update.message.reply_text("⚠️ Je n'ai pas compris ta réponse.\nRéponds à la question ou tape /cancel.")
    return FORM_STEP


async def _form_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("⏱ Le formulaire a expiré. Recommence avec la commande.")
    return ConversationHandler.END


async def _form_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = context.user_data.get("session_id")
    if session_id:
        from form.form import abandon_session
        await abandon_session(session_id)
    context.user_data.clear()
    await update.message.reply_text("Formulaire annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════════════════
# ENVOI DIRECT / BROADCAST
# ════════════════════════════════════════════════════════════════════════════

async def send_form_to_user(bot, telegram_id: int, form_id: int, context=None):
    form = await get_form_by_id(form_id)
    if not form: return
    fields  = form.get("fields", [])
    options = form.get("options", {}) or {}
    if not fields: return

    session = await get_or_create_session(form_id, telegram_id)
    if form.get("intro"):
        prenom = await _get_prenom(telegram_id)
        await bot.send_message(telegram_id, _inject_vars(form["intro"], telegram_id, prenom=prenom))
        await asyncio.sleep(0.4)

    if context:
        context.user_data.update({"form_id": form_id, "session_id": session["id"],
                                   "step": session["step_index"],
                                   "progress": options.get("progress", False),
                                   "multi_sel": [], "responses": {}})

    await _send_field(bot, telegram_id, fields[0], 1, len(fields), options.get("progress", False))


async def broadcast_form(bot, form_id: int, user_ids: list[int], admin_id: int = None):
    sent = errors = 0

    async def _send_one(uid):
        nonlocal sent, errors
        try:
            await send_form_to_user(bot, uid, form_id)
            sent += 1
        except Exception as e:
            errors += 1
            print(f"[broadcast] uid={uid}: {e}")
        await asyncio.sleep(0.15)

    for uid in user_ids:
        enqueue(_send_one(uid))

    if admin_id:
        await bot.send_message(admin_id, f"📋 Broadcast de {len(user_ids)} users mis en queue.")


# ════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT DES HANDLERS
# ════════════════════════════════════════════════════════════════════════════

def register_form_handlers(app: Application, bot, admin_id: int):
    app.bot_data["admin_id"] = admin_id
    #app.post_init = setup_background_worker

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.COMMAND, _form_start)],
        states={
            FORM_STEP: [
                CallbackQueryHandler(_form_receive_callback, pattern=r"^(fopt_|fmul_)"),
                MessageHandler(filters.CONTACT,      _form_receive_text),
                MessageHandler(filters.PHOTO,        _form_receive_media),
                MessageHandler(filters.VIDEO,        _form_receive_media),
                MessageHandler(filters.VOICE,        _form_receive_media),
                MessageHandler(filters.AUDIO,        _form_receive_media),
                MessageHandler(filters.Document.ALL, _form_receive_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _form_receive_text),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _form_timeout),
                CallbackQueryHandler(_form_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel",  _form_cancel),
            CommandHandler("annuler", _form_cancel),
            MessageHandler(filters.COMMAND, _form_cancel),
            MessageHandler(filters.ALL,     _form_fallback),
        ],
        per_chat=False, per_user=True, allow_reentry=True,
        conversation_timeout=600,
    )

    app.add_handler(conv, group=1)
    print("[form_engine] Handlers formulaires enregistrés.")