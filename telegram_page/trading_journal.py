"""
trading_journal.py — v4 MySQL
"""
import logging
logger = logging.getLogger(__name__)
import json
import asyncio
import httpx
import math

from datetime import datetime, timedelta
from pathlib import Path

from db import get_db   # ← pool MySQL

ADMIN_ID  = 571718066
MEDIA_DIR = Path("media")

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _now() -> str:
    return datetime.now().isoformat()

def _pips(entry: float, exit_: float, direction: str, decimals: int = 5) -> float:
    multiplier = 10 ** (decimals - 1)
    diff = (exit_ - entry) if direction == "long" else (entry - exit_)
    return round(diff * multiplier, 1)

def _percent(entry: float, exit_: float, direction: str) -> float:
    if entry == 0: return 0.0
    diff = (exit_ - entry) if direction == "long" else (entry - exit_)
    return round(diff / entry * 100, 2)


# ══════════════════════════════════════════════════════════════════════════════
# INIT — schéma déjà créé par schema_mysql.sql, on seed juste les paires
# ══════════════════════════════════════════════════════════════════════════════

def init_trading_tables():
    default_pairs = [
        ("EUR/USD", "forex",       10.0, 5, "EURUSDT"),
        ("GBP/USD", "forex",       10.0, 5, "GBPUSDT"),
        ("XAU/USD", "commodities",  1.0, 2, "XAUUSDT"),
        ("BTC/USD", "crypto",       1.0, 1, "BTCUSDT"),
        ("GBP/JPY", "forex",        8.2, 3, "GBPJPY"),
        ("NAS100",  "indices",      1.0, 1, "NASUSDT"),
    ]
    with get_db() as conn:
        for p in default_pairs:
            conn.execute("""
                INSERT IGNORE INTO trading_pairs
                    (symbol, category, pip_value, decimals, binance_symbol)
                VALUES (?, ?, ?, ?, ?)
            """, p)
    print("[trading_journal] Tables initialisées.")


def reset_problem_tables():
    with get_db() as conn:
        for t in ["signals", "trade_journal", "signal_participations", "followup_comments"]:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
            print(f"[DROP] {t}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — SIGNAUX
# ══════════════════════════════════════════════════════════════════════════════

async def publish_signal(payload: dict) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO signals
                (pair, direction, timeframe, entry_price, tp1, tp2, sl,
                 note, screenshot_url, category, lot_suggested, status, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NOW())
        """, (
            payload["pair"], payload["direction"],
            payload.get("timeframe", "H4"),
            float(payload["entry_price"]),
            float(payload["tp1"])  if payload.get("tp1") else None,
            float(payload["tp2"])  if payload.get("tp2") else None,
            float(payload["sl"])   if payload.get("sl")  else None,
            payload.get("note"), payload.get("screenshot_url"),
            payload.get("category", "clients_actifs"),
            payload.get("lot_suggested"),
        ))
        signal_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]
        signal    = dict(conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone())

    if _bot:
        try:
            from telegram_page.signal_broadcast import broadcast_signal
            report = await broadcast_signal(
                bot=_bot, signal=signal,
                category=payload.get("category", "clients_actifs"),
                media_url=payload.get("media_url") or payload.get("screenshot_url"),
                delay=0.08, retry=True, risk_pct=2.0,
            )
            signal["broadcast_report"] = report
        except Exception as e:
            signal["broadcast_warning"] = str(e)

    signal["id"] = signal_id
    return signal


async def get_signals(filters: dict = None) -> dict:
    f      = filters or {}
    status = f.get("status", "all")
    limit  = int(f.get("limit", 20))
    offset = int(f.get("offset", 0))
    where  = ["1=1"]; params = []

    if status != "all":
        where.append("s.status = ?"); params.append(status)
    if f.get("pair"):
        where.append("s.pair = ?");   params.append(f["pair"])
    if f.get("date_from"):
        where.append("s.published_at >= ?"); params.append(f["date_from"])
    if f.get("date_to"):
        where.append("s.published_at <= ?"); params.append(f["date_to"])

    where_sql = " AND ".join(where)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT s.*,
                COUNT(DISTINCT sp.user_id) AS total_participants,
                COUNT(DISTINCT CASE WHEN sp.response = 'in'  THEN sp.user_id END) AS count_in,
                COUNT(DISTINCT CASE WHEN sp.response = 'out' THEN sp.user_id END) AS count_out,
                COUNT(DISTINCT CASE WHEN tj.id IS NOT NULL   THEN tj.user_id END) AS journals_submitted,
                COUNT(DISTINCT fc.id) AS followup_count
            FROM signals s
            LEFT JOIN signal_participations sp ON sp.signal_id = s.id
            LEFT JOIN trade_journal tj         ON tj.signal_id = s.id
            LEFT JOIN followup_comments fc     ON fc.signal_id = s.id
            WHERE {where_sql}
            GROUP BY s.id
            ORDER BY s.published_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) as n FROM signals s WHERE {where_sql}", params
        ).fetchone()["n"]

    signals = []
    for r in rows:
        d = dict(r)
        if d.get("entry_price") and d.get("tp1") and d.get("sl"):
            tp_dist = abs(d["tp1"] - d["entry_price"])
            sl_dist = abs(d["entry_price"] - d["sl"])
            d["rr_ratio"] = round(tp_dist / sl_dist, 2) if sl_dist > 0 else None
        else:
            d["rr_ratio"] = None
        d["pips_to_tp1"] = None; d["pips_to_sl"] = None
        signals.append(d)

    return {"signals": signals, "total": total, "limit": limit, "offset": offset}


async def get_signal_detail(signal_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not row: return None
        signal = dict(row)

        parts = conn.execute("""
            SELECT sp.user_id, sp.response, sp.responded_at, u.name
            FROM signal_participations sp
            LEFT JOIN users u ON u.telegram_id = sp.user_id
            WHERE sp.signal_id = ? ORDER BY sp.responded_at DESC
        """, (signal_id,)).fetchall()
        signal["participations"] = [dict(p) for p in parts]

        behaviors = conn.execute("""
            SELECT behavior, COUNT(*) AS count,
                ROUND(AVG(result_percent), 2) AS avg_pct,
                ROUND(AVG(result_pips), 1)    AS avg_pips
            FROM trade_journal WHERE signal_id = ? AND participated = 1
            GROUP BY behavior
        """, (signal_id,)).fetchall()
        signal["behaviors"] = [dict(b) for b in behaviors]

        followups = conn.execute("""
            SELECT * FROM followup_comments WHERE signal_id = ? ORDER BY sent_at DESC
        """, (signal_id,)).fetchall()
        signal["followup_comments"] = [dict(f) for f in followups]

        stats = conn.execute("""
            SELECT
                COUNT(*) AS total_journals,
                ROUND(AVG(result_percent), 2) AS avg_result_percent,
                ROUND(AVG(result_pips), 1)    AS avg_pips,
                COUNT(CASE WHEN result_percent > 0 THEN 1 END) AS wins,
                COUNT(CASE WHEN result_percent < 0 THEN 1 END) AS losses,
                COUNT(CASE WHEN behavior = 'disciplined' THEN 1 END) AS disciplined,
                COUNT(CASE WHEN behavior = 'early_exit'  THEN 1 END) AS early_exits,
                COUNT(CASE WHEN behavior = 'sl_skip'     THEN 1 END) AS sl_skips
            FROM trade_journal WHERE signal_id = ? AND participated = 1
        """, (signal_id,)).fetchone()
        signal["journal_stats"] = dict(stats) if stats else {}

    return signal


async def close_signal(signal_id: int, payload: dict) -> dict:
    with get_db() as conn:
        signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal: return {"error": "Signal introuvable"}
        signal = dict(signal)

        close_price    = float(payload["close_price"])
        result_pips    = _pips(signal["entry_price"], close_price, signal["direction"],
                                _get_pair_decimals(signal["pair"]))
        result_percent = _percent(signal["entry_price"], close_price, signal["direction"])

        conn.execute("""
            UPDATE signals SET status='closed', close_price=?, close_result=?,
                close_screenshot=?, result_pips=?, result_percent=?, closed_at=NOW()
            WHERE id=?
        """, (close_price, payload["close_result"], payload.get("close_screenshot"),
              result_pips, result_percent, signal_id))

        form_command = ""
        if payload.get("form_id"):
            try:
                form = conn.execute("SELECT command FROM forms WHERE id=?", (payload["form_id"],)).fetchone()
                if form and form["command"]:
                    form_command = f"\n\n📋 *Remplis ton journal de trade :*\n{form['command']}"
            except Exception:
                pass

        updated = dict(conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone())

    if _bot:
        try:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT user_id FROM signal_participations WHERE signal_id=? AND response='in'
                """, (signal_id,)).fetchall()
            members_in = [r["user_id"] for r in rows]

            result_map = {
                "tp":        f"✅ *TP atteint sur {updated['pair']}*\n\n📈 Résultat : *+{result_pips} pips ({result_percent}%)*\n\nBravo 🎉{form_command}",
                "sl":        f"❌ *SL touché sur {updated['pair']}*\n\n📉 Résultat : *{result_pips} pips ({result_percent}%)*{form_command}",
                "partial":   f"⚡ *Clôture partielle sur {updated['pair']}*\n\n📊 {result_pips} pips ({result_percent}%){form_command}",
                "cancelled": f"🚫 *Signal {updated['pair']} annulé*",
            }
            message = result_map.get(payload["close_result"], f"Signal {updated['pair']} clôturé.")

            for user_id in members_in:
                try:
                    await _bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"Close notif uid={user_id}: {e}")
        except Exception as e:
            updated["notif_warning"] = str(e)

    if payload.get("form_id") and payload.get("send_form_to") and _bot:
        try:
            from form.form_engine import broadcast_form
            if payload.get("send_form_to") == "participated":
                with get_db() as conn:
                    rows = conn.execute("""
                        SELECT user_id FROM signal_participations WHERE signal_id=? AND response='in'
                    """, (signal_id,)).fetchall()
                user_ids = [r["user_id"] for r in rows]
            else:
                user_ids = await _get_signal_recipients(signal_id)
            await broadcast_form(bot=_bot, form_id=payload["form_id"],
                                  user_ids=user_ids, admin_id=ADMIN_ID)
            updated["form_sent_to"] = len(user_ids)
        except Exception as e:
            updated["form_warning"] = str(e)

    return updated


