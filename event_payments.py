"""
event_payments.py
──────────────────
Validation de paiement événementiel → accès à un canal Telegram dédié.

Principe (voir le document produit avec l'utilisateur) :

  1. Un paiement externe (FedaPay/PawaPay/Izichange) déclenche un webhook
     (voir routes_event_payments.py) qui appelle record_event_payment()
     et insère/actualise une ligne dans `paiement_unique`.

  2. L'utilisateur tape /validation_de_mon_paiement, donne son email.
     - email introuvable / aucun paiement disponible pour cet email
       → message clair, l'utilisateur peut retaper un email directement
         dans la foulée (jamais de flow qui reste ouvert indéfiniment).
     - un seul paiement disponible correspondant à l'email → on continue
       directement avec cet événement.
     - plusieurs paiements différents pour le même email (événements
       différents) → l'utilisateur choisit lequel valider via boutons.

  3. Une fois l'événement déterminé : le paiement est associé à son
     telegram_id, et le bot lui montre le bouton "Rejoindre le canal"
     (lien d'invitation configuré via /configurer_evenement). Un seul
     message est ÉDITÉ au fil des étapes (pas de succession de messages
     à nettoyer — Telegram ne permet de toute façon pas à un bot de
     supprimer les messages tapés par l'utilisateur en DM).

  4. Quand l'utilisateur clique et demande à rejoindre le canal, le
     ChatJoinRequestHandler dédié (event_join_request, groupe 1)
     vérifie : le canal correspond à un événement configuré, un
     paiement disponible et non utilisé existe pour (telegram_id,
     événement), l'accès n'a pas déjà été accordé. Si tout est bon :
     approve() + le message précédemment édité devient le message final
     de félicitations. Sinon : decline() automatique + message explicatif
     court à l'utilisateur.

  IMPORTANT — collision avec le canal principal existant :
  Le handler `approve_join_request` de script.py traite actuellement
  TOUTES les demandes d'adhésion, sans filtrer par chat. Ce module exige
  qu'on ajoute une garde en tête de cette fonction (chat.id == CANAL_B_ID
  → sinon return) pour que les deux flows ne se marchent jamais dessus —
  voir SCRIPT_PY_INTEGRATION dans la réponse associée à ce fichier.

Intégration dans script.py :

    from event_payments import (
        ensure_event_payments_schema,
        register_event_payment_handlers,
    )
    ...
    await ensure_event_payments_schema()             # dans _post_init
    ...
    register_event_payment_handlers(app)              # avec les autres register_*_handlers
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ChatJoinRequestHandler, ContextTypes, filters,
)

from db import get_db

logger = logging.getLogger("event_payments")

# ─────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────

# Même convention que le reste du projet (gold_core.ADMIN_ID, etc.) :
# IDs admin dupliqués par module plutôt qu'importés depuis script.py, pour
# éviter tout import circulaire. Garder synchronisé avec ADMIN_IDS
# (script.py) si la liste change.
ADMIN_IDS = [6992809421, 571718066]
FIACRE_CONTACT_URL = "https://t.me/Fiacrekpanou"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Statuts de paiement considérés comme "payé" par le webhook — À AJUSTER
# si l'agrégateur envoie d'autres libellés. Tant qu'un paiement n'est pas
# dans cette liste, il reste `disponible = 0` (non utilisable) même s'il
# est enregistré — un rejeu du webhook avec un statut définitif le fera
# passer à disponible=1 sans dupliquer la ligne (upsert sur payment_id).
PAID_STATUSES = {"paid", "completed", "success", "succeeded", "active"}

FINAL_TEXT = (
    "🎉 <b>Félicitations !</b>\n\n"
    "Votre paiement a bien été validé et votre accès au canal a été confirmé.\n\n"
    "Bienvenue dans cette nouvelle aventure. 🚀\n\n"
    "Profitez pleinement de votre accès et à très bientôt dans le canal.\n\n"
    "El Lobo 🐺"
)

# État en mémoire — flow court et synchrone (un échange direct avec
# l'utilisateur), pas besoin de persistance lourde ici. Ce qui doit
# survivre à un redémarrage (message à éditer plus tard, au moment du
# clic sur "Rejoindre le canal") est en base — voir bot_chat_id /
# bot_message_id dans paiement_unique.
_pending_email_input: dict[int, bool] = {}
_pending_event_choice: dict[int, list[dict]] = {}   # user_id -> candidats


# ─────────────────────────────────────────────────────────────────────────
# SCHÉMA WEBHOOK
# ─────────────────────────────────────────────────────────────────────────

class SubscriptionPayload_(BaseModel):
    plan:          str
    event_name:    str
    status:        Optional[str]   = "pending"
    note:          Optional[str]   = None
    order_id:      Optional[str]   = None
    name:          Optional[str]   = None
    email:         Optional[str]   = None
    phone:         Optional[str]   = None
    country_code:  Optional[str]   = None
    billing_cycle: Optional[str]   = None
    amount_usd:    Optional[float] = None
    currency:      Optional[str]   = None
    amount_local:  Optional[float] = None
    aggregator:    Optional[str]   = None
    paid_at:       Optional[str]   = None


# ─────────────────────────────────────────────────────────────────────────
# SCHÉMA DB
# ─────────────────────────────────────────────────────────────────────────

async def ensure_event_payments_schema():
    async with get_db() as cur:
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS paiement_unique (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                payment_id      VARCHAR(255) NOT NULL,
                email           VARCHAR(255) NOT NULL DEFAULT '',
                nom_evenement   VARCHAR(255) NOT NULL,
                montant         DECIMAL(12,2) NULL,
                devise          VARCHAR(10) NULL,
                processeur      VARCHAR(50) NULL,
                disponible      TINYINT(1) NOT NULL DEFAULT 0,
                deja_utilise    TINYINT(1) NOT NULL DEFAULT 0,
                telegram_id     BIGINT NULL,
                etat_parcours   VARCHAR(30) NOT NULL DEFAULT 'en_attente',
                acces_accorde   TINYINT(1) NOT NULL DEFAULT 0,
                bot_chat_id     BIGINT NULL,
                bot_message_id  BIGINT NULL,
                raw_payload     TEXT NULL,
                created_at      DATETIME NOT NULL,
                paid_at         DATETIME NULL,
                used_at         DATETIME NULL,
                UNIQUE KEY idx_payment_id (payment_id),
                INDEX idx_email_evenement (email, nom_evenement),
                INDEX idx_telegram_evenement (telegram_id, nom_evenement)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS evenement_canaux (
                nom_evenement    VARCHAR(255) PRIMARY KEY,
                lien_invitation  VARCHAR(500) NOT NULL,
                id_canal         BIGINT NOT NULL,
                created_at       DATETIME NOT NULL,
                updated_at       DATETIME NOT NULL,
                INDEX idx_id_canal (id_canal)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
    logger.info("[event_payments] schéma paiement_unique / evenement_canaux OK")


# ─────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────

def _normalize_event_name(name: str) -> str:
    """Normalisation appliquée PARTOUT où un nom d'événement apparaît
    (webhook, /configurer_evenement, recherche) — garantit que la
    correspondance ne dépend pas de la casse ou des espaces."""
    return (name or "").strip().lower().replace(" ", "_")


def _parse_paid_at(raw: str | None) -> datetime:
    if raw:
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.fromisoformat(raw) if fmt is None else datetime.strptime(raw, fmt)
            except (ValueError, TypeError):
                continue
        logger.warning(f"[event_payments] paid_at illisible ({raw!r}), NOW() utilisé à la place")
    return datetime.now()


async def _get_event_channel(cur, nom_evenement: str) -> dict | None:
    await cur.execute(
        "SELECT * FROM evenement_canaux WHERE nom_evenement = %s", (nom_evenement,)
    )
    return await cur.fetchone()


# ─────────────────────────────────────────────────────────────────────────
# 1. WEBHOOK — enregistrement du paiement
# ─────────────────────────────────────────────────────────────────────────

async def record_event_payment(payload: SubscriptionPayload_) -> dict:
    """
    Appelée par la route FastAPI du webhook (routes_event_payments.py).
    Idempotente sur payment_id (order_id) : un rejeu du même paiement met
    à jour la ligne au lieu d'en créer une seconde, et ne touche JAMAIS
    un paiement déjà marqué comme utilisé.
    """
    event_name = _normalize_event_name(payload.event_name)
    if not event_name:
        raise ValueError("event_name manquant ou vide")

    order_id = (payload.order_id or "").strip()
    if not order_id:
        # Pas d'identifiant stable fourni par l'agrégateur : on ne peut
        # pas garantir l'idempotence sur un rejeu du webhook. On génère
        # un identifiant de repli plutôt que de planter, mais ce cas
        # mérite d'être signalé côté agrégateur si ça arrive souvent.
        import uuid
        order_id = f"noid-{uuid.uuid4().hex[:16]}"
        logger.warning(f"[event_payments] order_id absent du webhook pour {payload.email!r} "
                        f"— identifiant de repli généré: {order_id}")

    email = (payload.email or "").strip().lower()
    if not email:
        logger.error(f"[event_payments] webhook SANS email — payment_id={order_id}, "
                      f"event={event_name}. Ce paiement ne pourra pas être auto-validé "
                      f"par l'utilisateur (aucun email pour le retrouver).")

    is_paid = (payload.status or "").strip().lower() in PAID_STATUSES
    paid_at = _parse_paid_at(payload.paid_at)
    raw_json = payload.model_dump_json()

    async with get_db() as cur:
        await cur.execute(
            "SELECT id, deja_utilise FROM paiement_unique WHERE payment_id = %s", (order_id,)
        )
        existing = await cur.fetchone()

        if existing:
            if existing["deja_utilise"]:
                logger.info(f"[event_payments] webhook rejoué pour paiement déjà utilisé "
                            f"payment_id={order_id} — ignoré")
                return {"status": "ignored_already_used", "payment_id": order_id}

            await cur.execute("""
                UPDATE paiement_unique SET
                    email = %s, nom_evenement = %s, montant = %s, devise = %s,
                    processeur = %s, disponible = %s, paid_at = %s, raw_payload = %s
                WHERE id = %s
            """, (email, event_name, payload.amount_usd, payload.currency,
                  payload.aggregator, int(is_paid), paid_at, raw_json, existing["id"]))
            return {"status": "updated", "payment_id": order_id, "disponible": is_paid}

        await cur.execute("""
            INSERT INTO paiement_unique
                (payment_id, email, nom_evenement, montant, devise, processeur,
                 disponible, deja_utilise, etat_parcours, acces_accorde,
                 raw_payload, created_at, paid_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,0,'en_attente',0,%s,NOW(),%s)
        """, (order_id, email, event_name, payload.amount_usd, payload.currency,
              payload.aggregator, int(is_paid), raw_json, paid_at))
        return {"status": "created", "payment_id": order_id, "disponible": is_paid}


# ─────────────────────────────────────────────────────────────────────────
# 2. COMMANDE ADMIN — /configurer_evenement
# ─────────────────────────────────────────────────────────────────────────

async def cmd_configurer_evenement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            "Usage :\n"
            "<code>/configurer_evenement &lt;nom_evenement&gt; &lt;lien_invitation&gt; &lt;id_canal&gt;</code>\n\n"
            "— nom_evenement : un seul mot, sans espace (ex. <code>conference_paris_2026</code>), "
            "doit correspondre à ce qu'envoie le webhook de paiement.\n"
            "— id_canal : l'ID numérique du canal Telegram (ex. <code>-1001234567890</code>).",
            parse_mode="HTML",
        )
        return

    nom_evenement_raw, lien_invitation, id_canal_raw = args
    nom_evenement = _normalize_event_name(nom_evenement_raw)

    if not lien_invitation.startswith("http"):
        await update.message.reply_text("⚠️ Le lien d'invitation doit commencer par http(s)://")
        return

    try:
        id_canal = int(id_canal_raw)
    except ValueError:
        await update.message.reply_text("⚠️ id_canal doit être un nombre entier (ex. -1001234567890).")
        return

    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO evenement_canaux (nom_evenement, lien_invitation, id_canal, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            AS new_vals
            ON DUPLICATE KEY UPDATE
                lien_invitation = new_vals.lien_invitation,
                id_canal        = new_vals.id_canal,
                updated_at      = NOW()
        """, (nom_evenement, lien_invitation, id_canal))

    await update.message.reply_text(
        f"✅ Événement configuré.\n\n"
        f"nom_evenement : <code>{nom_evenement}</code>\n"
        f"id_canal : <code>{id_canal}</code>\n"
        f"lien : {lien_invitation}",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────
# 3. COMMANDE UTILISATEUR — /validation_de_mon_paiement
# ─────────────────────────────────────────────────────────────────────────

ASK_EMAIL_TEXT = (
    "🚨 <b>ACTION REQUISE</b>\n\n"
    "Pour commencer la validation de votre paiement, veuillez renseigner "
    "l'adresse e-mail utilisée lors de votre achat."
)

INVALID_EMAIL_TEXT = (
    "⚠️ Ce n'est pas une adresse email valide.\n\n"
    "Renvoie-moi uniquement l'adresse email utilisée lors du paiement, "
    "au format : <code>tonadresse@email.com</code>"
)

NOT_FOUND_TEXT = (
    "❌ Aucun paiement disponible trouvé pour cette adresse email.\n\n"
    "Vérifie l'orthographe et renvoie ton adresse email, ou contacte "
    f"directement Monsieur Fiacre ici : {FIACRE_CONTACT_URL}"
)


async def cmd_validation_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _pending_email_input[user_id] = True
    _pending_event_choice.pop(user_id, None)
    msg = await update.message.reply_text(ASK_EMAIL_TEXT, parse_mode="HTML")
    context.user_data["event_payment_msg_id"] = msg.message_id


class _PendingEmailFilter(filters.MessageFilter):
    def filter(self, message):
        u = message.from_user
        return bool(u and u.id in _pending_email_input)


pending_email_filter = _PendingEmailFilter()


async def _edit_or_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         text: str, reply_markup=None) -> int:
    """Édite le message tracké de ce flow s'il existe, sinon en envoie un
    nouveau et le mémorise. Renvoie le message_id courant."""
    msg_id = context.user_data.get("event_payment_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text,
                parse_mode="HTML", reply_markup=reply_markup,
            )
            return msg_id
        except Exception:
            pass  # message trop vieux / déjà modifié ailleurs → on en renvoie un
    msg = await context.bot.send_message(chat_id=chat_id, text=text,
                                          parse_mode="HTML", reply_markup=reply_markup)
    context.user_data["event_payment_msg_id"] = msg.message_id
    return msg.message_id


