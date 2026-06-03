"""
routes_dashboard.py — Route agrégée pour le dashboard principal.

Un seul endpoint GET /dashboard/stats qui regroupe :
  - Stats membres (total, actifs, nouveaux, inactifs)
  - Abonnements (actifs, expirations proches)
  - Agent IA (messages traités, taux résolution, escalades)
  - Trading classique (win rate, perf totale, meilleur/pire trade, capital)
  - Gold (saison active, session active, comptes simulation)
  - Activité récente (5 dernières actions toutes sources)
  - Expirations proches (5 membres)

À intégrer dans api.py :
    from dashboard.routes_dashboard import router as dashboard_router
    app.include_router(dashboard_router)
"""

import sqlite3
from datetime import datetime, timedelta
from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DB_PATH = "preinscriptions.db"


# ══════════════════════════════════════════════════════════════════════════════
# HELPER DB
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()


def _fmt_ago(iso: str) -> str:
    """Convertit un timestamp ISO en 'il y a X min/h/j'."""
    if not iso:
        return "—"
    try:
        d    = datetime.fromisoformat(iso)
        diff = (datetime.now() - d).total_seconds()
        if diff < 60:
            return "À l'instant"
        if diff < 3600:
            return f"il y a {int(diff // 60)} min"
        if diff < 86400:
            return f"il y a {int(diff // 3600)}h"
        return f"il y a {int(diff // 86400)}j"
    except Exception:
        return "—"


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTEURS PAR DOMAINE
# ══════════════════════════════════════════════════════════════════════════════

def _get_membres_stats(conn: sqlite3.Connection) -> dict:
    """Stats membres depuis la table users et conversations."""
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM users WHERE telegram_id IS NOT NULL"
        ).fetchone()[0]

        actifs_7j = conn.execute("""
            SELECT COUNT(DISTINCT user_id) FROM messages
            WHERE created_at >= ? AND direction = 'in'
        """, (_days_ago(7),)).fetchone()[0]

        nouveaux_7j = conn.execute("""
            SELECT COUNT(*) FROM users
            WHERE created_at >= ?
        """, (_days_ago(7),)).fetchone()[0]

        inactifs_21j = conn.execute("""
            SELECT COUNT(DISTINCT u.telegram_id)
            FROM users u
            WHERE u.telegram_id NOT IN (
                SELECT DISTINCT user_id FROM messages
                WHERE created_at >= ? AND direction = 'in'
            )
        """, (_days_ago(21),)).fetchone()[0]

    except Exception:
        total = nouveaux_7j = actifs_7j = inactifs_21j = 0

    return {
        "total":        total,
        "actifs_7j":    actifs_7j,
        "nouveaux_7j":  nouveaux_7j,
        "inactifs_21j": inactifs_21j,
    }


def _get_abonnements_stats(conn: sqlite3.Connection) -> dict:
    """Stats abonnements depuis subscriptions."""
    try:
        actifs = conn.execute("""
            SELECT COUNT(*) FROM subscriptions
            WHERE status = 'active' AND expires_at > ?
        """, (_now(),)).fetchone()[0]

        expiration_7j = conn.execute("""
            SELECT COUNT(*) FROM subscriptions
            WHERE status = 'active'
              AND expires_at BETWEEN ? AND ?
        """, (_now(), _days_ago(-7))).fetchone()[0]

        expires_ce_mois = conn.execute("""
            SELECT COUNT(*) FROM subscriptions
            WHERE status = 'active'
              AND expires_at BETWEEN ? AND ?
        """, (_now(), _days_ago(-30))).fetchone()[0]

    except Exception:
        actifs = expiration_7j = expires_ce_mois = 0

    return {
        "actifs":           actifs,
        "expiration_7j":    expiration_7j,
        "expires_ce_mois":  expires_ce_mois,
    }


def _get_ia_stats(conn: sqlite3.Connection) -> dict:
    """Stats agent IA depuis messages et conversations."""
    try:
        messages_ia = conn.execute("""
            SELECT COUNT(*) FROM messages
            WHERE direction = 'out' AND is_ia = 1
        """).fetchone()[0]

        total_out = conn.execute("""
            SELECT COUNT(*) FROM messages WHERE direction = 'out'
        """).fetchone()[0]

        taux_resolution = round(
            messages_ia / total_out * 100, 1
        ) if total_out > 0 else 0

        escalades = conn.execute("""
            SELECT COUNT(*) FROM messages
            WHERE requires_admin = 1
        """).fetchone()[0]

    except Exception:
        messages_ia = taux_resolution = escalades = 0

    return {
        "messages_traites":  messages_ia,
        "taux_resolution":   taux_resolution,
        "escalades_attente": escalades,
    }