async def record_participation(signal_id: int, user_id: int, response: str) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO signal_participations (signal_id, user_id, response, responded_at)
            VALUES (?, ?, ?, NOW())
            ON DUPLICATE KEY UPDATE response = VALUES(response), responded_at = NOW()
        """, (signal_id, user_id, response))
    return {"status": "ok", "signal_id": signal_id, "user_id": user_id, "response": response}


async def send_followup_comment(signal_id: int, payload: dict) -> dict:
    with get_db() as conn:
        signal = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not signal: return {"error": "Signal introuvable"}
        signal = dict(signal)

        rows = conn.execute("""
            SELECT user_id FROM signal_participations WHERE signal_id=? AND response='in'
        """, (signal_id,)).fetchall()
        user_ids = [r["user_id"] for r in rows]

        type_emojis = {"update": "🔔", "invalidation": "⚠️", "secure": "🔒", "encourage": "💪"}
        emoji = type_emojis.get(payload["type"], "📌")

        conn.execute("""
            INSERT INTO followup_comments (signal_id, type, message, screenshot_url, sent_at)
            VALUES (?, ?, ?, ?, NOW())
        """, (signal_id, payload["type"], payload["message"], payload.get("screenshot_url")))
        comment_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]

    broadcast_id = None
    if user_ids and _bot:
        try:
            from telegram_page.broadcast_engine import broadcast_engine
            full_message = (
                f"{emoji} *{payload['type'].replace('_', ' ').title()} — "
                f"{signal['pair']}*\n\n{payload['message']}"
            )
            bc_payload = {"message": full_message, "format": "text",
                          "user_ids": user_ids, "tag": f"followup_{comment_id}_{signal_id}", "delay": 0.05}
            if payload.get("screenshot_url"):
                bc_payload["format"] = "image+text"; bc_payload["media_url"] = payload["screenshot_url"]
            await broadcast_engine(_bot, bc_payload)

            with get_db() as conn:
                bh = conn.execute("SELECT id FROM broadcast_history ORDER BY id DESC LIMIT 1").fetchone()
                if bh:
                    broadcast_id = bh["id"]
                    conn.execute("UPDATE followup_comments SET broadcast_id=? WHERE id=?",
                                  (broadcast_id, comment_id))
        except Exception as e:
            return {"error": str(e), "comment_id": comment_id}

    return {"comment_id": comment_id, "signal_id": signal_id,
            "type": payload["type"], "sent_to": len(user_ids), "broadcast_id": broadcast_id}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — JOURNAL MEMBRES
# ══════════════════════════════════════════════════════════════════════════════

async def submit_trade_result(signal_id: int, user_id: int, payload: dict) -> dict:
    with get_db() as conn:
        signal = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not signal: return {"error": "Signal introuvable"}
        signal = dict(signal)

        entry    = float(payload.get("entry_price") or signal["entry_price"])
        exit_p   = float(payload["exit_price"]) if payload.get("exit_price") else None
        decimals = _get_pair_decimals(signal["pair"])
        pips     = _pips(entry, exit_p, signal["direction"], decimals) if exit_p else None
        pct      = _percent(entry, exit_p, signal["direction"])        if exit_p else None
        lot      = float(payload.get("lot_used") or 0)
        pair_pv  = _get_pip_value(signal["pair"])
        gain_usd = round(pips * lot * pair_pv, 2) if (pips is not None and lot > 0) else None

        conn.execute("""
            INSERT INTO trade_journal
                (signal_id, user_id, participated, entry_price, exit_price,
                 result_pips, result_percent, gain_usd, lot_used,
                 behavior, screenshot_url, capital_before, capital_after, submitted_at, status)
            VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,NOW(),'closed')
            ON DUPLICATE KEY UPDATE
                exit_price=VALUES(exit_price), result_pips=VALUES(result_pips),
                result_percent=VALUES(result_percent), gain_usd=VALUES(gain_usd),
                lot_used=VALUES(lot_used), behavior=VALUES(behavior),
                screenshot_url=VALUES(screenshot_url), capital_before=VALUES(capital_before),
                capital_after=VALUES(capital_after), submitted_at=VALUES(submitted_at)
        """, (signal_id, user_id, entry, exit_p, pips, pct, gain_usd, lot,
              payload.get("behavior"), payload.get("screenshot_url"),
              payload.get("capital_before"), payload.get("capital_after")))

        if payload.get("capital_after"):
            conn.execute("""
                INSERT INTO member_capital (user_id, capital, type, declared_at, source)
                VALUES (?, ?, 'gains', NOW(), 'trade_result')
            """, (user_id, float(payload["capital_after"])))

        result = dict(conn.execute("""
            SELECT * FROM trade_journal WHERE signal_id=? AND user_id=?
        """, (signal_id, user_id)).fetchone())

    return result


async def get_history(filters: dict = None) -> dict:
    f      = filters or {}
    limit  = int(f.get("limit", 50)); offset = int(f.get("offset", 0))
    status = f.get("status", "all")
    where  = ["s.status = 'closed'"]; params = []

    if status == "took":   where.append("tj.participated = 1")
    elif status == "skip": where.append("(tj.participated = 0 OR tj.user_id IS NULL)")
    if f.get("member_id"):
        where.append("(sp.user_id = ? OR tj.user_id = ?)"); params += [f["member_id"], f["member_id"]]
    if f.get("signal_id"):
        where.append("s.id = ?"); params.append(f["signal_id"])
    if f.get("pair"):
        where.append("s.pair = ?"); params.append(f["pair"])
    if f.get("search"):
        where.append("(u.name LIKE ? OR s.pair LIKE ?)"); term = f"%{f['search']}%"; params += [term, term]
    if f.get("date_from"):
        where.append("s.published_at >= ?"); params.append(f["date_from"])

    where_sql = " AND ".join(where)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT u.telegram_id AS member_id, u.name AS member_name,
                s.id AS signal_id, s.pair, s.direction,
                s.close_result AS signal_result, s.result_pips AS admin_pips,
                s.result_percent AS admin_pct, sp.response AS participation,
                tj.entry_price, tj.exit_price, tj.result_pips, tj.result_percent,
                tj.gain_usd, tj.capital_after, tj.behavior,
                tj.screenshot_url AS capture_url, tj.submitted_at,
                COALESCE(tj.participated, 0) AS took_trade
            FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            JOIN users u                  ON u.telegram_id = sp.user_id
            LEFT JOIN trade_journal tj    ON tj.signal_id = s.id AND tj.user_id = sp.user_id
            WHERE {where_sql}
            ORDER BY s.published_at DESC, u.name ASC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        total = conn.execute(f"""
            SELECT COUNT(*) as n FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            JOIN users u                  ON u.telegram_id = sp.user_id
            LEFT JOIN trade_journal tj    ON tj.signal_id = s.id AND tj.user_id = sp.user_id
            WHERE {where_sql}
        """, params).fetchone()["n"]

    return {"history": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


async def get_crossed_performance(filters: dict = None) -> dict:
    f      = filters or {}
    period = f.get("period", "day")
    pair   = f.get("pair"); member = f.get("member_id")

    date_fmt = {"day": "%Y-%m-%d", "week": "%Y-%u", "month": "%Y-%m"}.get(period, "%Y-%m-%d")

    where_admin   = ["s.status = 'closed'"]; params_admin   = []
    where_members = ["s.status = 'closed'", "tj.participated = 1"]; params_members = []
    if pair:
        where_admin.append("s.pair = ?"); params_admin.append(pair)
        where_members.append("s.pair = ?"); params_members.append(pair)
    if member:
        where_members.append("tj.user_id = ?"); params_members.append(member)

    with get_db() as conn:
        admin_rows = conn.execute(f"""
            SELECT DATE_FORMAT(s.closed_at, '{date_fmt}') AS period,
                SUM(s.result_percent) AS total_pct, COUNT(*) AS trades
            FROM signals s WHERE {' AND '.join(where_admin)}
            GROUP BY period ORDER BY period ASC
        """, params_admin).fetchall()

        members_rows = conn.execute(f"""
            SELECT DATE_FORMAT(s.closed_at, '{date_fmt}') AS period,
                AVG(tj.result_percent) AS avg_pct, COUNT(*) AS journals
            FROM trade_journal tj JOIN signals s ON s.id = tj.signal_id
            WHERE {' AND '.join(where_members)}
            GROUP BY period ORDER BY period ASC
        """, params_members).fetchall()

        cap_rows = conn.execute(f"""
            SELECT DATE_FORMAT(mc.declared_at, '{date_fmt}') AS period,
                AVG(mc.capital) AS avg_capital
            FROM member_capital mc GROUP BY period ORDER BY period ASC
        """).fetchall()

    admin_curve = []; cumul = 0.0
    for r in admin_rows:
        cumul += (r["total_pct"] or 0)
        admin_curve.append({"period": r["period"], "cumulative_pct": round(cumul, 2), "trades": r["trades"]})

    members_curve = []; cumul = 0.0
    for r in members_rows:
        cumul += (r["avg_pct"] or 0)
        members_curve.append({"period": r["period"], "cumulative_pct": round(cumul, 2), "journals": r["journals"]})

    return {"admin_curve": admin_curve, "members_curve": members_curve,
            "capital_curve": [dict(r) for r in cap_rows], "period": period}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PERFORMANCES MEMBRES
# ══════════════════════════════════════════════════════════════════════════════

async def get_member_performance(user_id: int) -> dict | None:
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if not user: return None
        user = dict(user)

        stats = conn.execute("""
            SELECT COUNT(*) AS total_trades,
                COUNT(CASE WHEN result_percent > 0 THEN 1 END) AS wins,
                COUNT(CASE WHEN result_percent < 0 THEN 1 END) AS losses,
                CASE WHEN COUNT(*)=0 THEN NULL
                ELSE ROUND(COUNT(CASE WHEN result_percent>0 THEN 1 END)/COUNT(*)*100,1) END AS win_rate,
                ROUND(SUM(result_percent),2) AS perf_totale,
                ROUND(AVG(result_percent),2) AS avg_result,
                ROUND(SUM(gain_usd),2) AS total_gain_usd
            FROM trade_journal WHERE user_id=? AND participated=1 AND status='closed'
        """, (user_id,)).fetchone()
        stats = dict(stats) if stats else {}

        engagement = conn.execute("""
            SELECT COUNT(DISTINCT sp.signal_id) AS signals_received,
                COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END) AS signals_answered,
                COUNT(DISTINCT CASE WHEN sp.response='in' THEN sp.signal_id END) AS signals_taken
            FROM signal_participations sp WHERE sp.user_id=?
        """, (user_id,)).fetchone()
        if engagement and engagement["signals_received"] > 0:
            stats["engagement_rate"]  = round(engagement["signals_answered"] / engagement["signals_received"] * 100, 1)
            stats["signals_taken"]    = engagement["signals_taken"]
            stats["signals_received"] = engagement["signals_received"]
        else:
            stats["engagement_rate"] = 0

        cap_initial = conn.execute("""
            SELECT capital FROM member_capital WHERE user_id=? AND type='initial'
            ORDER BY declared_at ASC LIMIT 1
        """, (user_id,)).fetchone()
        cap_actuel = conn.execute("""
            SELECT capital, declared_at FROM member_capital WHERE user_id=?
            ORDER BY declared_at DESC LIMIT 1
        """, (user_id,)).fetchone()

        capital_initial = float(cap_initial["capital"]) if cap_initial else None
        capital_actuel  = float(cap_actuel["capital"])  if cap_actuel  else None
        evolution_pct   = None
        if capital_initial and capital_actuel and capital_initial > 0:
            evolution_pct = round((capital_actuel - capital_initial) / capital_initial * 100, 2)

        twenty_one_ago = (datetime.now() - timedelta(days=21)).isoformat()
        cap_rows = conn.execute("""
            SELECT DATE(declared_at) AS day, capital, type FROM member_capital
            WHERE user_id=? AND declared_at>=? ORDER BY declared_at ASC
        """, (user_id, twenty_one_ago)).fetchall()
        capital_21j = []; prev_cap = None
        for r in cap_rows:
            cap = float(r["capital"])
            direction = "flat" if prev_cap is None else ("up" if cap > prev_cap else ("down" if cap < prev_cap else "flat"))
            capital_21j.append({"date": r["day"], "capital": cap, "type": direction})
            prev_cap = cap

        theo_row = conn.execute("""
            SELECT ROUND(SUM(CASE WHEN s.close_result='tp' THEN s.result_percent ELSE 0 END),2) AS wins,
                ROUND(SUM(CASE WHEN s.close_result='sl' THEN s.result_percent ELSE 0 END),2) AS losses
            FROM signals s JOIN signal_participations sp ON sp.signal_id=s.id
            WHERE sp.user_id=? AND sp.response='in' AND s.status='closed'
        """, (user_id,)).fetchone()
        capital_theorique = None; manque_a_gagner = None
        if capital_initial and theo_row:
            cumul_theo = (theo_row["wins"] or 0) + (theo_row["losses"] or 0)
            capital_theorique = round(capital_initial * (1 + cumul_theo / 100), 2)
            if capital_actuel: manque_a_gagner = round(capital_theorique - capital_actuel, 2)

        perf_curve_rows = conn.execute("""
            SELECT tj.signal_id, s.pair, s.direction, tj.result_percent, tj.result_pips,
                tj.behavior, tj.gain_usd, s.close_result, s.closed_at AS trade_date
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND tj.participated=1 AND tj.status='closed'
            ORDER BY s.closed_at ASC
        """, (user_id,)).fetchall()
        performance_curve = []; cumul = 0.0
        for r in perf_curve_rows:
            cumul += (r["result_percent"] or 0)
            d = dict(r); d["cumulative_pct"] = round(cumul, 2); performance_curve.append(d)

        behaviors = conn.execute("""
            SELECT behavior, COUNT(*) AS count, ROUND(AVG(result_percent),2) AS avg_pct
            FROM trade_journal WHERE user_id=? AND participated=1 GROUP BY behavior
        """, (user_id,)).fetchall()

        lot_r = conn.execute("""
            SELECT COUNT(*) AS total,
                COUNT(CASE WHEN tj.lot_used<=(s.lot_suggested*1.1) AND tj.lot_used>0 THEN 1 END) AS respected
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND s.lot_suggested IS NOT NULL AND tj.lot_used IS NOT NULL
        """, (user_id,)).fetchone()
        lot_respect_rate = round(lot_r["respected"]/lot_r["total"]*100,1) if lot_r and lot_r["total"]>0 else None

        suivi = conn.execute("""
            SELECT COUNT(*) as n FROM member_capital WHERE user_id=? AND source='form'
            AND declared_at>=?
        """, (user_id, (datetime.now()-timedelta(days=10)).isoformat())).fetchone()["n"]

    return {
        "user_id": user_id, "name": user.get("name"), "stats": stats,
        "capital_initial": capital_initial, "capital_actuel": capital_actuel,
        "evolution_pct": evolution_pct, "capital_21j": capital_21j,
        "capital_theorique": capital_theorique, "manque_a_gagner": manque_a_gagner,
        "performance_curve": performance_curve, "behaviors": [dict(b) for b in behaviors],
        "lot_respect_rate": lot_respect_rate, "suivi_capital_actif": suivi > 0,
    }


async def get_performances_list(filters: dict = None) -> dict:
    f = filters or {}
    limit = int(f.get("limit", 50)); offset = int(f.get("offset", 0))
    sort_by = f.get("sort_by", "win_rate"); search = f.get("search", "")
    order_map = {"win_rate": "win_rate DESC", "discipline": "disciplined_count DESC",
                 "engagement": "engagement_rate DESC", "capital": "capital_actuel DESC", "perf": "perf_totale DESC"}
    order_sql = order_map.get(sort_by, "win_rate DESC")
    where = ["1=1"]; params = []
    if search: where.append("u.name LIKE ?"); params.append(f"%{search}%")
    where_sql = " AND ".join(where)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT u.telegram_id AS user_id, u.name,
                COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END) AS total_trades,
                COUNT(DISTINCT CASE WHEN tj.participated=1 AND tj.result_percent>0 THEN tj.signal_id END) AS wins,
                CASE WHEN COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END)=0 THEN NULL
                ELSE ROUND(COUNT(DISTINCT CASE WHEN tj.participated=1 AND tj.result_percent>0 THEN tj.signal_id END)
                     /COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END)*100,1) END AS win_rate,
                ROUND(SUM(CASE WHEN tj.participated=1 THEN tj.result_percent ELSE 0 END),2) AS perf_totale,
                COUNT(DISTINCT CASE WHEN tj.behavior='disciplined' THEN tj.signal_id END) AS disciplined_count,
                COUNT(DISTINCT sp.signal_id) AS signals_received,
                CASE WHEN COUNT(DISTINCT sp.signal_id)=0 THEN 0
                ELSE ROUND(COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END)
                     /COUNT(DISTINCT sp.signal_id)*100,1) END AS engagement_rate,
                mc_last.capital AS capital_actuel,
                mc_last.capital_delta_pct AS capital_evolution_pct,
                CASE WHEN mc_last.capital IS NULL THEN 'no_tracking' ELSE 'active' END AS suivi_status
            FROM users u
            LEFT JOIN trade_journal tj ON tj.user_id=u.telegram_id
            LEFT JOIN signal_participations sp ON sp.user_id=u.telegram_id
            LEFT JOIN (
                SELECT mc1.user_id, mc1.capital,
                    CASE WHEN mc_init.capital>0
                    THEN ROUND((mc1.capital-mc_init.capital)/mc_init.capital*100,2) ELSE NULL END AS capital_delta_pct
                FROM member_capital mc1
                LEFT JOIN (SELECT user_id, capital FROM member_capital WHERE type='initial') mc_init
                    ON mc_init.user_id=mc1.user_id
                WHERE mc1.declared_at=(SELECT MAX(declared_at) FROM member_capital WHERE user_id=mc1.user_id)
            ) mc_last ON mc_last.user_id=u.telegram_id
            WHERE {where_sql}
            AND EXISTS (SELECT 1 FROM signal_participations WHERE user_id=u.telegram_id)
            GROUP BY u.telegram_id ORDER BY {order_sql} LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        total = conn.execute(f"""
            SELECT COUNT(DISTINCT u.telegram_id) as n FROM users u WHERE {where_sql}
            AND EXISTS (SELECT 1 FROM signal_participations WHERE user_id=u.telegram_id)
        """, params).fetchone()["n"]

    return {"members": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CLASSEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def get_leaderboard(filters: dict = None) -> dict:
    f = filters or {}
    period = f.get("period", "all"); min_trades = int(f.get("min_trades", 3))
    limit = int(f.get("limit", 50)); offset = int(f.get("offset", 0))
    date_filter = ""; params = []
    if period == "week":
        date_filter = "AND s.closed_at >= ?"; params.append((datetime.now()-timedelta(days=7)).isoformat())
    elif period == "month":
        date_filter = "AND s.closed_at >= ?"; params.append((datetime.now()-timedelta(days=30)).isoformat())

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT u.telegram_id AS user_id, u.name,
                COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END) AS total_trades,
                ROUND(COUNT(DISTINCT CASE WHEN tj.participated=1 AND tj.result_percent>0 THEN tj.signal_id END)
                     /NULLIF(COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END),0)*100,1) AS win_rate,
                ROUND(SUM(CASE WHEN tj.participated=1 THEN tj.result_percent ELSE 0 END),2) AS perf_totale,
                CASE WHEN COUNT(DISTINCT sp.signal_id)=0 THEN 0
                ELSE ROUND(COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END)
                     /COUNT(DISTINCT sp.signal_id)*100,1) END AS engagement_rate,
                mc_last.capital AS capital_actuel,
                CASE WHEN mc_last.capital IS NULL THEN 1 ELSE 0 END AS suivi_off
            FROM users u
            JOIN trade_journal tj ON tj.user_id=u.telegram_id {date_filter}
            LEFT JOIN signals s ON s.id=tj.signal_id
            LEFT JOIN signal_participations sp ON sp.user_id=u.telegram_id
            LEFT JOIN (SELECT user_id, capital FROM member_capital
                       WHERE declared_at=(SELECT MAX(declared_at) FROM member_capital mc2
                                          WHERE mc2.user_id=member_capital.user_id)) mc_last
                ON mc_last.user_id=u.telegram_id
            WHERE tj.participated=1 AND tj.status='closed'
            GROUP BY u.telegram_id HAVING total_trades>=?
            ORDER BY perf_totale DESC LIMIT ? OFFSET ?
        """, params + [min_trades, limit, offset]).fetchall()
        total_row = conn.execute(f"""
            SELECT COUNT(DISTINCT tj.user_id) as n FROM trade_journal tj
            LEFT JOIN signals s ON s.id=tj.signal_id
            WHERE tj.participated=1 AND tj.status='closed' {date_filter}
            GROUP BY tj.user_id HAVING COUNT(DISTINCT tj.signal_id)>=?
        """, params + [min_trades]).fetchone()

    leaderboard = []
    for rank, r in enumerate(rows, start=1+offset):
        d = dict(r); d["rank"] = rank; leaderboard.append(d)

    return {"leaderboard": leaderboard, "total": total_row["n"] if total_row else 0,
            "period": period, "min_trades": min_trades}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PAIRES & PIP
