"""
gold_engine.py — Version corrigée complète v3

Corrections v3 :
  1. calculate_lot()         → nouvelle formule (capital/diviseur*0.01/sl_pips)
  2. calculate_gains_losses()→ nouvelle formule (lot/0.01*pips)
  3. sl_pips                 → différence brute abs(entry - sl), pas × 10
  4. Suppression INSERT member_capital (table inexistante)
  5. _apply_to_simulation_accounts → nouvelle formule lot
  6. confirm_gold_entry      → nouvelle formule lot
  7. Prix live               → Twelve Data (XAU/USD natif)
  8. watch_gold_price        → clôture après TP3
  9. get_diviseur()          → paliers 500$ au-delà de 1500$
"""

import logging
import sqlite3
import json
import asyncio
import httpx
import math

from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH  = "preinscriptions.db"
ADMIN_ID = 571718066

_bot = None

_db_write_lock     = asyncio.Lock()
_confirm_semaphore = asyncio.Semaphore(15)


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS BASE
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _now() -> str:
    return datetime.now().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# CALCUL LOT — FORMULE CORRIGÉE
# ══════════════════════════════════════════════════════════════════════════════

def get_diviseur(capital: float) -> int:
    """
    Nombre de trades perdants consécutifs avant de cramer le compte.
    501-1499$  → 12
    1500-1999$ → 12
    2000-2499$ → 13
    2500-2999$ → 14
    ...
    """
    if capital < 1500:
        return 12
    return 12 + math.floor((capital - 1001) / 500)


def calculate_lot(capital: float, entry: float, sl: float) -> float:
    """
    Calcule le lot recommandé XAU/USD.

    Règles :
      < 250$     → 0.01 fixe
      250-499$   → 0.015 fixe
      >= 500$    → lot = (capital/diviseur * 0.01) / sl_pips, min 0.01

    sl_pips = abs(entry - sl)  ← différence brute
    1 pip = 1$ pour 0.01 lot
    perte = (lot / 0.01) * sl_pips

    Exemples :
      capital=702$,  entry=4550, sl=4520 → sl=30,  lot=0.02
      capital=2000$, entry=4550, sl=4350 → sl=200, lot=0.01
      capital=5000$, entry=4550, sl=4520 → sl=30,  lot=0.08
    """
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
                            tp1: float = None, tp2: float = None,
                            tp3: float = None) -> dict:
    """
    Gains/pertes estimés.
    perte/gain = (lot / 0.01) * pips
    """
    def dollars(pips):
        return round((lot / 0.01) * pips, 2)

    sl_pips  = abs(entry - sl)
    perte_sl = dollars(sl_pips) * -1
    gain_tp1 = dollars(abs(tp1 - entry)) if tp1 else None
    gain_tp2 = dollars(abs(tp2 - entry)) if tp2 else None
    gain_tp3 = dollars(abs(tp3 - entry)) if tp3 else None

    return {
        "perte_sl": perte_sl,
        "gain_tp1": gain_tp1,
        "gain_tp2": gain_tp2,
        "gain_tp3": gain_tp3,
    }


def get_tp_level_for_capital(capital: float) -> tuple:
    """
    Détermine le niveau TP selon le capital.
    Lit les règles depuis gold_tp_rules — fallback hardcodé.
    """
    conn = get_conn()
    try:
        rule = conn.execute("""
            SELECT tp_level, risk_pct FROM gold_tp_rules
            WHERE is_active = 1
              AND min_capital <= ?
              AND (max_capital IS NULL OR max_capital >= ?)
            ORDER BY tp_level ASC LIMIT 1
        """, (capital, capital)).fetchone()
    finally:
        conn.close()

    if rule:
        return int(rule["tp_level"]), float(rule["risk_pct"])
    if capital < 500:
        return 1, 1.0
    elif capital < 2000:
        return 2, 1.5
    else:
        return 3, 2.0


def get_rule_messages(tp_level: int) -> dict:
    conn = get_conn()
    try:
        rule = conn.execute("""
            SELECT * FROM gold_tp_rules
            WHERE tp_level = ? AND is_active = 1
            LIMIT 1
        """, (tp_level,)).fetchone()
    finally:
        conn.close()
    return dict(rule) if rule else {}


# ══════════════════════════════════════════════════════════════════════════════
# INITIALISATION TABLES
# ══════════════════════════════════════════════════════════════════════════════

