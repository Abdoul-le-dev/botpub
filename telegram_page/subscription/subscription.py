from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3

router = APIRouter()
DB_PATH = 'preinscriptions.db'

class SubscriptionPayload(BaseModel):
    user_id:       int
    plan:          str
    duration_days: int
    started_at:    str
    expires_at:    str
    status:        Optional[str] = 'active'
    note:          Optional[str] = None
    order_id:      Optional[str] = None
    name:          Optional[str] = None
    email:         Optional[str] = None
    phone:         Optional[str] = None
    country_code:  Optional[str] = None
    billing_cycle: Optional[str] = None
    amount_usd:    Optional[float] = None
    currency:      Optional[str] = None
    amount_local:  Optional[float] = None
    aggregator:    Optional[str] = None
    paid_at:       Optional[str] = None


@router.post('/subscription-info')
def create_subscription(payload: SubscriptionPayload):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute('''
            INSERT INTO subscription_info
                (user_id, plan, duration_days, started_at, expires_at, status, note,
                 order_id, name, email, phone, country_code, billing_cycle,
                 amount_usd, currency, amount_local, aggregator, paid_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            payload.user_id, payload.plan, payload.duration_days,
            payload.started_at, payload.expires_at, payload.status, payload.note,
            payload.order_id, payload.name, payload.email, payload.phone,
            payload.country_code, payload.billing_cycle, payload.amount_usd,
            payload.currency, payload.amount_local, payload.aggregator, payload.paid_at
        ))
        conn.commit()
        return {'id': cur.lastrowid, 'message': 'subscription enregistrée'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get('/subscription-info/{user_id}')
def get_subscription(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            'SELECT * FROM subscription_info WHERE user_id = ?', (user_id,)
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail='Aucune subscription trouvée')
        return [dict(row) for row in rows]
    finally:
        conn.close()