# ══════════════════════════════════════════════════════════════════════════════

async def get_pairs(active_only: bool = False) -> list:
    with get_db() as conn:
        where = "WHERE is_active=1" if active_only else ""
        rows  = conn.execute(f"SELECT * FROM trading_pairs {where} ORDER BY category, symbol").fetchall()
    return [dict(r) for r in rows]


async def create_pair(payload: dict) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO trading_pairs (symbol, category, pip_value, decimals, binance_symbol, note, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (payload["symbol"].upper(), payload.get("category","forex"), float(payload["pip_value"]),
              int(payload.get("decimals",5)), payload.get("binance_symbol"), payload.get("note")))
        new_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]
        return dict(conn.execute("SELECT * FROM trading_pairs WHERE id=?", (new_id,)).fetchone())


async def update_pair(pair_id: int, payload: dict) -> dict:
    fields, values = [], []
    for col in ("symbol","category","pip_value","decimals","binance_symbol","is_active","note"):
        if col in payload: fields.append(f"{col}=?"); values.append(payload[col])
    if not fields: return {"status": "nothing_to_update"}
    fields.append("updated_at=NOW()"); values.append(pair_id)
    with get_db() as conn:
        conn.execute(f"UPDATE trading_pairs SET {', '.join(fields)} WHERE id=?", values)
        return dict(conn.execute("SELECT * FROM trading_pairs WHERE id=?", (pair_id,)).fetchone())