async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = (update.message.text or "").strip()
    email = raw.lower()

    if not _EMAIL_RE.match(email):
        await _edit_or_send(context, user_id, INVALID_EMAIL_TEXT)
        return   # reste en attente — l'utilisateur peut retaper directement

    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM paiement_unique
            WHERE email = %s AND disponible = 1 AND deja_utilise = 0
            ORDER BY created_at DESC
        """, (email,))
        candidates = list(await cur.fetchall())

    if not candidates:
        already = await _check_already_granted(email)
        if already:
            await _edit_or_send(
                context, user_id,
                f"✅ Tu es déjà dans le canal de l'événement "
                f"<b>{already['nom_evenement']}</b> — rien à faire de plus 🙌",
            )
            _pending_email_input.pop(user_id, None)
            return
        await _edit_or_send(context, user_id, NOT_FOUND_TEXT)
        return   # reste en attente — retape un email dans la foulée

    _pending_email_input.pop(user_id, None)

    if len(candidates) == 1:
        await _proceed_with_payment(context, user_id, candidates[0])
        return

    # Plusieurs paiements disponibles pour cet email → l'utilisateur choisit.
    _pending_event_choice[user_id] = candidates
    rows = [[InlineKeyboardButton(
        f"{c['nom_evenement']} — {c['montant'] or '?'}{c['devise'] or ''}",
        callback_data=f"evpay:choose:{c['id']}",
    )] for c in candidates]
    await _edit_or_send(
        context, user_id,
        "✅ Plusieurs paiements trouvés pour cet email.\n\n"
        "👉 Sélectionne celui que tu veux valider :",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_event_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    candidates = _pending_event_choice.get(user_id)
    if not candidates:
        await query.answer("Cette sélection a expiré, retape /validation_de_mon_paiement.",
                            show_alert=True)
        return

    payment_id = int(query.data.rsplit(":", 1)[-1])
    payment = next((c for c in candidates if c["id"] == payment_id), None)
    if payment is None:
        return

    _pending_event_choice.pop(user_id, None)
    context.user_data["event_payment_msg_id"] = query.message.message_id
    await _proceed_with_payment(context, user_id, payment)


async def _proceed_with_payment(context: ContextTypes.DEFAULT_TYPE, user_id: int, payment: dict):
    async with get_db() as cur:
        channel = await _get_event_channel(cur, payment["nom_evenement"])

    if channel is None:
        logger.error(f"[event_payments] événement '{payment['nom_evenement']}' sans canal "
                      f"configuré (paiement id={payment['id']}, user={user_id})")
        await _edit_or_send(
            context, user_id,
            "⚠️ Ton paiement est validé, mais l'accès au canal n'est pas encore configuré "
            f"côté équipe. Contacte Monsieur Fiacre ici : {FIACRE_CONTACT_URL}",
        )
        return

    msg_id = await _edit_or_send(
        context, user_id,
        "✅ <b>PAIEMENT VALIDÉ</b>\n\n"
        "Votre paiement a bien été reconnu.\n\n"
        "👉 <b>DERNIÈRE ÉTAPE</b> : demandez maintenant votre accès au canal "
        "en cliquant sur le bouton ci-dessous.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👉 Rejoindre le canal", url=channel["lien_invitation"])
        ]]),
    )

    # Persisté en base : la demande d'adhésion peut arriver bien plus
    # tard (après un redémarrage du bot), on doit pouvoir éditer ce
    # même message au moment de l'approbation.
    async with get_db() as cur:
        await cur.execute("""
            UPDATE paiement_unique
            SET telegram_id = %s, etat_parcours = 'email_valide',
                bot_chat_id = %s, bot_message_id = %s
            WHERE id = %s
        """, (user_id, user_id, msg_id, payment["id"]))


# ─────────────────────────────────────────────────────────────────────────
# 4. DEMANDE D'ADHÉSION AU CANAL ÉVÉNEMENT
# ─────────────────────────────────────────────────────────────────────────

DECLINE_TEXT = (
    "❌ Ta demande n'a pas pu être validée.\n\n"
    "Si tu as payé pour cet événement, utilise d'abord la commande "
    "/validation_de_mon_paiement puis clique sur le bouton fourni pour "
    "rejoindre le canal."
)


async def event_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Enregistré en groupe 1 (voir register_event_payment_handlers). Ne
    fait RIEN si le canal ciblé n'est pas un canal événement connu —
    dans ce cas la demande est laissée au handler du canal principal
    (approve_join_request, groupe 0, filtré sur CANAL_B_ID).
    """
    req = update.chat_join_request
    chat_id = req.chat.id
    user_id = req.from_user.id

    async with get_db() as cur:
        await cur.execute(
            "SELECT nom_evenement FROM evenement_canaux WHERE id_canal = %s", (chat_id,)
        )
        channel = await cur.fetchone()

    if channel is None:
        return   # pas un canal événement — géré ailleurs

    event_name = channel["nom_evenement"]

    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM paiement_unique
            WHERE telegram_id = %s AND nom_evenement = %s
              AND disponible = 1 AND deja_utilise = 0 AND acces_accorde = 0
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, event_name))
        payment = await cur.fetchone()

    if payment is None:
        try:
            await req.decline()
        except Exception as e:
            logger.warning(f"[event_payments] decline() échoué user={user_id} chat={chat_id}: {e}")
        try:
            await context.bot.send_message(chat_id=user_id, text=DECLINE_TEXT)
        except Exception:
            pass
        return

    try:
        await req.approve()
    except Exception as e:
        logger.error(f"[event_payments] approve() échoué user={user_id} chat={chat_id}: {e}",
                     exc_info=True)
        return   # ne pas marquer utilisé si l'ajout au canal a échoué

    async with get_db() as cur:
        await cur.execute("""
            UPDATE paiement_unique
            SET deja_utilise = 1, acces_accorde = 1,
                etat_parcours = 'acces_accorde', used_at = NOW()
            WHERE id = %s
        """, (payment["id"],))

    bot_chat_id = payment.get("bot_chat_id")
    bot_message_id = payment.get("bot_message_id")
    if bot_chat_id and bot_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=bot_chat_id, message_id=bot_message_id,
                text=FINAL_TEXT, parse_mode="HTML",
            )
            return
        except Exception:
            pass   # message introuvable/trop ancien → repli ci-dessous

    try:
        await context.bot.send_message(chat_id=user_id, text=FINAL_TEXT, parse_mode="HTML")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────
