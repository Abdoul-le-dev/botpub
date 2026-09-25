"""
telegram_page/routes_event_payments.py — webhook de paiement événementiel.

Reçoit les notifications de paiement (FedaPay/PawaPay/Izichange) pour un
achat d'événement, et les enregistre via event_payments.record_event_payment().

Ce fichier tourne dans le process API (voir api.py) — il ne touche jamais
Telegram directement, seulement la base de données. C'est le module
event_payments.py, côté process bot, qui lit ensuite ces paiements quand
l'utilisateur tape /validation_de_mon_paiement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from event_payments import SubscriptionPayload_, record_event_payment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["event-payments"])


@router.post("/payments/webhook")
async def api_event_payment_webhook(payload: SubscriptionPayload_):
    try:
        result = await record_event_payment(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("[routes_event_payments] échec enregistrement webhook")
        raise HTTPException(500, f"Échec enregistrement paiement: {e}")
    return result