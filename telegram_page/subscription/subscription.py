from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3

router = APIRouter()
DB_PATH = 'preinscriptions.db'

class SubscriptionPayload(BaseModel):
    plan:          str
    duration_days: int
    started_at:    str
    expires_at:    str
    status:        Optional[str]   = 'pending'
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


@router.post('/subscription-info')
def create_subscription(payload: SubscriptionPayload):
    conn = sqlite3.connect(DB_PATH)
    try:
        # Vérifier si un enregistrement existe déjà avec le même email ET paid_at
        existing = conn.execute('''
            SELECT id FROM subscription_info
            WHERE email = ? AND paid_at = ?
        ''', (payload.email, payload.paid_at)).fetchone()

        if existing:
            print(f"[subscription-info] Déjà sauvegardé — email={payload.email} | paid_at={payload.paid_at} | id={existing[0]}")
            return {'id': existing[0], 'message': 'déjà sauvegardé'}

        # Nouveau paiement → insérer
        cur = conn.execute('''
            INSERT INTO subscription_info
                (plan, duration_days, started_at, expires_at, status, note,
                 order_id, name, email, phone, country_code, billing_cycle,
                 amount_usd, currency, amount_local, aggregator, paid_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            payload.plan, payload.duration_days,
            payload.started_at, payload.expires_at, payload.status, payload.note,
            payload.order_id, payload.name, payload.email, payload.phone,
            payload.country_code, payload.billing_cycle, payload.amount_usd,
            payload.currency, payload.amount_local, payload.aggregator, payload.paid_at
        ))
        conn.commit()
        print(f"[subscription-info] Nouveau paiement enregistré — email={payload.email} | id={cur.lastrowid}")
        return {'id': cur.lastrowid, 'message': 'subscription enregistrée'}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get('/subscription-info')
def get_subscriptions(email: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if email:
            rows = conn.execute(
                'SELECT * FROM subscription_info WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))',
                (email,)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM subscription_info ORDER BY created_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()