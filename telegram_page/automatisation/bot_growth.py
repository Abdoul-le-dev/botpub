"""
telegram_page/bot_growth.py — v4 MySQL
Logique bot pour le Growth Hub : automations, rapports.
"""

import json
from datetime import datetime, timedelta

from db import get_db   # ← pool MySQL, remplace get_conn()

ADMIN_ID = 571718066
_bot = None


def set_growth_bot(b):
    global _bot
    _bot = b


# ─────────────────────────────────────────────────────────────
# resolve_job_targets
# ─────────────────────────────────────────────────────────────

async def resolve_job_targets(job: dict) -> list:
    t = job.get("target", "all")
    with get_db() as conn:
        if t == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        elif t == "admin":
            return [ADMIN_ID]
        elif t.startswith("cat:"):
            cat  = t.replace("cat:", "")
            rows = conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (cat,)
            ).fetchall()
        else:
            return []
    return [r[0] for r in rows if r[0]]


# ─────────────────────────────────────────────────────────────
# execute_automation_job
# ─────────────────────────────────────────────────────────────

async def execute_automation_job(bot, job: dict) -> dict:
    user_ids = await resolve_job_targets(job)
    total    = len(user_ids)
    sent = errors = 0
    action  = job.get("action_type", "")
    content = job.get("action_content", "") or ""

    if action == "send_message":
        from telegram_page.broadcast_engine import broadcast_engine
        r = await broadcast_engine(bot, {
            "message":  content,
            "format":   "text",
            "user_ids": user_ids,
            "tag":      f"job_{job['id']}_{datetime.now().strftime('%Y%m%d%H%M')}",
            "delay":    0.08,
        })
        sent, errors = r.get("sent", 0), r.get("errors", 0)

    elif action == "send_form":
        form_id = int(content) if content.isdigit() else None
        if form_id:
            from form.form_engine import broadcast_form
            await broadcast_form(bot, form_id, user_ids, ADMIN_ID)
            sent = len(user_ids)

    elif action == "send_ia_bilan":
        from telegram_page.trading_journal import generate_weekly_bilans
        now = datetime.now()
        mon = now - timedelta(days=now.weekday())
        r = await generate_weekly_bilans({
            "week_start": mon.replace(hour=0, minute=0, second=0).isoformat(),
            "week_end":   now.isoformat(),
            "week_label": f"Semaine du {mon.strftime('%d %B')}",
            "target":     "journalised",
            "send":       True,
            "admin_config": {
                "include_perf":           True,
                "include_behavior":       True,
                "include_recommendations": True,
                "include_comparison":     False,
            },
        })
        sent, errors = r.get("sent", 0), r.get("errors", 0)

    elif action == "add_to_category":
        from telegram_page.categorie import add_members_to_category
        r = await add_members_to_category(content, user_ids)
        sent = r.get("added", 0)

    elif action == "remove_from_category":
        from telegram_page.categorie import remove_member_from_category
        for uid in user_ids:
            await remove_member_from_category(content, uid)
        sent = len(user_ids)

    elif action == "notify_admin":
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=content or "Notification automation")
            sent = 1
        except Exception:
            errors = 1

    elif action == "webhook":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(content, json={"job_id": job["id"], "users": user_ids})
            sent = total
        except Exception:
            errors = total

    return {"total": total, "sent": sent, "errors": errors}


# ─────────────────────────────────────────────────────────────
# compute_next_run
# ─────────────────────────────────────────────────────────────

def compute_next_run(freq: str, run_time: str):
    if not freq or not run_time:
        return None
    if freq == "once":
        return None
    try:
        h, m = map(int, run_time.split(":"))
    except Exception:
        return None
    offsets = {
        "daily":       timedelta(days=1),
        "every3d":     timedelta(days=3),
        "weekly_mon":  timedelta(weeks=1),
        "weekly_fri":  timedelta(weeks=1),
        "monthly_1":   timedelta(days=30),
        "monthly_15":  timedelta(days=30),
    }
    delta = offsets.get(freq)
    if not delta:
        return None
    return (
        (datetime.now() + delta)
        .replace(hour=h, minute=m, second=0, microsecond=0)
        .isoformat()
    )


# ─────────────────────────────────────────────────────────────
# check_and_run_jobs  (appelé toutes les minutes par le scheduler)
# ─────────────────────────────────────────────────────────────

