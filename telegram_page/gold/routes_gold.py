"""
routes_gold.py — Routes FastAPI Gold v3

Corrections v3 :
  1. calculate_recommended_lot → calculate_lot (nouvelle formule)
  2. Import corrigé gold_engine (chemin complet)
  3. /calculate-lot → utilise calculate_lot(capital, entry, sl)
  4. /simulations PATCH → import corrigé
  5. direction validation → 'buy' | 'sell' (déjà correct)
"""

import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from telegram_page.gold.gold_engine import (
    # Saisons
    create_season,
    get_active_season,
    get_seasons,
    reset_season,
    get_season_stats,
    # Sessions
    create_gold_session,
    get_active_gold_session,
    get_gold_session_detail,
    get_gold_sessions,
    close_gold_session,
    # Entrées membres
    confirm_gold_entry,
    # TP / SL
    trigger_tp_reached,
    trigger_sl_touched,
    # Prix live
    get_live_gold_price,
    watch_gold_price,
    # Comptes simulation
    create_simulation_account,
    get_simulation_accounts,
    get_simulation_account_detail,
    # Alertes
    check_cramed_accounts,
    daily_cramed_check,
    # Règles TP
    get_tp_rules,
    create_tp_rule,
    update_tp_rule,
    # Calcul lot — CORRECTION v3 : nouvelle formule
    calculate_lot,
    calculate_gains_losses,
    get_tp_level_for_capital,
    get_conn,

)

import telegram_page.gold.gold_engine as gold_engine

from telegram_page.gold.gold_broadcast import send_gold_teaser

router = APIRouter(prefix="/gold", tags=["gold"])


# ════════════════════════════════════════════════════════════════════════
# SAISONS
# ════════════════════════════════════════════════════════════════════════

@router.get("/seasons")
async def api_get_seasons(include_closed: bool = True):
    return await get_seasons(include_closed)


@router.get("/seasons/active")
async def api_active_season():
    season = await get_active_season()
    if not season:
        raise HTTPException(404, "Aucune saison active")
    return season


@router.post("/seasons")
async def api_create_season(payload: dict):
    if not payload.get("name"):
        raise HTTPException(400, "name requis")
    return await create_season(payload)


@router.get("/seasons/{season_id}/stats")
async def api_season_stats(season_id: int):
    result = await get_season_stats(season_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/seasons/{season_id}/reset")
async def api_reset_season(season_id: int, payload: dict):
    if not payload.get("new_season_name"):
        raise HTTPException(400, "new_season_name requis")
    return await reset_season(season_id, payload)


# ════════════════════════════════════════════════════════════════════════
# SESSIONS DE TRADE GOLD
# ════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def api_get_sessions(
    season_id: Optional[int] = None,
    phase:     Optional[str] = None,
    limit:     int           = 20,
    offset:    int           = 0,
):
    return await get_gold_sessions({
        "season_id": season_id,
        "phase":     phase,
        "limit":     limit,
        "offset":    offset,
    })


@router.get("/sessions/active")
async def api_active_session():
    session = await get_active_gold_session()
    if not session:
        raise HTTPException(404, "Aucune session Gold active")
    return session


@router.get("/sessions/{session_id}")
async def api_session_detail(session_id: int):
    session = await get_gold_session_detail(session_id)
    if not session:
        raise HTTPException(404, "Session introuvable")
    return session


