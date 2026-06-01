"""
routes_gold.py — Routes FastAPI pour le système Gold Trading + Simulation.

À intégrer dans api.py :
    from trading.routes_gold import router as gold_router
    app.include_router(gold_router)

Et dans le lifespan :
    from trading.gold_engine import init_gold_tables, set_bot as set_gold_bot
    init_gold_tables()
    set_gold_bot(bot)
"""

import asyncio
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
    # Calcul lot
    calculate_recommended_lot,
    get_tp_level_for_capital,
)
from telegram_page.gold.gold_broadcast import send_gold_teaser

router = APIRouter(prefix="/gold", tags=["gold"])


# ════════════════════════════════════════════════════════════════════════
# SAISONS
# ════════════════════════════════════════════════════════════════════════

@router.get("/seasons")
async def api_get_seasons(include_closed: bool = True):
    """
    Liste toutes les saisons Gold.
    Retourne : id, name, status, start_date, trades_count, wins_count,
               losses_count, members_participated
    """
    return await get_seasons(include_closed)


@router.get("/seasons/active")
async def api_active_season():
    """Retourne la saison Gold active."""
    season = await get_active_season()
    if not season:
        raise HTTPException(404, "Aucune saison active")
    return season


@router.post("/seasons")
async def api_create_season(payload: dict):
    """
    Crée une nouvelle saison Gold.
    La saison active précédente est automatiquement clôturée.

    payload: { name*, description?, start_date?, initial_capital_ref? }
    """
    if not payload.get("name"):
        raise HTTPException(400, "name requis")
    return await create_season(payload)


