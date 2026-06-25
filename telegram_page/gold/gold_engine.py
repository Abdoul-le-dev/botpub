"""
gold_engine.py — Version MySQL v5 async

Changements v5 :
  - Toutes les fonctions with get_db() migrées vers async with get_db() as cur
  - ? → %s partout
  - LAST_INSERT_ID() → cur.lastrowid
  - ROW_COUNT() → cur.rowcount

Corrections appliquées dans cette version :
  1. Ajout de adjust_entry_sl_to_live_price() — manquait et faisait
     crasher le bot au démarrage (ImportError depuis gold_broadcast.py).
  2. save_user_step() — fix du warning MySQL "'VALUES function' is
     deprecated" (syntaxe ON DUPLICATE KEY UPDATE avec alias).
  3. trigger_sl_touched() — ne notifie plus aucun user, seul l'admin
     reçoit la notification de clôture (via _notify_admin_session_closed).
"""

import logging
import json
import asyncio
import httpx
import math
import time as _time

from datetime import datetime
from typing import Optional
from telegram_page.gold.gold_write_queue import enqueue_write
from db import get_db

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

_bot = None

_db_write_lock     = asyncio.Lock()
_confirm_semaphore = asyncio.Semaphore(15)


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# CALCUL LOT
# ══════════════════════════════════════════════════════════════════════════════

def get_diviseur(capital: float) -> int:
    if capital < 1500:
        return 12
    return 12 + math.floor((capital - 1001) / 500)


def calculate_lot(capital: float, entry: float, sl: float) -> float:
    if capital < 250:
        return 0.01
    if capital < 500:
        return 0.015
    sl_pips = abs(entry - sl)
    if sl_pips <= 0:
        return 0.01
    diviseur        = get_diviseur(capital)
    perte_par_trade = capital / diviseur
    lot             = (perte_par_trade * 0.01) / sl_pips
    lot             = math.floor(lot * 100) / 100
    return max(0.01, lot)


def calculate_gains_losses(lot: float, entry: float, sl: float,
                            tp1=None, tp2=None, tp3=None) -> dict:
    def dollars(pips):
        return round((lot / 0.01) * pips, 2)
    sl_pips = abs(entry - sl)
    return {
        "perte_sl": dollars(sl_pips) * -1,
        "gain_tp1": dollars(abs(tp1 - entry)) if tp1 else None,
        "gain_tp2": dollars(abs(tp2 - entry)) if tp2 else None,
        "gain_tp3": dollars(abs(tp3 - entry)) if tp3 else None,
    }


def adjust_entry_sl_to_live_price(direction: str, entry: float, sl: float,
                                    live_price: float | None) -> dict:
    """
    Ajuste entry/sl au prix live SI le prix live offre un meilleur point
    d'entrée que le prix original. Sinon, garde le trade tel qu'envoyé
    à l'origine. Les TP ne sont jamais recalculés ici — seul l'écart en
    pips entre entry et sl est préservé lors de l'ajustement.

    Règle :
      - sell : prix_live > entry → meilleure vente → ajuster
      - buy  : prix_live < entry → meilleur achat  → ajuster
      - sinon (ou prix live indisponible) → garder l'original

    Retourne {"entry": ..., "sl": ..., "adjusted": bool}
    """
    if live_price is None:
        return {"entry": entry, "sl": sl, "adjusted": False}

    sl_pips = round(abs(entry - sl), 2)

    if direction == "sell" and live_price > entry:
        return {"entry": live_price, "sl": round(live_price + sl_pips, 2), "adjusted": True}

    if direction == "buy" and live_price < entry:
        return {"entry": live_price, "sl": round(live_price - sl_pips, 2), "adjusted": True}

    return {"entry": entry, "sl": sl, "adjusted": False}


async def get_tp_level_for_capital(capital: float) -> tuple:
    async with get_db() as cur:
        await cur.execute("""
            SELECT tp_level, risk_pct FROM gold_tp_rules
            WHERE is_active = 1
              AND min_capital <= %s
              AND (max_capital IS NULL OR max_capital >= %s)
            ORDER BY tp_level ASC LIMIT 1
        """, (capital, capital))
        rule = await cur.fetchone()
    if rule:
        return int(rule["tp_level"]), float(rule["risk_pct"])
    if capital < 500:    return 1, 1.0
    elif capital < 2000: return 2, 1.5
    else:                return 3, 2.0


