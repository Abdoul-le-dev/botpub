"""
telegram_page/automatisation/routes_growth.py
Routes FastAPI pour le Growth Hub.
Préfixe : /growth
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
import sqlite3
import json
from datetime import datetime, timedelta

router = APIRouter(prefix="/growth", tags=["growth"])
DB = "preinscriptions.db"


def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def row_to_dict(row):
    return dict(row) if row else None


# ════════════════════════════════════════════════════════════════
# LIENS D'INVITATION
# ════════════════════════════════════════════════════════════════

class LinkCreate(BaseModel):
    name: str
    start_param: str
    auto_category: Optional[str] = None
    promo_code: Optional[str] = None
    quota_max: Optional[int] = None
    expires_at: Optional[str] = None
    source: Optional[str] = "direct"
    form_id: Optional[int] = None


class LinkUpdate(BaseModel):
    name: Optional[str] = None
    start_param: Optional[str] = None
    auto_category: Optional[str] = None
    promo_code: Optional[str] = None
    quota_max: Optional[int] = None
    expires_at: Optional[str] = None
    source: Optional[str] = None
    is_active: Optional[int] = None
    form_id: Optional[int] = None


class LinkClickEvent(BaseModel):
    user_id: Optional[int] = None
    event: str  # 'click' | 'register' | 'subscribe'


@router.get("/links")
def get_links():
    conn = get_conn()
    try:
        rows = conn.execute(
        """
        SELECT l.*,
        COUNT(CASE WHEN s.event='click'     THEN 1 END) as clicks,
        COUNT(CASE WHEN s.event='register'  THEN 1 END) as registrations,
        COUNT(CASE WHEN s.event='subscribe' THEN 1 END) as subscribers,
        f.name as form_name
        FROM invite_links l
        LEFT JOIN invite_link_stats s ON s.link_id=l.id
        LEFT JOIN forms f ON f.id=l.form_id
        GROUP BY l.id
        ORDER BY l.created_at DESC
        """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/links", status_code=201)
def create_link(body: LinkCreate):
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM invite_links WHERE start_param=?", (body.start_param,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="start_param déjà utilisé")
        cur = conn.execute(
            """
            INSERT INTO invite_links (name, start_param, auto_category, promo_code,
            quota_max, expires_at, source, form_id)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (body.name, body.start_param, body.auto_category, body.promo_code,
            body.quota_max, body.expires_at, body.source, body.form_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM invite_links WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/links/{link_id}")
def get_link(link_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM invite_links WHERE id=?", (link_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        stats = conn.execute(
            "SELECT * FROM invite_link_stats WHERE link_id=? ORDER BY occurred_at DESC LIMIT 50",
            (link_id,),
        ).fetchall()
        result = dict(row)
        result["stats"] = [dict(s) for s in stats]
        return result
    finally:
        conn.close()


@router.patch("/links/{link_id}")
def update_link(link_id: int, body: LinkUpdate):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM invite_links WHERE id=?", (link_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        data = body.dict(exclude_none=True)
        if not data:
            return dict(row)
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [link_id]
        conn.execute(f"UPDATE invite_links SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute("SELECT * FROM invite_links WHERE id=?", (link_id,)).fetchone())
    finally:
        conn.close()


@router.delete("/links/{link_id}")
def delete_link(link_id: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE invite_links SET is_active=0 WHERE id=?", (link_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/links/{link_id}/click")
async def record_link_event(link_id: int, body: LinkClickEvent):
    conn = get_conn()
    try:
        link = conn.execute(
            "SELECT * FROM invite_links WHERE id=?", (link_id,)
        ).fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        conn.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (?,?,?)",
            (link_id, body.user_id, body.event),
        )
        quota_reached = False
        if body.event == "register":
            conn.execute(
                "UPDATE invite_links SET quota_used=quota_used+1 WHERE id=?", (link_id,)
            )
            updated = conn.execute(
                "SELECT quota_max, quota_used FROM invite_links WHERE id=?", (link_id,)
            ).fetchone()
            if updated["quota_max"] and updated["quota_used"] >= updated["quota_max"]:
                quota_reached = True
            if link["auto_category"] and body.user_id:
                try:
                    from telegram_page.categorie import add_members_to_category
                    bot = None
                    try:
                        from telegram_page.chat import _bot as b
                        bot = b
                    except Exception:
                        pass
                    await add_members_to_category(link["auto_category"], [body.user_id])
                except Exception as e:
                    print(f"[link click] add_category error: {e}")
        conn.commit()
        return {"ok": True, "quota_reached": quota_reached}
    finally:
        conn.close()


@router.get("/links/{link_id}/qr")
def get_link_qr(link_id: int):
    try:
        import qrcode
        import base64
        from io import BytesIO
    except ImportError:
        raise HTTPException(status_code=501, detail="pip install qrcode[pil]")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT start_param FROM invite_links WHERE id=?", (link_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        url = f"https://t.me/TradingBot?start={row['start_param']}"
        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"qr_base64": b64, "url": url}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# IA TRIGGER
