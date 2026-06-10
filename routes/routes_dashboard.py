"""
routes_dashboard.py — v5 MySQL async
Route agrégée pour le dashboard principal.
"""

from datetime import datetime, timedelta
from fastapi import APIRouter

from db import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _now() -> str:
    return datetime.now().isoformat()

def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()

def _fmt_ago(iso) -> str:
    if not iso: return "—"
    try:
        d    = datetime.fromisoformat(str(iso))
        diff = (datetime.now() - d).total_seconds()
        if diff < 60:    return "À l'instant"
        if diff < 3600:  return f"il y a {int(diff // 60)} min"
        if diff < 86400: return f"il y a {int(diff // 3600)}h"
        return f"il y a {int(diff // 86400)}j"
    except Exception:
        return "—"

def _initials(name: str) -> str:
    if not name: return "?"
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts[:2])


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTEURS PAR DOMAINE
# ══════════════════════════════════════════════════════════════════════════════

async def _get_membres_stats(cur) -> dict:
    try:
        await cur.execute("SELECT COUNT(*) as n FROM users WHERE telegram_id IS NOT NULL")
        total = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(DISTINCT user_id) as n FROM messages
            WHERE created_at >= %s AND direction = 'inbound'
        """, (_days_ago(7),))
        actifs_7j = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(*) as n FROM users WHERE created_at >= %s", (_days_ago(7),))
        nouveaux_7j = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(DISTINCT u.telegram_id) as n FROM users u
            WHERE u.telegram_id NOT IN (
                SELECT DISTINCT user_id FROM messages WHERE created_at >= %s AND direction = 'inbound'
            )
        """, (_days_ago(21),))
        inactifs_21j = (await cur.fetchone())["n"]
    except Exception:
        total = nouveaux_7j = actifs_7j = inactifs_21j = 0
    return {"total": total, "actifs_7j": actifs_7j, "nouveaux_7j": nouveaux_7j, "inactifs_21j": inactifs_21j}


async def _get_abonnements_stats(cur) -> dict:
    try:
        await cur.execute("""
            SELECT COUNT(*) as n FROM subscriptions WHERE status='active' AND expires_at > NOW()
        """)
        actifs = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(*) as n FROM subscriptions
            WHERE status='active' AND expires_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 7 DAY)
        """)
        expiration_7j = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(*) as n FROM subscriptions
            WHERE status='active' AND expires_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 30 DAY)
        """)
        expires_ce_mois = (await cur.fetchone())["n"]
    except Exception:
        actifs = expiration_7j = expires_ce_mois = 0
    return {"actifs": actifs, "expiration_7j": expiration_7j, "expires_ce_mois": expires_ce_mois}


async def _get_ia_stats(cur) -> dict:
    try:
        await cur.execute("""
            SELECT COUNT(*) as n FROM messages WHERE direction='outbound' AND answered_by='ia'
        """)
        messages_ia = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(*) as n FROM messages WHERE direction='outbound'")
        total_out = (await cur.fetchone())["n"]

        taux_resolution = round(messages_ia / total_out * 100, 1) if total_out > 0 else 0

        await cur.execute("SELECT COUNT(*) as n FROM messages WHERE requires_admin=1")
        escalades = (await cur.fetchone())["n"]
    except Exception:
        messages_ia = taux_resolution = escalades = 0
    return {"messages_traites": messages_ia, "taux_resolution": taux_resolution, "escalades_attente": escalades}


