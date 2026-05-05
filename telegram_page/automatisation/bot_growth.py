"""
telegram_page/bot_growth.py
Logique bot pour le Growth Hub : automations, scoring, segments, rapports.
"""

import sqlite3
import json
from datetime import datetime, timedelta

DB = "preinscriptions.db"
ADMIN_ID = 571718066
_bot = None


def set_growth_bot(b):
    global _bot
    _bot = b


def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


# ─────────────────────────────────────────────────────────────
# resolve_job_targets
# ─────────────────────────────────────────────────────────────

async def resolve_job_targets(job: dict) -> list:
    t = job.get("target", "all")
    conn = get_conn()
    try:
        if t == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        elif t == "admin":
            return [ADMIN_ID]
        elif t.startswith("cat:"):
            # Table categories : colonne id_user (pas user_id)
            cat = t.replace("cat:", "")
            rows = conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie=?", (cat,)
            ).fetchall()
        elif t.startswith("seg:"):
            seg_id = int(t.replace("seg:", ""))
            rows = conn.execute(
                "SELECT user_id FROM segment_members WHERE segment_id=?", (seg_id,)
            ).fetchall()
        else:
            return []
        return [r[0] for r in rows if r[0]]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# execute_automation_job
# ─────────────────────────────────────────────────────────────

