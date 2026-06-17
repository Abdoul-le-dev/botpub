"""
telegram_page/automatisation/routes_growth.py — v4 MySQL (aiomysql)
Routes FastAPI pour le Growth Hub.
Préfixe : /growth
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
import json
from datetime import datetime, timedelta

from db import get_db   # ← pool aiomysql

router = APIRouter(prefix="/growth", tags=["growth"])


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
    event: str


@router.get("/links")
async def get_links():
    async with get_db() as cur:
        await cur.execute("""
            SELECT l.*,
                COUNT(CASE WHEN s.event='click'     THEN 1 END) as clicks,
                COUNT(DISTINCT CASE WHEN s.event='register'  THEN s.user_id END) as registrations,
                COUNT(DISTINCT CASE WHEN s.event='subscribe' THEN s.user_id END) as subscribers,
                (SELECT COUNT(DISTINCT fs.telegram_id)
                 FROM form_sessions fs
                 WHERE fs.form_id = l.form_id AND fs.status = 'completed') as forms_done,
                f.name as form_name
            FROM invite_links l
            LEFT JOIN invite_link_stats s ON s.link_id = l.id
            LEFT JOIN forms f ON f.id = l.form_id
            GROUP BY l.id
            ORDER BY l.created_at DESC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/links", status_code=201)
