"""
manual_revalidation.py
───────────────────────
Flow de revalidation manuelle d'un abonnement non pris en compte.

Principe :

  1. L'utilisateur tape la commande /revalidation (même principe que /valider).
     Le bot lui demande d'envoyer UNIQUEMENT l'email utilisé lors du paiement.
     Si le message envoyé n'est pas une adresse email valide (ou contient
     autre chose), on redemande — en rappelant le format attendu et en
     proposant, en alternative, de contacter directement Monsieur Fiacre
     (https://t.me/Fiacrekpanou).

  2. On cherche les paiements de cet email dans `subscription_info`, et on
     vérifie si un abonnement ACTIF (expires_at > NOW()) existe bien pour
     lui dans `subscriptions` (seule `subscriptions` fait foi pour dire si
     un user est "vraiment actif" — `subscription_info.status` peut être
     obsolète).

        - Abonnement déjà actif dans `subscriptions`  → pas de demande
          possible, message informatif au user, RIEN n'est envoyé à l'admin.
        - Paiement(s) trouvé(s) dans `subscription_info` mais AUCUN actif
          dans `subscriptions`                          → demande créée,
          admin notifié.
        - Aucun paiement du tout                        → pas de demande
          possible.

  3. L'admin (un seul : REVALIDATION_ADMIN_ID) reçoit un message avec :
         - l'email, le demandeur (telegram_id / username)
         - un avertissement si mismatch d'identité (email lié à plusieurs
           telegram_id, ou à un telegram_id différent du demandeur, ou à
           aucun telegram_id) — le user, lui, ne voit jamais ce détail
         - la liste complète des lignes `subscription_info` pour cet email
         - l'état actuel dans `subscriptions` (actif / inactif, jusqu'à quand)
         - 2 boutons : ✅ Valider / ❌ Refuser

  4. Valider → boutons rapides (+7 / +15 / +30 jours) + "Autre" (saisie
     manuelle du nombre de jours). Une fois le nombre connu :
         - abonnement actif existant  → on prolonge son expires_at
         - aucun abonnement actif     → nouvelle ligne (plan repris du
           paiement `subscription_info` le plus récent, duration_days = N)
         - les lignes `subscription_info` de cet email PAS DÉJÀ validées
           dans le passé sont marquées status='active' + note
         - tous les messages précédents envoyés à l'admin pour cette
           demande sont supprimés et remplacés par un récap final
         - le user reçoit une confirmation directe via son telegram_id

  5. Refuser → motif texte libre (ForceReply), mêmes nettoyage/récap côté
     admin, notification de refus (avec motif) au user.

  Anti-spam : une seule demande par email toutes les 24h.

Intégration dans script.py (3 lignes, dans _post_init et à la fin des
imports/handlers) :

    from manual_revalidation import (
        ensure_manual_revalidation_schema,
        register_manual_revalidation_handlers,
    )
    ...
    await ensure_manual_revalidation_schema()      # dans _post_init
    ...
    register_manual_revalidation_handlers(app)      # avec les autres register_*_handlers
"""

import json
import re
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from db import get_db

logger = logging.getLogger("manual_revalidation")

# ─────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────

REVALIDATION_ADMIN_ID = 571718066
FIACRE_CONTACT_URL = "https://t.me/Fiacrekpanou"
ANTISPAM_HOURS = 24
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# État en mémoire — un seul admin, pas besoin de persistance lourde.
# { telegram_id_user: True }                     → attend un email
_pending_email_input: dict[int, bool] = {}
# { admin_id: {"type": "days"|"reason", "request_id": int} }
_pending_admin_input: dict[int, dict] = {}


# ─────────────────────────────────────────────────────────────────────────
# SCHÉMA
# ─────────────────────────────────────────────────────────────────────────