async def check_and_run_jobs(bot):
    now = datetime.now().isoformat()

    with get_db() as conn:
        jobs = conn.execute("""
            SELECT * FROM automation_jobs
            WHERE is_active = 1 AND trig_type = 'time'
              AND next_run_at IS NOT NULL AND next_run_at <= ?
        """, (now,)).fetchall()

    for job in [dict(j) for j in jobs]:

        # Créer le log
        with get_db() as conn:
            conn.execute(
                "INSERT INTO automation_logs (job_id, started_at) VALUES (?, NOW())",
                (job["id"],)
            )
            log_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]

        try:
            r  = await execute_automation_job(bot, job)
            st = "success" if r["errors"] == 0 else "partial"
        except Exception:
            r  = {"total": 0, "sent": 0, "errors": 1}
            st = "failed"

        next_run = compute_next_run(job.get("freq"), job.get("run_time"))

        with get_db() as conn:
            conn.execute("""
                UPDATE automation_logs
                SET finished_at = NOW(), total = ?, sent = ?, errors = ?, status = ?
                WHERE id = ?
            """, (r["total"], r["sent"], r["errors"], st, log_id))
            conn.execute("""
                UPDATE automation_jobs
                SET last_run_at = NOW(), next_run_at = ?,
                    exec_count  = exec_count + 1,
                    err_count   = err_count + ?
                WHERE id = ?
            """, (next_run, r["errors"], job["id"]))


# ─────────────────────────────────────────────────────────────
# check_ia_trigger
# ─────────────────────────────────────────────────────────────

async def check_ia_trigger(user_id: int) -> bool:
    """Retourne True si l'IA peut démarrer la conversation."""
    with get_db() as conn:
        cfg = conn.execute(
            "SELECT * FROM ia_trigger_config WHERE id = 1"
        ).fetchone()
        if not cfg:
            return False

        t = cfg["trigger_type"]

        if t == "immediate":
            return True

        if t == "form":
            row = conn.execute("""
                SELECT id FROM form_sessions
                WHERE telegram_id = ? AND status = 'completed' LIMIT 1
            """, (user_id,)).fetchone()
            return row is not None

        if t == "messages":
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM messages
                WHERE user_id = ? AND direction = 'inbound'
            """, (user_id,)).fetchone()
            return (row["cnt"] if row else 0) >= (cfg["messages_count"] or 5)

        if t == "trade":
            row = conn.execute(
                "SELECT id FROM trade_journal WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
            return row is not None

    return False


# ─────────────────────────────────────────────────────────────
# send_admin_report
# ─────────────────────────────────────────────────────────────

async def send_admin_report(bot, period="week"):
    """Rapport hebdomadaire envoyé à l'admin chaque lundi 09h."""
    with get_db() as conn:
        mrr = conn.execute(
            "SELECT COALESCE(SUM(price_paid), 0) as n FROM growth_subscriptions WHERE status = 'active'"
        ).fetchone()["n"]

        actifs = conn.execute(
            "SELECT COUNT(*) as n FROM growth_subscriptions WHERE status = 'active'"
        ).fetchone()["n"]

        trials = conn.execute(
            "SELECT COUNT(*) as n FROM growth_subscriptions WHERE status = 'trial'"
        ).fetchone()["n"]

        expiring = conn.execute("""
            SELECT COUNT(*) as n FROM growth_subscriptions
            WHERE status = 'active'
              AND expires_at <= DATE_ADD(NOW(), INTERVAL 7 DAY)
        """).fetchone()["n"]

        new7d = conn.execute("""
            SELECT COUNT(*) as n FROM users
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        """).fetchone()["n"]

        avg_cap = conn.execute("""
            SELECT AVG(capital) as n FROM (
                SELECT user_id, MAX(capital) as capital
                FROM member_capital GROUP BY user_id
            ) t
        """).fetchone()["n"] or 0

        risk = conn.execute("""
            SELECT COUNT(*) as n FROM growth_subscriptions gs
            LEFT JOIN engagement_scores es ON es.user_id = gs.user_id
            WHERE gs.status = 'active'
              AND (es.score IS NULL OR es.score < 30)
        """).fetchone()["n"]

        top = conn.execute("""
            SELECT u.name, ROUND(SUM(tj.result_percent), 1) as perf
            FROM trade_journal tj
            JOIN users u ON u.telegram_id = tj.user_id
            WHERE tj.participated = 1 AND tj.status = 'closed'
              AND tj.submitted_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY tj.user_id
            ORDER BY perf DESC LIMIT 1
        """).fetchone()

    top_str = f"{top['name']} (+{top['perf']}%)" if top else "—"
    msg = (
        f"📊 *Rapport hebdomadaire TradingBot*\n\n"
        f"💰 *Revenus*\n"
        f"MRR : *${mrr:.0f}* · Actifs : {actifs} · Essais : {trials}\n\n"
        f"⚠ Expirent dans 7j : {expiring}\n\n"
        f"👥 *Communauté*\n"
        f"Nouveaux membres (7j) : +{new7d}\n"
        f"Capital moyen : ${avg_cap:.0f}\n"
        f"Membres à risque (score<30) : {risk}\n\n"
        f"🏆 *Top performer* : {top_str}\n\n"
        f"_Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}_"
    )
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[send_admin_report] Erreur: {e}")