@router.post("/sessions")
async def api_create_session(payload: dict):
    """
    Crée une nouvelle session Gold et envoie le teaser.
    """
    print(f"[DEBUG] >>> api_create_session appelé")
    print(f"[DEBUG] payload reçu: {payload}")

    required = ("direction", "entry_price", "sl")
    for f in required:
        if payload.get(f) is None:
            print(f"[DEBUG] ERREUR: champ requis manquant: {f}")
            raise HTTPException(400, f"{f} requis")

    print(f"[DEBUG] Champs requis OK")

    if payload["direction"] not in ("buy", "sell"):
        print(f"[DEBUG] ERREUR: direction invalide: {payload['direction']}")
        raise HTTPException(400, "direction doit être 'buy' ou 'sell'")

    print(f"[DEBUG] direction OK: {payload['direction']}")

    if payload.get("confidence_level") and not (1 <= int(payload["confidence_level"]) <= 5):
        print(f"[DEBUG] ERREUR: confidence_level invalide: {payload['confidence_level']}")
        raise HTTPException(400, "confidence_level doit être entre 1 et 5")

    if not payload.get("tp1"):
        print(f"[DEBUG] ERREUR: tp1 manquant")
        raise HTTPException(400, "tp1 requis")

    print(f"[DEBUG] Validations passées. Appel create_gold_session...")
    try:
        session = await create_gold_session(payload)
        print(f"[DEBUG] create_gold_session OK: {session}")
    except Exception as e:
        print(f"[DEBUG] ERREUR dans create_gold_session: {type(e).__name__}: {e}")
        raise

    send_teaser_flag = payload.get("send_teaser", True)
   

    if send_teaser_flag:
        if gold_engine._bot:
   
            print(f"[DEBUG] Appel send_gold_teaser avec category={payload.get('category', 'clients_actifs')}...")
            try:
                report = await send_gold_teaser(
                    bot      = gold_engine._bot,
                    session  = session,
                    category = payload.get("category", "clients_actifs"),
                    delay    = 0.08,
                )
                print(f"[DEBUG] send_gold_teaser OK: {report}")
                session["broadcast_report"] = report
            except Exception as e:
                print(f"[DEBUG] ERREUR dans send_gold_teaser: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                session["broadcast_warning"] = str(e)
        else:
            print(f"[DEBUG] _bot est None/falsy, teaser non envoyé")
    else:
        print(f"[DEBUG] send_teaser=False, teaser skippé")

    print(f"[DEBUG] <<< api_create_session terminé, retour: {session}")
    return session

@router.post("/sessions/{session_id}/close")
async def api_close_session(session_id: int, payload: dict):
    if not payload.get("close_type"):
        raise HTTPException(400, "close_type requis")
    if payload["close_type"] not in ("tp1", "tp2", "tp3", "sl", "manual"):
        raise HTTPException(400, "close_type invalide (tp1|tp2|tp3|sl|manual)")
    return await close_gold_session(session_id, payload)


@router.post("/sessions/{session_id}/tp/{tp_level}")
async def api_trigger_tp(session_id: int, tp_level: int):
    if tp_level not in (1, 2, 3):
        raise HTTPException(400, "tp_level doit être 1, 2 ou 3")
    return await trigger_tp_reached(session_id, tp_level)


@router.post("/sessions/{session_id}/sl")
async def api_trigger_sl(session_id: int):
    return await trigger_sl_touched(session_id)


# ════════════════════════════════════════════════════════════════════════
# PRIX LIVE
# ════════════════════════════════════════════════════════════════════════

@router.get("/price/live")
async def api_live_price():
    price = await get_live_gold_price()
    if price is None:
        raise HTTPException(503, "Prix live indisponible")
    return {
        "price":     price,
        "pair":      "XAU/USD",
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/sessions/{session_id}/watch")
async def api_start_watch(session_id: int):
    asyncio.create_task(watch_gold_price(session_id))
    return {"status": "watching", "session_id": session_id}


# ════════════════════════════════════════════════════════════════════════
# CALCUL LOT — CORRECTION v3
# ════════════════════════════════════════════════════════════════════════

@router.get("/calculate-lot")
async def api_calculate_lot(
    capital: float = Query(..., description="Capital en dollars"),
    entry:   float = Query(..., description="Prix d'entrée"),
    sl:      float = Query(..., description="Prix du Stop Loss"),
    tp1:     Optional[float] = Query(None, description="TP1 (optionnel pour gains)"),
    tp2:     Optional[float] = Query(None),
    tp3:     Optional[float] = Query(None),
):
    """
    Calcule le lot recommandé — CORRECTION v3.
    Utilise calculate_lot(capital, entry, sl) et calculate_gains_losses().

    Retourne : {
        lot, sl_pips, tp_level_assigned,
        perte_sl, gain_tp1, gain_tp2, gain_tp3,
        diviseur, capital
    }
    """
    if capital <= 0:
        raise HTTPException(400, "capital doit être > 0")
    if sl <= 0 or entry <= 0:
        raise HTTPException(400, "entry et sl doivent être > 0")

    sl_pips = abs(entry - sl)
    if sl_pips <= 0:
        raise HTTPException(400, "entry et sl doivent être différents")

    lot    = calculate_lot(capital, entry, sl)
    gains  = calculate_gains_losses(lot, entry, sl, tp1, tp2, tp3)
    tp_level, risk_pct = get_tp_level_for_capital(capital)

    # Calcul diviseur pour info
    import math
    diviseur = 12 + math.floor((capital - 1001) / 500) if capital >= 1500 else 12
    if capital < 500:
        diviseur = None

    return {
        "lot":               lot,
        "sl_pips":           round(sl_pips, 2),
        "tp_level_assigned": tp_level,
        "risk_pct":          risk_pct,
        "perte_sl":          gains["perte_sl"],
        "gain_tp1":          gains["gain_tp1"],
        "gain_tp2":          gains["gain_tp2"],
        "gain_tp3":          gains["gain_tp3"],
        "diviseur":          diviseur,
        "capital":           capital,
    }


# ════════════════════════════════════════════════════════════════════════
# ENTRÉES MEMBRES
# ════════════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/confirm")
async def api_confirm_entry(session_id: int, payload: dict):
    if not payload.get("user_id"):
        raise HTTPException(400, "user_id requis")
    if not payload.get("capital"):
        raise HTTPException(400, "capital requis")
    capital = float(payload["capital"])
    if capital < 30:
        raise HTTPException(400, "capital minimum 30$")
    return await confirm_gold_entry(session_id, payload["user_id"], capital)


# ════════════════════════════════════════════════════════════════════════
# COMPTES SIMULATION
# ════════════════════════════════════════════════════════════════════════

@router.get("/simulations")
async def api_get_simulations(active_only: bool = True):
    return await get_simulation_accounts(active_only)


@router.post("/simulations")
async def api_create_simulation(payload: dict):
    for f in ("name", "initial_capital"):
        if not payload.get(f):
            raise HTTPException(400, f"{f} requis")
    if float(payload["initial_capital"]) <= 0:
        raise HTTPException(400, "initial_capital doit être > 0")
    return await create_simulation_account(payload)


@router.get("/simulations/{account_id}")
async def api_simulation_detail(account_id: int):
    account = await get_simulation_account_detail(account_id)
    if not account:
        raise HTTPException(404, "Compte simulation introuvable")
    return account


@router.delete("/simulations/{account_id}")
async def api_delete_simulation(account_id: int):
    """
    Hard delete — supprime le compte simulation + tous ses trades.
    """
    conn = get_conn()
    try:
        account = conn.execute(
            "SELECT id, name FROM simulation_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not account:
            raise HTTPException(404, "Compte simulation introuvable")

        conn.execute("DELETE FROM simulation_trades WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM simulation_accounts WHERE id = ?", (account_id,))
        conn.commit()
    finally:
        conn.close()

    return {"deleted": True, "account_id": account_id, "name": account["name"]}


@router.patch("/simulations/{account_id}")
async def api_update_simulation(account_id: int, payload: dict):
    """
    Met à jour un compte simulation.
    CORRECTION v3 : import get_conn depuis gold_engine (chemin complet).
    """
    updatable = ("name", "description", "risk_pct_default", "is_active")
    updates   = {k: v for k, v in payload.items() if k in updatable}
    if not updates:
        raise HTTPException(400, "Aucun champ valide à mettre à jour")

    updates["updated_at"] = datetime.now().isoformat()
    fields = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [account_id]

    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE simulation_accounts SET {fields} WHERE id = ?", values
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM simulation_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Compte simulation introuvable")
        return dict(row)
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# ALERTES COMPTES CRAMÉS
# ════════════════════════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/danger-check")
async def api_danger_check(session_id: int):
    return await check_cramed_accounts(session_id)


@router.post("/daily-check")
async def api_daily_check():
    return await daily_cramed_check()


# ════════════════════════════════════════════════════════════════════════
# RÈGLES TP
# ════════════════════════════════════════════════════════════════════════

@router.get("/rules")
async def api_get_rules():
    return await get_tp_rules()


@router.post("/rules")
async def api_create_rule(payload: dict):
    required = ("rule_name", "tp_level", "min_capital", "risk_pct")
    for f in required:
        if payload.get(f) is None:
            raise HTTPException(400, f"{f} requis")
    if int(payload["tp_level"]) not in (1, 2, 3):
        raise HTTPException(400, "tp_level doit être 1, 2 ou 3")
    return await create_tp_rule(payload)


@router.patch("/rules/{rule_id}")
async def api_update_rule(rule_id: int, payload: dict):
    return await update_tp_rule(rule_id, payload)


# ════════════════════════════════════════════════════════════════════════
# DASHBOARD GOLD
# ════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def api_gold_dashboard():
    """
    Vue synthétique pour le dashboard Gold.

    Retourne : {
        active_session, active_season, live_price,
        simulation_accounts, recent_sessions, season_stats
    }
    """
    active_session  = await get_active_gold_session()
    active_season   = await get_active_season()
    live_price      = await get_live_gold_price()
    sim_accounts    = await get_simulation_accounts(active_only=True)
    recent_sessions = await get_gold_sessions({"limit": 5, "offset": 0})

    season_stats = None
    if active_season:
        season_stats = await get_season_stats(active_season["id"])

    return {
        "active_session":      active_session,
        "active_season":       active_season,
        "live_price":          live_price,
        "simulation_accounts": sim_accounts,
        "recent_sessions":     recent_sessions.get("sessions", []),
        "season_stats":        season_stats,
    }