async def ensure_manual_revalidation_schema():
    async with get_db() as cur:
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS manual_revalidation_requests (
                id                      INT AUTO_INCREMENT PRIMARY KEY,
                email                   VARCHAR(255) NOT NULL,
                requester_telegram_id   BIGINT NOT NULL,
                requester_username      VARCHAR(255) NULL,
                matched_telegram_ids    TEXT NULL,
                identity_mismatch       TINYINT(1) NOT NULL DEFAULT 0,
                subscription_info_ids   TEXT NULL,
                unvalidated_info_ids    TEXT NULL,
                status                  VARCHAR(20) NOT NULL DEFAULT 'pending',
                days_added              INT NULL,
                reject_reason           TEXT NULL,
                admin_chat_id           BIGINT NULL,
                admin_message_ids       TEXT NULL,
                created_at              DATETIME NOT NULL,
                handled_at              DATETIME NULL,
                INDEX idx_email (email)
            )
        """)


# ─────────────────────────────────────────────────────────────────────────
# HELPERS — lecture BDD
# ─────────────────────────────────────────────────────────────────────────

def _fmt(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d/%m/%Y %H:%M")


async def _get_subscription_info_rows(cur, email: str) -> list[dict]:
    await cur.execute(
        """
        SELECT id, email, plan, duration_days, started_at, expires_at,
               status, paid_at, amount_usd, note
        FROM subscription_info
        WHERE email = %s
        ORDER BY paid_at DESC
        """,
        (email,)
    )
    return list(await cur.fetchall())


async def _get_linked_telegram_ids(cur, email: str) -> list[int]:
    await cur.execute(
        "SELECT telegram_id FROM users WHERE email = %s AND telegram_id IS NOT NULL",
        (email,)
    )
    rows = await cur.fetchall()
    return [int(r["telegram_id"]) for r in rows]


async def _has_active_subscription(cur, telegram_id: int) -> dict | None:
    """Retourne la ligne subscriptions active la plus lointaine (ou None)."""
    await cur.execute(
        """
        SELECT id, plan, duration_days, expires_at
        FROM subscriptions
        WHERE user_id = %s AND expires_at > NOW()
        ORDER BY expires_at DESC
        LIMIT 1
        """,
        (telegram_id,)
    )
    return await cur.fetchone()


async def _any_active_subscription_for_email(cur, telegram_ids: list[int]) -> dict | None:
    """Vérifie, parmi tous les telegram_id liés à l'email, si un abonnement
    est actif. Retourne (telegram_id, ligne active) ou None."""
    for tg in telegram_ids:
        active = await _has_active_subscription(cur, tg)
        if active:
            return {"telegram_id": tg, **active}
    return None


async def _last_request_for_email(cur, email: str) -> dict | None:
    await cur.execute(
        """
        SELECT id, created_at, status
        FROM manual_revalidation_requests
        WHERE email = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (email,)
    )
    return await cur.fetchone()


def _already_validated(row: dict) -> bool:
    """Une ligne subscription_info est considérée déjà validée si son
    status est 'active' (posé par un traitement précédent, auto ou manuel)."""
    return (row.get("status") or "").lower() == "active"


# ─────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 — commande /revalidation → demande d'email
# ─────────────────────────────────────────────────────────────────────────

ASK_EMAIL_TEXT = (
    "🔎 <b>Revalidation d'abonnement</b>\n\n"
    "Tu as payé mais ton abonnement n'apparaît pas comme actif ?\n\n"
    "Envoie-moi <b>uniquement l'adresse email</b> utilisée lors du paiement "
    "— rien d'autre dans le message (pas de nom, pas de phrase, juste l'email).\n\n"
    "Exemple :\n"
    "<code>tonadresse@email.com</code>"
)

INVALID_EMAIL_TEXT = (
    "⚠️ Ce n'est pas une adresse email valide, ou le message contient autre chose "
    "que l'adresse.\n\n"
    "Renvoie-moi <b>uniquement l'adresse email</b> utilisée lors du paiement, "
    "au format :\n"
    "<code>tonadresse@email.com</code>\n\n"
    "Si tu n'y arrives pas, tu peux aussi contacter directement Monsieur Fiacre "
    f'ici : {FIACRE_CONTACT_URL}'
)