async def get_rule_messages(tp_level: int) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM gold_tp_rules
            WHERE tp_level = %s AND is_active = 1 LIMIT 1
        """, (tp_level,))
        rule = await cur.fetchone()
    return dict(rule) if rule else {}


# ══════════════════════════════════════════════════════════════════════════════
# INIT TABLES
# ══════════════════════════════════════════════════════════════════════════════

async def init_gold_tables():
    async with get_db() as cur:
        await _seed_default_tp_rules(cur)
    print("[gold_engine] Tables Gold v5 initialisées.")


async def _seed_default_tp_rules(cur):
    await cur.execute("SELECT COUNT(*) as n FROM gold_tp_rules")
    existing = (await cur.fetchone())["n"]
    if existing > 0:
        return

    rules = [
        {
            "rule_name": "Petit compte — TP1 seulement",
            "tp_level": 1, "min_capital": 0, "max_capital": 499.99, "risk_pct": 1.0,
            "message_tp1_reached":  "✅ *TP1 atteint sur XAU/USD !*\n\n🎯 Sortez maintenant et sécurisez vos gains.\nC'est votre niveau de sortie — ne soyez pas gourmand 💪",
            "message_tp2_reached":  None, "message_tp3_reached": None,
            "message_sl_touched":   "❌ *SL touché sur XAU/USD*\n\nVotre SL a bien protégé votre compte.\nC'est la discipline qui fait les vrais traders 💪",
            "message_breakeven": None, "message_partial_close": None,
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n \n\n_Cliquez ci-dessous pour accéder au trade._",
            "message_confirmation": "✅ *Trade enregistré !*\nTu recevras les instructions en temps réel.",
        },
        {
            "rule_name": "Compte moyen — TP1 + TP2",
            "tp_level": 2, "min_capital": 500, "max_capital": 1999.99, "risk_pct": 1.5,
            "message_tp1_reached":  "✅ *TP1 atteint sur XAU/USD !*\n\n🔒 Passez en *break even* maintenant.\nDéplacez votre SL au prix d'entrée et laissez courir jusqu'au TP2.",
            "message_tp2_reached":  "🎯 *TP2 atteint sur XAU/USD !*\n\nExcellent ! Fermez maintenant et encaissez vos gains 🎉",
            "message_tp3_reached":  None,
            "message_sl_touched":   "❌ *SL touché sur XAU/USD*\n\nBien géré — votre risque était contrôlé.\nRestez discipliné pour le prochain trade 💪",
            "message_breakeven":    "🔒 Passez en break even — déplacez votre SL au prix d'entrée.",
            "message_partial_close": None,
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n\n_Cliquez ci-dessous pour accéder au trade._",
            "message_confirmation": "✅ *Trade enregistré !*\nObjectif : TP1 + TP2. Tu recevras les instructions en temps réel.",
        },
        {
            "rule_name": "Grand compte — TP1 + TP2 + TP3",
            "tp_level": 3, "min_capital": 2000, "max_capital": None, "risk_pct": 2.0,
            "message_tp1_reached":  "✅ *TP1 atteint sur XAU/USD !*\n\n🔒 Passez en *break even* immédiatement.\nFermez 30% de votre position et laissez courir.",
            "message_tp2_reached":  "🎯 *TP2 atteint sur XAU/USD !*\n\nFermez encore 40% de votre position.\nLaissez les 30% restants courir vers TP3 🚀",
            "message_tp3_reached":  "🏆 *TP3 atteint sur XAU/USD !*\n\nTrade parfait ! Fermez tout et savourez 🎉\nC'est exactement comme ça qu'on trade.",
            "message_sl_touched":   "❌ *SL touché sur XAU/USD*\n\nBien géré — votre risque était contrôlé.\nRestez discipliné pour le prochain trade 💪",
            "message_breakeven":    "🔒 Break even — déplacez votre SL au prix d'entrée et fermez 30%.",
            "message_partial_close": "⚡ Clôture partielle — fermez 40% de votre position maintenant.",
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n\n_Cliquez ci-dessous pour accéder au trade._",
            "message_confirmation": "✅ *Trade enregistré !*\nObjectif : TP1 + TP2 + TP3. Tu recevras les instructions en temps réel.",
        },
    ]

    for r in rules:
        await cur.execute("""
            INSERT IGNORE INTO gold_tp_rules
                (rule_name, tp_level, min_capital, max_capital, risk_pct,
                 message_tp1_reached, message_tp2_reached, message_tp3_reached,
                 message_sl_touched, message_breakeven, message_partial_close,
                 message_teaser, message_confirmation)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            r["rule_name"], r["tp_level"], r["min_capital"], r["max_capital"], r["risk_pct"],
            r["message_tp1_reached"], r["message_tp2_reached"], r["message_tp3_reached"],
            r["message_sl_touched"], r["message_breakeven"], r["message_partial_close"],
            r["message_teaser"], r["message_confirmation"],
        ))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — SAISONS
# ══════════════════════════════════════════════════════════════════════════════

