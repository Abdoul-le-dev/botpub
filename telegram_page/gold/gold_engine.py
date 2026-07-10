"""
gold_engine.py — Version v7 (nettoyée).

Ce fichier a été refactoré pour éliminer tout ce qui touchait à la RAM
v5/v6 (StateManager, cache, buffer, write queue). Ce qui reste est
purement métier / SQL, sans dépendance à un composant obsolète.

RESTE dans ce fichier :
  - Saisons (CRUD, stats)
  - Création de session Gold + lecture (get_active, get_detail, list)
  - Comptes simulation (CRUD + application aux trades)
  - Prix live (get_live_gold_price, watch_gold_price)
  - Alertes comptes cramés
  - Règles TP (CRUD)
  - Seed default TP rules

SUPPRIMÉ (remplacé par v7) :
  - confirm_gold_entry / _persist_gold_entry     → broadcast_v7._process_trade_full
  - save_user_step / get_user_step / restore_..  → state_v7 + buffer_v7
  - trigger_tp_reached / trigger_sl_touched      → tp_notifier
  - close_gold_session                           → lifecycle.close_session
  - get_tp_level_for_capital / get_rule_messages → snapshot.tp_level_for_capital / rule_for
  - _log_flow_event                              → gold_buffer_v7.add_event
  - _bot / set_bot                               → notifs par bot passé en paramètre

Le paramètre `bot` reste passé aux fonctions qui envoient des messages
(alertes cramés notamment) via l'appelant.
"""

import logging
import asyncio
import httpx
import math
import time as _time

from datetime import datetime
from typing import Optional

from db import get_db

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

_db_write_lock = asyncio.Lock()

# _bot est conservé pour rétro-compat avec check_cramed_accounts qui
# envoie une alerte admin. Set via set_bot() depuis main.py.
_bot = None


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _now() -> str:
    return datetime.now().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# CALCUL LOT — helpers gardés (utilisés par les comptes simulation)
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
    if live_price is None:
        return {"entry": entry, "sl": sl, "adjusted": False}
    sl_pips = round(abs(entry - sl), 2)
    if direction == "sell" and live_price > entry:
        return {"entry": live_price, "sl": round(live_price + sl_pips, 2), "adjusted": True}
    if direction == "buy" and live_price < entry:
        return {"entry": live_price, "sl": round(live_price - sl_pips, 2), "adjusted": True}
    return {"entry": entry, "sl": sl, "adjusted": False}


async def get_tp_level_for_capital(capital: float) -> tuple:
    """
    Gardé pour usage par _apply_to_simulation_accounts et par la route
    /calculate-lot (debug admin). PAS utilisé dans le chemin chaud —
    le chemin chaud lit désormais depuis le SessionSnapshot immutable.
    """
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


# ══════════════════════════════════════════════════════════════════════════════
# INIT TABLES
# ══════════════════════════════════════════════════════════════════════════════