async def cmd_revalidation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _pending_email_input[user_id] = True
    await update.message.reply_text(ASK_EMAIL_TEXT, parse_mode="HTML")


class _PendingEmailFilter(filters.MessageFilter):
    def filter(self, message):
        u = message.from_user
        return bool(u and u.id in _pending_email_input)


pending_email_filter = _PendingEmailFilter()


async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    raw = (update.message.text or "").strip()
    email = raw.lower()

    # Le message doit contenir STRICTEMENT une adresse email, rien d'autre.
    if not _EMAIL_RE.match(email):
        # On reste en attente : le user peut réessayer directement.
        await update.message.reply_text(INVALID_EMAIL_TEXT, parse_mode="HTML")
        return

    # Email valide → on sort de l'état d'attente et on lance le process.
    _pending_email_input.pop(user_id, None)

    async with get_db() as cur:
        # Anti-spam : 1 demande / 24h / email
        last = await _last_request_for_email(cur, email)
        if last and last["created_at"] >= datetime.now() - timedelta(hours=ANTISPAM_HOURS):
            await update.message.reply_text(
                "⏳ Une demande a déjà été envoyée pour cet email il y a moins de "
                f"{ANTISPAM_HOURS}h.\n\n"
                "Merci de patienter avant d'en refaire une. Si c'est urgent, tu peux "
                f"contacter Monsieur Fiacre directement ici : {FIACRE_CONTACT_URL}",
                parse_mode="HTML",
            )
            return

        info_rows = await _get_subscription_info_rows(cur, email)
        if not info_rows:
            await update.message.reply_text(
                "❌ Aucun paiement trouvé pour cette adresse email.\n\n"
                "Vérifie l'orthographe et réessaie, ou contacte directement Monsieur "
                f"Fiacre ici : {FIACRE_CONTACT_URL}",
                parse_mode="HTML",
            )
            return

        linked_ids = await _get_linked_telegram_ids(cur, email)
        active = await _any_active_subscription_for_email(cur, linked_ids)

        if active:
            await update.message.reply_text(
                "✅ Bonne nouvelle : ton abonnement est déjà actif "
                f"(jusqu'au {_fmt(active['expires_at'])}).\n\n"
                "Rien à faire de plus de ton côté 🙌"
            )
            return

        # ── Éligible : on prépare la demande ────────────────────────────
        identity_mismatch = (
            len(linked_ids) == 0
            or len(linked_ids) > 1
            or linked_ids[0] != user_id
        )

        unvalidated_ids = [r["id"] for r in info_rows if not _already_validated(r)]

        now = datetime.now()
        await cur.execute(
            """
            INSERT INTO manual_revalidation_requests
                (email, requester_telegram_id, requester_username,
                 matched_telegram_ids, identity_mismatch,
                 subscription_info_ids, unvalidated_info_ids,
                 status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            """,
            (
                email, user_id, user.username,
                json.dumps(linked_ids), int(identity_mismatch),
                json.dumps([r["id"] for r in info_rows]),
                json.dumps(unvalidated_ids),
                now,
            )
        )
        request_id = cur.lastrowid

    await update.message.reply_text(
        "✅ Ta demande a bien été transmise à l'équipe.\n\n"
        "Tu recevras une confirmation ici dès qu'elle sera traitée."
    )

    await _notify_admin_new_request(context, request_id)


# ─────────────────────────────────────────────────────────────────────────
# NOTIFICATION ADMIN — nouvelle demande
# ─────────────────────────────────────────────────────────────────────────

async def _get_request(cur, request_id: int) -> dict | None:
    await cur.execute(
        "SELECT * FROM manual_revalidation_requests WHERE id = %s",
        (request_id,)
    )
    return await cur.fetchone()