def _get_trading_stats(conn: sqlite3.Connection) -> dict:
    """
    Stats trading classique depuis signals + trade_journal + member_capital.
    - Win rate global admin
    - Performance totale %
    - Meilleur / pire trade
    - Membres actifs trading
    - Capital moyen membres
    - Total journaux
    - Trades ouverts
    """
    try:
        # Win rate admin global
        wr_row = conn.execute("""
            SELECT
                CASE WHEN COUNT(*) = 0 THEN NULL
                ELSE ROUND(
                    CAST(COUNT(CASE WHEN close_result = 'tp' THEN 1 END) AS REAL)
                    / COUNT(*) * 100, 1
                ) END AS win_rate,
                ROUND(SUM(COALESCE(result_percent, 0)), 2) AS perf_totale
            FROM signals
            WHERE status = 'closed'
        """).fetchone()

        win_rate_global    = wr_row["win_rate"]    if wr_row else None
        performance_totale = wr_row["perf_totale"] if wr_row else 0.0

        # Meilleur trade
        best = conn.execute("""
            SELECT pair, result_percent, closed_at
            FROM signals
            WHERE status = 'closed' AND result_percent IS NOT NULL
            ORDER BY result_percent DESC LIMIT 1
        """).fetchone()

        # Pire trade
        worst = conn.execute("""
            SELECT pair, result_percent, closed_at
            FROM signals
            WHERE status = 'closed' AND result_percent IS NOT NULL
            ORDER BY result_percent ASC LIMIT 1
        """).fetchone()

        # Membres actifs trading (ont au moins 1 trade journalisé)
        membres_actifs = conn.execute("""
            SELECT COUNT(DISTINCT user_id) FROM trade_journal
            WHERE participated = 1
        """).fetchone()[0]

        # Capital moyen membres (dernière déclaration par membre)
        cap_moyen = conn.execute("""
            SELECT ROUND(AVG(last_cap), 2) FROM (
                SELECT user_id, MAX(capital) AS last_cap
                FROM member_capital
                GROUP BY user_id
            )
        """).fetchone()[0]

        # Total journaux
        total_journaux = conn.execute(
            "SELECT COUNT(*) FROM trade_journal WHERE participated = 1"
        ).fetchone()[0]

        # Trades ouverts
        trades_ouverts = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status = 'open'"
        ).fetchone()[0]

    except Exception as e:
        win_rate_global = performance_totale = 0
        membres_actifs  = cap_moyen = total_journaux = trades_ouverts = 0
        best = worst = None

    return {
        "win_rate_global":          win_rate_global,
        "performance_totale_pct":   performance_totale,
        "meilleur_trade":           dict(best)  if best  else None,
        "pire_trade":               dict(worst) if worst else None,
        "membres_actifs_trading":   membres_actifs,
        "capital_moyen_membres":    cap_moyen,
        "total_journaux":           total_journaux,
        "trades_ouverts":           trades_ouverts,
    }