# 5. "Déjà dans le canal" — retape la commande après coup
# ─────────────────────────────────────────────────────────────────────────
# Couvert naturellement par _proceed_with_payment : une fois acces_accorde=1,
# la ligne ne remonte plus dans `disponible=1 AND deja_utilise=0`, donc
# receive_email renverra NOT_FOUND_TEXT... ce qui serait trompeur (le
# paiement N'EST PAS introuvable, il est simplement déjà utilisé). On
# distingue donc explicitement ce cas avant le message générique.

async def _check_already_granted(email: str) -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT nom_evenement FROM paiement_unique
            WHERE email = %s AND acces_accorde = 1
            ORDER BY used_at DESC LIMIT 1
        """, (email,))
        return await cur.fetchone()


# ─────────────────────────────────────────────────────────────────────────
# ENREGISTREMENT DES HANDLERS
# ─────────────────────────────────────────────────────────────────────────

def register_event_payment_handlers(app: Application):
    app.add_handler(CommandHandler("validation_de_mon_paiement", cmd_validation_start))
    app.add_handler(CommandHandler("configurer_evenement", cmd_configurer_evenement))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & pending_email_filter,
        receive_email,
    ))
    app.add_handler(CallbackQueryHandler(on_event_choice, pattern=r"^evpay:choose:\d+$"))

    # Groupe 1 — ne prend jamais la main sur le canal principal (géré en
    # groupe 0 par approve_join_request, filtré sur CANAL_B_ID). Les deux
    # coexistent sans collision car PTB traite chaque groupe indépendamment.
    app.add_handler(ChatJoinRequestHandler(event_join_request), group=1)

    logger.info("[event_payments] Handlers enregistrés "
                "(/validation_de_mon_paiement + /configurer_evenement + adhésion canaux événements) ✓")