async def _append_admin_message_id(cur, request_id: int, message_id: int):
    req = await _get_request(cur, request_id)
    ids = json.loads(req["admin_message_ids"]) if req and req["admin_message_ids"] else []
    ids.append(message_id)
    await cur.execute(
        "UPDATE manual_revalidation_requests SET admin_message_ids = %s, admin_chat_id = %s WHERE id = %s",
        (json.dumps(ids), REVALIDATION_ADMIN_ID, request_id)
    )


def _build_admin_recap_text(req: dict, info_rows: list[dict]) -> str:
    lines = [
        "🆕 <b>Demande de revalidation d'abonnement</b>",
        "",
        f"📧 Email : <code>{req['email']}</code>",
        f"👤 Demandeur : id <code>{req['requester_telegram_id']}</code>"
        + (f" (@{req['requester_username']})" if req.get('requester_username') else ""),
    ]

    if req["identity_mismatch"]:
        matched = json.loads(req["matched_telegram_ids"]) if req["matched_telegram_ids"] else []
        if not matched:
            lines.append("⚠️ <b>Aucun compte Telegram lié à cet email.</b>")
        elif len(matched) > 1:
            lines.append(f"⚠️ <b>Plusieurs comptes liés à cet email :</b> {matched}")
        else:
            lines.append(
                f"⚠️ <b>L'email est lié à un autre compte</b> (id <code>{matched[0]}</code>) "
                "que celui du demandeur."
            )

    lines.append("")
    lines.append("📄 <b>Paiements trouvés (subscription_info)</b> :")
    for r in info_rows:
        marker = "✅ déjà validé" if _already_validated(r) else "⚠️ non validé"
        lines.append(
            f"  • #{r['id']} — {r['plan']} ({r['duration_days']}j) — "
            f"{r['amount_usd']}$ — payé le {_fmt(r['paid_at'])} — "
            f"expire prévu {_fmt(r['expires_at'])} — {marker}"
        )

    lines.append("")
    lines.append("❗ Aucun abonnement actif trouvé dans `subscriptions` pour cet email.")

    return "\n".join(lines)


async def _notify_admin_new_request(context: ContextTypes.DEFAULT_TYPE, request_id: int):
    async with get_db() as cur:
        req = await _get_request(cur, request_id)
        info_rows = await _get_subscription_info_rows(cur, req["email"])

    text = _build_admin_recap_text(req, info_rows)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Valider", callback_data=f"revalid:validate:{request_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"revalid:reject:{request_id}"),
    ]])

    msg = await context.bot.send_message(
        chat_id=REVALIDATION_ADMIN_ID, text=text, parse_mode="HTML", reply_markup=keyboard,
    )

    async with get_db() as cur:
        await _append_admin_message_id(cur, request_id, msg.message_id)


# ─────────────────────────────────────────────────────────────────────────
# CALLBACKS ADMIN
# ─────────────────────────────────────────────────────────────────────────