@router.get("/seasons/{season_id}/stats")
async def api_season_stats(season_id: int):
    """
    Stats complètes d'une saison pour l'interface web.

    Retourne :
      session_stats   : trades, wins, losses, avg_members_per_trade
      member_stats    : unique_members, total_gains, instruction_follow_rate
      best_trade / worst_trade
      simulation_accounts : [{name, initial, current, rendement_pct}]
      top_members     : [{name, trades, total_usd}]
    """
    result = await get_season_stats(season_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/seasons/{season_id}/reset")
async def api_reset_season(season_id: int, payload: dict):
    """
    Réinitialise une saison — archive les données, repart à zéro.
    Les comptes simulation sont remis à leur capital initial.

    payload: { new_season_name*, new_initial_capital? }
    """
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
    """
    Liste des sessions Gold avec pagination.
    Retourne : session + season_name + confirmed_members
    """
    return await get_gold_sessions({
        "season_id": season_id,
        "phase":     phase,
        "limit":     limit,
        "offset":    offset,
    })


@router.get("/sessions/active")
async def api_active_session():
    """
    Retourne la session Gold active (phase != closed/cancelled/sl_touched).
    Inclut : prix live_price_last, agrégats temps réel.
    """
    session = await get_active_gold_session()
    if not session:
        raise HTTPException(404, "Aucune session Gold active")
    return session


@router.get("/sessions/{session_id}")
async def api_session_detail(session_id: int):
    """
    Détail complet d'une session Gold.

    Retourne :
      session + entries [{user, capital, lot, tp_level, perte_sl, gains}]
      tp_distribution [{tp_level, members, total_lots, total_risk, gains}]
      flow_events [{event_type, user, created_at}]
      simulation_trades [{account_name, lot, result_usd, capital_after}]
    """
    session = await get_gold_session_detail(session_id)
    if not session:
        raise HTTPException(404, "Session introuvable")
    return session


@router.post("/sessions")
async def api_create_session(payload: dict):
    """
    Crée une nouvelle session de trade Gold et envoie le teaser.

    payload: {
        direction*,   entry_price*,  sl*,
        tp1?,         tp2?,          tp3?,
        timeframe?,   confidence_level? (1-5),
        note?,        screenshot_url?,
        category?     (défaut: clients_actifs),
        send_teaser?  (bool, défaut: true)
    }

    Retourne : session + broadcast_report (si teaser envoyé)
    """
    required = ("direction", "entry_price", "sl")
    for f in required:
        if payload.get(f) is None:
            raise HTTPException(400, f"{f} requis")
    if payload["direction"] not in ("buy", "sell"):   
        raise HTTPException(400, "direction doit être 'buy' ou 'sell'")
    if payload.get("confidence_level") and not (1 <= int(payload["confidence_level"]) <= 5):
        raise HTTPException(400, "confidence_level doit être entre 1 et 5")

    session = await create_gold_session(payload)

    # Envoyer le teaser si demandé (défaut True)
    if payload.get("send_teaser", True):
        from telegram_page.gold.gold_engine import _bot
        if _bot:
            try:
                report = await send_gold_teaser(
                    bot      = _bot,
                    session  = session,
                    category = payload.get("category", "clients_actifs"),
                    delay    = 0.08,
                )
                session["broadcast_report"] = report
            except Exception as e:
                session["broadcast_warning"] = str(e)

    return session


@router.post("/sessions/{session_id}/close")
async def api_close_session(session_id: int, payload: dict):
    """
    Clôture manuelle d'une session Gold (admin).
    Déclenche les notifications membres.

    payload: { close_type*: tp1 | tp2 | tp3 | sl | manual }
    """
    if not payload.get("close_type"):
        raise HTTPException(400, "close_type requis")
    if payload["close_type"] not in ("tp1", "tp2", "tp3", "sl", "manual"):
        raise HTTPException(400, "close_type invalide (tp1|tp2|tp3|sl|manual)")
    return await close_gold_session(session_id, payload)


@router.post("/sessions/{session_id}/tp/{tp_level}")
async def api_trigger_tp(session_id: int, tp_level: int):
    """
    Déclenche manuellement les messages TP pour un niveau donné.
    Utilisé si la surveillance automatique n'est pas disponible.
    tp_level: 1 | 2 | 3
    """
    if tp_level not in (1, 2, 3):
        raise HTTPException(400, "tp_level doit être 1, 2 ou 3")
    return await trigger_tp_reached(session_id, tp_level)


@router.post("/sessions/{session_id}/sl")
async def api_trigger_sl(session_id: int):
    """
    Déclenche manuellement la notification SL touché.
    Clôture la session et notifie tous les membres.
    """
    return await trigger_sl_touched(session_id)


# ════════════════════════════════════════════════════════════════════════
# PRIX LIVE
# ════════════════════════════════════════════════════════════════════════

@router.get("/price/live")
async def api_live_price():
    """
    Prix live de XAU/USD depuis Binance.
    Retourne : { price, timestamp }
    """
    price = await get_live_gold_price()
    if price is None:
        raise HTTPException(503, "Prix live indisponible")
    return {"price": price, "pair": "XAU/USD", "timestamp": __import__('datetime').datetime.now().isoformat()}


@router.post("/sessions/{session_id}/watch")
async def api_start_watch(session_id: int):
    """
    Démarre manuellement la surveillance prix en arrière-plan.
    Normalement démarrée automatiquement lors du teaser.
    """
    asyncio.create_task(watch_gold_price(session_id))
    return {"status": "watching", "session_id": session_id}


# ════════════════════════════════════════════════════════════════════════
# CALCUL LOT
# ════════════════════════════════════════════════════════════════════════

@router.get("/calculate-lot")
async def api_calculate_lot(
    capital:          float = Query(...),
    confidence_level: int   = Query(3),
    sl_pips:          float = Query(...),
    pip_value:        float = Query(1.0),
):
    """
    Calcule le lot recommandé pour un trade Gold.

    Retourne : {
        lot, risk_pct, risk_usd, tp_level_assigned,
        perte_sl_estimee (à remplir si tp1_pips connu)
    }
    """
    if sl_pips <= 0:
        raise HTTPException(400, "sl_pips doit être > 0")
    if not (1 <= confidence_level <= 5):
        raise HTTPException(400, "confidence_level entre 1 et 5")

    lot             = calculate_recommended_lot(capital, confidence_level, sl_pips, pip_value)
    tp_level, risk_pct = get_tp_level_for_capital(capital)
    risk_usd        = round(capital * risk_pct / 100, 2)
    perte_sl        = round(lot * sl_pips * pip_value, 2)

    return {
        "lot":                 lot,
        "risk_pct":            risk_pct,
        "risk_usd":            risk_usd,
        "tp_level_assigned":   tp_level,
        "perte_sl_estimee":    -perte_sl,
        "confidence_level":    confidence_level,
        "capital":             capital,
    }


# ════════════════════════════════════════════════════════════════════════
# ENTRÉES MEMBRES (webhook bot)
# ════════════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/confirm")
async def api_confirm_entry(session_id: int, payload: dict):
    """
    Enregistre la confirmation d'un membre depuis le bot Telegram.
    Appelé par gold_broadcast.py via l'API interne.

    payload: { user_id*, capital* }

    Retourne : {
        entry    : {lot, tp_level, perte_sl, gain_tp1, gain_tp2, gain_tp3},
        aggregates: {total_members, total_lots, estimated_loss_sl, gains...},
        message  : texte de confirmation personnalisé
    }
    """
    if not payload.get("user_id"):
        raise HTTPException(400, "user_id requis")
    if not payload.get("capital"):
        raise HTTPException(400, "capital requis")
    return await confirm_gold_entry(session_id, payload["user_id"], float(payload["capital"]))


# ════════════════════════════════════════════════════════════════════════
# COMPTES SIMULATION
# ════════════════════════════════════════════════════════════════════════

@router.get("/simulations")
async def api_get_simulations(active_only: bool = True):
    """
    Liste tous les comptes simulation.

    Retourne : [{
        id, name, initial_capital, current_capital,
        rendement_pct, total_trades, wins, losses,
        max_drawdown_pct, season_name
    }]
    """
    return await get_simulation_accounts(active_only)


@router.post("/simulations")
async def api_create_simulation(payload: dict):
    """
    Crée un compte simulation depuis l'interface web.

    payload: {
        name*,            initial_capital*,
        description?,     risk_pct_default?
    }
    Exemples : "Compte 100$", "Compte 500$", "Compte 1000$"
    """
    for f in ("name", "initial_capital"):
        if not payload.get(f):
            raise HTTPException(400, f"{f} requis")
    if float(payload["initial_capital"]) <= 0:
        raise HTTPException(400, "initial_capital doit être > 0")
    return await create_simulation_account(payload)


@router.get("/simulations/{account_id}")
async def api_simulation_detail(account_id: int):
    """
    Détail complet d'un compte simulation.

    Retourne :
      account + tous les trades + capital_curve [{trade_id, capital, result_usd, date}]
      rendement_pct, max_drawdown_pct
    """
    account = await get_simulation_account_detail(account_id)
    if not account:
        raise HTTPException(404, "Compte simulation introuvable")
    return account


@router.patch("/simulations/{account_id}")
async def api_update_simulation(account_id: int, payload: dict):
    """
    Met à jour un compte simulation (nom, description, risk_pct, is_active).
    """
    updatable = ("name", "description", "risk_pct_default", "is_active")
    updates   = {k: v for k, v in payload.items() if k in updatable}
    if not updates:
        raise HTTPException(400, "Aucun champ valide à mettre à jour")

    updates["updated_at"] = __import__('datetime').datetime.now().isoformat()
    fields = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [account_id]

    from gold_engine import get_conn
    conn = get_conn()
    try:
        conn.execute(f"UPDATE simulation_accounts SET {fields} WHERE id = ?", values)
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM simulation_accounts WHERE id = ?", (account_id,)
        ).fetchone())
    finally:
        conn.close()
    return row