async def create_season(payload: dict) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_seasons SET status = 'closed', closed_at = NOW()
            WHERE status = 'active'
        """)
        await cur.execute("""
            INSERT INTO gold_seasons
                (name, description, start_date, initial_capital_ref, status)
            VALUES (%s, %s, %s, %s, 'active')
        """, (
            payload["name"], payload.get("description"),
            payload.get("start_date", _now()),
            payload.get("initial_capital_ref"),
        ))
        season_id = cur.lastrowid
        await cur.execute("SELECT * FROM gold_seasons WHERE id = %s", (season_id,))
        season = dict(await cur.fetchone())
    return season


async def get_active_season() -> dict | None:
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM gold_seasons WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_seasons(include_closed: bool = True) -> list:
    async with get_db() as cur:
        where = "" if include_closed else "WHERE s.status = 'active'"
        await cur.execute(f"""
            SELECT s.*,
                   COUNT(DISTINCT gts.id) AS trades_count,
                   SUM(CASE WHEN gts.current_phase IN
                       ('tp1_reached','tp2_reached','tp3_reached') THEN 1 ELSE 0 END) AS wins_count,
                   SUM(CASE WHEN gts.current_phase = 'sl_touched' THEN 1 ELSE 0 END) AS losses_count,
                   COUNT(DISTINCT gme.user_id) AS members_participated
            FROM gold_seasons s
            LEFT JOIN gold_trade_sessions gts ON gts.season_id = s.id
            LEFT JOIN gold_member_entries gme ON gme.season_id = s.id
            {where}
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def reset_season(season_id: int, payload: dict) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_seasons
            SET status = 'reset', closed_at = NOW(), end_date = NOW()
            WHERE id = %s
        """, (season_id,))
        await cur.execute(
            "SELECT id, initial_capital FROM simulation_accounts WHERE season_id = %s AND is_active = 1",
            (season_id,)
        )
        sim_accounts = await cur.fetchall()
        for acc in sim_accounts:
            await cur.execute("""
                UPDATE simulation_accounts
                SET current_capital  = initial_capital,
                    total_trades     = 0, wins = 0, losses = 0,
                    max_drawdown_pct = 0, peak_capital = initial_capital,
                    updated_at       = NOW()
                WHERE id = %s
            """, (acc["id"],))

    new_season = await create_season({
        "name": payload["new_season_name"],
        "initial_capital_ref": payload.get("new_initial_capital"),
    })

    async with get_db() as cur:
        await cur.execute("""
            UPDATE simulation_accounts SET season_id = %s, updated_at = NOW()
            WHERE is_active = 1
        """, (new_season["id"],))

    return {
        "archived_season_id": season_id,
        "new_season":         new_season,
        "accounts_reset":     len(sim_accounts),
    }


async def get_season_stats(season_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_seasons WHERE id = %s", (season_id,))
        season = await cur.fetchone()
        if not season:
            return {"error": "Saison introuvable"}
        season = dict(season)

        await cur.execute("""
            SELECT
                COUNT(*) AS total_trades,
                COUNT(CASE WHEN current_phase IN ('tp1_reached','tp2_reached','tp3_reached') THEN 1 END) AS wins,
                COUNT(CASE WHEN current_phase = 'sl_touched' THEN 1 END) AS losses,
                AVG(total_members_in)  AS avg_members_per_trade,
                SUM(total_members_in)  AS total_confirmations
            FROM gold_trade_sessions WHERE season_id = %s
        """, (season_id,))
        session_stats = await cur.fetchone()

        await cur.execute("""
            SELECT
                COUNT(DISTINCT user_id)                    AS unique_members,
                ROUND(SUM(result_usd), 2)                  AS total_gains_members,
                ROUND(AVG(result_usd), 2)                  AS avg_gain_per_trade,
                COUNT(CASE WHEN result_usd > 0 THEN 1 END) AS member_wins,
                COUNT(CASE WHEN result_usd < 0 THEN 1 END) AS member_losses
            FROM gold_member_entries WHERE season_id = %s
        """, (season_id,))
        member_stats = await cur.fetchone()

        await cur.execute("""
            SELECT sa.*,
                   ROUND((sa.current_capital - sa.initial_capital)
                         / sa.initial_capital * 100, 2) AS rendement_pct
            FROM simulation_accounts sa WHERE sa.season_id = %s
            ORDER BY sa.initial_capital ASC
        """, (season_id,))
        sim_accounts = await cur.fetchall()

        await cur.execute("""
            SELECT u.name, gme.user_id,
                   COUNT(*) AS trades,
                   ROUND(SUM(gme.result_usd), 2) AS total_usd
            FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.season_id = %s
            GROUP BY gme.user_id ORDER BY total_usd DESC LIMIT 10
        """, (season_id,))
        top_members = await cur.fetchall()

    return {
        "season":              season,
        "session_stats":       dict(session_stats) if session_stats else {},
        "member_stats":        dict(member_stats)  if member_stats  else {},
        "simulation_accounts": [dict(a) for a in sim_accounts],
        "top_members":         [dict(m) for m in top_members],
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — SESSIONS DE TRADE GOLD
# ══════════════════════════════════════════════════════════════════════════════

async def create_gold_session(payload: dict) -> dict:
    entry     = float(payload["entry_price"])
    sl        = float(payload["sl"])
    tp1       = float(payload["tp1"]) if payload.get("tp1") else None
    tp2       = float(payload["tp2"]) if payload.get("tp2") else None
    tp3       = float(payload["tp3"]) if payload.get("tp3") else None
    direction = payload["direction"]

    if direction not in ("buy", "sell"):
        raise ValueError("direction doit être 'buy' ou 'sell'")

    def _pips(a, b): return round(abs(a - b), 2)

    sl_pips  = _pips(entry, sl)
    tp1_pips = _pips(entry, tp1) if tp1 else None
    tp2_pips = _pips(entry, tp2) if tp2 else None
    tp3_pips = _pips(entry, tp3) if tp3 else None

    season    = await get_active_season()
    season_id = season["id"] if season else None

    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO gold_trade_sessions
                (signal_id, season_id, direction, entry_price,
                 tp1, tp2, tp3, sl, sl_pips, tp1_pips, tp2_pips, tp3_pips,
                 timeframe, confidence_level, note, screenshot_url,
                 current_phase, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'teaser',NOW())
        """, (
            payload.get("signal_id"), season_id, direction, entry,
            tp1, tp2, tp3, sl, sl_pips, tp1_pips, tp2_pips, tp3_pips,
            payload.get("timeframe", "M15"),
            int(payload.get("confidence_level", 3)),
            payload.get("note"), payload.get("screenshot_url"),
        ))
        session_id = cur.lastrowid
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = dict(await cur.fetchone())
    return session


async def get_active_gold_session() -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT gts.*, gs.name AS season_name
            FROM gold_trade_sessions gts
            LEFT JOIN gold_seasons gs ON gs.id = gts.season_id
            WHERE gts.current_phase IN ('teaser', 'open')
            ORDER BY gts.created_at DESC LIMIT 1
        """)
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_gold_session_detail(session_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        if not session:
            return None
        session = dict(session)

        await cur.execute("""
            SELECT gme.*, u.name FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.session_id = %s ORDER BY gme.confirmed_at ASC
        """, (session_id,))
        session["entries"] = [dict(e) for e in await cur.fetchall()]

        await cur.execute("""
            SELECT tp_level_assigned,
                   COUNT(*) AS members,
                   ROUND(SUM(lot_calculated), 4) AS total_lots,
                   ROUND(SUM(ABS(perte_sl)), 2)   AS total_risk,
                   ROUND(SUM(gain_tp1), 2)         AS total_gain_tp1,
                   ROUND(SUM(COALESCE(gain_tp2,0)), 2) AS total_gain_tp2,
                   ROUND(SUM(COALESCE(gain_tp3,0)), 2) AS total_gain_tp3
            FROM gold_member_entries WHERE session_id = %s
            GROUP BY tp_level_assigned ORDER BY tp_level_assigned
        """, (session_id,))
        session["tp_distribution"] = [dict(d) for d in await cur.fetchall()]

        await cur.execute("""
            SELECT st.*, sa.name AS account_name, sa.initial_capital
            FROM simulation_trades st
            JOIN simulation_accounts sa ON sa.id = st.account_id
            WHERE st.session_id = %s ORDER BY sa.initial_capital ASC
        """, (session_id,))
        session["simulation_trades"] = [dict(s) for s in await cur.fetchall()]

    return session


async def get_gold_sessions(filters: dict = None) -> dict:
    f      = filters or {}
    limit  = int(f.get("limit", 20))
    offset = int(f.get("offset", 0))
    where  = ["1=1"]
    params = []

    if f.get("season_id"):
        where.append("gts.season_id = %s")
        params.append(f["season_id"])
    if f.get("phase"):
        where.append("gts.current_phase = %s")
        params.append(f["phase"])

    where_sql = " AND ".join(where)
    async with get_db() as cur:
        await cur.execute(f"""
            SELECT gts.*, gs.name AS season_name,
                   COUNT(DISTINCT gme.user_id) AS confirmed_members
            FROM gold_trade_sessions gts
            LEFT JOIN gold_seasons gs ON gs.id = gts.season_id
            LEFT JOIN gold_member_entries gme ON gme.session_id = gts.id
            WHERE {where_sql}
            GROUP BY gts.id
            ORDER BY gts.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = await cur.fetchall()

        await cur.execute(
            f"SELECT COUNT(*) as n FROM gold_trade_sessions gts WHERE {where_sql}", params
        )
        total = (await cur.fetchone())["n"]

    return {"sessions": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — SESSION UTILISATEUR
# ══════════════════════════════════════════════════════════════════════════════

async def save_user_step(session_id: int, user_id: int, step: str, capital: float = None):
    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO gold_user_sessions
                    (session_id, user_id, step, capital_input, updated_at)
                VALUES (%s, %s, %s, %s, NOW()) AS new_vals
                ON DUPLICATE KEY UPDATE
                    step          = new_vals.step,
                    capital_input = new_vals.capital_input,
                    updated_at    = new_vals.updated_at
            """, (session_id, user_id, step, capital))


async def get_user_step(session_id: int, user_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM gold_user_sessions
            WHERE session_id = %s AND user_id = %s
        """, (session_id, user_id))
        row = await cur.fetchone()
    return dict(row) if row else None