async def _get_trading_stats(cur) -> dict:
    try:
        await cur.execute("""
            SELECT
                CASE WHEN COUNT(*)=0 THEN NULL
                ELSE ROUND(COUNT(CASE WHEN close_result='tp' THEN 1 END)/COUNT(*)*100,1) END AS win_rate,
                ROUND(SUM(COALESCE(result_percent,0)),2) AS perf_totale
            FROM signals WHERE status='closed'
        """)
        wr_row = await cur.fetchone()
        win_rate_global    = wr_row["win_rate"]    if wr_row else None
        performance_totale = wr_row["perf_totale"] if wr_row else 0.0

        await cur.execute("""
            SELECT pair, result_percent, closed_at FROM signals
            WHERE status='closed' AND result_percent IS NOT NULL
            ORDER BY result_percent DESC LIMIT 1
        """)
        best = await cur.fetchone()

        await cur.execute("""
            SELECT pair, result_percent, closed_at FROM signals
            WHERE status='closed' AND result_percent IS NOT NULL
            ORDER BY result_percent ASC LIMIT 1
        """)
        worst = await cur.fetchone()

        await cur.execute("SELECT COUNT(DISTINCT user_id) as n FROM trade_journal WHERE participated=1")
        membres_actifs = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT ROUND(AVG(last_cap),2) as n
            FROM (SELECT user_id, MAX(capital) AS last_cap FROM member_capital GROUP BY user_id) t
        """)
        cap_moyen = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(*) as n FROM trade_journal WHERE participated=1")
        total_journaux = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(*) as n FROM signals WHERE status='open'")
        trades_ouverts = (await cur.fetchone())["n"]
    except Exception:
        win_rate_global = performance_totale = membres_actifs = cap_moyen = total_journaux = trades_ouverts = 0
        best = worst = None
    return {
        "win_rate_global": win_rate_global, "performance_totale_pct": performance_totale,
        "meilleur_trade": dict(best) if best else None, "pire_trade": dict(worst) if worst else None,
        "membres_actifs_trading": membres_actifs, "capital_moyen_membres": cap_moyen,
        "total_journaux": total_journaux, "trades_ouverts": trades_ouverts,
    }


async def _get_gold_stats(cur) -> dict:
    try:
        await cur.execute("""
            SELECT s.*,
                COUNT(DISTINCT gts.id) AS total_trades,
                COUNT(DISTINCT CASE WHEN gts.current_phase IN ('tp1_reached','tp2_reached','tp3_reached') THEN gts.id END) AS wins,
                COUNT(DISTINCT CASE WHEN gts.current_phase='sl_touched' THEN gts.id END) AS losses
            FROM gold_seasons s
            LEFT JOIN gold_trade_sessions gts ON gts.season_id=s.id
            WHERE s.status='active' GROUP BY s.id ORDER BY s.created_at DESC LIMIT 1
        """)
        season = await cur.fetchone()

        saison = {}
        if season:
            s     = dict(season)
            total = s.get("total_trades", 0) or 0
            wins  = s.get("wins", 0) or 0

            await cur.execute("""
                SELECT ROUND(AVG(CASE WHEN gme.result_usd IS NOT NULL AND gme.capital_before>0
                    THEN (gme.result_usd/gme.capital_before)*100 ELSE 0 END),2) AS avg_rendement
                FROM gold_member_entries gme JOIN gold_trade_sessions gts ON gts.id=gme.session_id
                WHERE gts.season_id=%s
            """, (s["id"],))
            rendement = await cur.fetchone()
            saison = {
                "nom": s.get("name"), "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "trades": total, "wins": wins, "losses": s.get("losses", 0),
                "rendement_pct": float(rendement["avg_rendement"] or 0) if rendement else 0,
            }

        await cur.execute("""
            SELECT * FROM gold_trade_sessions
            WHERE current_phase NOT IN ('closed','cancelled','sl_touched')
            ORDER BY created_at DESC LIMIT 1
        """)
        session = await cur.fetchone()

        session_active = {}
        if session:
            s = dict(session)
            session_active = {
                "id": s["id"], "direction": s["direction"], "entry_price": s["entry_price"],
                "current_phase": s["current_phase"], "membres_confirmes": s.get("total_members_in", 0),
                "lots_engages": round(s.get("total_lots_engaged", 0) or 0, 2),
                "risque_sl": round(s.get("estimated_loss_sl", 0) or 0, 0),
                "gain_tp1": round(s.get("estimated_gain_tp1", 0) or 0, 0),
                "gain_tp2": round(s.get("estimated_gain_tp2", 0) or 0, 0),
            }

        await cur.execute("""
            SELECT sa.name, sa.initial_capital, sa.current_capital,
                ROUND((sa.current_capital-sa.initial_capital)/sa.initial_capital*100,2) AS rendement_pct,
                sa.wins, sa.losses, sa.max_drawdown_pct
            FROM simulation_accounts sa WHERE sa.is_active=1 ORDER BY sa.initial_capital ASC
        """)
        sims = await cur.fetchall()

    except Exception:
        saison = session_active = {}
        sims   = []

    return {"saison_active": saison, "session_active": session_active, "simulations": [dict(s) for s in sims]}


async def _get_activite_recente(cur) -> list:
    activites = []
    try:
        await cur.execute("""
            SELECT u.name, tj.submitted_at, s.pair, tj.result_percent
            FROM trade_journal tj JOIN users u ON u.telegram_id=tj.user_id
            JOIN signals s ON s.id=tj.signal_id WHERE tj.participated=1
            ORDER BY tj.submitted_at DESC LIMIT 3
        """)
        trades = await cur.fetchall()
        for t in trades:
            pct  = t["result_percent"]; sign = "+" if (pct or 0) > 0 else ""
            activites.append({"type": "trade_journal", "nom": t["name"] or "Membre",
                               "initiales": _initials(t["name"]), "couleur": "green" if (pct or 0) > 0 else "red",
                               "description": f"Trade {t['pair']} {sign}{pct}% journalisé",
                               "temps": _fmt_ago(t["submitted_at"]), "badge": "Trade", "ts": t["submitted_at"]})

        await cur.execute("""
            SELECT m.created_at, u.name FROM messages m JOIN users u ON u.telegram_id=m.user_id
            WHERE m.direction='outbound' AND m.answered_by='ia' ORDER BY m.created_at DESC LIMIT 2
        """)
        ia_msgs = await cur.fetchall()
        for m in ia_msgs:
            activites.append({"type": "ia_auto", "nom": "Agent IA", "initiales": "IA", "couleur": "teal",
                               "description": f"Réponse automatique à {m['name'] or 'un membre'}",
                               "temps": _fmt_ago(m["created_at"]), "badge": "Auto", "ts": m["created_at"]})

        await cur.execute("""
            SELECT fs.updated_at, u.name, f.name AS form_name
            FROM form_sessions fs JOIN users u ON u.telegram_id=fs.telegram_id
            JOIN forms f ON f.id=fs.form_id WHERE fs.status='completed'
            ORDER BY fs.updated_at DESC LIMIT 2
        """)
        forms = await cur.fetchall()
        for f in forms:
            activites.append({"type": "form_completed", "nom": f["name"] or "Membre",
                               "initiales": _initials(f["name"]), "couleur": "sky",
                               "description": f"Formulaire « {f['form_name']} » complété",
                               "temps": _fmt_ago(f["updated_at"]), "badge": "Form", "ts": f["updated_at"]})

        await cur.execute("""
            SELECT gme.confirmed_at, u.name, gme.tp_level_assigned, gme.capital_declared
            FROM gold_member_entries gme JOIN users u ON u.telegram_id=gme.user_id
            ORDER BY gme.confirmed_at DESC LIMIT 2
        """)
        gold = await cur.fetchall()
        for g in gold:
            activites.append({"type": "gold_confirm", "nom": g["name"] or "Membre",
                               "initiales": _initials(g["name"]), "couleur": "amber",
                               "description": f"Trade Gold confirmé (TP{g['tp_level_assigned']} · {g['capital_declared']}$)",
                               "temps": _fmt_ago(g["confirmed_at"]), "badge": "Gold", "ts": g["confirmed_at"]})

        await cur.execute("""
            SELECT s.updated_at, u.name, s.plan FROM subscriptions s
            JOIN users u ON u.telegram_id=s.user_id WHERE s.status='expired'
            ORDER BY s.updated_at DESC LIMIT 2
        """)
        subs = await cur.fetchall()
        for s in subs:
            activites.append({"type": "subscription_expired", "nom": s["name"] or "Membre",
                               "initiales": _initials(s["name"]), "couleur": "red",
                               "description": f"Abonnement {s['plan']} expiré",
                               "temps": _fmt_ago(s["updated_at"]), "badge": "Expiré", "ts": s["updated_at"]})

    except Exception:
        pass

    activites.sort(key=lambda x: x.get("ts", ""), reverse=True)
    for a in activites: a.pop("ts", None)
    return activites[:5]


async def _get_expirations_proches(cur) -> list:
    try:
        await cur.execute("""
            SELECT u.telegram_id AS user_id, u.name, s.plan, s.expires_at,
                DATEDIFF(s.expires_at, NOW()) AS jours_restants
            FROM subscriptions s JOIN users u ON u.telegram_id=s.user_id
            WHERE s.status='active' AND s.expires_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 7 DAY)
            ORDER BY s.expires_at ASC LIMIT 5
        """)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def api_dashboard_stats():
    async with get_db() as cur:
        result = {
            "membres":             await _get_membres_stats(cur),
            "abonnements":         await _get_abonnements_stats(cur),
            "ia":                  await _get_ia_stats(cur),
            "trading":             await _get_trading_stats(cur),
            "gold":                await _get_gold_stats(cur),
            "activite_recente":    await _get_activite_recente(cur),
            "expirations_proches": await _get_expirations_proches(cur),
            "generated_at":        _now(),
        }
    return result