def _get_gold_stats(conn: sqlite3.Connection) -> dict:
    """
    Stats Gold depuis gold_seasons + gold_trade_sessions + gold_member_entries.
    - Saison active (nom, rendement, win rate, trades)
    - Session active (membres confirmés, lots, risque SL, gain TP1)
    - Comptes simulation
    """
    try:
        # Saison active
        season = conn.execute("""
            SELECT s.*,
                   COUNT(DISTINCT gts.id) AS total_trades,
                   COUNT(DISTINCT CASE WHEN gts.current_phase IN
                       ('tp1_reached','tp2_reached','tp3_reached') THEN gts.id END) AS wins,
                   COUNT(DISTINCT CASE WHEN gts.current_phase = 'sl_touched'
                       THEN gts.id END) AS losses
            FROM gold_seasons s
            LEFT JOIN gold_trade_sessions gts ON gts.season_id = s.id
            WHERE s.status = 'active'
            GROUP BY s.id
            ORDER BY s.created_at DESC LIMIT 1
        """).fetchone()

        saison = {}
        if season:
            s            = dict(season)
            total        = s.get("total_trades", 0) or 0
            wins         = s.get("wins", 0) or 0
            saison_wr    = round(wins / total * 100, 1) if total > 0 else 0

            # Rendement saison (basé sur les résultats membres)
            rendement = conn.execute("""
                SELECT ROUND(AVG(
                    CASE WHEN gme.result_usd IS NOT NULL AND gme.capital_before > 0
                    THEN (gme.result_usd / gme.capital_before) * 100
                    ELSE 0 END
                ), 2) AS avg_rendement
                FROM gold_member_entries gme
                JOIN gold_trade_sessions gts ON gts.id = gme.session_id
                WHERE gts.season_id = ?
            """, (s["id"],)).fetchone()

            saison = {
                "nom":             s.get("name"),
                "win_rate":        saison_wr,
                "trades":          total,
                "wins":            wins,
                "losses":          s.get("losses", 0),
                "rendement_pct":   float(rendement["avg_rendement"] or 0) if rendement else 0,
            }

        # Session active
        session = conn.execute("""
            SELECT * FROM gold_trade_sessions
            WHERE current_phase NOT IN ('closed','cancelled','sl_touched')
            ORDER BY created_at DESC LIMIT 1
        """).fetchone()

        session_active = {}
        if session:
            s = dict(session)
            session_active = {
                "id":                    s["id"],
                "direction":             s["direction"],
                "entry_price":           s["entry_price"],
                "current_phase":         s["current_phase"],
                "membres_confirmes":     s.get("total_members_in", 0),
                "lots_engages":          round(s.get("total_lots_engaged", 0) or 0, 2),
                "risque_sl":             round(s.get("estimated_loss_sl", 0) or 0, 0),
                "gain_tp1":              round(s.get("estimated_gain_tp1", 0) or 0, 0),
                "gain_tp2":              round(s.get("estimated_gain_tp2", 0) or 0, 0),
            }

        # Comptes simulation
        sims = conn.execute("""
            SELECT sa.name,
                   sa.initial_capital,
                   sa.current_capital,
                   ROUND((sa.current_capital - sa.initial_capital)
                         / sa.initial_capital * 100, 2) AS rendement_pct,
                   sa.wins, sa.losses,
                   sa.max_drawdown_pct
            FROM simulation_accounts sa
            WHERE sa.is_active = 1
            ORDER BY sa.initial_capital ASC
        """).fetchall()

    except Exception as e:
        saison = session_active = {}
        sims   = []

    return {
        "saison_active":   saison,
        "session_active":  session_active,
        "simulations":     [dict(s) for s in sims],
    }


def _get_activite_recente(conn: sqlite3.Connection) -> list:
    """
    5 dernières actions toutes sources confondues :
    - trade_journal (résultat soumis)
    - messages IA automatiques
    - form_sessions complétées
    - gold_member_entries (confirmation trade)
    - subscriptions expirées
    """
    activites = []

    try:
        # 1. Trades journalisés récents
        trades = conn.execute("""
            SELECT u.name, tj.submitted_at, s.pair, tj.result_percent
            FROM trade_journal tj
            JOIN users u ON u.telegram_id = tj.user_id
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.participated = 1
            ORDER BY tj.submitted_at DESC LIMIT 3
        """).fetchall()

        for t in trades:
            pct  = t["result_percent"]
            sign = "+" if (pct or 0) > 0 else ""
            activites.append({
                "type":        "trade_journal",
                "nom":         t["name"] or "Membre",
                "initiales":   _initials(t["name"]),
                "couleur":     "green" if (pct or 0) > 0 else "red",
                "description": f"Trade {t['pair']} {sign}{pct}% journalisé",
                "temps":       _fmt_ago(t["submitted_at"]),
                "badge":       "Trade",
                "ts":          t["submitted_at"],
            })

        # 2. Messages IA automatiques récents
        ia_msgs = conn.execute("""
            SELECT m.created_at, u.name
            FROM messages m
            JOIN users u ON u.telegram_id = m.user_id
            WHERE m.direction = 'out' AND m.is_ia = 1
            ORDER BY m.created_at DESC LIMIT 2
        """).fetchall()

        for m in ia_msgs:
            activites.append({
                "type":        "ia_auto",
                "nom":         "Agent IA",
                "initiales":   "IA",
                "couleur":     "teal",
                "description": f"Réponse automatique à {m['name'] or 'un membre'}",
                "temps":       _fmt_ago(m["created_at"]),
                "badge":       "Auto",
                "ts":          m["created_at"],
            })

        # 3. Formulaires complétés récents
        forms = conn.execute("""
            SELECT fs.updated_at, u.name, f.name AS form_name
            FROM form_sessions fs
            JOIN users u ON u.telegram_id = fs.telegram_id
            JOIN forms f ON f.id = fs.form_id
            WHERE fs.status = 'completed'
            ORDER BY fs.updated_at DESC LIMIT 2
        """).fetchall()

        for f in forms:
            activites.append({
                "type":        "form_completed",
                "nom":         f["name"] or "Membre",
                "initiales":   _initials(f["name"]),
                "couleur":     "sky",
                "description": f"Formulaire « {f['form_name']} » complété",
                "temps":       _fmt_ago(f["updated_at"]),
                "badge":       "Form",
                "ts":          f["updated_at"],
            })

        # 4. Confirmations trade Gold récentes
        gold = conn.execute("""
            SELECT gme.confirmed_at, u.name, gme.tp_level_assigned, gme.capital_declared
            FROM gold_member_entries gme
            JOIN users u ON u.telegram_id = gme.user_id
            ORDER BY gme.confirmed_at DESC LIMIT 2
        """).fetchall()

        for g in gold:
            activites.append({
                "type":        "gold_confirm",
                "nom":         g["name"] or "Membre",
                "initiales":   _initials(g["name"]),
                "couleur":     "amber",
                "description": f"Trade Gold confirmé (TP{g['tp_level_assigned']} · {g['capital_declared']}$)",
                "temps":       _fmt_ago(g["confirmed_at"]),
                "badge":       "Gold",
                "ts":          g["confirmed_at"],
            })

        # 5. Abonnements expirés récents
        subs = conn.execute("""
            SELECT s.updated_at, u.name, s.plan
            FROM subscriptions s
            JOIN users u ON u.telegram_id = s.user_id
            WHERE s.status = 'expired'
            ORDER BY s.updated_at DESC LIMIT 2
        """).fetchall()

        for s in subs:
            activites.append({
                "type":        "subscription_expired",
                "nom":         s["name"] or "Membre",
                "initiales":   _initials(s["name"]),
                "couleur":     "red",
                "description": f"Abonnement {s['plan']} expiré",
                "temps":       _fmt_ago(s["updated_at"]),
                "badge":       "Expiré",
                "ts":          s["updated_at"],
            })

    except Exception as e:
        pass

    # Trier par date décroissante et prendre les 5 plus récents
    activites.sort(key=lambda x: x.get("ts", ""), reverse=True)
    for a in activites:
        a.pop("ts", None)  # supprimer le champ technique

    return activites[:5]