async def restore_user_context(user_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT gus.*, gts.entry_price, gts.direction, gts.current_phase,
                   gts.tp1, gts.tp2, gts.tp3, gts.sl
            FROM gold_user_sessions gus
            JOIN gold_trade_sessions gts ON gts.id = gus.session_id
            WHERE gus.user_id = %s
              AND gts.current_phase NOT IN ('closed','cancelled','sl_touched')
              AND gus.step NOT IN ('confirmed','cancelled')
            ORDER BY gus.updated_at DESC LIMIT 1
        """, (user_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CONFIRMATION MEMBRE
# ══════════════════════════════════════════════════════════════════════════════

async def confirm_gold_entry(session_id: int, user_id: int, capital: float,
                               override_entry: float | None = None,
                               override_sl: float | None = None) -> dict:
    async with _confirm_semaphore:
        # 1. Lecture rapide — une seule requête DB
        async with get_db() as cur:
            await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
            session = await cur.fetchone()
            if not session:
                return {"error": "Session introuvable"}
            session = dict(session)

        if session["current_phase"] not in ("teaser", "open"):
            return {"error": "Ce trade n'est plus ouvert aux participations"}

        # Utilise les valeurs ajustées (meilleur point d'entrée selon le
        # prix live au moment où l'utilisateur a vu le détail du trade)
        # si elles ont été fournies, sinon retombe sur les valeurs de
        # session d'origine.
        effective_entry = override_entry if override_entry is not None else session["entry_price"]
        effective_sl    = override_sl    if override_sl    is not None else session["sl"]

        # 2. Calcul Python pur — instantané, aucune DB
        #
        # La perte SL est calculée avec l'entry/sl EFFECTIFS (ajustés au prix
        # live si meilleur point d'entrée) — l'écart en pips reste celui prévu
        # à l'origine, donc le risque réel de l'utilisateur est cohérent.
        #
        # Les gains TP restent calculés depuis l'ENTRY ORIGINAL de la session,
        # PAS l'entry ajusté — l'utilisateur garde le gain annoncé au teaser,
        # peu importe son point d'entrée réel.
        original_entry = session["entry_price"]

        lot = calculate_lot(capital, effective_entry, effective_sl)

        risk_gains = calculate_gains_losses(lot=lot, entry=effective_entry, sl=effective_sl)
        perte_sl   = risk_gains["perte_sl"]

        tp_gains = calculate_gains_losses(lot=lot, entry=original_entry, sl=effective_sl,
                                           tp1=session.get("tp1"), tp2=session.get("tp2"), tp3=session.get("tp3"))
        gain_tp1 = tp_gains["gain_tp1"]
        gain_tp2 = tp_gains["gain_tp2"]
        gain_tp3 = tp_gains["gain_tp3"]

        tp_level, risk_pct = await get_tp_level_for_capital(capital)
        risk_usd = round(capital * risk_pct / 100, 2)

        # 3. Message prêt à renvoyer — disponible immédiatement
        tp_labels = {1: "TP1", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
        dir_label = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"
        lines = [
            "✅ *Trade confirmé — XAU/USD*", "",
            f"💼 Lot recommandé : *{lot}*",
            f"🎯 Objectif : *{tp_labels[tp_level]}*",
            f"📈 Direction : *{dir_label}*", "",
            "📊 *Scénarios :*",
            f"❌ Si SL touché → *{perte_sl}$*",
            f"✅ Si TP1 touché → *+{gain_tp1}$*",
        ]
        if gain_tp2: lines.append(f"🎯 Si TP2 touché → *+{gain_tp2}$*")
        if gain_tp3: lines.append(f"🏆 Si TP3 touché → *+{gain_tp3}$*")
        lines += ["", "_Tu recevras les instructions en temps réel._"]
        message = "\n".join(lines)

        # 4. Empile la persistance — ne bloque PAS la réponse à l'utilisateur
        await enqueue_write(
            "confirm_gold_entry",
            _persist_gold_entry,
            session_id, user_id, session, capital,
            risk_pct, risk_usd, lot, tp_level,
            perte_sl, gain_tp1, gain_tp2, gain_tp3,
        )

        # 5. Retourne immédiatement — l'utilisateur n'attend aucune écriture DB
        return {
            "entry": {"session_id": session_id, "user_id": user_id, "capital": capital,
                      "lot": lot, "tp_level": tp_level, "perte_sl": perte_sl,
                      "gain_tp1": gain_tp1, "gain_tp2": gain_tp2, "gain_tp3": gain_tp3},
            "message": message,
        }


async def _persist_gold_entry(session_id, user_id, session, capital,
                                risk_pct, risk_usd, lot, tp_level,
                                perte_sl, gain_tp1, gain_tp2, gain_tp3):
    """
    Exécutée par le worker de gold_write_queue, jamais directement par
    un handler Telegram. Si elle lève une exception, l'admin est alerté
    automatiquement par le worker — pas besoin de try/except ici.
    """
    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO gold_member_entries
                    (session_id, user_id, season_id, capital_declared, risk_pct,
                     risk_usd, lot_calculated, tp_level_assigned,
                     perte_sl, gain_tp1, gain_tp2, gain_tp3,
                     capital_before, step_reached, confirmed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',NOW()) AS new_vals
                ON DUPLICATE KEY UPDATE
                    capital_declared  = new_vals.capital_declared,
                    risk_pct          = new_vals.risk_pct,
                    risk_usd          = new_vals.risk_usd,
                    lot_calculated    = new_vals.lot_calculated,
                    tp_level_assigned = new_vals.tp_level_assigned,
                    perte_sl          = new_vals.perte_sl,
                    gain_tp1          = new_vals.gain_tp1,
                    gain_tp2          = new_vals.gain_tp2,
                    gain_tp3          = new_vals.gain_tp3,
                    capital_before    = new_vals.capital_before,
                    confirmed_at      = new_vals.confirmed_at
            """, (session_id, user_id, session["season_id"],
                  capital, risk_pct, risk_usd, lot, tp_level,
                  perte_sl, gain_tp1, gain_tp2, gain_tp3, capital))

            if session["current_phase"] == "teaser":
                await cur.execute("""
                    UPDATE gold_trade_sessions
                    SET current_phase = 'open', opened_at = NOW()
                    WHERE id = %s
                """, (session_id,))

            await cur.execute("""
                SELECT
                    COUNT(*) AS total_members,
                    ROUND(SUM(lot_calculated), 4)          AS total_lots,
                    ROUND(SUM(ABS(perte_sl)), 2)            AS total_loss_sl,
                    ROUND(SUM(gain_tp1), 2)                 AS total_gain_tp1,
                    ROUND(SUM(COALESCE(gain_tp2,0)), 2)     AS total_gain_tp2,
                    ROUND(SUM(COALESCE(gain_tp3,0)), 2)     AS total_gain_tp3
                FROM gold_member_entries WHERE session_id = %s
            """, (session_id,))
            agg = await cur.fetchone()

            await cur.execute("""
                UPDATE gold_trade_sessions SET
                    total_members_in      = %s,
                    total_lots_engaged    = %s,
                    estimated_loss_sl     = %s,
                    estimated_gain_tp1    = %s,
                    estimated_gain_tp2    = %s,
                    estimated_gain_tp3    = %s,
                    aggregates_updated_at = NOW()
                WHERE id = %s
            """, (agg["total_members"] or 0, agg["total_lots"] or 0,
                  agg["total_loss_sl"] or 0, agg["total_gain_tp1"] or 0,
                  agg["total_gain_tp2"] or 0, agg["total_gain_tp3"] or 0,
                  session_id))

    await save_user_step(session_id, user_id, "confirmed", capital)
    await _log_flow_event(session_id, user_id, "confirmed", {"capital": capital, "lot": lot, "tp_level": tp_level})
    await _apply_to_simulation_accounts(session_id, session)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — GESTION TP / SL