async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != REVALIDATION_ADMIN_ID:
        await query.answer("Non autorisé.", show_alert=True)
        return
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]
    request_id = int(parts[2])

    async with get_db() as cur:
        req = await _get_request(cur, request_id)

    if not req or req["status"] != "pending":
        await query.answer("Cette demande a déjà été traitée.", show_alert=True)
        return

    if action == "validate":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("+7 jours", callback_data=f"revalid:days:{request_id}:7"),
                InlineKeyboardButton("+15 jours", callback_data=f"revalid:days:{request_id}:15"),
                InlineKeyboardButton("+30 jours", callback_data=f"revalid:days:{request_id}:30"),
            ],
            [InlineKeyboardButton("✏️ Autre (saisir)", callback_data=f"revalid:days:{request_id}:manual")],
        ])
        msg = await context.bot.send_message(
            chat_id=REVALIDATION_ADMIN_ID,
            text="Combien de jours souhaitez-vous ajouter ?",
            reply_markup=keyboard,
        )
        async with get_db() as cur:
            await _append_admin_message_id(cur, request_id, msg.message_id)
        return

    if action == "reject":
        _pending_admin_input[REVALIDATION_ADMIN_ID] = {"type": "reason", "request_id": request_id}
        msg = await context.bot.send_message(
            chat_id=REVALIDATION_ADMIN_ID,
            text="Tapez le motif du refus (réponse libre) :",
            reply_markup=ForceReply(selective=True),
        )
        async with get_db() as cur:
            await _append_admin_message_id(cur, request_id, msg.message_id)
        return

    if action == "days":
        value = parts[3]
        if value == "manual":
            _pending_admin_input[REVALIDATION_ADMIN_ID] = {"type": "days", "request_id": request_id}
            msg = await context.bot.send_message(
                chat_id=REVALIDATION_ADMIN_ID,
                text="Tapez le nombre de jours à ajouter :",
                reply_markup=ForceReply(selective=True),
            )
            async with get_db() as cur:
                await _append_admin_message_id(cur, request_id, msg.message_id)
            return

        await _apply_validation(context, request_id, int(value))


async def on_admin_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = _pending_admin_input.get(REVALIDATION_ADMIN_ID)
    if not pending:
        return

    text = (update.message.text or "").strip()
    request_id = pending["request_id"]

    if pending["type"] == "days":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("⚠️ Entrez un nombre de jours valide (entier positif).")
            return
        _pending_admin_input.pop(REVALIDATION_ADMIN_ID, None)
        await _apply_validation(context, request_id, int(text))
        return

    if pending["type"] == "reason":
        _pending_admin_input.pop(REVALIDATION_ADMIN_ID, None)
        await _apply_rejection(context, request_id, text)
        return


class _AdminReplyFilter(filters.MessageFilter):
    def filter(self, message):
        u = message.from_user
        return bool(
            u and u.id == REVALIDATION_ADMIN_ID
            and REVALIDATION_ADMIN_ID in _pending_admin_input
        )


admin_reply_filter = _AdminReplyFilter()


# ─────────────────────────────────────────────────────────────────────────
# APPLICATION — validation / refus
# ─────────────────────────────────────────────────────────────────────────

async def _cleanup_admin_messages(context: ContextTypes.DEFAULT_TYPE, req: dict):
    ids = json.loads(req["admin_message_ids"]) if req["admin_message_ids"] else []
    for mid in ids:
        try:
            await context.bot.delete_message(chat_id=REVALIDATION_ADMIN_ID, message_id=mid)
        except Exception:
            pass  # message déjà supprimé / trop ancien, on ignore