# ════════════════════════════════════════════════════════════════

class IATriggerUpdate(BaseModel):
    trigger_type: str
    messages_count: Optional[int] = None


@router.get("/ia-trigger")
def get_ia_trigger():
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM ia_trigger_config WHERE id=1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


@router.patch("/ia-trigger")
def update_ia_trigger(body: IATriggerUpdate):
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE ia_trigger_config
            SET trigger_type=?, messages_count=?, updated_at=datetime('now')
            WHERE id=1
            """,
            (body.trigger_type, body.messages_count),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM ia_trigger_config WHERE id=1").fetchone())
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# AUTOMATIONS / JOBS
# ════════════════════════════════════════════════════════════════

class JobCreate(BaseModel):
    name: str
    trig_type: str
    freq: Optional[str] = None
    run_time: Optional[str] = None
    cond_field: Optional[str] = None
    cond_value: Optional[str] = None
    cond_extra: Optional[str] = None
    event_type: Optional[str] = None
    target: str = "all"
    action_type: str
    action_content: Optional[str] = None


class JobUpdate(BaseModel):
    is_active: Optional[int] = None
    name: Optional[str] = None
    freq: Optional[str] = None
    run_time: Optional[str] = None
    target: Optional[str] = None
    action_type: Optional[str] = None
    action_content: Optional[str] = None
    cond_field: Optional[str] = None
    cond_value: Optional[str] = None
    cond_extra: Optional[str] = None


def _compute_next_run(freq, run_time):
    from telegram_page.automatisation.bot_growth import compute_next_run
    return compute_next_run(freq, run_time)


@router.get("/jobs")
def get_jobs():
    conn = get_conn()
    try:
        jobs = [dict(r) for r in conn.execute(
            "SELECT * FROM automation_jobs ORDER BY created_at DESC"
        ).fetchall()]
        for j in jobs:
            j["log_count"] = conn.execute(
                "SELECT COUNT(*) FROM automation_logs WHERE job_id=?", (j["id"],)
            ).fetchone()[0]
        return jobs
    finally:
        conn.close()


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate):
    next_run = _compute_next_run(body.freq, body.run_time)
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO automation_jobs
              (name, trig_type, freq, run_time, cond_field, cond_value, cond_extra,
               event_type, target, action_type, action_content, next_run_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (body.name, body.trig_type, body.freq, body.run_time,
             body.cond_field, body.cond_value, body.cond_extra,
             body.event_type, body.target, body.action_type,
             body.action_content, next_run),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM automation_jobs WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job introuvable")
        logs = conn.execute(
            "SELECT * FROM automation_logs WHERE job_id=? ORDER BY started_at DESC LIMIT 10",
            (job_id,),
        ).fetchall()
        result = dict(row)
        result["logs"] = [dict(l) for l in logs]
        return result
    finally:
        conn.close()


@router.patch("/jobs/{job_id}")
def update_job(job_id: int, body: JobUpdate):
    conn = get_conn()
    try:
        if not conn.execute(
            "SELECT id FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone():
            raise HTTPException(status_code=404, detail="Job introuvable")
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [job_id]
        conn.execute(f"UPDATE automation_jobs SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM automation_logs WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM automation_jobs WHERE id=?", (job_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/jobs/{job_id}/run")
async def run_job_now(job_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job introuvable")
    finally:
        conn.close()

    from telegram_page.automatisation.bot_growth import execute_automation_job, compute_next_run
    try:
        from telegram_page.chat import _bot as bot
    except Exception:
        bot = None

    job = dict(row)
    log_conn = get_conn()
    log_id = log_conn.execute(
        "INSERT INTO automation_logs (job_id, started_at) VALUES (?,?)",
        (job_id, datetime.now().isoformat()),
    ).lastrowid
    log_conn.commit()
    log_conn.close()

    try:
        r = await execute_automation_job(bot, job)
        st = "success" if r["errors"] == 0 else "partial"
    except Exception as e:
        r = {"total": 0, "sent": 0, "errors": 1}
        st = "failed"

    up_conn = get_conn()
    up_conn.execute(
        """
        UPDATE automation_logs
        SET finished_at=?, total=?, sent=?, errors=?, status=?
        WHERE id=?
        """,
        (datetime.now().isoformat(), r["total"], r["sent"], r["errors"], st, log_id),
    )
    next_run = compute_next_run(job.get("freq"), job.get("run_time"))
    up_conn.execute(
        """
        UPDATE automation_jobs
        SET exec_count=exec_count+1, last_run_at=?, next_run_at=?, err_count=err_count+?
        WHERE id=?
        """,
        (datetime.now().isoformat(), next_run, r["errors"], job_id),
    )
    up_conn.commit()
    up_conn.close()
    return r


@router.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: int):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM automation_logs WHERE job_id=? ORDER BY started_at DESC LIMIT 20",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# PLANS D'ABONNEMENT
# ════════════════════════════════════════════════════════════════

class PlanCreate(BaseModel):
    name: str
    price_usd: float
    duration_days: Optional[int] = 30
    trial_days: Optional[int] = 0
    categories: Optional[List[str]] = []
    description: Optional[str] = None


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    price_usd: Optional[float] = None
    duration_days: Optional[int] = None
    trial_days: Optional[int] = None
    categories: Optional[List[str]] = None
    description: Optional[str] = None
    is_active: Optional[int] = None


@router.get("/plans")
def get_plans():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT p.*,
              COUNT(CASE WHEN gs.status='active' THEN 1 END) as active_count,
              COUNT(CASE WHEN gs.status='trial'  THEN 1 END) as trial_count
            FROM subscription_plans p
            LEFT JOIN growth_subscriptions gs ON gs.plan_id=p.id
            WHERE p.is_active=1
            GROUP BY p.id
            ORDER BY p.created_at
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/plans", status_code=201)
def create_plan(body: PlanCreate):
    conn = get_conn()
    try:
        cats = json.dumps(body.categories or [])
        cur = conn.execute(
            """
            INSERT INTO subscription_plans
              (name, price_usd, duration_days, trial_days, categories, description)
            VALUES (?,?,?,?,?,?)
            """,
            (body.name, body.price_usd, body.duration_days,
             body.trial_days, cats, body.description),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM subscription_plans WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanUpdate):
    conn = get_conn()
    try:
        if not conn.execute(
            "SELECT id FROM subscription_plans WHERE id=?", (plan_id,)
        ).fetchone():
            raise HTTPException(status_code=404, detail="Plan introuvable")
        data = body.dict(exclude_none=True)
        if "categories" in data:
            data["categories"] = json.dumps(data["categories"])
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [plan_id]
        conn.execute(f"UPDATE subscription_plans SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM subscription_plans WHERE id=?", (plan_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE subscription_plans SET is_active=0 WHERE id=?", (plan_id,)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# ABONNEMENTS GROWTH
# ════════════════════════════════════════════════════════════════

class SubCreate(BaseModel):
    user_id: Optional[int] = None
    member_name: str
    plan_id: int
    status: str = "active"
    promo_code: Optional[str] = None


class SubUpdate(BaseModel):
    status: Optional[str] = None
    expires_at: Optional[str] = None
    member_name: Optional[str] = None


@router.get("/subscriptions")
def get_subscriptions(
    plan_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    conn = get_conn()
    try:
        where = ["1=1"]
        params = []
        if plan_id:
            where.append("gs.plan_id=?")
            params.append(plan_id)
        if status:
            where.append("gs.status=?")
            params.append(status)
        if search:
            where.append("gs.member_name LIKE ?")
            params.append(f"%{search}%")
        params += [limit, offset]
        rows = conn.execute(
            f"""
            SELECT gs.*, sp.name as plan_name, sp.price_usd
            FROM growth_subscriptions gs
            JOIN subscription_plans sp ON sp.id=gs.plan_id
            WHERE {' AND '.join(where)}
            ORDER BY gs.started_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/subscriptions/stats")