# ══════════════════════════════════════════════════════════════════════════════

async def trigger_tp_reached(session_id: int, tp_level: int) -> dict:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        if not session:
            return {"error": "Session introuvable"}
        session = dict(session)

        await cur.execute("""
            SELECT gme.user_id, gme.tp_level_assigned,
                   gme.gain_tp1, gme.gain_tp2, gme.gain_tp3
            FROM gold_member_entries gme
            WHERE gme.session_id = %s AND gme.step_reached = 'confirmed'
        """, (session_id,))
        entries = await cur.fetchall()

        phase_map = {1: "tp1_reached", 2: "tp2_reached", 3: "tp3_reached"}
        new_phase = phase_map[tp_level]
        tp_field  = f"tp{tp_level}_reached_at"
        await cur.execute(f"""
            UPDATE gold_trade_sessions
            SET current_phase = %s, {tp_field} = NOW()
            WHERE id = %s
        """, (new_phase, session_id))

    sent_exit = sent_continue = errors = 0
    for entry in entries:
        entry     = dict(entry)
        rule_msgs = await get_rule_messages(entry["tp_level_assigned"])
        message   = rule_msgs.get(f"message_tp{tp_level}_reached")
        if not message:
            continue
        gain = {1: entry["gain_tp1"], 2: entry["gain_tp2"], 3: entry["gain_tp3"]}.get(tp_level)
        if gain:
            message += f"\n\n💰 *Ton gain estimé : +{gain}$*"
        try:
            if _bot:
                await _bot.send_message(chat_id=entry["user_id"], text=message, parse_mode="Markdown")
                await asyncio.sleep(0.05)
            await _log_flow_event(session_id, entry["user_id"], f"tp{tp_level}_notified", {"gain": gain})
            sent_exit     += 1 if entry["tp_level_assigned"] == tp_level else 0
            sent_continue += 1 if entry["tp_level_assigned"] != tp_level else 0
        except Exception as e:
            logger.warning(f"[trigger_tp] uid={entry['user_id']}: {e}")
            errors += 1

    return {"session_id": session_id, "tp_level": tp_level, "sent_exit": sent_exit,
            "sent_continue": sent_continue, "errors": errors, "new_phase": new_phase}