async def delete_pair(pair_id: int) -> dict:
    with get_db() as conn:
        conn.execute("UPDATE trading_pairs SET is_active=0, updated_at=NOW() WHERE id=?", (pair_id,))
    return {"status": "deactivated", "id": pair_id}


def calculate_lot(capital, risk_pct, sl_pips, pair_symbol, tp1_pips=0):
    pip_value = _get_pip_value(pair_symbol)
    risk_usd  = round(capital * risk_pct / 100, 2)
    lot       = round(risk_usd / (sl_pips * pip_value), 4) if sl_pips > 0 else 0
    gain_tp1  = round(lot * tp1_pips * pip_value, 2) if tp1_pips > 0 else 0
    rr_ratio  = round(tp1_pips / sl_pips, 2) if (sl_pips > 0 and tp1_pips > 0) else None
    return {"risk_usd": risk_usd, "lot_suggested": lot, "max_loss": -risk_usd,
            "gain_tp1": gain_tp1, "rr_ratio": rr_ratio, "pip_value_used": pip_value}


async def get_suggested_lot_for_signal(signal_id, risk_pct=2.0):
    with get_db() as conn:
        signal = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not signal: return {"error": "Signal introuvable"}
        signal = dict(signal)
        cat_members = conn.execute("""
            SELECT DISTINCT u.telegram_id AS user_id, u.name, mc.capital
            FROM categories c JOIN users u ON u.telegram_id=c.id_user
            LEFT JOIN (SELECT user_id, capital FROM member_capital
                       WHERE declared_at=(SELECT MAX(declared_at) FROM member_capital mc2
                                          WHERE mc2.user_id=member_capital.user_id)) mc
                ON mc.user_id=u.telegram_id
            WHERE c.name_categorie=?
        """, (signal["category"],)).fetchall()

    if not signal.get("sl") or not signal.get("entry_price"):
        return {"error": "Signal sans SL — calcul impossible"}

    decimals = _get_pair_decimals(signal["pair"])
    sl_pips  = abs(_pips(signal["entry_price"], signal["sl"], signal["direction"], decimals))
    per_member = []
    for m in cat_members:
        capital = float(m["capital"]) if m["capital"] else 1000.0
        result  = calculate_lot(capital, risk_pct, sl_pips, signal["pair"])
        per_member.append({"user_id": m["user_id"], "name": m["name"],
                           "capital": capital, "lot": result["lot_suggested"]})
    avg_capital = round(sum(m["capital"] for m in per_member)/len(per_member),2) if per_member else 0
    avg_lot     = round(sum(m["lot"] for m in per_member)/len(per_member),4)     if per_member else 0
    return {"signal_id": signal_id, "pair": signal["pair"], "sl_pips": sl_pips,
            "risk_pct": risk_pct, "avg_capital": avg_capital, "avg_lot": avg_lot, "per_member": per_member}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — FORMULAIRES & COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