async def init_gold_tables():
    async with get_db() as cur:
        await _seed_default_tp_rules(cur)
    print("[gold_engine] Tables Gold initialisées.")


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
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n\n_Cliquez ci-dessous pour accéder au trade._",
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
            "message_confirmation": "✅ *Trade enregistré !*\nObjectif : TP1 + TP2.",
        },
        {
            "rule_name": "Grand compte — TP1 + TP2 + TP3",
            "tp_level": 3, "min_capital": 2000, "max_capital": None, "risk_pct": 2.0,
            "message_tp1_reached":  "✅ *TP1 atteint sur XAU/USD !*\n\n🔒 Passez en *break even* immédiatement.\nFermez 30% de votre position et laissez courir.",
            "message_tp2_reached":  "🎯 *TP2 atteint sur XAU/USD !*\n\nFermez encore 40% de votre position.\nLaissez les 30% restants courir vers TP3 🚀",
            "message_tp3_reached":  "🏆 *TP3 atteint sur XAU/USD !*\n\nTrade parfait ! Fermez tout et savourez 🎉",
            "message_sl_touched":   "❌ *SL touché sur XAU/USD*\n\nBien géré — votre risque était contrôlé.",
            "message_breakeven":    "🔒 Break even — déplacez votre SL au prix d'entrée et fermez 30%.",
            "message_partial_close": "⚡ Clôture partielle — fermez 40% de votre position maintenant.",
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n\n_Cliquez ci-dessous pour accéder au trade._",
            "message_confirmation": "✅ *Trade enregistré !*\nObjectif : TP1 + TP2 + TP3.",
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
# SAISONS
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
        """, (payload["name"], payload.get("description"),
              payload.get("start_date", _now()),
              payload.get("initial_capital_ref")))
        season_id = cur.lastrowid
        await cur.execute("SELECT * FROM gold_seasons WHERE id = %s", (season_id,))
        return dict(await cur.fetchone())


async def get_active_season() -> dict | None:
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM gold_seasons WHERE status = 'active' "
            "ORDER BY created_at DESC LIMIT 1"
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
            "SELECT id, initial_capital FROM simulation_accounts "
            "WHERE season_id = %s AND is_active = 1", (season_id,)
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
# SESSIONS DE TRADE GOLD — création + lecture uniquement
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
        """, (payload.get("signal_id"), season_id, direction, entry,
              tp1, tp2, tp3, sl, sl_pips, tp1_pips, tp2_pips, tp3_pips,
              payload.get("timeframe", "M15"),
              int(payload.get("confidence_level", 3)),
              payload.get("note"), payload.get("screenshot_url")))
        session_id = cur.lastrowid
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        return dict(await cur.fetchone())


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
        where.append("gts.season_id = %s"); params.append(f["season_id"])
    if f.get("phase"):
        where.append("gts.current_phase = %s"); params.append(f["phase"])

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

    return {"sessions": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


# ══════════════════════════════════════════════════════════════════════════════
# PRIX LIVE + SURVEILLANCE
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
    """
    Surveille le prix et déclenche TP/SL v7 quand touchés.
    Import local pour éviter les cycles avec gold_v7.
    """
    logger.info(f"[gold_watch] Démarrage session {session_id}")

    # Import local pour éviter les cycles
    from telegram_page.gold.gold_v7.tp_notifier import (
        notify_tp_reached, notify_sl_touched,
        apply_tp_closure_in_db, notify_admin_session_closed,
    )
    from telegram_page.gold.gold_v7 import close_session, session_registry

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

            # SL touché
            if (direction == "buy" and price <= sl) or (direction == "sell" and price >= sl):
                if _bot:
                    await notify_sl_touched(_bot, session_id)
                # Cleanup v7 si session courante
                reg = session_registry.current()
                if reg is not None and reg.session_id == session_id:
                    await close_session(session_id, reg.version, close_type="sl")
                if _bot:
                    await notify_admin_session_closed(_bot, session_id, "sl", 0)
                break

            # TP3 : fermeture définitive
            if tp3 and phase not in ("tp3_reached", "closed"):
                if (direction == "buy" and price >= tp3) or (direction == "sell" and price <= tp3):
                    if _bot:
                        r = await notify_tp_reached(_bot, session_id, 3)
                        await apply_tp_closure_in_db(session_id, 3)
                        reg = session_registry.current()
                        if reg is not None and reg.session_id == session_id:
                            await close_session(session_id, reg.version, close_type="tp3")
                        await notify_admin_session_closed(_bot, session_id, "tp3",
                                                            r.get("sent_exit", 0))
                    break

            # TP2 : notif + attente TP3
            if tp2 and phase not in ("tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy" and price >= tp2) or (direction == "sell" and price <= tp2):
                    if _bot:
                        await notify_tp_reached(_bot, session_id, 2)
                    await asyncio.sleep(interval)
                    continue

            # TP1
            if tp1 and phase not in ("tp1_reached", "tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy" and price >= tp1) or (direction == "sell" and price <= tp1):
                    if _bot:
                        await notify_tp_reached(_bot, session_id, 1)

        except Exception as e:
            logger.error(f"[gold_watch] Session {session_id}: {e}", exc_info=True)

        await asyncio.sleep(_watch_interval())


# ══════════════════════════════════════════════════════════════════════════════
# COMPTES SIMULATION
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
            return dict(await cur.fetchone())


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
            capital_curve.append({"capital": round(cap, 2),
                                   "result_usd": t["result_usd"],
                                   "date": t["closed_at"] or t["opened_at"]})
        account["capital_curve"] = capital_curve
        account["rendement_pct"] = round(
            (account["current_capital"] - account["initial_capital"])
            / account["initial_capital"] * 100, 2
        ) if account["initial_capital"] > 0 else 0
    return account


async def _apply_to_simulation_accounts(session_id: int, session: dict):
    """Appelé UNE fois au broadcast v7. Inchangé vs v5."""
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
                                          tp1=session.get("tp1"), tp2=session.get("tp2"),
                                          tp3=session.get("tp3"))
        tp_level, risk_pct = await get_tp_level_for_capital(capital)
        risk_usd = round(capital * risk_pct / 100, 2)
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