async def trigger_sl_touched(session_id: int) -> dict:
    """
    Marque la session et toutes les entrées membres comme clôturées en SL.
    NE NOTIFIE PLUS AUCUN USER — seul l'admin reçoit la notification de
    clôture via _notify_admin_session_closed, appelée plus bas.
    """
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        if not session:
            return {"error": "Session introuvable"}
        session = dict(session)

        await cur.execute("""
            SELECT gme.*, u.name FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.session_id = %s AND gme.step_reached = 'confirmed'
        """, (session_id,))
        entries = await cur.fetchall()

    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                UPDATE gold_trade_sessions
                SET current_phase = 'sl_touched', sl_touched_at = NOW(), closed_at = NOW()
                WHERE id = %s
            """, (session_id,))
            await cur.execute("""
                UPDATE gold_member_entries
                SET result_usd    = perte_sl,
                    capital_after = capital_before + perte_sl,
                    exit_tp_level = NULL,
                    exited_at     = NOW()
                WHERE session_id = %s
            """, (session_id,))
            await cur.execute("""
                UPDATE simulation_trades
                SET result_usd    = perte_sl,
                    capital_after = capital_before + perte_sl,
                    status        = 'closed',
                    closed_at     = NOW()
                WHERE session_id = %s
            """, (session_id,))

    notified = len(entries)

    await _close_simulation_trades(session_id, "sl")
    await _notify_admin_session_closed(session_id, "sl", notified)
    return {"session_id": session_id, "phase": "sl_touched", "notified": notified}


async def close_gold_session(session_id: int, payload: dict) -> dict:
    close_type = payload["close_type"]
    if close_type == "sl":
        return await trigger_sl_touched(session_id)

    tp_map = {"tp1": 1, "tp2": 2, "tp3": 3}
    if close_type not in tp_map:
        return {"error": "close_type invalide"}

    tp_num = tp_map[close_type]
    result = await trigger_tp_reached(session_id, tp_num)

    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                UPDATE gold_trade_sessions
                SET current_phase = 'closed', closed_at = NOW()
                WHERE id = %s
            """, (session_id,))
            await cur.execute(f"""
                UPDATE gold_member_entries
                SET result_usd = CASE
                        WHEN tp_level_assigned >= {tp_num} AND gain_tp{tp_num} IS NOT NULL THEN gain_tp{tp_num}
                        WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 AND gain_tp2 IS NOT NULL THEN gain_tp2
                        ELSE gain_tp1
                    END,
                    exit_tp_level = CASE
                        WHEN tp_level_assigned >= {tp_num} THEN {tp_num}
                        WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 THEN 2
                        ELSE 1
                    END,
                    capital_after = capital_before + CASE
                        WHEN tp_level_assigned >= {tp_num} AND gain_tp{tp_num} IS NOT NULL THEN gain_tp{tp_num}
                        WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 AND gain_tp2 IS NOT NULL THEN gain_tp2
                        ELSE gain_tp1
                    END,
                    exited_at = NOW()
                WHERE session_id = %s
            """, (session_id,))

    await _close_simulation_trades(session_id, close_type)
    await _notify_admin_session_closed(session_id, close_type, result.get("sent_exit", 0))
    return {**result, "phase": "closed"}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — PRIX LIVE & SURVEILLANCE
# ══════════════════════════════════════════════════════════════════════════════

TWELVE_DATA_KEY = "db6836eaf4ae4cb68faea2443554929f"
_price_cache: dict = {"price": None, "ts": 0.0}


def _watch_interval() -> int:
    h = datetime.now().hour
    if 8 <= h < 20:  return 120
    elif h < 8:      return 1800
    else:            return 300


async def get_live_gold_price() -> float | None:
    ttl = _watch_interval()
    if _price_cache["price"] and _time.time() - _price_cache["ts"] < ttl:
        return _price_cache["price"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.twelvedata.com/price",
                                     params={"symbol": "XAU/USD", "apikey": TWELVE_DATA_KEY})
            data = resp.json()
            if "price" in data:
                _price_cache["price"] = float(data["price"])
                _price_cache["ts"]    = _time.time()
                return _price_cache["price"]
            logger.warning(f"[gold_price] {data.get('message', data)}")
            return _price_cache["price"]
    except Exception as e:
        logger.warning(f"[gold_price] {e}")
        return _price_cache["price"]


async def watch_gold_price(session_id: int):
    logger.info(f"[gold_watch] Démarrage session {session_id}")
    while True:
        try:
            async with get_db() as cur:
                await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
                session = await cur.fetchone()

            if not session:
                break
            session = dict(session)
            phase   = session["current_phase"]

            if phase in ("closed", "cancelled", "sl_touched"):
                break
            if phase == "tp3_reached":
                await close_gold_session(session_id, {"close_type": "tp3"})
                break

            price    = await get_live_gold_price()
            interval = _watch_interval()

            if price is None:
                await asyncio.sleep(interval)
                continue

            async with get_db() as cur:
                await cur.execute("""
                    UPDATE gold_trade_sessions
                    SET live_price_last = %s, live_price_updated_at = NOW()
                    WHERE id = %s
                """, (price, session_id))

            direction     = session["direction"]
            tp1, tp2, tp3 = session.get("tp1"), session.get("tp2"), session.get("tp3")
            sl            = session["sl"]

            if (direction == "buy" and price <= sl) or (direction == "sell" and price >= sl):
                await trigger_sl_touched(session_id)
                break
            if tp3 and phase not in ("tp3_reached", "closed"):
                if (direction == "buy" and price >= tp3) or (direction == "sell" and price <= tp3):
                    await close_gold_session(session_id, {"close_type": "tp3"})
                    break
            if tp2 and phase not in ("tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy" and price >= tp2) or (direction == "sell" and price <= tp2):
                    await trigger_tp_reached(session_id, 2)
                    await asyncio.sleep(interval)
                    continue
            if tp1 and phase not in ("tp1_reached", "tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy" and price >= tp1) or (direction == "sell" and price <= tp1):
                    await trigger_tp_reached(session_id, 1)

        except Exception as e:
            logger.error(f"[gold_watch] Session {session_id}: {e}")

        await asyncio.sleep(_watch_interval())


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — COMPTES SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