async def get_form_stats() -> dict:
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(DISTINCT f.id) AS total_forms, COUNT(DISTINCT fr.id) AS total_responses,
                COUNT(DISTINCT fr.telegram_id) AS unique_respondents,
                COUNT(DISTINCT CASE WHEN f.type='system' THEN f.id END) AS system_forms,
                COUNT(DISTINCT CASE WHEN f.type='custom' THEN f.id END) AS custom_forms
            FROM forms f LEFT JOIN form_responses fr ON fr.form_id=f.id
        """).fetchone()
        stats = dict(row)
        completion = conn.execute("""
            SELECT COUNT(*) AS total_sessions,
                COUNT(CASE WHEN status='completed' THEN 1 END) AS completed_sessions
            FROM form_sessions
        """).fetchone()
        stats["completion_rate"] = round(
            completion["completed_sessions"]/completion["total_sessions"]*100,1
        ) if completion and completion["total_sessions"]>0 else 0
    return stats


async def get_forms_list() -> list:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT f.id, f.name, f.command, f.type, f.is_active, f.created_at,
                COUNT(DISTINCT fr.telegram_id) AS respondents,
                COUNT(DISTINCT fr.id) AS total_responses,
                MAX(fr.created_at) AS last_response_at
            FROM forms f LEFT JOIN form_responses fr ON fr.form_id=f.id
            GROUP BY f.id ORDER BY f.type DESC, f.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


async def get_form_field_mapping(form_id: int) -> dict:
    with get_db() as conn:
        form = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
        if not form: return {"error": "Formulaire introuvable"}
        form = dict(form)
        fields = json.loads(form.get("fields","[]"))
        enriched = []
        for field in fields:
            fid = field.get("id"); samples = []
            if fid:
                sample_rows = conn.execute("""
                    SELECT value, created_at FROM form_responses
                    WHERE form_id=? AND field_id=? ORDER BY created_at DESC LIMIT 5
                """, (form_id, fid)).fetchall()
                samples = [{"value": r["value"], "at": r["created_at"]} for r in sample_rows]
            enriched.append({"field_id": fid, "field_label": field.get("label"),
                              "field_type": field.get("type"), "maps_to_stat": field.get("maps_to_stat"),
                              "aggregation": field.get("aggregation","last"),
                              "data_type": field.get("data_type","text"),
                              "required": field.get("required",True), "sample_values": samples})
    return {"form_id": form_id, "form_name": form.get("name"),
            "form_type": form.get("type"), "fields": enriched}


async def update_form_field_mapping(form_id: int, payload: dict) -> dict:
    with get_db() as conn:
        form = conn.execute("SELECT fields FROM forms WHERE id=?", (form_id,)).fetchone()
        if not form: return {"error": "Formulaire introuvable"}
        fields  = json.loads(form["fields"] or "[]")
        updates = {str(f["field_id"]): f for f in payload.get("fields",[])}
        for field in fields:
            fid = str(field.get("id",""))
            if fid in updates:
                upd = updates[fid]
                for k in ("maps_to_stat","aggregation","data_type"):
                    if k in upd: field[k] = upd[k]
        conn.execute("UPDATE forms SET fields=? WHERE id=?",
                     (json.dumps(fields, ensure_ascii=False), form_id))
    return {"status": "updated", "form_id": form_id}


async def get_collected_data_summary() -> list:
    with get_db() as conn:
        forms = conn.execute("""
            SELECT f.id, f.name, f.type, f.fields,
                COUNT(DISTINCT fr.telegram_id) AS total_responses,
                MAX(fr.created_at) AS last_response_at
            FROM forms f LEFT JOIN form_responses fr ON fr.form_id=f.id GROUP BY f.id
        """).fetchall()
    summary = []
    for f in forms:
        d = dict(f)
        try: fields = json.loads(d.get("fields") or "[]")
        except: fields = []
        summary.append({
            "form_id": d["id"], "form_name": d["name"], "form_type": d["type"],
            "fields_collected": [fld.get("label") for fld in fields if fld.get("label")],
            "stats_produced": list({fld.get("maps_to_stat") for fld in fields if fld.get("maps_to_stat")}),
            "total_responses": d["total_responses"], "last_response_at": d["last_response_at"],
        })
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — BILAN IA
# ══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL   = "claude-sonnet-4-20250514"


async def _build_member_bilan_context(user_id, week_start, week_end, week_label, admin_config):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if not user: return None
        name = dict(user).get("name","l'ami")

        perf = dict(conn.execute("""
            SELECT COUNT(*) AS total_trades,
                COUNT(CASE WHEN tj.result_percent>0 THEN 1 END) AS wins,
                COUNT(CASE WHEN tj.result_percent<0 THEN 1 END) AS losses,
                CASE WHEN COUNT(*)=0 THEN NULL
                ELSE ROUND(COUNT(CASE WHEN tj.result_percent>0 THEN 1 END)/COUNT(*)*100,1) END AS win_rate,
                ROUND(SUM(tj.result_percent),2) AS perf_totale,
                ROUND(AVG(tj.result_percent),2) AS avg_result
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND tj.participated=1 AND s.closed_at>=? AND s.closed_at<=?
        """, (user_id, week_start, week_end)).fetchone() or {})

        best  = conn.execute("""SELECT s.pair, tj.result_percent, s.closed_at FROM trade_journal tj
            JOIN signals s ON s.id=tj.signal_id WHERE tj.user_id=? AND s.closed_at>=? AND s.closed_at<=?
            ORDER BY tj.result_percent DESC LIMIT 1""", (user_id, week_start, week_end)).fetchone()
        worst = conn.execute("""SELECT s.pair, tj.result_percent, s.closed_at FROM trade_journal tj
            JOIN signals s ON s.id=tj.signal_id WHERE tj.user_id=? AND s.closed_at>=? AND s.closed_at<=?
            ORDER BY tj.result_percent ASC LIMIT 1""", (user_id, week_start, week_end)).fetchone()

        beh = dict(conn.execute("""
            SELECT COUNT(*) AS total,
                COUNT(CASE WHEN behavior='disciplined' THEN 1 END) AS disciplined,
                COUNT(CASE WHEN behavior='early_exit'  THEN 1 END) AS early_exit,
                COUNT(CASE WHEN behavior='sl_skip'     THEN 1 END) AS sl_skip
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND tj.participated=1 AND s.closed_at>=? AND s.closed_at<=?
        """, (user_id, week_start, week_end)).fetchone() or {})
        total_beh = beh.get("total") or 1

        early_cost = conn.execute("""
            SELECT ROUND(SUM(s.result_percent-tj.result_percent),2) AS cost
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND tj.behavior='early_exit' AND s.closed_at>=? AND s.closed_at<=?
        """, (user_id, week_start, week_end)).fetchone()

        cap_actuel  = conn.execute("SELECT capital FROM member_capital WHERE user_id=? ORDER BY declared_at DESC LIMIT 1", (user_id,)).fetchone()
        cap_initial = conn.execute("SELECT capital FROM member_capital WHERE user_id=? AND type='initial' ORDER BY declared_at ASC LIMIT 1", (user_id,)).fetchone()
        capital_actuel  = float(cap_actuel["capital"])  if cap_actuel  else None
        capital_initial = float(cap_initial["capital"]) if cap_initial else None
        evo_pct = round((capital_actuel-capital_initial)/capital_initial*100,2) if capital_initial and capital_actuel else None

        theo = conn.execute("""
            SELECT ROUND(SUM(s.result_percent),2) AS admin_sum
            FROM signals s JOIN signal_participations sp ON sp.signal_id=s.id
            WHERE sp.user_id=? AND sp.response='in' AND s.closed_at>=? AND s.closed_at<=? AND s.status='closed'
        """, (user_id, week_start, week_end)).fetchone()
        cap_theo = manque = None
        if capital_initial and theo and theo["admin_sum"]:
            cap_theo = round(capital_initial*(1+theo["admin_sum"]/100),2)
            if capital_actuel: manque = round(cap_theo-capital_actuel,2)

        eng = dict(conn.execute("""
            SELECT COUNT(DISTINCT sp.signal_id) AS received,
                COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END) AS answered,
                COUNT(DISTINCT CASE WHEN sp.response='in' THEN sp.signal_id END) AS taken
            FROM signal_participations sp JOIN signals s ON s.id=sp.signal_id
            WHERE sp.user_id=? AND s.published_at>=? AND s.published_at<=?
        """, (user_id, week_start, week_end)).fetchone() or {})
        eng_rate = round(eng.get("answered",0)/eng["received"]*100,1) if eng.get("received") else 0

        admin_perf = dict(conn.execute("""
            SELECT ROUND(AVG(CASE WHEN close_result='tp' THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
                ROUND(SUM(result_percent),2) AS perf_totale
            FROM signals WHERE status='closed' AND closed_at>=? AND closed_at<=?
        """, (week_start, week_end)).fetchone() or {})

        lot_r = conn.execute("""
            SELECT COUNT(*) AS total,
                COUNT(CASE WHEN tj.lot_used<=(s.lot_suggested*1.1) AND tj.lot_used>0 THEN 1 END) AS respected
            FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
            WHERE tj.user_id=? AND s.lot_suggested IS NOT NULL AND s.closed_at>=? AND s.closed_at<=?
        """, (user_id, week_start, week_end)).fetchone()
        lot_respect = round(lot_r["respected"]/lot_r["total"]*100,1) if lot_r and lot_r["total"]>0 else None

    return {
        "member": {"user_id": user_id, "name": name, "week_label": week_label},
        "performance": {"total_trades": perf.get("total_trades",0), "wins": perf.get("wins",0),
                        "losses": perf.get("losses",0), "win_rate": perf.get("win_rate"),
                        "perf_totale": perf.get("perf_totale"), "avg_result": perf.get("avg_result"),
                        "best_trade": dict(best) if best else None, "worst_trade": dict(worst) if worst else None},
        "behavior": {"disciplined_pct": round(beh.get("disciplined",0)/total_beh*100,1),
                     "early_exit_pct": round(beh.get("early_exit",0)/total_beh*100,1),
                     "sl_skip_pct": round(beh.get("sl_skip",0)/total_beh*100,1),
                     "lot_respect_rate": lot_respect,
                     "early_exit_cost": float(early_cost["cost"]) if early_cost and early_cost["cost"] else 0},
        "capital": {"initial": capital_initial, "actuel": capital_actuel,
                    "theorique": cap_theo, "manque": manque, "evolution_pct": evo_pct},
        "engagement": {"rate": eng_rate, "signals_taken": eng.get("taken",0), "signals_received": eng.get("received",0)},
        "comparison": {"admin_win_rate": admin_perf.get("win_rate"), "admin_perf": admin_perf.get("perf_totale"),
                       "diff_win_rate": round((perf.get("win_rate") or 0)-(admin_perf.get("win_rate") or 0),1),
                       "diff_perf": round((perf.get("perf_totale") or 0)-(admin_perf.get("perf_totale") or 0),2)},
        "admin_config": admin_config,
    }


async def generate_member_bilan(context: dict) -> str:
    member = context["member"]; perf = context["performance"]; beh = context["behavior"]
    cap    = context["capital"]; eng  = context["engagement"]; comp = context["comparison"]
    config = context["admin_config"]
    sections = [
        f"Tu es un coach de trading bienveillant et direct. Génère le bilan hebdomadaire de {member['name']} "
        f"pour {member['week_label']}. Réponds en français. Format Telegram Markdown. "
        f"Commence directement par le bilan. Sois concis (max 200 mots), personnalisé et actionnable."
    ]
    if config.get("include_perf",True) and perf.get("total_trades",0)>0:
        sections.append(f"PERFORMANCE : {perf['total_trades']} trades · Win rate : {perf.get('win_rate') or 'N/A'}% · Perf : {perf.get('perf_totale') or 0}%")
    if config.get("include_behavior",True):
        sections.append(f"COMPORTEMENT : Discipliné {beh['disciplined_pct']}% · Sortie anticipée {beh['early_exit_pct']}% · Ignore SL {beh['sl_skip_pct']}%")
    if cap.get("actuel"):
        sections.append(f"CAPITAL : {cap['actuel']}$ (évolution {cap.get('evolution_pct') or 0}%)")
    sections.append(f"ENGAGEMENT : {eng['rate']}% des signaux ({eng['signals_taken']}/{eng['signals_received']})")
    if config.get("include_recommendations",True):
        sections.append("CONSIGNE : 1-2 recommandations concrètes. Si sorties anticipées>20% : insiste sur patience. Si sl_skip>10% : insiste discipline SL. Si win_rate>70% : félicite.")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(ANTHROPIC_API_URL,
                headers={"Content-Type": "application/json"},
                json={"model": ANTHROPIC_MODEL, "max_tokens": 500,
                      "messages": [{"role": "user", "content": "\n".join(sections)}]})
            return resp.json()["content"][0]["text"].strip()
    except Exception:
        return (f"📊 *Bilan {member['week_label']}*\n\n"
                f"💪 {perf.get('total_trades',0)} trades · {perf.get('win_rate') or 'N/A'}% win rate\n"
                f"_Bilan détaillé temporairement indisponible._")


async def generate_weekly_bilans(payload: dict) -> dict:
    week_start   = payload["week_start"]; week_end = payload["week_end"]
    week_label   = payload.get("week_label","Cette semaine")
    target       = payload.get("target","journalised"); send = payload.get("send",False)
    admin_config = payload.get("admin_config", {"include_perf":True,"include_behavior":True,
                                                 "include_recommendations":True,"include_comparison":False})

    with get_db() as conn:
        if target == "journalised":
            rows = conn.execute("""
                SELECT DISTINCT tj.user_id FROM trade_journal tj JOIN signals s ON s.id=tj.signal_id
                WHERE s.closed_at>=? AND s.closed_at<=? AND tj.participated=1
            """, (week_start, week_end)).fetchall()
        elif target == "all":
            rows = conn.execute("SELECT telegram_id AS user_id FROM users WHERE telegram_id IS NOT NULL").fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT id_user AS user_id FROM categories WHERE name_categorie=?", (target,)).fetchall()
        user_ids = [r["user_id"] for r in rows]

        conn.execute("""
            INSERT INTO ai_bilans (week_label, week_start, week_end, target, generated_at)
            VALUES (?, ?, ?, ?, NOW())
        """, (week_label, week_start, week_end, target))
        bilan_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]

    total = len(user_ids); generated = sent = errors = 0; preview = preview_user = None

    for idx, user_id in enumerate(user_ids):
        try:
            context = await _build_member_bilan_context(user_id, week_start, week_end, week_label, admin_config)
            if not context: errors += 1; continue
            message = await generate_member_bilan(context)
            generated += 1
            if idx == 0: preview = message; preview_user = context["member"]["name"]
            if send and _bot:
                try:
                    await _bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                    sent += 1; await asyncio.sleep(0.1)
                except Exception: pass
        except Exception: errors += 1

    with get_db() as conn:
        conn.execute("UPDATE ai_bilans SET total_sent=? WHERE id=?", (sent if send else 0, bilan_id))

    return {"bilan_id": bilan_id, "total": total, "generated": generated,
            "sent": sent if send else 0, "errors": errors,
            "preview": preview, "preview_user": preview_user, "week_label": week_label}


async def get_bilan_history() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM ai_bilans ORDER BY generated_at DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — STATS DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

async def get_dashboard_stats(period: str = "month") -> dict:
    date_from = {
        "week":  (datetime.now()-timedelta(days=7)).isoformat(),
        "month": (datetime.now()-timedelta(days=30)).isoformat(),
        "all":   "2000-01-01",
    }.get(period, (datetime.now()-timedelta(days=30)).isoformat())

    with get_db() as conn:
        trades_pub = conn.execute("SELECT COUNT(*) as n FROM signals WHERE published_at>=?", (date_from,)).fetchone()["n"]
        wr = conn.execute("""
            SELECT CASE WHEN COUNT(*)=0 THEN NULL
            ELSE ROUND(COUNT(CASE WHEN close_result='tp' THEN 1 END)/COUNT(*)*100,1) END AS win_rate
            FROM signals WHERE status='closed' AND closed_at>=?
        """, (date_from,)).fetchone()
        win_rate_admin = wr["win_rate"] if wr else None

        eng = conn.execute("""
            SELECT COUNT(DISTINCT sp.signal_id) AS signals,
                COUNT(DISTINCT CASE WHEN sp.response='in' THEN sp.user_id END) AS users_in
            FROM signal_participations sp JOIN signals s ON s.id=sp.signal_id WHERE s.published_at>=?
        """, (date_from,)).fetchone()
        engagement_rate = None
        if eng and eng["signals"]>0 and eng["users_in"]>0:
            dest = conn.execute("SELECT COUNT(DISTINCT telegram_id) as n FROM users").fetchone()["n"]
            if dest>0: engagement_rate = round(eng["users_in"]/dest*100,1)

        journals    = conn.execute("SELECT COUNT(*) as n FROM trade_journal WHERE submitted_at>=?", (date_from,)).fetchone()["n"]
        open_trades = conn.execute("SELECT COUNT(*) as n FROM signals WHERE status='open'").fetchone()["n"]
        avg_cap     = conn.execute("SELECT ROUND(AVG(last_cap),2) as n FROM (SELECT user_id, MAX(capital) AS last_cap FROM member_capital GROUP BY user_id) t").fetchone()["n"]

        weekly = conn.execute("""
            SELECT DATE(closed_at) AS day, COUNT(*) AS trades,
                COUNT(CASE WHEN close_result='tp' THEN 1 END) AS wins,
                COUNT(CASE WHEN close_result='sl' THEN 1 END) AS losses
            FROM signals WHERE status='closed' AND closed_at>=?
            GROUP BY day ORDER BY day ASC
        """, ((datetime.now()-timedelta(days=7)).isoformat(),)).fetchall()

    return {"trades_published": trades_pub, "win_rate_admin": win_rate_admin,
            "engagement_rate": engagement_rate, "journals_collected": journals,
            "open_trades_count": open_trades, "avg_member_capital": avg_cap,
            "weekly_performance": [dict(r) for r in weekly], "period": period}


async def declare_member_capital(user_id: int, payload: dict) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO member_capital (user_id, capital, type, declared_at, source)
            VALUES (?, ?, ?, NOW(), ?)
        """, (user_id, float(payload["capital"]), payload.get("type","gains"), payload.get("source","form")))
        new_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]
        return dict(conn.execute("SELECT * FROM member_capital WHERE id=?", (new_id,)).fetchone())


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════════════════

def _get_pip_value(symbol: str) -> float:
    with get_db() as conn:
        row = conn.execute("SELECT pip_value FROM trading_pairs WHERE symbol=?", (symbol.upper(),)).fetchone()
    return float(row["pip_value"]) if row else 10.0

def _get_pair_decimals(symbol: str) -> int:
    with get_db() as conn:
        row = conn.execute("SELECT decimals FROM trading_pairs WHERE symbol=?", (symbol.upper(),)).fetchone()
    return int(row["decimals"]) if row else 5

async def _get_signal_recipients(signal_id: int) -> list:
    with get_db() as conn:
        signal = conn.execute("SELECT category FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not signal: return []
        rows = conn.execute("SELECT DISTINCT id_user FROM categories WHERE name_categorie=?",
                             (signal["category"],)).fetchall()
    return [r["id_user"] for r in rows]