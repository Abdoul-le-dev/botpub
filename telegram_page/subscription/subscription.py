# subscription.py — v5 : cumul intelligent + notification réabonnement
#
# Nouveautés vs v4 :
#   1. Idempotence conservée : (email, paid_at) identique → "déjà sauvegardé"
#   2. Recherche par email d'un abonnement existant
#      - Aucun trouvé → INSERT (nouveau paiement, comportement v4 conservé)
#      - Trouvé → UPDATE de la ligne existante (cumul par extension) :
#          nouvelle expires_at = max(ancienne expires_at, NOW()) + duration_days
#   3. Notification admin adaptée : "Nouveau paiement" ou "Réabonnement"
#      envoyée à TOUS les admins configurés
#   4. Message de confirmation envoyé au user Telegram UNIQUEMENT en cas
#      de réabonnement (lookup via users.email → telegram_id)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
import os
import logging
from telegram import Bot
from dotenv import load_dotenv

from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

from db import get_db
# Liste des admins (var d'env BROADCAST_ADMIN_IDS ou défaut) partagée
# avec le moteur de broadcast — un seul point de vérité.
from broadcast.config import ADMIN_IDS

load_dotenv()
logger = logging.getLogger(__name__)

bot = Bot(token=os.getenv("tokens"))
router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════

class SubscriptionPayload(BaseModel):
    plan:          str
    duration_days: int
    started_at:    str
    expires_at:    str
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


class FormationValidationRequest(BaseModel):
    email: EmailStr


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _notify_all_admins(bot_instance, text: str) -> None:
    """Envoie un message à tous les admins configurés (best-effort)."""
    for admin_id in ADMIN_IDS:
        try:
            await bot_instance.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning(f"[admin_notif] échec {admin_id} : {e}")


async def _find_user_by_email(email: str) -> Optional[dict]:
    """
    Cherche telegram_id + name dans la table users à partir de l'email.
    Retourne None si l'email n'a pas de match ou si le user n'a pas
    de telegram_id (compte web-only).
    """
    if not email:
        return None
    async with get_db() as cur:
        await cur.execute(
            "SELECT telegram_id, name FROM users "
            "WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s)) "
            "  AND telegram_id IS NOT NULL "
            "LIMIT 1",
            (email,),
        )
        return await cur.fetchone()


async def _find_latest_subscription(email: str) -> Optional[dict]:
    """
    Récupère l'abonnement le plus récent pour cet email, ou None.
    Trié par expires_at DESC puis id DESC pour prendre la ligne la plus à jour.
    """
    if not email:
        return None
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM subscription_info "
            "WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s)) "
            "ORDER BY expires_at DESC, id DESC LIMIT 1",
            (email,),
        )
        return await cur.fetchone()


async def _check_duplicate_payment(email: str, paid_at: Optional[str]) -> Optional[dict]:
    """
    Protection anti-webhook doublé : si (email, paid_at) existe déjà,
    on considère que c'est le même paiement rejoué.
    """
    if not email or not paid_at:
        return None
    async with get_db() as cur:
        await cur.execute(
            "SELECT id FROM subscription_info WHERE email = %s AND paid_at = %s LIMIT 1",
            (email, paid_at),
        )
        return await cur.fetchone()


