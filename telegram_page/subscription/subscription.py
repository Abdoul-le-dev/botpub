# subscription.py — v4 MySQL

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from telegram_page.broadcast_engine import _notify_admin
import os
from telegram import Bot
from dotenv import load_dotenv
load_dotenv()
bot = Bot(token=os.getenv("tokens"))

from db import get_db

ADMIN_ID = 1075516687
router = APIRouter()


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

@router.post("/subscription-info")
async def create_subscription(payload: SubscriptionPayload):
    try:
        async with get_db() as cur:                          # 1. async with + cur (pas conn)
            # Vérifier si déjà enregistré (même email + paid_at)
            await cur.execute("""
                SELECT id FROM subscription_info
                WHERE email = %s AND paid_at = %s
            """, (payload.email, payload.paid_at))           # 2. %s au lieu de ?
            existing = await cur.fetchone()

            if existing:
                print(f"[subscription] Déjà sauvegardé — email={payload.email} | id={existing['id']}")
                return {"id": existing["id"], "message": "déjà sauvegardé"}

            await cur.execute("""
                INSERT INTO subscription_info
                    (plan, duration_days, started_at, expires_at, status, note,
                     order_id, name, email, phone, country_code, billing_cycle,
                     amount_usd, currency, amount_local, aggregator, paid_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                payload.plan, payload.duration_days,
                payload.started_at, payload.expires_at, payload.status, payload.note,
                payload.order_id, payload.name, payload.email, payload.phone,
                payload.country_code, payload.billing_cycle, payload.amount_usd,
                payload.currency, payload.amount_local, payload.aggregator, payload.paid_at,
            ))

            # LAST_INSERT_ID() — avec aiomysql
            await cur.execute("SELECT LAST_INSERT_ID() AS id")
            new_id = (await cur.fetchone())["id"]

        print(f"[subscription] Nouveau paiement — email={payload.email} | id={new_id}")
        await _notify_admin(bot, ADMIN_ID, f"[subscription] Nouveau paiement — email={payload.email} | id={new_id}")
        return {"id": new_id, "message": "subscription enregistrée"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscription-info")
async def get_subscriptions(email: Optional[str] = None):
    try:
        async with get_db() as cur:
            if email:
                await cur.execute("""
                    SELECT * FROM subscription_info
                    WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
                """, (email,))
            else:
                await cur.execute("""
                    SELECT * FROM subscription_info ORDER BY created_at DESC
                """)
            rows = await cur.fetchall()
            return rows  # déjà une liste de dicts grâce à DictCursor
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))