async def _apply_validation(context: ContextTypes.DEFAULT_TYPE, request_id: int, days: int):
    async with get_db() as cur:
        req = await _get_request(cur, request_id)
        if not req or req["status"] != "pending":
            return

        email = req["email"]
        linked_ids = await _get_linked_telegram_ids(cur, email)
        target_telegram_id = req["requester_telegram_id"]
        # Si un des comptes liés a déjà un abonnement (même expiré), on
        # priorise ce compte pour la prolongation ; sinon celui du demandeur.
        if linked_ids and req["requester_telegram_id"] not in linked_ids:
            target_telegram_id = linked_ids[0]

        # ── 1. subscriptions : prolongation ou nouvelle ligne ───────────
        await cur.execute(
            """
            SELECT id, plan, duration_days, expires_at
            FROM subscriptions
            WHERE user_id = %s
            ORDER BY expires_at DESC
            LIMIT 1
            """,
            (target_telegram_id,)
        )
        existing = await cur.fetchone()

        info_rows = await _get_subscription_info_rows(cur, email)
        latest_plan = info_rows[0]["plan"] if info_rows else "Manuel"

        now = datetime.now()
        if existing:
            base = existing["expires_at"] if existing["expires_at"] and existing["expires_at"] > now else now
            new_expires = base + timedelta(days=days)
            await cur.execute(
                """
                UPDATE subscriptions
                SET expires_at = %s, status = 'active',
                    note = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (new_expires, f"revalidation manuelle admin (+{days}j, demande #{request_id})", existing["id"])
            )
        else:
            new_expires = now + timedelta(days=days)
            await cur.execute(
                """
                INSERT INTO subscriptions
                    (user_id, plan, duration_days, started_at, expires_at,
                     status, note, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'active', %s, NOW(), NOW())
                """,
                (
                    target_telegram_id, latest_plan, days, now, new_expires,
                    f"revalidation manuelle admin (demande #{request_id})",
                )
            )

        # ── 2. subscription_info : marquer comme validé, uniquement les
        #       lignes pas déjà validées par le passé ──────────────────
        unvalidated_ids = json.loads(req["unvalidated_info_ids"]) if req["unvalidated_info_ids"] else []
        for info_id in unvalidated_ids:
            await cur.execute(
                """
                UPDATE subscription_info
                SET status = 'active',
                    note = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (f"revalidation manuelle admin (demande #{request_id})", info_id)
            )

        # ── 3. clôture de la demande ──────────────────────────────────
        await cur.execute(
            """
            UPDATE manual_revalidation_requests
            SET status = 'validated', days_added = %s, handled_at = NOW()
            WHERE id = %s
            """,
            (days, request_id)
        )

    await _cleanup_admin_messages(context, req)

    await context.bot.send_message(
        chat_id=REVALIDATION_ADMIN_ID,
        text=(
            f"✅ <b>Demande #{request_id} validée</b>\n\n"
            f"📧 {req['email']}\n"
            f"👤 Compte crédité : <code>{target_telegram_id}</code>\n"
            f"➕ Jours ajoutés : <b>{days}</b>\n"
            f"📅 Nouvelle expiration : <b>{_fmt(new_expires)}</b>"
        ),
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_telegram_id"],
            text=(
                "✅ <b>Ton abonnement a été revalidé !</b>\n\n"
                f"➕ Jours ajoutés : <b>{days}</b>\n"
                f"📅 Actif jusqu'au : <b>{_fmt(new_expires)}</b>\n\n"
                "Merci pour ta patience 🙏"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning(f"[manual_revalidation] impossible de notifier le user {req['requester_telegram_id']}")


async def _apply_rejection(context: ContextTypes.DEFAULT_TYPE, request_id: int, reason: str):
    async with get_db() as cur:
        req = await _get_request(cur, request_id)
        if not req or req["status"] != "pending":
            return
        await cur.execute(
            """
            UPDATE manual_revalidation_requests
            SET status = 'rejected', reject_reason = %s, handled_at = NOW()
            WHERE id = %s
            """,
            (reason, request_id)
        )

    await _cleanup_admin_messages(context, req)

    await context.bot.send_message(
        chat_id=REVALIDATION_ADMIN_ID,
        text=(
            f"❌ <b>Demande #{request_id} refusée</b>\n\n"
            f"📧 {req['email']}\n"
            f"👤 Demandeur : <code>{req['requester_telegram_id']}</code>\n"
            f"📝 Motif : {reason}"
        ),
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_telegram_id"],
            text=(
                "❌ <b>Ta demande de revalidation n'a pas été acceptée.</b>\n\n"
                f"📝 Motif : {reason}\n\n"
                f"Pour toute question, contacte Monsieur Fiacre ici : {FIACRE_CONTACT_URL}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning(f"[manual_revalidation] impossible de notifier le user {req['requester_telegram_id']}")


# ─────────────────────────────────────────────────────────────────────────
# ENREGISTREMENT DES HANDLERS
# ─────────────────────────────────────────────────────────────────────────

def register_manual_revalidation_handlers(app: Application):
    app.add_handler(CommandHandler("revalidation", cmd_revalidation_start))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & pending_email_filter,
        receive_email,
    ))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^revalid:"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & admin_reply_filter,
        on_admin_text_reply,
    ))