def _compute_new_expires_at(current_expires, duration_days: int) -> datetime:
    """
    Cumul par extension (règle métier confirmée) :
      - Si abonnement en cours (expires_at > NOW) → base = expires_at
        → le client conserve ses jours restants et empile les nouveaux.
      - Sinon (expiré ou nouveau) → base = NOW
        → l'abonnement repart de zéro avec les nouveaux jours.
    Résultat : base + duration_days.
    """
    now = datetime.now()
    if isinstance(current_expires, str):
        try:
            current_expires = datetime.strptime(current_expires, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            current_expires = None
    base = current_expires if (current_expires and current_expires > now) else now
    return base + timedelta(days=duration_days)


def _extract_prenom(*candidates: Optional[str], fallback: str = "cher client") -> str:
    """
    Extrait un prénom présentable du premier candidat non vide.
    Prend le premier mot (jusqu'à 30 caractères).
    """
    for c in candidates:
        if c and c.strip():
            first = c.strip().split()[0]
            if 1 <= len(first) <= 30:
                return first
    return fallback


def _format_datetime_fr(dt) -> str:
    """Format lisible : JJ/MM/AAAA à HH:MM. Accepte datetime ou str."""
    if not dt:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return dt
    return dt.strftime("%d/%m/%Y à %H:%M")

async def _send_reabo_message(telegram_id: int, prenom: str, plan: str, new_expires) -> bool:
    """
    Envoie deux messages consécutifs à l'utilisateur :
      1. Confirmation de réabonnement (texte simple)
      2. Rappel d'accès au canal Master Class (MarkdownV2 + bouton)

    Retourne True si le message 1 est bien parti (l'essentiel).
    Le message 2 est un bonus : son échec est loggé mais n'invalide pas
    le succès global.
    """
    # ── Message 1 : confirmation de réabonnement ─────────────────────────────
    text = (
        f"🎉 Réabonnement confirmé !\n\n"
        f"Bonjour {prenom},\n\n"
        f"Nous avons bien enregistré votre réabonnement à notre offre {plan}.\n\n"
        f"Votre accès est prolongé jusqu'au {_format_datetime_fr(new_expires)}.\n\n"
        f"Merci pour la confiance renouvelée envers nos services.\n\n"
        f"📋 Pour maintenir votre rentabilité au meilleur niveau, suivez "
        f"attentivement nos instructions et signaux quotidiens.\n\n"
        f"À très vite,\n"
        f"L'équipe FDK"
    )
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except Exception as e:
        logger.warning(f"[reabo_message] échec envoi message 1 à {telegram_id} : {e}")
        return False

    # ── Message 2 : rappel accès canal Master Class ──────────────────────────
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                "*Votre accès au canal Master Class est prêt\\.*\n"
                "\n"
                "Cliquez sur le bouton ci\\-dessous pour rejoindre le canal\\.\n"
                "\n"
                "\n"
                "*Instructions importantes*\n"
                "\n"
                "Épinglez le canal en haut de votre liste dès votre arrivée\\.\n"
                "\n"
                "Activez les notifications pour être informé de chaque publication\\.\n"
                "\n"
                "Consultez régulièrement le canal afin de rester à jour\\.\n"
                "\n"
                "\n"
                "*Petit conseil*\n"
                "\n"
                "Prenez le temps de lire les premières publications épinglées\\. "
                "Elles contiennent les informations essentielles pour bien démarrer\\."
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Accéder au canal Master Class",
                                      url="https://t.me/+-1hIhAeAvc1hMWM0")]
            ]),
        )
    except Exception as e:
        logger.warning(f"[reabo_message] échec envoi message 2 (canal) à {telegram_id} : {e}")
        # On ne renvoie PAS False : le message principal est bien parti.

    return True