async def create_simulation_account(payload: dict) -> dict:
    season    = await get_active_season()
    season_id = season["id"] if season else None
    capital   = float(payload["initial_capital"])

    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO simulation_accounts
                    (name, description, initial_capital, current_capital,
                     risk_pct_default, peak_capital, season_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            """, (payload["name"], payload.get("description"), capital, capital,
                  float(payload.get("risk_pct_default", 1.0)), capital, season_id))
            account_id = cur.lastrowid
            await cur.execute("SELECT * FROM simulation_accounts WHERE id = %s", (account_id,))
            account = dict(await cur.fetchone())
    return account


async def get_simulation_accounts(active_only: bool = True) -> list:
    async with get_db() as cur:
        where = "WHERE sa.is_active = 1" if active_only else ""
        await cur.execute(f"""
            SELECT sa.*, gs.name AS season_name,
                   COUNT(st.id) AS total_trades_count,
                   ROUND((sa.current_capital - sa.initial_capital)
                         / sa.initial_capital * 100, 2) AS rendement_pct
            FROM simulation_accounts sa
            LEFT JOIN gold_seasons gs      ON gs.id = sa.season_id
            LEFT JOIN simulation_trades st ON st.account_id = sa.id
            {where}
            GROUP BY sa.id
            ORDER BY sa.initial_capital ASC
        """)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_simulation_account_detail(account_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM simulation_accounts WHERE id = %s", (account_id,))
        account = await cur.fetchone()
        if not account:
            return None
        account = dict(account)

        await cur.execute("""
            SELECT st.*, gts.current_phase FROM simulation_trades st
            JOIN gold_trade_sessions gts ON gts.id = st.session_id
            WHERE st.account_id = %s ORDER BY st.opened_at ASC
        """, (account_id,))
        trades = await cur.fetchall()
        account["trades"] = [dict(t) for t in trades]

        capital_curve = []
        cap = account["initial_capital"]
        for t in account["trades"]:
            t = dict(t)
            if t["result_usd"] is not None:
                cap += t["result_usd"]
            capital_curve.append({"capital": round(cap, 2), "result_usd": t["result_usd"],
                                   "date": t["closed_at"] or t["opened_at"]})
        account["capital_curve"]  = capital_curve
        account["rendement_pct"]  = round(
            (account["current_capital"] - account["initial_capital"])
            / account["initial_capital"] * 100, 2
        ) if account["initial_capital"] > 0 else 0
    return account


async def _apply_to_simulation_accounts(session_id: int, session: dict):
    async with get_db() as cur:
        await cur.execute(
            "SELECT COUNT(*) as n FROM simulation_trades WHERE session_id = %s", (session_id,)
        )
        existing = (await cur.fetchone())["n"]
        if existing > 0:
            return
        await cur.execute("SELECT * FROM simulation_accounts WHERE is_active = 1")
        accounts = await cur.fetchall()

    rows_to_insert = []
    for acc in accounts:
        acc     = dict(acc)
        capital = acc["current_capital"]
        lot     = calculate_lot(capital, session["entry_price"], session["sl"])
        gains   = calculate_gains_losses(lot=lot, entry=session["entry_price"], sl=session["sl"],
                                          tp1=session.get("tp1"), tp2=session.get("tp2"), tp3=session.get("tp3"))
        tp_level, risk_pct = await get_tp_level_for_capital(capital)
        risk_usd           = round(capital * risk_pct / 100, 2)
        rows_to_insert.append((
            acc["id"], session_id, session.get("season_id"),
            session["entry_price"], session.get("tp1"), session.get("tp2"), session.get("tp3"),
            session["sl"], session["direction"], capital, risk_pct, risk_usd, lot, tp_level,
            gains["perte_sl"], gains["gain_tp1"] or 0, gains["gain_tp2"], gains["gain_tp3"],
        ))

    if not rows_to_insert:
        return

    async with _db_write_lock:
        async with get_db() as cur:
            for row in rows_to_insert:
                await cur.execute("""
                    INSERT IGNORE INTO simulation_trades
                        (account_id, session_id, season_id,
                         entry_price, tp1, tp2, tp3, sl, direction,
                         capital_before, risk_pct, risk_usd, lot_used, tp_level_target,
                         perte_sl, gain_tp1, gain_tp2, gain_tp3, opened_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """, row)


async def _close_simulation_trades(session_id: int, close_type: str):
    async with get_db() as cur:
        await cur.execute("""
            SELECT st.*, sa.current_capital, sa.id AS acc_id, sa.peak_capital
            FROM simulation_trades st
            JOIN simulation_accounts sa ON sa.id = st.account_id
            WHERE st.session_id = %s AND st.status = 'open'
        """, (session_id,))
        trades = await cur.fetchall()

    if not trades:
        return

    async with _db_write_lock:
        async with get_db() as cur:
            for t in trades:
                t = dict(t)
                if close_type == "sl":
                    result_usd = t["perte_sl"]; exit_tp = None
                elif close_type == "tp1":
                    result_usd = t["gain_tp1"]; exit_tp = 1
                elif close_type == "tp2":
                    result_usd = t["gain_tp2"] if t["tp_level_target"] >= 2 else t["gain_tp1"]
                    exit_tp    = 2 if t["tp_level_target"] >= 2 else 1
                elif close_type == "tp3":
                    result_usd = t["gain_tp3"] if t["tp_level_target"] >= 3 else (
                        t["gain_tp2"] if t["tp_level_target"] >= 2 else t["gain_tp1"])
                    exit_tp = t["tp_level_target"]
                else:
                    result_usd = 0; exit_tp = None

                result_usd  = result_usd or 0
                new_capital = round(t["capital_before"] + result_usd, 2)
                peak        = max(t["peak_capital"] or t["capital_before"], new_capital)
                drawdown    = round((peak - new_capital) / peak * 100, 2) if peak > 0 else 0
                is_win      = result_usd > 0

                await cur.execute("""
                    UPDATE simulation_trades
                    SET result_usd = %s, capital_after = %s, exit_tp_level = %s,
                        status = 'closed', closed_at = NOW()
                    WHERE id = %s
                """, (result_usd, new_capital, exit_tp, t["id"]))
                await cur.execute("""
                    UPDATE simulation_accounts SET
                        current_capital  = %s,
                        total_trades     = total_trades + 1,
                        wins             = wins + %s,
                        losses           = losses + %s,
                        peak_capital     = %s,
                        max_drawdown_pct = GREATEST(max_drawdown_pct, %s),
                        updated_at       = NOW()
                    WHERE id = %s
                """, (new_capital, 1 if is_win else 0, 0 if is_win else 1,
                      peak, drawdown, t["acc_id"]))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — ALERTES
# ══════════════════════════════════════════════════════════════════════════════

async def check_cramed_accounts(session_id: int = None) -> dict:
    cramed_risk = []; already_cramed = []; simulation_danger = []
    async with get_db() as cur:
        if session_id:
            await cur.execute("""
                SELECT gme.user_id, gme.capital_declared, gme.perte_sl, u.name
                FROM gold_member_entries gme
                LEFT JOIN users u ON u.telegram_id = gme.user_id
                WHERE gme.session_id = %s AND gme.step_reached = 'confirmed'
            """, (session_id,))
            entries = await cur.fetchall()
            for e in entries:
                e = dict(e)
                capital = e["capital_declared"]
                perte   = abs(e["perte_sl"] or 0)
                apres   = capital - perte
                if apres <= 0:
                    already_cramed.append({"user_id": e["user_id"], "name": e["name"],
                                           "capital": capital, "perte_sl": -perte, "capital_restant": apres})
                elif apres < capital * 0.3:
                    cramed_risk.append({"user_id": e["user_id"], "name": e["name"],
                                        "capital": capital, "perte_sl": -perte,
                                        "capital_restant": round(apres, 2),
                                        "pct_restant": round(apres / capital * 100, 1)})

            await cur.execute("""
                SELECT st.perte_sl, st.capital_before, sa.name AS account_name
                FROM simulation_trades st
                JOIN simulation_accounts sa ON sa.id = st.account_id
                WHERE st.session_id = %s AND st.status = 'open'
            """, (session_id,))
            sims = await cur.fetchall()
            for s in sims:
                s = dict(s)
                apres = s["capital_before"] + (s["perte_sl"] or 0)
                if apres < s["capital_before"] * 0.3:
                    simulation_danger.append({"account_name": s["account_name"],
                                              "capital": s["capital_before"],
                                              "perte_sl": s["perte_sl"],
                                              "capital_restant": round(apres, 2)})

    total_danger = len(cramed_risk) + len(already_cramed)
    if _bot and (cramed_risk or already_cramed or simulation_danger):
        lines = ["⚠️ *Alerte comptes en danger*\n"]
        if already_cramed:
            lines.append(f"🔴 *{len(already_cramed)} compte(s) qui se crament si SL :*")
            for c in already_cramed:
                lines.append(f"  • {c['name']} — {c['capital']}$ → *{c['capital_restant']}$*")
        if cramed_risk:
            lines.append(f"\n🟡 *{len(cramed_risk)} compte(s) à risque (<30% restant) :*")
            for c in cramed_risk:
                lines.append(f"  • {c['name']} — {c['capital']}$ → {c['capital_restant']}$ ({c['pct_restant']}%)")
        if simulation_danger:
            lines.append(f"\n📊 *{len(simulation_danger)} compte(s) simulation en danger :*")
            for s in simulation_danger:
                lines.append(f"  • {s['account_name']} — {s['capital']}$ → {s['capital_restant']}$")
        try:
            await _bot.send_message(chat_id=ADMIN_ID, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"[cramed] {e}")

    return {"total_danger": total_danger, "cramed_risk": cramed_risk,
            "already_cramed": already_cramed, "simulation_danger": simulation_danger}


async def daily_cramed_check():
    async with get_db() as cur:
        await cur.execute(
            "SELECT id FROM gold_trade_sessions WHERE current_phase = 'open'"
        )
        open_sessions = await cur.fetchall()
    results = []
    for s in open_sessions:
        result = await check_cramed_accounts(s["id"])
        results.append({"session_id": s["id"], **result})
    return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — RÈGLES TP (CRUD)
# ══════════════════════════════════════════════════════════════════════════════

async def get_tp_rules() -> list:
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM gold_tp_rules ORDER BY tp_level ASC, min_capital ASC"
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def create_tp_rule(payload: dict) -> dict:
    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO gold_tp_rules
                    (rule_name, tp_level, min_capital, max_capital, risk_pct,
                     message_tp1_reached, message_tp2_reached, message_tp3_reached,
                     message_sl_touched, message_breakeven, message_partial_close,
                     message_teaser, message_confirmation)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                payload["rule_name"], int(payload["tp_level"]),
                float(payload["min_capital"]),
                float(payload["max_capital"]) if payload.get("max_capital") else None,
                float(payload["risk_pct"]),
                payload.get("message_tp1_reached"), payload.get("message_tp2_reached"),
                payload.get("message_tp3_reached"), payload.get("message_sl_touched"),
                payload.get("message_breakeven"), payload.get("message_partial_close"),
                payload.get("message_teaser"), payload.get("message_confirmation"),
            ))
            rule_id = cur.lastrowid
            await cur.execute("SELECT * FROM gold_tp_rules WHERE id = %s", (rule_id,))
            rule = dict(await cur.fetchone())
    return rule


async def update_tp_rule(rule_id: int, payload: dict) -> dict:
    fields, values = [], []
    updatable = ["rule_name","tp_level","min_capital","max_capital","risk_pct",
                 "message_tp1_reached","message_tp2_reached","message_tp3_reached",
                 "message_sl_touched","message_breakeven","message_partial_close",
                 "message_teaser","message_confirmation","is_active"]
    for col in updatable:
        if col in payload:
            fields.append(f"{col} = %s")
            values.append(payload[col])
    if not fields:
        return {"status": "nothing_to_update"}
    fields.append("updated_at = NOW()")
    values.append(rule_id)

    async with _db_write_lock:
        async with get_db() as cur:
            await cur.execute(f"UPDATE gold_tp_rules SET {', '.join(fields)} WHERE id = %s", values)
            await cur.execute("SELECT * FROM gold_tp_rules WHERE id = %s", (rule_id,))
            rule = dict(await cur.fetchone())
    return rule


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════════════════

async def _log_flow_event(session_id: int, user_id: int, event_type: str, payload: dict = None):
    try:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO gold_flow_events
                    (session_id, user_id, event_type, payload, created_at)
                VALUES (%s,%s,%s,%s,NOW())
            """, (session_id, user_id, event_type, json.dumps(payload) if payload else None))
    except Exception as e:
        logger.warning(f"[flow_event] {e}")


async def _notify_admin_session_closed(session_id: int, close_type: str, notified: int):
    if not _bot:
        return
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        await cur.execute("""
            SELECT
                ROUND(SUM(result_usd), 2) AS total_result,
                ROUND(SUM(CASE WHEN result_usd > 0 THEN result_usd ELSE 0 END), 2) AS total_gains,
                ROUND(SUM(CASE WHEN result_usd < 0 THEN result_usd ELSE 0 END), 2) AS total_losses
            FROM gold_member_entries WHERE session_id = %s
        """, (session_id,))
        agg = await cur.fetchone()

    if not session:
        return
    session = dict(session)
    agg     = dict(agg)
    emoji   = {"tp1": "✅", "tp2": "🎯", "tp3": "🏆", "sl": "❌"}.get(close_type, "📊")
    try:
        await _bot.send_message(
            chat_id    = ADMIN_ID,
            text       = (f"{emoji} *Session Gold clôturée — {close_type.upper()}*\n\n"
                          f"Membres notifiés : {notified}\n"
                          f"Résultat global : {agg['total_result']}$\n"
                          f"Gains : +{agg['total_gains']}$ | Pertes : {agg['total_losses']}$\n"
                          f"Lots engagés : {session['total_lots_engaged']}"),
            parse_mode = "Markdown",
        )
    except Exception as e:
        logger.warning(f"[notify_admin] {e}")