def _get_expirations_proches(conn: sqlite3.Connection) -> list:
    """5 membres dont l'abonnement expire dans les 7 prochains jours."""
    try:
        rows = conn.execute("""
            SELECT u.telegram_id AS user_id, u.name, s.plan, s.expires_at,
                   CAST(
                       (julianday(s.expires_at) - julianday('now'))
                   AS INTEGER) AS jours_restants
            FROM subscriptions s
            JOIN users u ON u.telegram_id = s.user_id
            WHERE s.status = 'active'
              AND s.expires_at BETWEEN datetime('now') AND datetime('now', '+7 days')
            ORDER BY s.expires_at ASC LIMIT 5
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _initials(name: str) -> str:
    if not name:
        return "?"
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts[:2])


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def api_dashboard_stats():
    """
    Agrégat complet pour le dashboard principal.

    Retourne en un seul appel :
      - membres       : total, actifs 7j, nouveaux 7j, inactifs 21j
      - abonnements   : actifs, expirations 7j, expirations ce mois
      - ia            : messages traités, taux résolution, escalades
      - trading       : win rate global, perf totale, meilleur/pire trade,
                        membres actifs, capital moyen, journaux, trades ouverts
      - gold          : saison active (stats), session active (agrégats),
                        comptes simulation
      - activite      : 5 dernières actions toutes sources
      - expirations   : 5 membres expirant dans 7 jours

    Temps de réponse estimé : < 200ms (requêtes SQLite locales)
    """
    conn = get_conn()
    try:
        result = {
            "membres":     _get_membres_stats(conn),
            "abonnements": _get_abonnements_stats(conn),
            "ia":          _get_ia_stats(conn),
            "trading":     _get_trading_stats(conn),
            "gold":        _get_gold_stats(conn),
            "activite_recente":   _get_activite_recente(conn),
            "expirations_proches": _get_expirations_proches(conn),
            "generated_at": _now(),
        }
    finally:
        conn.close()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# INTÉGRATION api.py
# ══════════════════════════════════════════════════════════════════════════════
"""
Dans api.py, ajouter :

    from dashboard.routes_dashboard import router as dashboard_router
    app.include_router(dashboard_router)

C'est tout — aucune dépendance supplémentaire, tout tourne sur SQLite.
"""