async def execute_automation_job(bot, job: dict) -> dict:
    user_ids = await resolve_job_targets(job)
    total = len(user_ids)
    sent = errors = 0
    action = job.get("action_type", "")
    content = job.get("action_content", "") or ""

    if action == "send_message":
        from telegram_page.broadcast_engine import broadcast_engine
        r = await broadcast_engine(bot, {
            "message": content,
            "format": "text",
            "user_ids": user_ids,
            "tag": f"job_{job['id']}_{datetime.now().strftime('%Y%m%d%H%M')}",
            "delay": 0.08,
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
            "week_end": now.isoformat(),
            "week_label": f"Semaine du {mon.strftime('%d %B')}",
            "target": "journalised",
            "send": True,
            "admin_config": {
                "include_perf": True,
                "include_behavior": True,
                "include_recommendations": True,
                "include_comparison": False,
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
        "daily": timedelta(days=1),
        "every3d": timedelta(days=3),
        "weekly_mon": timedelta(weeks=1),
        "weekly_fri": timedelta(weeks=1),
        "monthly_1": timedelta(days=30),
        "monthly_15": timedelta(days=30),
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
    conn = get_conn()
    try:
        jobs = conn.execute(
            """
            SELECT * FROM automation_jobs
            WHERE is_active=1 AND trig_type='time'
              AND next_run_at IS NOT NULL AND next_run_at <= ?
            """,
            (now,),
        ).fetchall()
    finally:
        conn.close()

    for job in [dict(j) for j in jobs]:
        conn2 = get_conn()
        log_id = conn2.execute(
            "INSERT INTO automation_logs (job_id, started_at) VALUES (?,?)",
            (job["id"], datetime.now().isoformat()),
        ).lastrowid
        conn2.commit()
        conn2.close()

        try:
            r = await execute_automation_job(bot, job)
            st = "success" if r["errors"] == 0 else "partial"
        except Exception:
            r = {"total": 0, "sent": 0, "errors": 1}
            st = "failed"

        next_run = compute_next_run(job.get("freq"), job.get("run_time"))
        conn3 = get_conn()
        conn3.execute(
            """
            UPDATE automation_logs
            SET finished_at=?, total=?, sent=?, errors=?, status=?
            WHERE id=?
            """,
            (datetime.now().isoformat(), r["total"], r["sent"], r["errors"], st, log_id),
        )
        conn3.execute(
            """
            UPDATE automation_jobs
            SET last_run_at=?, next_run_at=?,
                exec_count=exec_count+1, err_count=err_count+?
            WHERE id=?
            """,
            (datetime.now().isoformat(), next_run, r["errors"], job["id"]),
        )
        conn3.commit()
        conn3.close()


# ─────────────────────────────────────────────────────────────
# check_ia_trigger
# ─────────────────────────────────────────────────────────────

async def check_ia_trigger(user_id: int) -> bool:
    """Retourne True si l'IA peut démarrer la conversation."""
    conn = get_conn()
    try:
        cfg = conn.execute(
            "SELECT * FROM ia_trigger_config WHERE id=1"
        ).fetchone()
        if not cfg:
            return False
        t = cfg["trigger_type"]

        if t == "immediate":
            return True

        if t == "form":
            # form_sessions utilise telegram_id
            row = conn.execute(
                "SELECT id FROM form_sessions WHERE telegram_id=? AND status='completed' LIMIT 1",
                (user_id,),
            ).fetchone()
            return row is not None

        if t == "messages":
            # messages utilise user_id (= telegram_id)
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE user_id=? AND direction='inbound'",
                (user_id,),
            ).fetchone()
            return (row["cnt"] if row else 0) >= (cfg["messages_count"] or 5)

        if t == "trade":
            row = conn.execute(
                "SELECT id FROM trade_journal WHERE user_id=? LIMIT 1",
                (user_id,),
            ).fetchone()
            return row is not None

    finally:
        conn.close()
    return False


# ─────────────────────────────────────────────────────────────
# compute_all_segments
# ─────────────────────────────────────────────────────────────

async def compute_all_segments():
    """Recalcule tous les segments. Appelé au boot et toutes les heures."""
    conn = get_conn()
    try:
        segs = [dict(r) for r in conn.execute("SELECT * FROM segments").fetchall()]
    finally:
        conn.close()

    for seg in segs:
        conditions = json.loads(seg.get("conditions") or "[]")
        conn2 = get_conn()
        try:
            all_ids = {
                r[0]
                for r in conn2.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
                ).fetchall()
            }
            matching = set(all_ids)

            for cond in conditions:
                field = cond.get("field", "")
                value = cond.get("value", "")

                if "ormulaire" in field or "form_completed" in field:
                    rows = conn2.execute(
                        "SELECT DISTINCT telegram_id FROM form_sessions WHERE status='completed'"
                    ).fetchall()
                    matching &= {r[0] for r in rows}

                elif "apital" in field:
                    try:
                        threshold = float(value) if value else 0
                        rows = conn2.execute(
                            """
                            SELECT user_id FROM member_capital
                            GROUP BY user_id HAVING MAX(capital) > ?
                            """,
                            (threshold,),
                        ).fetchall()
                        matching &= {r[0] for r in rows}
                    except (ValueError, TypeError):
                        pass

                elif "core" in field:
                    try:
                        threshold = int(value) if value else 0
                        rows = conn2.execute(
                            "SELECT user_id FROM engagement_scores WHERE score > ?",
                            (threshold,),
                        ).fetchall()
                        matching &= {r[0] for r in rows}
                    except (ValueError, TypeError):
                        pass

                elif "bonne" in field or "subscri" in field:
                    rows = conn2.execute(
                        "SELECT user_id FROM growth_subscriptions WHERE status='active' AND user_id IS NOT NULL"
                    ).fetchall()
                    matching &= {r[0] for r in rows}

                elif "nactif" in field or "inactive" in field:
                    try:
                        days = int(value) if value else 14
                        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                        rows = conn2.execute(
                            "SELECT DISTINCT user_id FROM messages WHERE created_at >= ?",
                            (cutoff,),
                        ).fetchall()
                        matching -= {r[0] for r in rows}
                    except (ValueError, TypeError):
                        pass

                elif "in rate" in field or "win_rate" in field:
                    try:
                        threshold = float(value) if value else 0
                        rows = conn2.execute(
                            """
                            SELECT user_id,
                                CAST(SUM(CASE WHEN result_percent>0 THEN 1 ELSE 0 END)
                                     AS REAL) / NULLIF(COUNT(*),0) * 100 as wr
                            FROM trade_journal
                            WHERE participated=1 AND status='closed'
                            GROUP BY user_id HAVING wr > ?
                            """,
                            (threshold,),
                        ).fetchall()
                        matching &= {r[0] for r in rows}
                    except (ValueError, TypeError):
                        pass

            conn2.execute(
                "DELETE FROM segment_members WHERE segment_id=?", (seg["id"],)
            )
            if matching:
                conn2.executemany(
                    "INSERT OR IGNORE INTO segment_members (segment_id, user_id) VALUES (?,?)",
                    [(seg["id"], uid) for uid in matching],
                )
            conn2.execute(
                "UPDATE segments SET member_count=?, last_computed=datetime('now') WHERE id=?",
                (len(matching), seg["id"]),
            )
            conn2.commit()
        finally:
            conn2.close()


# ─────────────────────────────────────────────────────────────
# send_admin_report
# ─────────────────────────────────────────────────────────────

async def send_admin_report(bot, period="week"):
    """Rapport hebdomadaire envoyé à l'admin chaque lundi 09h."""
    conn = get_conn()
    try:
        mrr = conn.execute(
            "SELECT COALESCE(SUM(price_paid),0) FROM growth_subscriptions WHERE status='active'"
        ).fetchone()[0]
        actifs = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status='active'"
        ).fetchone()[0]
        trials = conn.execute(
            "SELECT COUNT(*) FROM growth_subscriptions WHERE status='trial'"
        ).fetchone()[0]
        expiring = conn.execute(
            """
            SELECT COUNT(*) FROM growth_subscriptions WHERE status='active'
            AND expires_at <= datetime('now','+7 days')
            """
        ).fetchone()[0]
        new7d = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        avg_cap = conn.execute(
            """
            SELECT AVG(capital) FROM (
                SELECT user_id, MAX(capital) as capital
                FROM member_capital GROUP BY user_id
            )
            """
        ).fetchone()[0] or 0
        risk = conn.execute(
            """
            SELECT COUNT(*) FROM growth_subscriptions gs
            LEFT JOIN engagement_scores es ON es.user_id=gs.user_id
            WHERE gs.status='active' AND (es.score IS NULL OR es.score < 30)
            """
        ).fetchone()[0]
        top = conn.execute(
            """
            SELECT u.name, ROUND(SUM(tj.result_percent),1) as perf
            FROM trade_journal tj
            JOIN users u ON u.telegram_id=tj.user_id
            WHERE tj.participated=1 AND tj.status='closed'
              AND tj.submitted_at >= datetime('now','-7 days')
            GROUP BY tj.user_id ORDER BY perf DESC LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    top_str = f"{top['name']} (+{top['perf']}%)" if top else "—"
    msg = (
        f"📊 *Rapport hebdomadaire TradingBot*\n\n"
        f"💰 *Revenus*\n"
        f"MRR : *${mrr:.0f}* · Actifs : {actifs} · Essais : {trials}\n\n"
        f"⚠ Expirent dans 7j : {expiring}\n\n"
        f"👥 *Communauté*\n"
        f"Nouveaux membres (7j) : +{new7d}\n"
        f"Capital moyen : ${avg_cap:.0f} (dollars américains)\n"
        f"Membres à risque (score<30) : {risk}\n\n"
        f"🏆 *Top performer* : {top_str}\n\n"
        f"_Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}_"
    )
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[send_admin_report] Erreur: {e}")