def get_sub_stats():
    conn = get_conn()
    try:
        mrr = conn.execute(
            "SELECT COALESCE(SUM(price_paid),0) FROM growth_subscriptions WHERE status IN ('active','trial')"
        ).fetchone()[0]
        actifs = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status='active'"
        ).fetchone()[0]
        essais = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status='trial'"
        ).fetchone()[0]
        expired_n = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status='expired'"
        ).fetchone()[0]
        total_n = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions"
        ).fetchone()[0]
        churn = round(expired_n / max(total_n, 1) * 100, 1)
        expiring = conn.execute(
            """
            SELECT COUNT(*) FROM growth_subscriptions WHERE status='active'
            AND expires_at <= datetime('now','+7 days')
            """
        ).fetchone()[0]
        return {
            "mrr": mrr,
            "actifs": actifs,
            "essais": essais,
            "churn_rate": churn,
            "expiring_soon": expiring,
        }
    finally:
        conn.close()


@router.post("/subscriptions", status_code=201)
async def create_subscription(body: SubCreate):
    conn = get_conn()
    try:
        plan = conn.execute(
            "SELECT * FROM subscription_plans WHERE id=?", (body.plan_id,)
        ).fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan introuvable")
        plan = dict(plan)
        price_paid = plan["price_usd"]

        if body.promo_code:
            promo = conn.execute(
                """
                SELECT * FROM promo_codes
                WHERE code=? AND is_active=1
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                  AND (quota_max IS NULL OR current_uses < quota_max)
                """,
                (body.promo_code,),
            ).fetchone()
            if promo:
                promo = dict(promo)
                if promo["discount_type"] == "percent":
                    price_paid = price_paid * (1 - promo["discount_value"] / 100)
                else:
                    price_paid = max(0, price_paid - promo["discount_value"])
                conn.execute(
                    "UPDATE promo_codes SET current_uses=current_uses+1 WHERE id=?",
                    (promo["id"],),
                )

        expires_at = (
            datetime.now() + timedelta(days=plan["duration_days"])
        ).isoformat()
        if body.status == "trial" and plan["trial_days"] > 0:
            expires_at = (
                datetime.now() + timedelta(days=plan["trial_days"])
            ).isoformat()

        cur = conn.execute(
            """
            INSERT INTO growth_subscriptions
              (user_id, member_name, plan_id, status, price_paid, promo_code, expires_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (body.user_id, body.member_name, body.plan_id, body.status,
             round(price_paid, 2), body.promo_code, expires_at),
        )
        conn.commit()

        # Ajouter aux catégories du plan si user_id fourni
        if plan.get("categories") and body.user_id:
            cats = json.loads(plan["categories"] or "[]")
            for cat in cats:
                try:
                    from telegram_page.categorie import add_members_to_category
                    await add_members_to_category(cat, [body.user_id])
                except Exception as e:
                    print(f"[sub create] add_category error: {e}")

        return dict(conn.execute(
            "SELECT * FROM growth_subscriptions WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.patch("/subscriptions/{sub_id}")
def update_subscription(sub_id: int, body: SubUpdate):
    conn = get_conn()
    try:
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [sub_id]
        conn.execute(f"UPDATE growth_subscriptions SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM growth_subscriptions WHERE id=?", (sub_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM growth_subscriptions WHERE id=?", (sub_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# PROMOTIONS
# ════════════════════════════════════════════════════════════════

class PromoCreate(BaseModel):
    code: str
    discount_type: str
    discount_value: float
    plan_id: Optional[int] = None
    quota_max: Optional[int] = None
    first_time_only: Optional[int] = 1
    expires_at: Optional[str] = None


class PromoUpdate(BaseModel):
    is_active: Optional[int] = None
    code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    plan_id: Optional[int] = None
    quota_max: Optional[int] = None
    first_time_only: Optional[int] = None
    expires_at: Optional[str] = None


class PromoValidate(BaseModel):
    code: str
    user_id: Optional[int] = None
    plan_id: Optional[int] = None


@router.get("/promos")
def get_promos():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT pc.*, sp.name as plan_name
            FROM promo_codes pc
            LEFT JOIN subscription_plans sp ON sp.id=pc.plan_id
            ORDER BY pc.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/promos", status_code=201)
def create_promo(body: PromoCreate):
    conn = get_conn()
    try:
        if conn.execute(
            "SELECT id FROM promo_codes WHERE code=?", (body.code.upper(),)
        ).fetchone():
            raise HTTPException(status_code=400, detail="Code déjà existant")
        cur = conn.execute(
            """
            INSERT INTO promo_codes
              (code, discount_type, discount_value, plan_id, quota_max,
               first_time_only, expires_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (body.code.upper(), body.discount_type, body.discount_value,
             body.plan_id, body.quota_max, body.first_time_only, body.expires_at),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM promo_codes WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.patch("/promos/{promo_id}")
def update_promo(promo_id: int, body: PromoUpdate):
    conn = get_conn()
    try:
        data = body.dict(exclude_none=True)
        if "code" in data:
            data["code"] = data["code"].upper()
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [promo_id]
        conn.execute(f"UPDATE promo_codes SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM promo_codes WHERE id=?", (promo_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/promos/{promo_id}")
def delete_promo(promo_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM promo_codes WHERE id=?", (promo_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/promos/validate")
def validate_promo(body: PromoValidate):
    conn = get_conn()
    try:
        promo = conn.execute(
            """
            SELECT pc.*, sp.name as plan_name
            FROM promo_codes pc
            LEFT JOIN subscription_plans sp ON sp.id=pc.plan_id
            WHERE pc.code=?
            """,
            (body.code.upper(),),
        ).fetchone()
        if not promo:
            return {"valid": False, "error": "Code invalide"}
        promo = dict(promo)
        if not promo["is_active"]:
            return {"valid": False, "error": "Code désactivé"}
        if promo["expires_at"] and promo["expires_at"] < datetime.now().isoformat():
            return {"valid": False, "error": "Code expiré"}
        if promo["quota_max"] and promo["current_uses"] >= promo["quota_max"]:
            return {"valid": False, "error": "Quota atteint"}
        if promo["first_time_only"] and body.user_id:
            existing = conn.execute(
                """
                SELECT id FROM growth_subscriptions
                WHERE user_id=? AND status NOT IN ('expired','cancelled') LIMIT 1
                """,
                (body.user_id,),
            ).fetchone()
            if existing:
                return {"valid": False, "error": "Réservé aux nouvelles souscriptions"}
        if body.plan_id and promo["plan_id"] and body.plan_id != promo["plan_id"]:
            return {"valid": False, "error": "Code non applicable à ce plan"}
        return {
            "valid": True,
            "discount_type": promo["discount_type"],
            "discount_value": promo["discount_value"],
            "plan_name": promo["plan_name"],
        }
    finally:
        conn.close()


@router.get("/promos/auto-config")
def get_auto_promo_config():
    conn = get_conn()
    try:
        return dict(conn.execute(
            "SELECT * FROM auto_promo_config WHERE id=1"
        ).fetchone())
    finally:
        conn.close()


class AutoPromoUpdate(BaseModel):
    anniversary_active: Optional[int] = None
    anniversary_pct: Optional[float] = None
    winback_active: Optional[int] = None
    winback_pct: Optional[float] = None
    upgrade_active: Optional[int] = None
    upgrade_pct: Optional[float] = None


@router.patch("/promos/auto-config")
def update_auto_promo_config(body: AutoPromoUpdate):
    conn = get_conn()
    try:
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        data["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values())
        conn.execute(f"UPDATE auto_promo_config SET {sets} WHERE id=1", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM auto_promo_config WHERE id=1"
        ).fetchone())
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# SEGMENTS & SCORING
# ════════════════════════════════════════════════════════════════

class SegmentCreate(BaseModel):
    name: str
    tag: str
    conditions: List[Any] = []
    auto_action: Optional[str] = None


class SegmentUpdate(BaseModel):
    name: Optional[str] = None
    tag: Optional[str] = None
    conditions: Optional[List[Any]] = None
    auto_action: Optional[str] = None


@router.get("/segments")
def get_segments():
    conn = get_conn()
    try:
        segs = [dict(r) for r in conn.execute(
            "SELECT * FROM segments ORDER BY created_at DESC"
        ).fetchall()]
        for seg in segs:
            seg["member_count"] = conn.execute(
                "SELECT COUNT(*) FROM segment_members WHERE segment_id=?", (seg["id"],)
            ).fetchone()[0]
        return segs
    finally:
        conn.close()


@router.post("/segments", status_code=201)
def create_segment(body: SegmentCreate):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO segments (name, tag, conditions, auto_action) VALUES (?,?,?,?)",
            (body.name, body.tag, json.dumps(body.conditions), body.auto_action),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM segments WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.patch("/segments/{seg_id}")
def update_segment(seg_id: int, body: SegmentUpdate):
    conn = get_conn()
    try:
        data = body.dict(exclude_none=True)
        if "conditions" in data:
            data["conditions"] = json.dumps(data["conditions"])
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [seg_id]
        conn.execute(f"UPDATE segments SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM segments WHERE id=?", (seg_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/segments/{seg_id}")
def delete_segment(seg_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM segment_members WHERE segment_id=?", (seg_id,))
        conn.execute("DELETE FROM segments WHERE id=?", (seg_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/segments/compute")
async def compute_segments():
    from telegram_page.automatisation.bot_growth import compute_all_segments
    await compute_all_segments()
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "segments_computed": n}


@router.get("/scoring")
def get_scoring():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT u.telegram_id, u.name, COALESCE(es.score, 0) as score
            FROM users u
            LEFT JOIN engagement_scores es ON es.user_id=u.telegram_id
            WHERE u.telegram_id IS NOT NULL
            ORDER BY score DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/scoring/{user_id}")
def get_user_score(user_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(score, 0) as score FROM engagement_scores WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return {"user_id": user_id, "score": row["score"] if row else 0}
    finally:
        conn.close()


class ScoringEvent(BaseModel):
    user_id: int
    event_type: str
    points: int


@router.post("/scoring/event")
def record_scoring_event(body: ScoringEvent):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO engagement_scores (user_id, score) VALUES (?,0)",
            (body.user_id,),
        )
        conn.execute(
            """
            UPDATE engagement_scores
            SET score=MAX(0, score+?), updated_at=datetime('now')
            WHERE user_id=?
            """,
            (body.points, body.user_id),
        )
        conn.commit()
        new_score = conn.execute(
            "SELECT score FROM engagement_scores WHERE user_id=?", (body.user_id,)
        ).fetchone()["score"]
        return {"user_id": body.user_id, "new_score": new_score}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# PIPELINE CRM
# ════════════════════════════════════════════════════════════════

class ProspectCreate(BaseModel):
    name: str
    source: Optional[str] = "direct"
    col: Optional[str] = "nouveau"
    link_id: Optional[int] = None
    user_id: Optional[int] = None
    score: Optional[int] = 0


class ProspectUpdate(BaseModel):
    col: Optional[str] = None
    score: Optional[int] = None
    name: Optional[str] = None
    source: Optional[str] = None


@router.get("/pipeline")
def get_pipeline():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT cp.*, il.name as link_name
            FROM crm_prospects cp
            LEFT JOIN invite_links il ON il.id=cp.link_id
            ORDER BY cp.created_at DESC
            """
        ).fetchall()
        grouped = {col: [] for col in ["nouveau", "engage", "offre", "abonne", "vip"]}
        for r in rows:
            d = dict(r)
            col = d.get("col", "nouveau")
            if col in grouped:
                grouped[col].append(d)
        return grouped
    finally:
        conn.close()


@router.post("/pipeline", status_code=201)
def create_prospect(body: ProspectCreate):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO crm_prospects (name, source, col, link_id, user_id, score) VALUES (?,?,?,?,?,?)",
            (body.name, body.source, body.col, body.link_id, body.user_id, body.score),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM crm_prospects WHERE id=?", (cur.lastrowid,)
        ).fetchone())
    finally:
        conn.close()


@router.patch("/pipeline/{prospect_id}")
def update_prospect(prospect_id: int, body: ProspectUpdate):
    conn = get_conn()
    try:
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [prospect_id]
        conn.execute(f"UPDATE crm_prospects SET {sets} WHERE id=?", vals)
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM crm_prospects WHERE id=?", (prospect_id,)
        ).fetchone())
    finally:
        conn.close()


@router.delete("/pipeline/{prospect_id}")
def delete_prospect(prospect_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM crm_prospects WHERE id=?", (prospect_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/pipeline/relances")
def get_relances():
    conn = get_conn()
    try:
        relances = []
        # 1. Prospects dans col='offre' depuis > 3 jours
        old_offers = conn.execute(
            """
            SELECT * FROM crm_prospects
            WHERE col='offre' AND created_at <= datetime('now','-3 days')
            """
        ).fetchall()
        for p in old_offers:
            relances.append({
                "type": "offre_sans_reponse",
                "name": p["name"],
                "msg": "Offre envoyée sans réponse depuis 3+ jours",
                "prospect_id": p["id"],
            })
        # 2. Abonnements expirant dans 7 jours
        expiring = conn.execute(
            """
            SELECT gs.*, u.name, u.telegram_id
            FROM growth_subscriptions gs
            LEFT JOIN users u ON u.telegram_id=gs.user_id
            WHERE gs.status='active'
              AND gs.expires_at <= datetime('now','+7 days')
              AND gs.expires_at > datetime('now')
            """
        ).fetchall()
        for s in expiring:
            relances.append({
                "type": "expiration",
                "name": s["name"] or s["member_name"],
                "msg": f"Abonnement expire bientôt ({s['expires_at'][:10] if s['expires_at'] else '?'})",
                "user_id": s["user_id"],
            })
        # 3. Membres abonnés inactifs > 14 jours
        inactive = conn.execute(
            """
            SELECT gs.user_id, u.name
            FROM growth_subscriptions gs
            LEFT JOIN users u ON u.telegram_id=gs.user_id
            LEFT JOIN messages m ON m.user_id=gs.user_id
            WHERE gs.status='active'
            GROUP BY gs.user_id
            HAVING MAX(m.created_at) < datetime('now','-14 days')
               OR MAX(m.created_at) IS NULL
            """
        ).fetchall()
        for m in inactive:
            relances.append({
                "type": "inactivite",
                "name": m["name"] or "Membre inconnu",
                "msg": "Abonné actif sans activité depuis 14+ jours",
                "user_id": m["user_id"],
            })
        return relances
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# ANALYTICS
# ════════════════════════════════════════════════════════════════

@router.get("/analytics")
def get_analytics():
    conn = get_conn()
    try:
        mrr = conn.execute(
            "SELECT COALESCE(SUM(price_paid),0) FROM growth_subscriptions WHERE status='active'"
        ).fetchone()[0]
        nouveaux_7j = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        total_gs = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions"
        ).fetchone()[0]
        ltv_moyen = mrr * 3 / max(total_gs, 1)
        total_reg = conn.execute(
            "SELECT COUNT(*) FROM invite_link_stats WHERE event='register'"
        ).fetchone()[0]
        total_pay = conn.execute(
            "SELECT COUNT(*) FROM invite_link_stats WHERE event='subscribe'"
        ).fetchone()[0]
        conv_rate = round(total_pay / max(total_reg, 1) * 100, 1)
        return {
            "mrr": mrr,
            "nouveaux_7j": nouveaux_7j,
            "ltv_moyen": round(ltv_moyen, 2),
            "conv_rate": conv_rate,
        }
    finally:
        conn.close()


@router.get("/analytics/sources")
def get_analytics_sources():
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.name, l.source,
              COUNT(CASE WHEN s.event='click'     THEN 1 END) as clicks,
              COUNT(CASE WHEN s.event='register'  THEN 1 END) as registrations,
              COUNT(CASE WHEN s.event='subscribe' THEN 1 END) as subscribers
            FROM invite_links l
            LEFT JOIN invite_link_stats s ON s.link_id=l.id
            GROUP BY l.id
            ORDER BY registrations DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/analytics/funnel")
def get_analytics_funnel():
    conn = get_conn()
    try:
        clicks = conn.execute(
            "SELECT COUNT(*) FROM invite_link_stats WHERE event='click'"
        ).fetchone()[0]
        registered = conn.execute(
            "SELECT COUNT(*) FROM invite_link_stats WHERE event='register'"
        ).fetchone()[0]
        forms_done = conn.execute(
            "SELECT COUNT(*) FROM form_sessions WHERE status='completed'"
        ).fetchone()[0]
        paying = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status IN ('active','trial')"
        ).fetchone()[0]
        return {
            "clicks": clicks,
            "registered": registered,
            "forms_done": forms_done,
            "paying": paying,
        }
    finally:
        conn.close()


class ReportRequest(BaseModel):
    period: Optional[str] = "week"


@router.post("/analytics/report/send")
async def send_report(body: ReportRequest):
    from telegram_page.automatisation.bot_growth import send_admin_report
    try:
        from telegram_page.chat import _bot as bot
    except Exception:
        bot = None
    await send_admin_report(bot, body.period or "week")
    return {"sent": True}