async def create_link(body: LinkCreate):
    async with get_db() as cur:
        await cur.execute("SELECT id FROM invite_links WHERE start_param = %s", (body.start_param,))
        if await cur.fetchone():
            raise HTTPException(status_code=400, detail="start_param déjà utilisé")
        await cur.execute("""
            INSERT INTO invite_links
                (name, start_param, auto_category, promo_code, quota_max, expires_at, source, form_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (body.name, body.start_param, body.auto_category, body.promo_code,
              body.quota_max, body.expires_at, body.source, body.form_id))
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM invite_links WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.get("/links/{link_id}")
async def get_link(link_id: int):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM invite_links WHERE id = %s", (link_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        await cur.execute(
            "SELECT * FROM invite_link_stats WHERE link_id = %s ORDER BY occurred_at DESC LIMIT 50",
            (link_id,)
        )
        stats = await cur.fetchall()
    result = dict(row)
    result["stats"] = [dict(s) for s in stats]
    return result


@router.patch("/links/{link_id}")
async def update_link(link_id: int, body: LinkUpdate):
    async with get_db() as cur:
        await cur.execute("SELECT id FROM invite_links WHERE id = %s", (link_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Lien introuvable")
        data = body.dict(exclude_none=True)
        if not data:
            await cur.execute("SELECT * FROM invite_links WHERE id = %s", (link_id,))
            return dict(await cur.fetchone())
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE invite_links SET {sets} WHERE id = %s", list(data.values()) + [link_id])
        await cur.execute("SELECT * FROM invite_links WHERE id = %s", (link_id,))
        return dict(await cur.fetchone())


@router.delete("/links/{link_id}")
async def delete_link(link_id: int):
    async with get_db() as cur:
        await cur.execute("UPDATE invite_links SET is_active = 0 WHERE id = %s", (link_id,))
    return {"ok": True}


@router.post("/links/{link_id}/click")
async def record_link_event(link_id: int, body: LinkClickEvent):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM invite_links WHERE id = %s", (link_id,))
        link = await cur.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Lien introuvable")
        link = dict(link)
        await cur.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (%s,%s,%s)",
            (link_id, body.user_id, body.event)
        )
        quota_reached = False
        if body.event == "register":
            await cur.execute(
                "UPDATE invite_links SET quota_used = quota_used + 1 WHERE id = %s", (link_id,)
            )
            await cur.execute(
                "SELECT quota_max, quota_used FROM invite_links WHERE id = %s", (link_id,)
            )
            updated = await cur.fetchone()
            if updated["quota_max"] and updated["quota_used"] >= updated["quota_max"]:
                quota_reached = True
            if link["auto_category"] and body.user_id:
                try:
                    from telegram_page.categorie import add_members_to_category
                    await add_members_to_category(link["auto_category"], [body.user_id])
                except Exception as e:
                    print(f"[link click] add_category error: {e}")
    return {"ok": True, "quota_reached": quota_reached}


@router.get("/links/{link_id}/qr")
async def get_link_qr(link_id: int):
    try:
        import qrcode, base64
        from io import BytesIO
    except ImportError:
        raise HTTPException(status_code=501, detail="pip install qrcode[pil]")
    async with get_db() as cur:
        await cur.execute("SELECT start_param FROM invite_links WHERE id = %s", (link_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lien introuvable")
    url = f"https://t.me/TradingBot?start={row['start_param']}"
    img = qrcode.make(url)
    buf = BytesIO(); img.save(buf, "PNG")
    return {"qr_base64": base64.b64encode(buf.getvalue()).decode(), "url": url}


# ════════════════════════════════════════════════════════════════
# IA TRIGGER
# ════════════════════════════════════════════════════════════════

class IATriggerUpdate(BaseModel):
    trigger_type: str
    messages_count: Optional[int] = None


@router.get("/ia-trigger")
async def get_ia_trigger():
    async with get_db() as cur:
        await cur.execute("SELECT * FROM ia_trigger_config WHERE id = 1")
        row = await cur.fetchone()
    return dict(row) if row else {}


@router.patch("/ia-trigger")
async def update_ia_trigger(body: IATriggerUpdate):
    async with get_db() as cur:
        await cur.execute("""
            UPDATE ia_trigger_config
            SET trigger_type = %s, messages_count = %s, updated_at = NOW()
            WHERE id = 1
        """, (body.trigger_type, body.messages_count))
        await cur.execute("SELECT * FROM ia_trigger_config WHERE id = 1")
        return dict(await cur.fetchone())


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
async def get_jobs():
    async with get_db() as cur:
        await cur.execute("SELECT * FROM automation_jobs ORDER BY created_at DESC")
        jobs = [dict(r) for r in await cur.fetchall()]
        for j in jobs:
            await cur.execute(
                "SELECT COUNT(*) as n FROM automation_logs WHERE job_id = %s", (j["id"],)
            )
            j["log_count"] = (await cur.fetchone())["n"]
    return jobs


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate):
    next_run = _compute_next_run(body.freq, body.run_time)
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO automation_jobs
                (name, trig_type, freq, run_time, cond_field, cond_value, cond_extra,
                 event_type, target, action_type, action_content, next_run_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (body.name, body.trig_type, body.freq, body.run_time,
              body.cond_field, body.cond_value, body.cond_extra,
              body.event_type, body.target, body.action_type,
              body.action_content, next_run))
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM automation_jobs WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.get("/jobs/{job_id}")
async def get_job(job_id: int):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM automation_jobs WHERE id = %s", (job_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job introuvable")
        await cur.execute(
            "SELECT * FROM automation_logs WHERE job_id = %s ORDER BY started_at DESC LIMIT 10",
            (job_id,)
        )
        logs = await cur.fetchall()
    result = dict(row)
    result["logs"] = [dict(l) for l in logs]
    return result


@router.patch("/jobs/{job_id}")
async def update_job(job_id: int, body: JobUpdate):
    async with get_db() as cur:
        await cur.execute("SELECT id FROM automation_jobs WHERE id = %s", (job_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Job introuvable")
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE automation_jobs SET {sets} WHERE id = %s", list(data.values()) + [job_id])
        await cur.execute("SELECT * FROM automation_jobs WHERE id = %s", (job_id,))
        return dict(await cur.fetchone())


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM automation_logs WHERE job_id = %s", (job_id,))
        await cur.execute("DELETE FROM automation_jobs WHERE id = %s", (job_id,))
    return {"ok": True}


@router.post("/jobs/{job_id}/run")
async def run_job_now(job_id: int):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM automation_jobs WHERE id = %s", (job_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job introuvable")

    from telegram_page.automatisation.bot_growth import execute_automation_job, compute_next_run
    try:
        from telegram_page.chat import _bot as bot
    except Exception:
        bot = None

    job = dict(row)

    async with get_db() as cur:
        await cur.execute(
            "INSERT INTO automation_logs (job_id, started_at) VALUES (%s, NOW())", (job_id,)
        )
        log_id = cur.lastrowid

    try:
        r  = await execute_automation_job(bot, job)
        st = "success" if r["errors"] == 0 else "partial"
    except Exception:
        r  = {"total": 0, "sent": 0, "errors": 1}
        st = "failed"

    next_run = compute_next_run(job.get("freq"), job.get("run_time"))
    async with get_db() as cur:
        await cur.execute("""
            UPDATE automation_logs
            SET finished_at = NOW(), total = %s, sent = %s, errors = %s, status = %s
            WHERE id = %s
        """, (r["total"], r["sent"], r["errors"], st, log_id))
        await cur.execute("""
            UPDATE automation_jobs
            SET exec_count = exec_count + 1, last_run_at = NOW(),
                next_run_at = %s, err_count = err_count + %s
            WHERE id = %s
        """, (next_run, r["errors"], job_id))
    return r


@router.get("/jobs/{job_id}/logs")
async def get_job_logs(job_id: int):
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM automation_logs WHERE job_id = %s ORDER BY started_at DESC LIMIT 20",
            (job_id,)
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


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
async def get_plans():
    async with get_db() as cur:
        await cur.execute("""
            SELECT p.*,
                COUNT(CASE WHEN gs.status='active' THEN 1 END) as active_count,
                COUNT(CASE WHEN gs.status='trial'  THEN 1 END) as trial_count
            FROM subscription_plans p
            LEFT JOIN growth_subscriptions gs ON gs.plan_id = p.id
            WHERE p.is_active = 1
            GROUP BY p.id
            ORDER BY p.created_at
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/plans", status_code=201)
async def create_plan(body: PlanCreate):
    async with get_db() as cur:
        cats = json.dumps(body.categories or [])
        await cur.execute("""
            INSERT INTO subscription_plans
                (name, price_usd, duration_days, trial_days, categories, description)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (body.name, body.price_usd, body.duration_days, body.trial_days, cats, body.description))
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM subscription_plans WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.patch("/plans/{plan_id}")
async def update_plan(plan_id: int, body: PlanUpdate):
    async with get_db() as cur:
        await cur.execute("SELECT id FROM subscription_plans WHERE id = %s", (plan_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Plan introuvable")
        data = body.dict(exclude_none=True)
        if "categories" in data:
            data["categories"] = json.dumps(data["categories"])
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE subscription_plans SET {sets} WHERE id = %s", list(data.values()) + [plan_id])
        await cur.execute("SELECT * FROM subscription_plans WHERE id = %s", (plan_id,))
        return dict(await cur.fetchone())


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: int):
    async with get_db() as cur:
        await cur.execute("UPDATE subscription_plans SET is_active = 0 WHERE id = %s", (plan_id,))
    return {"ok": True}


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
async def get_subscriptions(plan_id: Optional[int]=None, status: Optional[str]=None,
                             search: Optional[str]=None, limit: int=50, offset: int=0):
    async with get_db() as cur:
        where = ["1=1"]; params = []
        if plan_id: where.append("gs.plan_id = %s");    params.append(plan_id)
        if status:  where.append("gs.status = %s");     params.append(status)
        if search:  where.append("gs.member_name LIKE %s"); params.append(f"%%{search}%%")
        await cur.execute(f"""
            SELECT gs.*, sp.name as plan_name, sp.price_usd
            FROM growth_subscriptions gs
            JOIN subscription_plans sp ON sp.id = gs.plan_id
            WHERE {' AND '.join(where)}
            ORDER BY gs.started_at DESC LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/subscriptions/stats")
async def get_sub_stats():
    async with get_db() as cur:
        await cur.execute("SELECT COALESCE(SUM(price_paid),0) as n FROM growth_subscriptions WHERE status IN ('active','trial')")
        mrr = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions WHERE status='active'")
        actifs = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions WHERE status='trial'")
        essais = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions WHERE status='expired'")
        expired_n = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions")
        total_n = (await cur.fetchone())["n"]
        await cur.execute("""
            SELECT COUNT(*) as n FROM growth_subscriptions
            WHERE status = 'active' AND expires_at <= DATE_ADD(NOW(), INTERVAL 7 DAY)
        """)
        expiring = (await cur.fetchone())["n"]
    return {
        "mrr": mrr, "actifs": actifs, "essais": essais,
        "churn_rate": round(expired_n / max(total_n, 1) * 100, 1),
        "expiring_soon": expiring,
    }


@router.post("/subscriptions", status_code=201)
async def create_subscription(body: SubCreate):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM subscription_plans WHERE id = %s", (body.plan_id,))
        plan = await cur.fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan introuvable")
        plan = dict(plan)
        price_paid = plan["price_usd"]

        if body.promo_code:
            await cur.execute("""
                SELECT * FROM promo_codes
                WHERE code = %s AND is_active = 1
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND (quota_max IS NULL OR current_uses < quota_max)
            """, (body.promo_code,))
            promo = await cur.fetchone()
            if promo:
                promo = dict(promo)
                price_paid = price_paid * (1 - promo["discount_value"] / 100) if promo["discount_type"] == "percent" else max(0, price_paid - promo["discount_value"])
                await cur.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = %s", (promo["id"],))

        expires_at = (datetime.now() + timedelta(days=plan["duration_days"])).isoformat()
        if body.status == "trial" and plan["trial_days"] > 0:
            expires_at = (datetime.now() + timedelta(days=plan["trial_days"])).isoformat()

        await cur.execute("""
            INSERT INTO growth_subscriptions
                (user_id, member_name, plan_id, status, price_paid, promo_code, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (body.user_id, body.member_name, body.plan_id, body.status,
              round(price_paid, 2), body.promo_code, expires_at))
        new_id = cur.lastrowid

        if plan.get("categories") and body.user_id:
            for cat in json.loads(plan["categories"] or "[]"):
                try:
                    from telegram_page.categorie import add_members_to_category
                    await add_members_to_category(cat, [body.user_id])
                except Exception as e:
                    print(f"[sub create] add_category error: {e}")

        await cur.execute("SELECT * FROM growth_subscriptions WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, body: SubUpdate):
    async with get_db() as cur:
        data = body.dict(exclude_none=True)
        if not data:
            return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE growth_subscriptions SET {sets} WHERE id = %s", list(data.values()) + [sub_id])
        await cur.execute("SELECT * FROM growth_subscriptions WHERE id = %s", (sub_id,))
        return dict(await cur.fetchone())


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM growth_subscriptions WHERE id = %s", (sub_id,))
    return {"ok": True}


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
async def get_promos():
    async with get_db() as cur:
        await cur.execute("""
            SELECT pc.*, sp.name as plan_name
            FROM promo_codes pc
            LEFT JOIN subscription_plans sp ON sp.id = pc.plan_id
            ORDER BY pc.created_at DESC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/promos", status_code=201)
async def create_promo(body: PromoCreate):
    async with get_db() as cur:
        await cur.execute("SELECT id FROM promo_codes WHERE code = %s", (body.code.upper(),))
        if await cur.fetchone():
            raise HTTPException(status_code=400, detail="Code déjà existant")
        await cur.execute("""
            INSERT INTO promo_codes
                (code, discount_type, discount_value, plan_id, quota_max, first_time_only, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (body.code.upper(), body.discount_type, body.discount_value,
              body.plan_id, body.quota_max, body.first_time_only, body.expires_at))
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM promo_codes WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.patch("/promos/{promo_id}")
async def update_promo(promo_id: int, body: PromoUpdate):
    async with get_db() as cur:
        data = body.dict(exclude_none=True)
        if "code" in data: data["code"] = data["code"].upper()
        if not data: return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE promo_codes SET {sets} WHERE id = %s", list(data.values()) + [promo_id])
        await cur.execute("SELECT * FROM promo_codes WHERE id = %s", (promo_id,))
        return dict(await cur.fetchone())


@router.delete("/promos/{promo_id}")
async def delete_promo(promo_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM promo_codes WHERE id = %s", (promo_id,))
    return {"ok": True}


@router.post("/promos/validate")
async def validate_promo(body: PromoValidate):
    async with get_db() as cur:
        await cur.execute("""
            SELECT pc.*, sp.name as plan_name
            FROM promo_codes pc
            LEFT JOIN subscription_plans sp ON sp.id = pc.plan_id
            WHERE pc.code = %s
        """, (body.code.upper(),))
        promo = await cur.fetchone()
        if not promo: return {"valid": False, "error": "Code invalide"}
        promo = dict(promo)
        if not promo["is_active"]: return {"valid": False, "error": "Code désactivé"}
        if promo["expires_at"] and promo["expires_at"] < datetime.now().isoformat():
            return {"valid": False, "error": "Code expiré"}
        if promo["quota_max"] and promo["current_uses"] >= promo["quota_max"]:
            return {"valid": False, "error": "Quota atteint"}
        if promo["first_time_only"] and body.user_id:
            await cur.execute("""
                SELECT id FROM growth_subscriptions
                WHERE user_id = %s AND status NOT IN ('expired','cancelled') LIMIT 1
            """, (body.user_id,))
            existing = await cur.fetchone()
            if existing: return {"valid": False, "error": "Réservé aux nouvelles souscriptions"}
        if body.plan_id and promo["plan_id"] and body.plan_id != promo["plan_id"]:
            return {"valid": False, "error": "Code non applicable à ce plan"}
    return {"valid": True, "discount_type": promo["discount_type"],
            "discount_value": promo["discount_value"], "plan_name": promo["plan_name"]}


@router.get("/promos/auto-config")
async def get_auto_promo_config():
    async with get_db() as cur:
        await cur.execute("SELECT * FROM auto_promo_config WHERE id = 1")
        return dict(await cur.fetchone())


class AutoPromoUpdate(BaseModel):
    anniversary_active: Optional[int] = None
    anniversary_pct: Optional[float] = None
    winback_active: Optional[int] = None
    winback_pct: Optional[float] = None
    upgrade_active: Optional[int] = None
    upgrade_pct: Optional[float] = None


@router.patch("/promos/auto-config")
async def update_auto_promo_config(body: AutoPromoUpdate):
    async with get_db() as cur:
        data = body.dict(exclude_none=True)
        if not data: return {"ok": True}
        data["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE auto_promo_config SET {sets} WHERE id = 1", list(data.values()))
        await cur.execute("SELECT * FROM auto_promo_config WHERE id = 1")
        return dict(await cur.fetchone())


# ════════════════════════════════════════════════════════════════
# SEGMENTS & SCORING
# ════════════════════════════════════════════════════════════════

class SegmentCreate(BaseModel):
    name: str; tag: str
    conditions: List[Any] = []
    auto_action: Optional[str] = None

class SegmentUpdate(BaseModel):
    name: Optional[str] = None; tag: Optional[str] = None
    conditions: Optional[List[Any]] = None
    auto_action: Optional[str] = None


@router.get("/segments")
async def get_segments():
    async with get_db() as cur:
        await cur.execute("SELECT * FROM segments ORDER BY created_at DESC")
        segs = [dict(r) for r in await cur.fetchall()]
        for seg in segs:
            await cur.execute(
                "SELECT COUNT(*) as n FROM segment_members WHERE segment_id = %s", (seg["id"],)
            )
            seg["member_count"] = (await cur.fetchone())["n"]
    return segs


@router.post("/segments", status_code=201)
async def create_segment(body: SegmentCreate):
    async with get_db() as cur:
        await cur.execute("INSERT INTO segments (name, tag, conditions, auto_action) VALUES (%s,%s,%s,%s)",
                           (body.name, body.tag, json.dumps(body.conditions), body.auto_action))
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM segments WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.patch("/segments/{seg_id}")
async def update_segment(seg_id: int, body: SegmentUpdate):
    async with get_db() as cur:
        data = body.dict(exclude_none=True)
        if "conditions" in data: data["conditions"] = json.dumps(data["conditions"])
        if not data: return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE segments SET {sets} WHERE id = %s", list(data.values()) + [seg_id])
        await cur.execute("SELECT * FROM segments WHERE id = %s", (seg_id,))
        return dict(await cur.fetchone())


@router.delete("/segments/{seg_id}")
async def delete_segment(seg_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM segment_members WHERE segment_id = %s", (seg_id,))
        await cur.execute("DELETE FROM segments WHERE id = %s", (seg_id,))
    return {"ok": True}


@router.post("/segments/compute")
async def compute_segments():
    from telegram_page.automatisation.bot_growth import compute_all_segments
    await compute_all_segments()
    async with get_db() as cur:
        await cur.execute("SELECT COUNT(*) as n FROM segments")
        n = (await cur.fetchone())["n"]
    return {"ok": True, "segments_computed": n}


@router.get("/scoring")
async def get_scoring():
    async with get_db() as cur:
        await cur.execute("""
            SELECT u.telegram_id, u.name, COALESCE(es.score, 0) as score
            FROM users u
            LEFT JOIN engagement_scores es ON es.user_id = u.telegram_id
            WHERE u.telegram_id IS NOT NULL
            ORDER BY score DESC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/scoring/{user_id}")
async def get_user_score(user_id: int):
    async with get_db() as cur:
        await cur.execute(
            "SELECT COALESCE(score, 0) as score FROM engagement_scores WHERE user_id = %s", (user_id,)
        )
        row = await cur.fetchone()
    return {"user_id": user_id, "score": row["score"] if row else 0}


class ScoringEvent(BaseModel):
    user_id: int; event_type: str; points: int


@router.post("/scoring/event")
async def record_scoring_event(body: ScoringEvent):
    async with get_db() as cur:
        await cur.execute(
            "INSERT IGNORE INTO engagement_scores (user_id, score) VALUES (%s, 0)", (body.user_id,)
        )
        await cur.execute("""
            UPDATE engagement_scores
            SET score = GREATEST(0, score + %s), updated_at = NOW()
            WHERE user_id = %s
        """, (body.points, body.user_id))
        await cur.execute(
            "SELECT score FROM engagement_scores WHERE user_id = %s", (body.user_id,)
        )
        new_score = (await cur.fetchone())["score"]
    return {"user_id": body.user_id, "new_score": new_score}


# ════════════════════════════════════════════════════════════════
# PIPELINE CRM
# ════════════════════════════════════════════════════════════════

class ProspectCreate(BaseModel):
    name: str; source: Optional[str] = "direct"; col: Optional[str] = "nouveau"
    link_id: Optional[int] = None; user_id: Optional[int] = None; score: Optional[int] = 0

class ProspectUpdate(BaseModel):
    col: Optional[str] = None; score: Optional[int] = None
    name: Optional[str] = None; source: Optional[str] = None


@router.get("/pipeline")
async def get_pipeline():
    async with get_db() as cur:
        await cur.execute("""
            SELECT cp.*, il.name as link_name
            FROM crm_prospects cp
            LEFT JOIN invite_links il ON il.id = cp.link_id
            ORDER BY cp.created_at DESC
        """)
        rows = await cur.fetchall()
    grouped = {col: [] for col in ["nouveau", "engage", "offre", "abonne", "vip"]}
    for r in rows:
        d = dict(r); col = d.get("col", "nouveau")
        if col in grouped: grouped[col].append(d)
    return grouped


@router.post("/pipeline", status_code=201)
async def create_prospect(body: ProspectCreate):
    async with get_db() as cur:
        await cur.execute(
            "INSERT INTO crm_prospects (name, source, col, link_id, user_id, score) VALUES (%s,%s,%s,%s,%s,%s)",
            (body.name, body.source, body.col, body.link_id, body.user_id, body.score)
        )
        new_id = cur.lastrowid
        await cur.execute("SELECT * FROM crm_prospects WHERE id = %s", (new_id,))
        return dict(await cur.fetchone())


@router.patch("/pipeline/{prospect_id}")
async def update_prospect(prospect_id: int, body: ProspectUpdate):
    async with get_db() as cur:
        data = body.dict(exclude_none=True)
        if not data: return {"ok": True}
        sets = ", ".join(f"{k} = %s" for k in data)
        await cur.execute(f"UPDATE crm_prospects SET {sets} WHERE id = %s", list(data.values()) + [prospect_id])
        await cur.execute("SELECT * FROM crm_prospects WHERE id = %s", (prospect_id,))
        return dict(await cur.fetchone())


@router.delete("/pipeline/{prospect_id}")
async def delete_prospect(prospect_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM crm_prospects WHERE id = %s", (prospect_id,))
    return {"ok": True}


@router.get("/pipeline/relances")
async def get_relances():
    async with get_db() as cur:
        relances = []
        await cur.execute("""
            SELECT * FROM crm_prospects
            WHERE col = 'offre' AND created_at <= DATE_SUB(NOW(), INTERVAL 3 DAY)
        """)
        old_offers = await cur.fetchall()
        for p in old_offers:
            relances.append({"type": "offre_sans_reponse", "name": p["name"],
                             "msg": "Offre envoyée sans réponse depuis 3+ jours", "prospect_id": p["id"]})

        await cur.execute("""
            SELECT gs.*, u.name, u.telegram_id
            FROM growth_subscriptions gs
            LEFT JOIN users u ON u.telegram_id = gs.user_id
            WHERE gs.status = 'active'
              AND gs.expires_at <= DATE_ADD(NOW(), INTERVAL 7 DAY)
              AND gs.expires_at > NOW()
        """)
        expiring = await cur.fetchall()
        for s in expiring:
            s = dict(s)
            relances.append({"type": "expiration", "name": s["name"] or s["member_name"],
                             "msg": f"Abonnement expire bientôt ({str(s['expires_at'])[:10] if s['expires_at'] else '?'})",
                             "user_id": s["user_id"]})

        await cur.execute("""
            SELECT gs.user_id, u.name
            FROM growth_subscriptions gs
            LEFT JOIN users u ON u.telegram_id = gs.user_id
            LEFT JOIN messages m ON m.user_id = gs.user_id
            WHERE gs.status = 'active'
            GROUP BY gs.user_id
            HAVING MAX(m.created_at) < DATE_SUB(NOW(), INTERVAL 14 DAY)
               OR MAX(m.created_at) IS NULL
        """)
        inactive = await cur.fetchall()
        for m in inactive:
            m = dict(m)
            relances.append({"type": "inactivite", "name": m["name"] or "Membre inconnu",
                             "msg": "Abonné actif sans activité depuis 14+ jours", "user_id": m["user_id"]})
    return relances


# ════════════════════════════════════════════════════════════════
# ANALYTICS
# ════════════════════════════════════════════════════════════════

@router.get("/analytics")
async def get_analytics():
    async with get_db() as cur:
        await cur.execute("SELECT COALESCE(SUM(price_paid),0) as n FROM growth_subscriptions WHERE status='active'")
        mrr = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM users WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)")
        new7j = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions")
        total_gs = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM invite_link_stats WHERE event='register'")
        total_reg = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM invite_link_stats WHERE event='subscribe'")
        total_pay = (await cur.fetchone())["n"]
    return {
        "mrr": mrr, "nouveaux_7j": new7j,
        "ltv_moyen": round(mrr * 3 / max(total_gs, 1), 2),
        "conv_rate": round(total_pay / max(total_reg, 1) * 100, 1),
    }


@router.get("/analytics/sources")
async def get_analytics_sources():
    async with get_db() as cur:
        await cur.execute("""
            SELECT l.id, l.name, l.source,
                COUNT(CASE WHEN s.event='click'     THEN 1 END) as clicks,
                COUNT(CASE WHEN s.event='register'  THEN 1 END) as registrations,
                COUNT(CASE WHEN s.event='subscribe' THEN 1 END) as subscribers
            FROM invite_links l
            LEFT JOIN invite_link_stats s ON s.link_id = l.id
            GROUP BY l.id ORDER BY registrations DESC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/analytics/funnel")
async def get_analytics_funnel():
    async with get_db() as cur:
        await cur.execute("SELECT COUNT(*) as n FROM invite_link_stats WHERE event='click'")
        clicks = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM invite_link_stats WHERE event='register'")
        registered = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM form_sessions WHERE status='completed'")
        forms_done = (await cur.fetchone())["n"]
        await cur.execute("SELECT COUNT(*) as n FROM growth_subscriptions WHERE status IN ('active','trial')")
        paying = (await cur.fetchone())["n"]
    return {"clicks": clicks, "registered": registered, "forms_done": forms_done, "paying": paying}


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