# ════════════════════════════════════════════════════════════════════════
# ALERTES COMPTES CRAMÉS
# ════════════════════════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/danger-check")
async def api_danger_check(session_id: int):
    """
    Vérifie les comptes en danger pour ce trade.

    Retourne : {
        total_danger,
        cramed_risk      : [{user_id, name, capital, perte_sl, capital_restant, pct_restant}],
        already_cramed   : [{user_id, name, capital, perte_sl, capital_restant}],
        simulation_danger: [{account_name, capital, perte_sl, capital_restant}]
    }
    """
    return await check_cramed_accounts(session_id)


@router.post("/daily-check")
async def api_daily_check():
    """
    Lance manuellement le check de fin de journée sur tous les trades ouverts.
    Normalement déclenché automatiquement par le scheduler.
    Notifie l'admin des comptes en danger.
    """
    return await daily_cramed_check()


# ════════════════════════════════════════════════════════════════════════
# RÈGLES TP
# ════════════════════════════════════════════════════════════════════════

@router.get("/rules")
async def api_get_rules():
    """
    Liste toutes les règles TP.

    Retourne : [{
        id, rule_name, tp_level, min_capital, max_capital, risk_pct,
        message_tp1_reached, message_tp2_reached, message_tp3_reached,
        message_sl_touched, message_breakeven, message_partial_close,
        is_active
    }]
    """
    return await get_tp_rules()


@router.post("/rules")
async def api_create_rule(payload: dict):
    """
    Crée une règle TP.

    payload: {
        rule_name*,    tp_level* (1|2|3),
        min_capital*,  max_capital?,   risk_pct*,
        message_tp1_reached?,  message_tp2_reached?,  message_tp3_reached?,
        message_sl_touched?,   message_breakeven?,    message_partial_close?
    }
    """
    required = ("rule_name", "tp_level", "min_capital", "risk_pct")
    for f in required:
        if payload.get(f) is None:
            raise HTTPException(400, f"{f} requis")
    if int(payload["tp_level"]) not in (1, 2, 3):
        raise HTTPException(400, "tp_level doit être 1, 2 ou 3")
    return await create_tp_rule(payload)


@router.patch("/rules/{rule_id}")
async def api_update_rule(rule_id: int, payload: dict):
    """
    Met à jour une règle TP (messages, seuils, risk_pct, is_active).
    Tous les champs sont optionnels.
    """
    return await update_tp_rule(rule_id, payload)


# ════════════════════════════════════════════════════════════════════════
# DASHBOARD GOLD (vue synthétique interface web)
# ════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def api_gold_dashboard():
    """
    Vue synthétique pour le dashboard Gold de l'interface web.

    Retourne : {
        active_session     : session en cours (ou null),
        active_season      : saison active,
        live_price         : prix XAU/USD actuel,
        simulation_accounts: [{name, capital, rendement_pct}],
        recent_sessions    : 5 dernières sessions,
        season_stats       : stats de la saison active
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
        "active_session":     active_session,
        "active_season":      active_season,
        "live_price":         live_price,
        "simulation_accounts": sim_accounts,
        "recent_sessions":    recent_sessions.get("sessions", []),
        "season_stats":       season_stats,
    }