async def close_simulation_trades(session_id: int, close_type: str):
    """
    Clôture les simulation_trades ouverts pour cette session.
    Renommé de _close_simulation_trades → close_simulation_trades pour
    être appelable depuis les routes/tp_notifier v7.
    """
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
# ALERTES COMPTES CRAMÉS
# ══════════════════════════════════════════════════════════════════════════════

async def check_cramed_accounts(session_id: int = None) -> dict:
    cramed_risk = []; already_cramed = []; simulation_danger = []
    async with get_db() as cur:
        if session_id:
            await cur.execute("""
                SELECT gme.user_id, gme.capital_declared, gme.perte_sl, u.name
                FROM gold_member_entries gme
                LEFT JOIN users u ON u.telegram_id = gme.user_id
                WHERE gme.session_id = %s AND gme.step_reached IN ('processed', 'confirmed')
            """, (session_id,))
            entries = await cur.fetchall()
            for e in entries:
                e = dict(e)
                capital = e["capital_declared"]
                perte   = abs(e["perte_sl"] or 0)
                apres   = capital - perte
                if apres <= 0:
                    already_cramed.append({"user_id": e["user_id"], "name": e["name"],
                                            "capital": capital, "perte_sl": -perte,
                                            "capital_restant": apres})
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
            await _bot.send_message(chat_id=ADMIN_ID, text="\n".join(lines),
                                     parse_mode="Markdown")
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
# RÈGLES TP — CRUD
# ══════════════════════════════════════════════════════════════════════════════

async def get_tp_rules() -> list:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_tp_rules ORDER BY tp_level ASC, min_capital ASC")
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
            """, (payload["rule_name"], int(payload["tp_level"]),
                  float(payload["min_capital"]),
                  float(payload["max_capital"]) if payload.get("max_capital") else None,
                  float(payload["risk_pct"]),
                  payload.get("message_tp1_reached"), payload.get("message_tp2_reached"),
                  payload.get("message_tp3_reached"), payload.get("message_sl_touched"),
                  payload.get("message_breakeven"), payload.get("message_partial_close"),
                  payload.get("message_teaser"), payload.get("message_confirmation")))
            rule_id = cur.lastrowid
            await cur.execute("SELECT * FROM gold_tp_rules WHERE id = %s", (rule_id,))
            return dict(await cur.fetchone())


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
            return dict(await cur.fetchone())

# ══════════════════════════════════════════════════════════════════════════════
# SHIMS V6 → V7 — compat routes API (routes_gold.py)
# ══════════════════════════════════════════════════════════════════════════════

async def confirm_gold_entry(session_id: int, user_id: int, capital: float) -> dict:
    from telegram_page.gold.lifecycle import current_snapshot
    from telegram_page.gold.gold_buffer import gold_buffer
    from telegram_page.gold.weekly_capital_cache import weekly_capital
    from telegram_page.gold.gold_broadcast import build_calc_context, adjust_entry_sl

    snap = current_snapshot()
    if snap is None or snap.session_id != session_id:
        raise RuntimeError(f"Session v7 active ne correspond pas à session_id={session_id}.")

    await weekly_capital.set(int(user_id), float(capital))

    live_price = await get_live_gold_price()
    effective_entry, effective_sl, _ = adjust_entry_sl(snap, live_price)

    calc = build_calc_context(
        snap, int(user_id), float(capital),
        effective_entry, effective_sl,
    )

    gold_buffer.add_entry(
        snap.session_id, int(user_id), snap.season_id,
        calc.capital, calc.risk_pct, calc.risk_usd,
        calc.lot, calc.tp_level,
        calc.perte_sl, calc.gain_tp1, calc.gain_tp2, calc.gain_tp3,
    )

    return {
        "ok": True, "session_id": session_id, "user_id": user_id,
        "capital": calc.capital, "lot": calc.lot, "tp_level": calc.tp_level,
    }


async def trigger_tp_reached(session_id: int, tp_level: int) -> dict:
    from telegram_page.gold.gold_buffer import gold_buffer
    gold_buffer.set_phase(session_id, f"tp{tp_level}_reached")
    gold_buffer.add_event(session_id, 0, f"tp{tp_level}_reached", {"tp_level": tp_level})
    return {"ok": True, "session_id": session_id, "tp_level": tp_level}


async def trigger_sl_touched(session_id: int) -> dict:
    from telegram_page.gold.gold_buffer import gold_buffer
    gold_buffer.set_phase(session_id, "sl_touched")
    gold_buffer.add_event(session_id, 0, "sl_touched", {})
    return {"ok": True, "session_id": session_id}