async def _send_reabo_messages(telegram_id: int, prenom: str, plan: str, new_expires) -> bool:
    """Envoie le message de confirmation de réabonnement à l'utilisateur."""
    text = (
        f"🎉 Réabonnement confirmé !\n\n"
        f"Bonjour {prenom},\n\n"
        f"Nous avons bien enregistré votre réabonnement à notre offre {plan}.\n\n"
        f"Votre accès est prolongé jusqu'au {_format_datetime_fr(new_expires)}.\n\n"
        f"Merci pour la confiance renouvelée envers nos services.\n\n"
        f"📋 Pour maintenir votre rentabilité au meilleur niveau, suivez "
        f"attentivement nos instructions et signaux quotidiens.\n\n"
        f"À très vite,\n"
        f"L'équipe FDK"
    )
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
        return True
    except Exception as e:
        logger.warning(f"[reabo_message] échec envoi à {telegram_id} : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT PRINCIPAL — /subscription-info POST
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/subscription-info")
async def create_subscription(payload: SubscriptionPayload):
    try:
        # ── 1. Idempotence : webhook rejoué avec exactement le même paid_at ?
        dup = await _check_duplicate_payment(payload.email, payload.paid_at)
        if dup:
            print(f"[subscription] Déjà sauvegardé — email={payload.email} | id={dup['id']}")
            return {"id": dup["id"], "message": "déjà sauvegardé"}

        # ── 2. Recherche d'un abonnement existant pour cet email
        existing = await _find_latest_subscription(payload.email)

        # ══ CAS A : PREMIER ABONNEMENT ═══════════════════════════════════════
        # Aucun historique pour cet email → INSERT, notif admin, PAS de
        # message user (règle : message user réservé aux réabonnements).
        if not existing:
            async with get_db() as cur:
                await cur.execute(
                    """
                    INSERT INTO subscription_info
                        (plan, duration_days, started_at, expires_at, status, note,
                         order_id, name, email, phone, country_code, billing_cycle,
                         amount_usd, currency, amount_local, aggregator, paid_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        payload.plan, payload.duration_days,
                        payload.started_at, payload.expires_at, payload.status, payload.note,
                        payload.order_id, payload.name, payload.email, payload.phone,
                        payload.country_code, payload.billing_cycle, payload.amount_usd,
                        payload.currency, payload.amount_local, payload.aggregator, payload.paid_at,
                    ),
                )
                await cur.execute("SELECT LAST_INSERT_ID() AS id")
                new_id = (await cur.fetchone())["id"]

            print(f"[subscription] Nouveau paiement — email={payload.email} | id={new_id}")
            await _notify_all_admins(
                bot,
                f"💳 Nouveau paiement\n"
                f"Plan : {payload.plan}\n"
                f"Email : {payload.email}\n"
                f"Montant : {payload.amount_usd} {payload.currency or 'USD'}\n"
                f"Durée : {payload.duration_days} jours\n"
                f"Expire le : {_format_datetime_fr(payload.expires_at)}\n"
                f"ID : {new_id}"
            )
            return {"id": new_id, "message": "subscription enregistrée"}

        # ══ CAS B : RÉABONNEMENT ═════════════════════════════════════════════
        # Cumul par extension : on UPDATE la ligne existante avec la nouvelle
        # expires_at calculée. Le plan du dernier paiement gagne.
        old_expires = existing["expires_at"]
        new_expires = _compute_new_expires_at(old_expires, payload.duration_days)

        async with get_db() as cur:
            await cur.execute(
                """
                UPDATE subscription_info SET
                    plan          = %s,
                    duration_days = %s,
                    started_at    = %s,
                    expires_at    = %s,
                    status        = %s,
                    note          = %s,
                    order_id      = %s,
                    name          = COALESCE(%s, name),
                    phone         = COALESCE(%s, phone),
                    country_code  = COALESCE(%s, country_code),
                    billing_cycle = %s,
                    amount_usd    = %s,
                    currency      = %s,
                    amount_local  = %s,
                    aggregator    = %s,
                    paid_at       = %s,
                    updated_at    = NOW()
                WHERE id = %s
                """,
                (
                    payload.plan, payload.duration_days,
                    payload.started_at, new_expires, payload.status, payload.note,
                    payload.order_id, payload.name, payload.phone, payload.country_code,
                    payload.billing_cycle, payload.amount_usd, payload.currency,
                    payload.amount_local, payload.aggregator, payload.paid_at,
                    existing["id"],
                ),
            )

        sub_id = existing["id"]
        print(
            f"[subscription] Réabonnement — email={payload.email} | id={sub_id} "
            f"| {_format_datetime_fr(old_expires)} → {_format_datetime_fr(new_expires)}"
        )

        # ── 3. Notification admins — RÉABONNEMENT
        await _notify_all_admins(
            bot,
            f"🔄 Réabonnement — {payload.plan}\n"
            f"Email : {payload.email}\n"
            f"Ancien expires_at : {_format_datetime_fr(old_expires)}\n"
            f"Nouveau expires_at : {_format_datetime_fr(new_expires)}\n"
            f"Jours ajoutés : +{payload.duration_days}\n"
            f"Montant : {payload.amount_usd} {payload.currency or 'USD'}\n"
            f"ID : {sub_id}"
        )

        # ── 4. Message de confirmation au user (UNIQUEMENT réabonnement)
        user_row = await _find_user_by_email(payload.email)
        if user_row and user_row.get("telegram_id"):
            prenom = _extract_prenom(payload.name, user_row.get("name"))
            sent_ok = await _send_reabo_message(
                telegram_id=int(user_row["telegram_id"]),
                prenom=prenom,
                plan=payload.plan,
                new_expires=new_expires,
            )
            if not sent_ok:
                await _notify_all_admins(
                    bot,
                    f"⚠️ Message de réabonnement NON reçu par le user\n"
                    f"Email : {payload.email}\n"
                    f"telegram_id : {user_row['telegram_id']}\n"
                    f"(user a probablement bloqué le bot)"
                )
        else:
            await _notify_all_admins(
                bot,
                f"⚠️ Réabonnement enregistré mais user Telegram introuvable\n"
                f"Email : {payload.email}\n"
                f"(pas de telegram_id associé dans users pour cet email)"
            )

        return {
            "id":             sub_id,
            "message":        "réabonnement enregistré",
            "old_expires_at": str(old_expires),
            "new_expires_at": str(new_expires),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[subscription] erreur inattendue")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT INCHANGÉ — /subscription-info GET
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/subscription-info")
async def get_subscriptions(email: Optional[str] = None):
    try:
        async with get_db() as cur:
            if email:
                await cur.execute(
                    """
                    SELECT * FROM subscription_info
                    WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
                    """,
                    (email,),
                )
            else:
                await cur.execute(
                    "SELECT * FROM subscription_info ORDER BY created_at DESC"
                )
            rows = await cur.fetchall()
            return rows
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT INCHANGÉ (juste la notif admin étendue) — /formation-validation
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/formation-validation")
async def create_formation_validation(payload: FormationValidationRequest):
    email = payload.email
    try:
        async with get_db() as cur:
            await cur.execute(
                "SELECT id FROM formation_validation WHERE email = %s",
                (email,),
            )
            existing = await cur.fetchone()

            if existing:
                print(f"[formation-validation] Déjà sauvegardé — email={email} | id={existing['id']}")
                return {"id": existing["id"], "message": "déjà sauvegardé"}

            await cur.execute(
                "INSERT INTO formation_validation (email, is_active) VALUES (%s, 1)",
                (email,),
            )
            await cur.execute("SELECT LAST_INSERT_ID() AS id")
            new_id = (await cur.fetchone())["id"]

        print(f"[formation-validation] Nouveau enregistrement — email={email} | id={new_id}")
        await _notify_all_admins(
            bot,
            f"📚 Nouvelle formation validation\nEmail : {email}\nID : {new_id}"
        )
        return {"id": new_id, "message": "formation validation enregistrée"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))