def init_gold_tables():
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_seasons (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                description         TEXT,
                start_date          TEXT    NOT NULL,
                end_date            TEXT,
                initial_capital_ref REAL,
                status              TEXT    DEFAULT 'active'
                                            CHECK(status IN ('active','closed','reset')),
                total_trades        INTEGER DEFAULT 0,
                wins                INTEGER DEFAULT 0,
                losses              INTEGER DEFAULT 0,
                created_by          INTEGER DEFAULT 571718066,
                created_at          TEXT    DEFAULT (datetime('now')),
                closed_at           TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_tp_rules (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name               TEXT    NOT NULL,
                tp_level                INTEGER NOT NULL CHECK(tp_level IN (1,2,3)),
                min_capital             REAL    NOT NULL DEFAULT 0,
                max_capital             REAL,
                risk_pct                REAL    NOT NULL DEFAULT 1.0,
                message_tp1_reached     TEXT,
                message_tp2_reached     TEXT,
                message_tp3_reached     TEXT,
                message_sl_touched      TEXT,
                message_breakeven       TEXT,
                message_partial_close   TEXT,
                message_teaser          TEXT,
                message_confirmation    TEXT,
                is_active               INTEGER DEFAULT 1,
                created_at              TEXT    DEFAULT (datetime('now')),
                updated_at              TEXT    DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_trade_sessions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id               INTEGER REFERENCES signals(id),
                season_id               INTEGER REFERENCES gold_seasons(id),
                pair                    TEXT    NOT NULL DEFAULT 'XAU/USD',
                direction               TEXT    NOT NULL CHECK(direction IN ('buy','sell')),
                entry_price             REAL    NOT NULL,
                tp1                     REAL,
                tp2                     REAL,
                tp3                     REAL,
                sl                      REAL    NOT NULL,
                sl_pips                 REAL,
                tp1_pips                REAL,
                tp2_pips                REAL,
                tp3_pips                REAL,
                timeframe               TEXT    DEFAULT 'M15',
                confidence_level        INTEGER DEFAULT 3
                                                CHECK(confidence_level BETWEEN 1 AND 5),
                note                    TEXT,
                screenshot_url          TEXT,
                current_phase           TEXT    DEFAULT 'teaser'
                                                CHECK(current_phase IN (
                                                    'teaser','open','tp1_reached',
                                                    'tp2_reached','tp3_reached',
                                                    'sl_touched','closed','cancelled'
                                                )),
                live_price_last         REAL,
                live_price_updated_at   TEXT,
                tp1_reached_at          TEXT,
                tp2_reached_at          TEXT,
                tp3_reached_at          TEXT,
                sl_touched_at           TEXT,
                total_members_in        INTEGER DEFAULT 0,
                total_lots_engaged      REAL    DEFAULT 0,
                estimated_loss_sl       REAL    DEFAULT 0,
                estimated_gain_tp1      REAL    DEFAULT 0,
                estimated_gain_tp2      REAL    DEFAULT 0,
                estimated_gain_tp3      REAL    DEFAULT 0,
                aggregates_updated_at   TEXT,
                teaser_sent_at          TEXT,
                opened_at               TEXT,
                closed_at               TEXT,
                created_at              TEXT    DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_user_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL REFERENCES gold_trade_sessions(id),
                user_id         INTEGER NOT NULL,
                step            TEXT    NOT NULL DEFAULT 'teaser'
                                        CHECK(step IN (
                                            'teaser','waiting_capital',
                                            'trade_shown','confirmed','cancelled'
                                        )),
                capital_input   REAL,
                updated_at      TEXT    DEFAULT (datetime('now')),
                UNIQUE(session_id, user_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_member_entries (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          INTEGER NOT NULL REFERENCES gold_trade_sessions(id),
                user_id             INTEGER NOT NULL,
                season_id           INTEGER REFERENCES gold_seasons(id),
                capital_declared    REAL    NOT NULL,
                risk_pct            REAL    NOT NULL DEFAULT 1.0,
                risk_usd            REAL    NOT NULL,
                lot_calculated      REAL    NOT NULL,
                tp_level_assigned   INTEGER NOT NULL CHECK(tp_level_assigned IN (1,2,3)),
                perte_sl            REAL    NOT NULL,
                gain_tp1            REAL    NOT NULL,
                gain_tp2            REAL,
                gain_tp3            REAL,
                exit_price          REAL,
                exit_tp_level       INTEGER,
                result_pips         REAL,
                result_usd          REAL,
                capital_before      REAL,
                capital_after       REAL,
                followed_instruction INTEGER DEFAULT NULL,
                behavior            TEXT,
                step_reached        TEXT    DEFAULT 'confirmed',
                confirmed_at        TEXT    DEFAULT (datetime('now')),
                exited_at           TEXT,
                UNIQUE(session_id, user_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gold_flow_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES gold_trade_sessions(id),
                user_id     INTEGER NOT NULL,
                event_type  TEXT    NOT NULL,
                payload     TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS simulation_accounts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                description         TEXT,
                initial_capital     REAL    NOT NULL,
                current_capital     REAL    NOT NULL,
                currency            TEXT    DEFAULT 'USD',
                risk_pct_default    REAL    DEFAULT 1.0,
                total_trades        INTEGER DEFAULT 0,
                wins                INTEGER DEFAULT 0,
                losses              INTEGER DEFAULT 0,
                max_drawdown_pct    REAL    DEFAULT 0,
                peak_capital        REAL,
                is_active           INTEGER DEFAULT 1,
                season_id           INTEGER REFERENCES gold_seasons(id),
                created_by          INTEGER DEFAULT 571718066,
                created_at          TEXT    DEFAULT (datetime('now')),
                updated_at          TEXT    DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS simulation_trades (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id          INTEGER NOT NULL REFERENCES simulation_accounts(id),
                session_id          INTEGER NOT NULL REFERENCES gold_trade_sessions(id),
                season_id           INTEGER REFERENCES gold_seasons(id),
                entry_price         REAL    NOT NULL,
                tp1                 REAL,
                tp2                 REAL,
                tp3                 REAL,
                sl                  REAL    NOT NULL,
                direction           TEXT    NOT NULL,
                capital_before      REAL    NOT NULL,
                risk_pct            REAL    NOT NULL,
                risk_usd            REAL    NOT NULL,
                lot_used            REAL    NOT NULL,
                tp_level_target     INTEGER NOT NULL,
                perte_sl            REAL    NOT NULL,
                gain_tp1            REAL    NOT NULL,
                gain_tp2            REAL,
                gain_tp3            REAL,
                exit_price          REAL,
                exit_tp_level       INTEGER,
                result_pips         REAL,
                result_usd          REAL,
                capital_after       REAL,
                status              TEXT    DEFAULT 'open',
                opened_at           TEXT    DEFAULT (datetime('now')),
                closed_at           TEXT
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_gts_season   ON gold_trade_sessions(season_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gts_phase    ON gold_trade_sessions(current_phase)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gme_session  ON gold_member_entries(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gme_user     ON gold_member_entries(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gus_session  ON gold_user_sessions(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gus_user     ON gold_user_sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gfe_session  ON gold_flow_events(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_account  ON simulation_trades(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_session  ON simulation_trades(session_id)")

        _seed_default_tp_rules(conn)
        conn.commit()
        print("[gold_engine] Tables Gold v3 initialisées.")
    finally:
        conn.close()


def _seed_default_tp_rules(conn):
    existing = conn.execute("SELECT COUNT(*) FROM gold_tp_rules").fetchone()[0]
    if existing > 0:
        return

    rules = [
        {
            "rule_name":            "Petit compte — TP1 seulement",
            "tp_level":             1,
            "min_capital":          0,
            "max_capital":          499.99,
            "risk_pct":             1.0,
            "message_tp1_reached":  "✅ *TP1 atteint sur XAU/USD !*\n\n🎯 Sortez maintenant et sécurisez vos gains.\nC'est votre niveau de sortie — ne soyez pas gourmand 💪",
            "message_tp2_reached":  None,
            "message_tp3_reached":  None,
            "message_sl_touched":   "❌ *SL touché sur XAU/USD*\n\nVotre SL a bien protégé votre compte.\nC'est la discipline qui fait les vrais traders 💪",
            "message_breakeven":    None,
            "message_partial_close": None,
            "message_teaser":       "🔔 *Le trade du jour est disponible !*\n\n📊 Paire : *XAU/USD* (Gold)\n📈 Achat (Buy)\n\n_Cliquez ci-dessous pour accéder au trade._",
            "message_confirmation": "✅ *Trade enregistré !*\nTu recevras les instructions en temps réel.",
        },
        {
            "rule_name":            "Compte moyen — TP1 + TP2",
            "tp_level":             2,
            "min_capital":          500,
            "max_capital":          1999.99,
            "risk_pct":             1.5,
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
            "rule_name":            "Grand compte — TP1 + TP2 + TP3",
            "tp_level":             3,
            "min_capital":          2000,
            "max_capital":          None,
            "risk_pct":             2.0,
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
        conn.execute("""
            INSERT INTO gold_tp_rules
                (rule_name, tp_level, min_capital, max_capital, risk_pct,
                 message_tp1_reached, message_tp2_reached, message_tp3_reached,
                 message_sl_touched, message_breakeven, message_partial_close,
                 message_teaser, message_confirmation)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE gold_seasons SET status = 'closed', closed_at = ?
            WHERE status = 'active'
        """, (_now(),))
        cur = conn.execute("""
            INSERT INTO gold_seasons
                (name, description, start_date, initial_capital_ref, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (
            payload["name"],
            payload.get("description"),
            payload.get("start_date", _now()),
            payload.get("initial_capital_ref"),
        ))
        season_id = cur.lastrowid
        conn.commit()
        season = dict(conn.execute(
            "SELECT * FROM gold_seasons WHERE id = ?", (season_id,)
        ).fetchone())
    finally:
        conn.close()
    return season


async def get_active_season() -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM gold_seasons WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


async def get_seasons(include_closed: bool = True) -> list:
    conn = get_conn()
    try:
        where = "" if include_closed else "WHERE s.status = 'active'"
        rows  = conn.execute(f"""
            SELECT s.*,
                   COUNT(DISTINCT gts.id)      AS trades_count,
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
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def reset_season(season_id: int, payload: dict) -> dict:
    conn = get_conn()
    sim_accounts = []
    try:
        conn.execute("""
            UPDATE gold_seasons
            SET status = 'reset', closed_at = ?, end_date = ?
            WHERE id = ?
        """, (_now(), _now(), season_id))

        sim_accounts = conn.execute(
            "SELECT id, initial_capital FROM simulation_accounts WHERE season_id = ? AND is_active = 1",
            (season_id,)
        ).fetchall()

        for acc in sim_accounts:
            conn.execute("""
                UPDATE simulation_accounts
                SET current_capital = initial_capital,
                    total_trades = 0, wins = 0, losses = 0,
                    max_drawdown_pct = 0, peak_capital = initial_capital,
                    updated_at = ?
                WHERE id = ?
            """, (_now(), acc["id"]))

        conn.commit()
    finally:
        conn.close()

    new_season = await create_season({
        "name": payload["new_season_name"],
        "initial_capital_ref": payload.get("new_initial_capital"),
    })

    conn2 = get_conn()
    try:
        conn2.execute("""
            UPDATE simulation_accounts SET season_id = ?, updated_at = ?
            WHERE is_active = 1
        """, (new_season["id"], _now()))
        conn2.commit()
    finally:
        conn2.close()

    return {
        "archived_season_id": season_id,
        "new_season":         new_season,
        "accounts_reset":     len(sim_accounts),
    }


async def get_season_stats(season_id: int) -> dict:
    conn = get_conn()
    try:
        season = conn.execute("SELECT * FROM gold_seasons WHERE id = ?", (season_id,)).fetchone()
        if not season:
            return {"error": "Saison introuvable"}
        season = dict(season)

        session_stats = conn.execute("""
            SELECT
                COUNT(*)                                                       AS total_trades,
                COUNT(CASE WHEN current_phase IN
                    ('tp1_reached','tp2_reached','tp3_reached') THEN 1 END)    AS wins,
                COUNT(CASE WHEN current_phase = 'sl_touched' THEN 1 END)      AS losses,
                AVG(total_members_in)                                          AS avg_members_per_trade,
                SUM(total_members_in)                                          AS total_confirmations
            FROM gold_trade_sessions WHERE season_id = ?
        """, (season_id,)).fetchone()

        member_stats = conn.execute("""
            SELECT
                COUNT(DISTINCT user_id)                    AS unique_members,
                ROUND(SUM(result_usd), 2)                  AS total_gains_members,
                ROUND(AVG(result_usd), 2)                  AS avg_gain_per_trade,
                COUNT(CASE WHEN result_usd > 0 THEN 1 END) AS member_wins,
                COUNT(CASE WHEN result_usd < 0 THEN 1 END) AS member_losses
            FROM gold_member_entries WHERE season_id = ?
        """, (season_id,)).fetchone()

        sim_accounts = conn.execute("""
            SELECT sa.*,
                   ROUND((sa.current_capital - sa.initial_capital)
                         / sa.initial_capital * 100, 2) AS rendement_pct
            FROM simulation_accounts sa
            WHERE sa.season_id = ?
            ORDER BY sa.initial_capital ASC
        """, (season_id,)).fetchall()

        top_members = conn.execute("""
            SELECT u.name, gme.user_id,
                   COUNT(*) AS trades,
                   ROUND(SUM(gme.result_usd), 2) AS total_usd
            FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.season_id = ?
            GROUP BY gme.user_id
            ORDER BY total_usd DESC LIMIT 10
        """, (season_id,)).fetchall()

    finally:
        conn.close()

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
    """
    Crée une session Gold.
    CORRECTION v3 : sl_pips = abs(entry - sl) — différence brute, pas × 10
    """
    entry     = float(payload["entry_price"])
    sl        = float(payload["sl"])
    tp1       = float(payload["tp1"]) if payload.get("tp1") else None
    tp2       = float(payload["tp2"]) if payload.get("tp2") else None
    tp3       = float(payload["tp3"]) if payload.get("tp3") else None
    direction = payload["direction"]

    if direction not in ("buy", "sell"):
        raise ValueError("direction doit être 'buy' ou 'sell'")

    # CORRECTION : différence brute, pas de multiplicateur
    def _pips(a, b):
        return round(abs(a - b), 2)

    sl_pips  = _pips(entry, sl)
    tp1_pips = _pips(entry, tp1) if tp1 else None
    tp2_pips = _pips(entry, tp2) if tp2 else None
    tp3_pips = _pips(entry, tp3) if tp3 else None

    season    = await get_active_season()
    season_id = season["id"] if season else None

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO gold_trade_sessions
                (signal_id, season_id, direction, entry_price,
                 tp1, tp2, tp3, sl,
                 sl_pips, tp1_pips, tp2_pips, tp3_pips,
                 timeframe, confidence_level, note, screenshot_url,
                 current_phase, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'teaser', ?)
        """, (
            payload.get("signal_id"), season_id,
            direction, entry, tp1, tp2, tp3, sl,
            sl_pips, tp1_pips, tp2_pips, tp3_pips,
            payload.get("timeframe", "M15"),
            int(payload.get("confidence_level", 3)),
            payload.get("note"),
            payload.get("screenshot_url"),
            _now(),
        ))
        session_id = cur.lastrowid
        conn.commit()
        session = dict(conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone())
    finally:
        conn.close()
    return session


async def get_active_gold_session() -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT gts.*, gs.name AS season_name
            FROM gold_trade_sessions gts
            LEFT JOIN gold_seasons gs ON gs.id = gts.season_id
            WHERE gts.current_phase NOT IN ('closed','cancelled','sl_touched')
            ORDER BY gts.created_at DESC LIMIT 1
        """).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


async def get_gold_session_detail(session_id: int) -> dict | None:
    conn = get_conn()
    try:
        session = conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return None
        session = dict(session)

        entries = conn.execute("""
            SELECT gme.*, u.name
            FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.session_id = ? ORDER BY gme.confirmed_at ASC
        """, (session_id,)).fetchall()
        session["entries"] = [dict(e) for e in entries]

        tp_dist = conn.execute("""
            SELECT tp_level_assigned,
                   COUNT(*)                              AS members,
                   ROUND(SUM(lot_calculated), 4)         AS total_lots,
                   ROUND(SUM(ABS(perte_sl)), 2)           AS total_risk,
                   ROUND(SUM(gain_tp1), 2)                AS total_gain_tp1,
                   ROUND(SUM(COALESCE(gain_tp2,0)), 2)    AS total_gain_tp2,
                   ROUND(SUM(COALESCE(gain_tp3,0)), 2)    AS total_gain_tp3
            FROM gold_member_entries WHERE session_id = ?
            GROUP BY tp_level_assigned ORDER BY tp_level_assigned
        """, (session_id,)).fetchall()
        session["tp_distribution"] = [dict(d) for d in tp_dist]

        sim_trades = conn.execute("""
            SELECT st.*, sa.name AS account_name, sa.initial_capital
            FROM simulation_trades st
            JOIN simulation_accounts sa ON sa.id = st.account_id
            WHERE st.session_id = ? ORDER BY sa.initial_capital ASC
        """, (session_id,)).fetchall()
        session["simulation_trades"] = [dict(s) for s in sim_trades]

    finally:
        conn.close()
    return session


async def get_gold_sessions(filters: dict = None) -> dict:
    f      = filters or {}
    limit  = int(f.get("limit", 20))
    offset = int(f.get("offset", 0))
    where  = ["1=1"]
    params = []

    if f.get("season_id"):
        where.append("gts.season_id = ?")
        params.append(f["season_id"])
    if f.get("phase"):
        where.append("gts.current_phase = ?")
        params.append(f["phase"])

    where_sql = " AND ".join(where)
    conn = get_conn()
    try:
        rows = conn.execute(f"""
            SELECT gts.*, gs.name AS season_name,
                   COUNT(DISTINCT gme.user_id) AS confirmed_members
            FROM gold_trade_sessions gts
            LEFT JOIN gold_seasons gs  ON gs.id  = gts.season_id
            LEFT JOIN gold_member_entries gme ON gme.session_id = gts.id
            WHERE {where_sql}
            GROUP BY gts.id
            ORDER BY gts.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM gold_trade_sessions gts WHERE {where_sql}", params
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "sessions": [dict(r) for r in rows],
        "total":    total,
        "limit":    limit,
        "offset":   offset,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PERSISTANCE SESSION UTILISATEUR
# ══════════════════════════════════════════════════════════════════════════════

async def save_user_step(session_id: int, user_id: int, step: str,
                          capital: float = None):
    async with _db_write_lock:
        conn = get_conn()
        try:
            conn.execute("""
                INSERT INTO gold_user_sessions
                    (session_id, user_id, step, capital_input, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, user_id) DO UPDATE SET
                    step          = excluded.step,
                    capital_input = excluded.capital_input,
                    updated_at    = excluded.updated_at
            """, (session_id, user_id, step, capital, _now()))
            conn.commit()
        finally:
            conn.close()


async def get_user_step(session_id: int, user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT * FROM gold_user_sessions
            WHERE session_id = ? AND user_id = ?
        """, (session_id, user_id)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


async def restore_user_context(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT gus.*, gts.entry_price, gts.direction, gts.current_phase,
                   gts.tp1, gts.tp2, gts.tp3, gts.sl
            FROM gold_user_sessions gus
            JOIN gold_trade_sessions gts ON gts.id = gus.session_id
            WHERE gus.user_id = ?
              AND gts.current_phase NOT IN ('closed','cancelled','sl_touched')
              AND gus.step NOT IN ('confirmed','cancelled')
            ORDER BY gus.updated_at DESC LIMIT 1
        """, (user_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CONFIRMATION MEMBRE
# ══════════════════════════════════════════════════════════════════════════════

async def confirm_gold_entry(session_id: int, user_id: int, capital: float) -> dict:
    async with _confirm_semaphore:
        return await _confirm_gold_entry_inner(session_id, user_id, capital)


async def _confirm_gold_entry_inner(session_id: int, user_id: int,
                                     capital: float) -> dict:
    conn = get_conn()
    try:
        session = conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"error": "Session introuvable"}
        session = dict(session)
    finally:
        conn.close()

    if session["current_phase"] not in ("teaser", "open"):
        return {"error": "Ce trade n'est plus ouvert aux participations"}

    # CORRECTION v3 : nouvelle formule lot
    lot    = calculate_lot(capital, session["entry_price"], session["sl"])
    gains  = calculate_gains_losses(
        lot   = lot,
        entry = session["entry_price"],
        sl    = session["sl"],
        tp1   = session.get("tp1"),
        tp2   = session.get("tp2"),
        tp3   = session.get("tp3"),
    )
    perte_sl = gains["perte_sl"]
    gain_tp1 = gains["gain_tp1"]
    gain_tp2 = gains["gain_tp2"]
    gain_tp3 = gains["gain_tp3"]

    tp_level, risk_pct = get_tp_level_for_capital(capital)
    risk_usd = round(capital * risk_pct / 100, 2)

    async with _db_write_lock:
        conn2 = get_conn()
        try:
            conn2.execute("""
                INSERT INTO gold_member_entries
                    (session_id, user_id, season_id, capital_declared, risk_pct,
                     risk_usd, lot_calculated, tp_level_assigned,
                     perte_sl, gain_tp1, gain_tp2, gain_tp3,
                     capital_before, step_reached, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
                ON CONFLICT(session_id, user_id) DO UPDATE SET
                    capital_declared  = excluded.capital_declared,
                    risk_pct          = excluded.risk_pct,
                    risk_usd          = excluded.risk_usd,
                    lot_calculated    = excluded.lot_calculated,
                    tp_level_assigned = excluded.tp_level_assigned,
                    perte_sl          = excluded.perte_sl,
                    gain_tp1          = excluded.gain_tp1,
                    gain_tp2          = excluded.gain_tp2,
                    gain_tp3          = excluded.gain_tp3,
                    capital_before    = excluded.capital_before,
                    confirmed_at      = excluded.confirmed_at
            """, (
                session_id, user_id, session["season_id"],
                capital, risk_pct, risk_usd, lot, tp_level,
                perte_sl, gain_tp1, gain_tp2, gain_tp3,
                capital, _now(),
            ))

            # CORRECTION v3 : supprimé INSERT member_capital (table inexistante)

            if session["current_phase"] == "teaser":
                conn2.execute("""
                    UPDATE gold_trade_sessions
                    SET current_phase = 'open', opened_at = ?
                    WHERE id = ?
                """, (_now(), session_id))

            agg = conn2.execute("""
                SELECT
                    COUNT(*)                              AS total_members,
                    ROUND(SUM(lot_calculated), 4)         AS total_lots,
                    ROUND(SUM(ABS(perte_sl)), 2)           AS total_loss_sl,
                    ROUND(SUM(gain_tp1), 2)                AS total_gain_tp1,
                    ROUND(SUM(COALESCE(gain_tp2,0)), 2)    AS total_gain_tp2,
                    ROUND(SUM(COALESCE(gain_tp3,0)), 2)    AS total_gain_tp3
                FROM gold_member_entries WHERE session_id = ?
            """, (session_id,)).fetchone()

            conn2.execute("""
                UPDATE gold_trade_sessions SET
                    total_members_in      = ?,
                    total_lots_engaged    = ?,
                    estimated_loss_sl     = ?,
                    estimated_gain_tp1    = ?,
                    estimated_gain_tp2    = ?,
                    estimated_gain_tp3    = ?,
                    aggregates_updated_at = ?
                WHERE id = ?
            """, (
                agg["total_members"]  or 0,
                agg["total_lots"]     or 0,
                agg["total_loss_sl"]  or 0,
                agg["total_gain_tp1"] or 0,
                agg["total_gain_tp2"] or 0,
                agg["total_gain_tp3"] or 0,
                _now(), session_id,
            ))

            conn2.commit()
            aggregates = dict(agg)
        finally:
            conn2.close()

    await save_user_step(session_id, user_id, "confirmed", capital)
    await _log_flow_event(session_id, user_id, "confirmed", {
        "capital": capital, "lot": lot, "tp_level": tp_level
    })
    await _apply_to_simulation_accounts(session_id, session)

    rule_msgs   = get_rule_messages(tp_level)
    tp_labels   = {1: "TP1", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
    dir_label   = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"

    lines = [
        "✅ *Trade confirmé — XAU/USD*",
        "",
        f"💼 Lot recommandé : *{lot}*",
        f"🎯 Objectif : *{tp_labels[tp_level]}*",
        f"📈 Direction : *{dir_label}*",
        "",
        "📊 *Scénarios :*",
        f"❌ Si SL touché → *{perte_sl}$*",
        f"✅ Si TP1 touché → *+{gain_tp1}$*",
    ]
    if gain_tp2:
        lines.append(f"🎯 Si TP2 touché → *+{gain_tp2}$*")
    if gain_tp3:
        lines.append(f"🏆 Si TP3 touché → *+{gain_tp3}$*")
    lines += ["", "_Tu recevras les instructions en temps réel._"]

    return {
        "entry": {
            "session_id": session_id, "user_id": user_id,
            "capital": capital, "lot": lot, "tp_level": tp_level,
            "perte_sl": perte_sl, "gain_tp1": gain_tp1,
            "gain_tp2": gain_tp2, "gain_tp3": gain_tp3,
        },
        "session_aggregates": aggregates,
        "message":            "\n".join(lines),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — GESTION TP / SL
# ══════════════════════════════════════════════════════════════════════════════

async def trigger_tp_reached(session_id: int, tp_level: int) -> dict:
    conn = get_conn()
    try:
        session = conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"error": "Session introuvable"}
        session = dict(session)

        entries = conn.execute("""
            SELECT gme.user_id, gme.tp_level_assigned,
                   gme.gain_tp1, gme.gain_tp2, gme.gain_tp3
            FROM gold_member_entries gme
            WHERE gme.session_id = ? AND gme.step_reached = 'confirmed'
        """, (session_id,)).fetchall()

        phase_map = {1: "tp1_reached", 2: "tp2_reached", 3: "tp3_reached"}
        new_phase = phase_map[tp_level]
        tp_field  = f"tp{tp_level}_reached_at"

        conn.execute(f"""
            UPDATE gold_trade_sessions
            SET current_phase = ?, {tp_field} = ?
            WHERE id = ?
        """, (new_phase, _now(), session_id))
        conn.commit()
    finally:
        conn.close()

    sent_exit = sent_continue = errors = 0

    for entry in entries:
        member_level = entry["tp_level_assigned"]
        user_id      = entry["user_id"]
        rule_msgs    = get_rule_messages(member_level)
        msg_field    = f"message_tp{tp_level}_reached"
        message      = rule_msgs.get(msg_field)
        if not message:
            continue

        gain_map = {1: entry["gain_tp1"], 2: entry["gain_tp2"], 3: entry["gain_tp3"]}
        gain     = gain_map.get(tp_level)
        if gain:
            message += f"\n\n💰 *Ton gain estimé : +{gain}$*"

        try:
            if _bot:
                await _bot.send_message(
                    chat_id=user_id, text=message, parse_mode="Markdown"
                )
                await asyncio.sleep(0.05)
            await _log_flow_event(
                session_id, user_id, f"tp{tp_level}_notified", {"gain": gain}
            )
            if member_level == tp_level:
                sent_exit += 1
            else:
                sent_continue += 1
        except Exception as e:
            logger.warning(f"[trigger_tp] uid={user_id}: {e}")
            errors += 1

    return {
        "session_id":    session_id,
        "tp_level":      tp_level,
        "sent_exit":     sent_exit,
        "sent_continue": sent_continue,
        "errors":        errors,
        "new_phase":     new_phase,
    }


async def trigger_sl_touched(session_id: int) -> dict:
    conn = get_conn()
    try:
        session = conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"error": "Session introuvable"}
        session = dict(session)

        entries = conn.execute("""
            SELECT gme.*, u.name FROM gold_member_entries gme
            LEFT JOIN users u ON u.telegram_id = gme.user_id
            WHERE gme.session_id = ? AND gme.step_reached = 'confirmed'
        """, (session_id,)).fetchall()

        async with _db_write_lock:
            conn.execute("""
                UPDATE gold_trade_sessions
                SET current_phase = 'sl_touched', sl_touched_at = ?, closed_at = ?
                WHERE id = ?
            """, (_now(), _now(), session_id))
            conn.execute("""
                UPDATE gold_member_entries
                SET result_usd    = perte_sl,
                    capital_after = capital_before + perte_sl,
                    exit_tp_level = NULL,
                    exited_at     = ?
                WHERE session_id = ?
            """, (_now(), session_id))
            conn.execute("""
                UPDATE simulation_trades
                SET result_usd    = perte_sl,
                    capital_after = capital_before + perte_sl,
                    status        = 'closed',
                    closed_at     = ?
                WHERE session_id = ?
            """, (_now(), session_id))
            conn.commit()
    finally:
        conn.close()

    notified = 0
    for entry in entries:
        rule_msgs = get_rule_messages(entry["tp_level_assigned"])
        message   = rule_msgs.get(
            "message_sl_touched",
            f"❌ *SL touché sur XAU/USD*\n\nPerte : *{entry['perte_sl']}$*\nRestez discipliné 💪"
        )
        message += f"\n\n📉 Perte sur ce trade : *{entry['perte_sl']}$*"
        try:
            if _bot:
                await _bot.send_message(
                    chat_id=entry["user_id"], text=message, parse_mode="Markdown"
                )
                await asyncio.sleep(0.05)
            notified += 1
        except Exception as e:
            logger.warning(f"[trigger_sl] uid={entry['user_id']}: {e}")

    await _close_simulation_trades(session_id, "sl")
    await _notify_admin_session_closed(session_id, "sl", notified)
    return {"session_id": session_id, "phase": "sl_touched", "notified": notified}


async def close_gold_session(session_id: int, payload: dict) -> dict:
    close_type = payload["close_type"]

    if close_type == "sl":
        return await trigger_sl_touched(session_id)

    tp_map = {"tp1": 1, "tp2": 2, "tp3": 3}
    if close_type in tp_map:
        tp_num = tp_map[close_type]
        result = await trigger_tp_reached(session_id, tp_num)

        async with _db_write_lock:
            conn = get_conn()
            try:
                # Clôturer la session
                conn.execute("""
                    UPDATE gold_trade_sessions
                    SET current_phase = 'closed', closed_at = ?
                    WHERE id = ?
                """, (_now(), session_id))

                # CORRECTION : chaque membre sort au meilleur TP qu'il peut atteindre
                # Ex : TP3 atteint, user assigné TP2 → sort au gain_tp2 (pas gain_tp1)
                #      TP3 atteint, user assigné TP1 → sort au gain_tp1
                #      TP3 atteint, user assigné TP3 → sort au gain_tp3
                conn.execute(f"""
                    UPDATE gold_member_entries
                    SET result_usd = CASE
                            WHEN tp_level_assigned >= {tp_num} AND gain_tp{tp_num} IS NOT NULL
                                THEN gain_tp{tp_num}
                            WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 AND gain_tp2 IS NOT NULL
                                THEN gain_tp2
                            ELSE gain_tp1
                        END,
                        exit_tp_level = CASE
                            WHEN tp_level_assigned >= {tp_num} THEN {tp_num}
                            WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 THEN 2
                            ELSE 1
                        END,
                        capital_after = capital_before + CASE
                            WHEN tp_level_assigned >= {tp_num} AND gain_tp{tp_num} IS NOT NULL
                                THEN gain_tp{tp_num}
                            WHEN tp_level_assigned >= 2 AND {tp_num} >= 2 AND gain_tp2 IS NOT NULL
                                THEN gain_tp2
                            ELSE gain_tp1
                        END,
                        exited_at = ?
                    WHERE session_id = ?
                """, (_now(), session_id))

                conn.commit()
            finally:
                conn.close()

        await _close_simulation_trades(session_id, close_type)
        await _notify_admin_session_closed(session_id, close_type, result.get("sent_exit", 0))
        return {**result, "phase": "closed"}

    return {"error": "close_type invalide"}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — PRIX LIVE & SURVEILLANCE
# ══════════════════════════════════════════════════════════════════════════════

import time as _time

TWELVE_DATA_KEY = "f5652ad530f04fbaa23412f87658180d"

# Cache prix — évite de dépasser la limite API
_price_cache: dict = {"price": None, "ts": 0.0}


def _watch_interval() -> int:
    """
    Intervalle de surveillance selon l'heure locale :
      00h–07h59 → 1800s (30 min)  — marché peu actif, nuit
      08h–19h59 →  120s  (2 min)  — heures de trading actives
      20h–23h59 →  300s  (5 min)  — soirée, activité réduite
    """
    h = datetime.now().hour
    if 8 <= h < 20:
        return 120    # 2 min
    elif h < 8:
        return 1800   # 30 min
    else:
        return 300    # 5 min


async def get_live_gold_price() -> float | None:
    """
    Prix live XAU/USD via Twelve Data avec cache intelligent.
    Le TTL du cache suit la même logique que l'intervalle du watcher.
    Retourne le dernier prix connu si l'API échoue (429, timeout...).
    """
    ttl = _watch_interval()
    if _price_cache["price"] and _time.time() - _price_cache["ts"] < ttl:
        return _price_cache["price"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.twelvedata.com/price",
                params={"symbol": "XAU/USD", "apikey": TWELVE_DATA_KEY}
            )
            data = resp.json()
            if "price" in data:
                _price_cache["price"] = float(data["price"])
                _price_cache["ts"]    = _time.time()
                return _price_cache["price"]
            # 429 ou autre erreur → retourner dernier prix connu sans crasher
            logger.warning(f"[gold_price] {data.get('message', data)}")
            return _price_cache["price"]
    except Exception as e:
        logger.warning(f"[gold_price] {e}")
        return _price_cache["price"]


async def watch_gold_price(session_id: int):
    """
    Surveillance prix avec intervalle adaptatif selon l'heure :
      Nuit (00-08h) → toutes les 30 min
      Jour (08-20h) → toutes les 2 min
      Soirée (20-24h) → toutes les 5 min
    Clôture complète après TP3.
    """
    logger.info(f"[gold_watch] Démarrage session {session_id}")

    while True:
        try:
            conn = get_conn()
            try:
                session = conn.execute(
                    "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
                ).fetchone()
            finally:
                conn.close()

            if not session:
                break

            session = dict(session)
            phase   = session["current_phase"]

            # Arrêt si session terminée
            if phase in ("closed", "cancelled", "sl_touched"):
                logger.info(f"[gold_watch] Session {session_id} terminée ({phase})")
                break

            # CORRECTION : si TP3 déjà atteint mais pas encore clôturé → clôturer
            if phase == "tp3_reached":
                logger.info(f"[gold_watch] Session {session_id} TP3 atteint, clôture...")
                await close_gold_session(session_id, {"close_type": "tp3"})
                break

            price = await get_live_gold_price()
            interval = _watch_interval()

            if price is None:
                await asyncio.sleep(interval)
                continue

            conn2 = get_conn()
            try:
                conn2.execute("""
                    UPDATE gold_trade_sessions
                    SET live_price_last = ?, live_price_updated_at = ?
                    WHERE id = ?
                """, (price, _now(), session_id))
                conn2.commit()
            finally:
                conn2.close()

            direction     = session["direction"]
            tp1, tp2, tp3 = session.get("tp1"), session.get("tp2"), session.get("tp3")
            sl            = session["sl"]

            # SL touché
            if (direction == "buy"  and price <= sl) or \
               (direction == "sell" and price >= sl):
                await trigger_sl_touched(session_id)
                break

            # TP3 — clôture complète + break
            if tp3 and phase not in ("tp3_reached", "closed"):
                if (direction == "buy"  and price >= tp3) or \
                   (direction == "sell" and price <= tp3):
                    await close_gold_session(session_id, {"close_type": "tp3"})
                    break

            # TP2
            if tp2 and phase not in ("tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy"  and price >= tp2) or \
                   (direction == "sell" and price <= tp2):
                    await trigger_tp_reached(session_id, 2)
                    await asyncio.sleep(interval)
                    continue

            # TP1
            if tp1 and phase not in ("tp1_reached", "tp2_reached", "tp3_reached", "closed"):
                if (direction == "buy"  and price >= tp1) or \
                   (direction == "sell" and price <= tp1):
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
        conn = get_conn()
        try:
            cur = conn.execute("""
                INSERT INTO simulation_accounts
                    (name, description, initial_capital, current_capital,
                     risk_pct_default, peak_capital, season_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                payload["name"], payload.get("description"),
                capital, capital,
                float(payload.get("risk_pct_default", 1.0)),
                capital, season_id, _now(), _now(),
            ))
            account_id = cur.lastrowid
            conn.commit()
            account = dict(conn.execute(
                "SELECT * FROM simulation_accounts WHERE id = ?", (account_id,)
            ).fetchone())
        finally:
            conn.close()
    return account


async def get_simulation_accounts(active_only: bool = True) -> list:
    conn = get_conn()
    try:
        where = "WHERE sa.is_active = 1" if active_only else ""
        rows  = conn.execute(f"""
            SELECT sa.*,
                   gs.name AS season_name,
                   COUNT(st.id) AS total_trades_count,
                   ROUND((sa.current_capital - sa.initial_capital)
                         / sa.initial_capital * 100, 2) AS rendement_pct
            FROM simulation_accounts sa
            LEFT JOIN gold_seasons gs      ON gs.id = sa.season_id
            LEFT JOIN simulation_trades st ON st.account_id = sa.id
            {where}
            GROUP BY sa.id
            ORDER BY sa.initial_capital ASC
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def get_simulation_account_detail(account_id: int) -> dict | None:
    conn = get_conn()
    try:
        account = conn.execute(
            "SELECT * FROM simulation_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not account:
            return None
        account = dict(account)

        trades = conn.execute("""
            SELECT st.*, gts.current_phase
            FROM simulation_trades st
            JOIN gold_trade_sessions gts ON gts.id = st.session_id
            WHERE st.account_id = ?
            ORDER BY st.opened_at ASC
        """, (account_id,)).fetchall()
        account["trades"] = [dict(t) for t in trades]

        capital_curve = []
        cap = account["initial_capital"]
        for t in account["trades"]:
            if t["result_usd"] is not None:
                cap += t["result_usd"]
            capital_curve.append({
                "capital":    round(cap, 2),
                "result_usd": t["result_usd"],
                "date":       t["closed_at"] or t["opened_at"],
            })
        account["capital_curve"] = capital_curve
        account["rendement_pct"] = round(
            (account["current_capital"] - account["initial_capital"])
            / account["initial_capital"] * 100, 2
        ) if account["initial_capital"] > 0 else 0
    finally:
        conn.close()
    return account


async def _apply_to_simulation_accounts(session_id: int, session: dict):
    """
    CORRECTION v3 : utilise calculate_lot() et calculate_gains_losses()
    au lieu de l'ancienne formule.
    """
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM simulation_trades WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0]
        if existing > 0:
            return

        accounts = conn.execute(
            "SELECT * FROM simulation_accounts WHERE is_active = 1"
        ).fetchall()

        rows_to_insert = []
        for acc in accounts:
            acc     = dict(acc)
            capital = acc["current_capital"]

            lot    = calculate_lot(capital, session["entry_price"], session["sl"])
            gains  = calculate_gains_losses(
                lot   = lot,
                entry = session["entry_price"],
                sl    = session["sl"],
                tp1   = session.get("tp1"),
                tp2   = session.get("tp2"),
                tp3   = session.get("tp3"),
            )
            tp_level, risk_pct = get_tp_level_for_capital(capital)
            risk_usd           = round(capital * risk_pct / 100, 2)

            rows_to_insert.append((
                acc["id"], session_id, session.get("season_id"),
                session["entry_price"],
                session.get("tp1"), session.get("tp2"), session.get("tp3"),
                session["sl"], session["direction"],
                capital, risk_pct, risk_usd, lot, tp_level,
                gains["perte_sl"],
                gains["gain_tp1"] or 0,
                gains["gain_tp2"],
                gains["gain_tp3"],
                _now(),
            ))
    finally:
        conn.close()

    if not rows_to_insert:
        return

    async with _db_write_lock:
        conn2 = get_conn()
        try:
            conn2.executemany("""
                INSERT OR IGNORE INTO simulation_trades
                    (account_id, session_id, season_id,
                     entry_price, tp1, tp2, tp3, sl, direction,
                     capital_before, risk_pct, risk_usd, lot_used, tp_level_target,
                     perte_sl, gain_tp1, gain_tp2, gain_tp3, opened_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows_to_insert)
            conn2.commit()
        finally:
            conn2.close()


async def _close_simulation_trades(session_id: int, close_type: str):
    conn = get_conn()
    try:
        trades = conn.execute("""
            SELECT st.*, sa.current_capital, sa.id AS acc_id, sa.peak_capital
            FROM simulation_trades st
            JOIN simulation_accounts sa ON sa.id = st.account_id
            WHERE st.session_id = ? AND st.status = 'open'
        """, (session_id,)).fetchall()
    finally:
        conn.close()

    if not trades:
        return

    async with _db_write_lock:
        conn2 = get_conn()
        try:
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

                conn2.execute("""
                    UPDATE simulation_trades
                    SET result_usd    = ?,
                        capital_after = ?,
                        exit_tp_level = ?,
                        status        = 'closed',
                        closed_at     = ?
                    WHERE id = ?
                """, (result_usd, new_capital, exit_tp, _now(), t["id"]))

                conn2.execute("""
                    UPDATE simulation_accounts SET
                        current_capital  = ?,
                        total_trades     = total_trades + 1,
                        wins             = wins + ?,
                        losses           = losses + ?,
                        peak_capital     = ?,
                        max_drawdown_pct = MAX(max_drawdown_pct, ?),
                        updated_at       = ?
                    WHERE id = ?
                """, (
                    new_capital,
                    1 if is_win else 0,
                    0 if is_win else 1,
                    peak, drawdown, _now(), t["acc_id"]
                ))

            conn2.commit()
        finally:
            conn2.close()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — ALERTES COMPTES CRAMÉS
# ══════════════════════════════════════════════════════════════════════════════

async def check_cramed_accounts(session_id: int = None) -> dict:
    conn = get_conn()
    cramed_risk = []; already_cramed = []; simulation_danger = []
    try:
        if session_id:
            entries = conn.execute("""
                SELECT gme.user_id, gme.capital_declared, gme.perte_sl, u.name
                FROM gold_member_entries gme
                LEFT JOIN users u ON u.telegram_id = gme.user_id
                WHERE gme.session_id = ? AND gme.step_reached = 'confirmed'
            """, (session_id,)).fetchall()

            for e in entries:
                capital = e["capital_declared"]
                perte   = abs(e["perte_sl"] or 0)
                apres   = capital - perte
                if apres <= 0:
                    already_cramed.append({
                        "user_id": e["user_id"], "name": e["name"],
                        "capital": capital, "perte_sl": -perte,
                        "capital_restant": apres,
                    })
                elif apres < capital * 0.3:
                    cramed_risk.append({
                        "user_id": e["user_id"], "name": e["name"],
                        "capital": capital, "perte_sl": -perte,
                        "capital_restant": round(apres, 2),
                        "pct_restant":     round(apres / capital * 100, 1),
                    })

            sim = conn.execute("""
                SELECT st.perte_sl, st.capital_before, sa.name AS account_name
                FROM simulation_trades st
                JOIN simulation_accounts sa ON sa.id = st.account_id
                WHERE st.session_id = ? AND st.status = 'open'
            """, (session_id,)).fetchall()

            for s in sim:
                apres = s["capital_before"] + (s["perte_sl"] or 0)
                if apres < s["capital_before"] * 0.3:
                    simulation_danger.append({
                        "account_name":   s["account_name"],
                        "capital":        s["capital_before"],
                        "perte_sl":       s["perte_sl"],
                        "capital_restant": round(apres, 2),
                    })
    finally:
        conn.close()

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
            await _bot.send_message(
                chat_id=ADMIN_ID, text="\n".join(lines), parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"[cramed] {e}")

    return {
        "total_danger":     total_danger,
        "cramed_risk":      cramed_risk,
        "already_cramed":   already_cramed,
        "simulation_danger": simulation_danger,
    }


async def daily_cramed_check():
    conn = get_conn()
    try:
        open_sessions = conn.execute(
            "SELECT id FROM gold_trade_sessions WHERE current_phase = 'open'"
        ).fetchall()
    finally:
        conn.close()

    results = []
    for s in open_sessions:
        result = await check_cramed_accounts(s["id"])
        results.append({"session_id": s["id"], **result})
    return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — RÈGLES TP (CRUD)
# ══════════════════════════════════════════════════════════════════════════════

async def get_tp_rules() -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM gold_tp_rules ORDER BY tp_level ASC, min_capital ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def create_tp_rule(payload: dict) -> dict:
    async with _db_write_lock:
        conn = get_conn()
        try:
            cur = conn.execute("""
                INSERT INTO gold_tp_rules
                    (rule_name, tp_level, min_capital, max_capital, risk_pct,
                     message_tp1_reached, message_tp2_reached, message_tp3_reached,
                     message_sl_touched, message_breakeven, message_partial_close,
                     message_teaser, message_confirmation)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                payload["rule_name"], int(payload["tp_level"]),
                float(payload["min_capital"]),
                float(payload["max_capital"]) if payload.get("max_capital") else None,
                float(payload["risk_pct"]),
                payload.get("message_tp1_reached"),
                payload.get("message_tp2_reached"),
                payload.get("message_tp3_reached"),
                payload.get("message_sl_touched"),
                payload.get("message_breakeven"),
                payload.get("message_partial_close"),
                payload.get("message_teaser"),
                payload.get("message_confirmation"),
            ))
            rule_id = cur.lastrowid
            conn.commit()
            rule = dict(conn.execute(
                "SELECT * FROM gold_tp_rules WHERE id = ?", (rule_id,)
            ).fetchone())
        finally:
            conn.close()
    return rule


async def update_tp_rule(rule_id: int, payload: dict) -> dict:
    fields, values = [], []
    updatable = [
        "rule_name", "tp_level", "min_capital", "max_capital", "risk_pct",
        "message_tp1_reached", "message_tp2_reached", "message_tp3_reached",
        "message_sl_touched", "message_breakeven", "message_partial_close",
        "message_teaser", "message_confirmation", "is_active",
    ]
    for col in updatable:
        if col in payload:
            fields.append(f"{col} = ?")
            values.append(payload[col])
    if not fields:
        return {"status": "nothing_to_update"}
    fields.append("updated_at = ?")
    values += [_now(), rule_id]

    async with _db_write_lock:
        conn = get_conn()
        try:
            conn.execute(
                f"UPDATE gold_tp_rules SET {', '.join(fields)} WHERE id = ?", values
            )
            conn.commit()
            rule = dict(conn.execute(
                "SELECT * FROM gold_tp_rules WHERE id = ?", (rule_id,)
            ).fetchone())
        finally:
            conn.close()
    return rule


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════════════════

async def _log_flow_event(session_id: int, user_id: int, event_type: str,
                           payload: dict = None):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO gold_flow_events
                (session_id, user_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id, user_id, event_type,
            json.dumps(payload) if payload else None,
            _now(),
        ))
        conn.commit()
    except Exception as e:
        logger.warning(f"[flow_event] {e}")
    finally:
        conn.close()


async def _notify_admin_session_closed(session_id: int, close_type: str,
                                        notified: int):
    if not _bot:
        return
    conn = get_conn()
    try:
        session = conn.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        agg = conn.execute("""
            SELECT
                ROUND(SUM(result_usd), 2)                                  AS total_result,
                ROUND(SUM(CASE WHEN result_usd > 0 THEN result_usd ELSE 0 END), 2) AS total_gains,
                ROUND(SUM(CASE WHEN result_usd < 0 THEN result_usd ELSE 0 END), 2) AS total_losses
            FROM gold_member_entries WHERE session_id = ?
        """, (session_id,)).fetchone()
    finally:
        conn.close()

    if not session:
        return

    emoji = {"tp1": "✅", "tp2": "🎯", "tp3": "🏆", "sl": "❌"}.get(close_type, "📊")
    try:
        await _bot.send_message(
            chat_id    = ADMIN_ID,
            text       = (
                f"{emoji} *Session Gold clôturée — {close_type.upper()}*\n\n"
                f"Membres notifiés : {notified}\n"
                f"Résultat global : {agg['total_result']}$\n"
                f"Gains : +{agg['total_gains']}$ | Pertes : {agg['total_losses']}$\n"
                f"Lots engagés : {session['total_lots_engaged']}"
            ),
            parse_mode = "Markdown",
        )
    except Exception as e:
        logger.warning(f"[notify_admin] {e}")