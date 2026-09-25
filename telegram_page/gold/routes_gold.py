"""
routes_gold.py — API admin FastAPI pour le module Gold (v8).

CHANGEMENTS PAR RAPPORT À LA VERSION PRÉCÉDENTE
  - POST /sessions déclenche directement gold_broadcast.send_signal()
    (via le pont HTTP interne 127.0.0.1:9100, côté process bot) — plus
    d'appel à lifecycle.open_new_session().
  - Un seul endpoint de fermeture (POST /sessions/{id}/close, avec
    close_type dans le corps) remplace /confirm, /tp/{n}, /sl éclatés
    — voir gold_followup.admin_force_close().
  - Supprimé : POST /sessions/{id}/watch — la surveillance démarre
    maintenant automatiquement à la fin de l'envoi, plus besoin de la
    déclencher à la main.
  - Le endpoint dashboard ne référence plus session_registry (RAM du
    process bot, invisible depuis l'API) — le statut "session ouverte"
    vient uniquement de MySQL (current_phase), qui est la même vérité
    pour les deux process.

Le process API (où tourne ce fichier) et le process bot Telegram
(script.py) sont deux process séparés qui ne partagent aucune RAM.
Seul le pont HTTP interne sur 127.0.0.1:9100 les relie, et uniquement
pour DÉCLENCHER un envoi Telegram (ce qui doit forcément se faire dans
le process qui détient la connexion Telegram) — plus aucune
orchestration de cycle de vie à faire transiter par ce pont.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from telegram_page.gold import gold_core
from telegram_page.gold.gold_followup import admin_force_close

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gold", tags=["gold"])

INTERNAL_BOT_URL = os.getenv("GOLD_INTERNAL_BOT_URL", "http://127.0.0.1:9100/internal/gold/open")


# ══════════════════════════════════════════════════════════════════════════════
# Schémas
# ══════════════════════════════════════════════════════════════════════════════

class SeasonCreate(BaseModel):
    name: str
    description: str | None = None
    start_date: str | None = None
    initial_capital_ref: float | None = None


class SeasonReset(BaseModel):
    new_season_name: str
    new_initial_capital: float | None = None


class SessionCreate(BaseModel):
    direction: str
    entry_price: float
    sl: float
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    timeframe: str = "M15"
    confidence_level: int = 3
    note: str | None = None
    screenshot_url: str | None = None
    signal_id: str | None = None
    category: str | None = None   # défaut : gold_broadcast.CATEGORY_TARGET


class SessionClose(BaseModel):
    close_type: str = Field(..., description="manual | tp1 | tp2 | tp3 | sl")


class SimulationAccountCreate(BaseModel):
    name: str
    description: str | None = None
    initial_capital: float
    risk_pct_default: float = 1.0


class TpRuleCreate(BaseModel):
    rule_name: str
    tp_level: int
    min_capital: float
    max_capital: float | None = None
    risk_pct: float
    message_tp1_reached: str | None = None
    message_tp2_reached: str | None = None
    message_tp3_reached: str | None = None
    message_sl_touched: str | None = None
    message_breakeven: str | None = None
    message_partial_close: str | None = None
    message_teaser: str | None = None
    message_confirmation: str | None = None


class TpRuleUpdate(BaseModel):
    rule_name: str | None = None
    tp_level: int | None = None
    min_capital: float | None = None
    max_capital: float | None = None
    risk_pct: float | None = None
    message_tp1_reached: str | None = None
    message_tp2_reached: str | None = None
    message_tp3_reached: str | None = None
    message_sl_touched: str | None = None
    message_breakeven: str | None = None
    message_partial_close: str | None = None
    message_teaser: str | None = None
    message_confirmation: str | None = None
    is_active: bool | None = None


# ══════════════════════════════════════════════════════════════════════════════
# Saisons
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/seasons")
async def api_create_season(payload: SeasonCreate):
    return await gold_core.create_season(payload.model_dump(exclude_none=True))


@router.get("/seasons")
async def api_get_seasons(include_closed: bool = True):
    return await gold_core.get_seasons(include_closed=include_closed)


@router.get("/seasons/active")
async def api_get_active_season():
    season = await gold_core.get_active_season()
    if season is None:
        raise HTTPException(404, "Aucune saison active")
    return season


@router.get("/seasons/{season_id}/stats")
async def api_get_season_stats(season_id: int):
    return await gold_core.get_season_stats(season_id)


@router.post("/seasons/{season_id}/reset")
async def api_reset_season(season_id: int, payload: SeasonReset):
    return await gold_core.reset_season(season_id, payload.model_dump(exclude_none=True))


# ══════════════════════════════════════════════════════════════════════════════
# Sessions de trade
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sessions")
async def api_create_session(payload: SessionCreate):
    """
    Crée la session puis déclenche l'envoi du signal brut côté bot
    (process séparé, via le pont HTTP interne). La création SQL est
    synchrone ; l'envoi Telegram démarre en tâche de fond côté bot —
    cette route répond dès que la session existe, sans attendre la fin
    du broadcast (qui peut prendre plusieurs dizaines de secondes sur
    30 000 membres).
    """
    data = payload.model_dump(exclude={"category"})
    session = await gold_core.create_gold_session(data)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(INTERNAL_BOT_URL, json={
                "session_id": session["id"],
                "category": payload.category,
            })
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"[routes_gold] déclenchement envoi échoué sid={session['id']}: {e}")
        raise HTTPException(502, f"Session créée (#{session['id']}) mais l'envoi n'a pas pu "
                                  f"être déclenché côté bot : {e}")

    return {"session": session, "broadcast": "started"}


@router.get("/sessions")
async def api_get_sessions(season_id: int | None = None, phase: str | None = None,
                            limit: int = 20, offset: int = 0):
    return await gold_core.get_gold_sessions({
        "season_id": season_id, "phase": phase, "limit": limit, "offset": offset,
    })


@router.get("/sessions/active")
async def api_get_active_session():
    session = await gold_core.get_active_gold_session()
    if session is None:
        raise HTTPException(404, "Aucune session Gold active")
    return session


@router.get("/sessions/{session_id}")
async def api_get_session_detail(session_id: int):
    session = await gold_core.get_gold_session_detail(session_id)
    if session is None:
        raise HTTPException(404, "Session introuvable")
    return session


@router.post("/sessions/{session_id}/close")
async def api_close_session(session_id: int, payload: SessionClose):
    """
    Fermeture manuelle — remplace les anciens /confirm, /tp/{n}, /sl.
    Utile pour corriger une erreur de saisie ou fermer un trade à la
    main ; en temps normal, gold_followup.watch_and_close ferme les
    sessions tout seul via le sondage du prix live.
    """
    result = await admin_force_close(session_id, payload.close_type)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Échec de la fermeture"))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Prix live + calcul (outils admin)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/price/live")
async def api_get_live_price():
    price = await gold_core.get_live_gold_price()
    if price is None:
        raise HTTPException(503, "Prix live indisponible pour le moment")
    return {"symbol": "XAU/USD", "price": price}


@router.get("/calculate-lot")
async def api_calculate_lot(capital: float, entry: float, sl: float,
                             tp1: float | None = None, tp2: float | None = None,
                             tp3: float | None = None):
    lot = gold_core.calculate_lot(capital, entry, sl)
    gains = gold_core.calculate_gains_losses(lot, entry, sl, tp1, tp2, tp3)
    tp_level, risk_pct = await gold_core.get_tp_level_for_capital(capital)
    return {"lot": lot, "tp_level": tp_level, "risk_pct": risk_pct, **gains}


# ══════════════════════════════════════════════════════════════════════════════
# Comptes simulation
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/simulation-accounts")
async def api_create_simulation_account(payload: SimulationAccountCreate):
    return await gold_core.create_simulation_account(payload.model_dump(exclude_none=True))


@router.get("/simulation-accounts")
async def api_get_simulation_accounts(active_only: bool = True):
    return await gold_core.get_simulation_accounts(active_only=active_only)


@router.get("/simulation-accounts/{account_id}")
async def api_get_simulation_account_detail(account_id: int):
    account = await gold_core.get_simulation_account_detail(account_id)
    if account is None:
        raise HTTPException(404, "Compte simulation introuvable")
    return account


# ══════════════════════════════════════════════════════════════════════════════
# Règles TP
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/tp-rules")
async def api_get_tp_rules():
    return await gold_core.get_tp_rules()


@router.post("/tp-rules")
async def api_create_tp_rule(payload: TpRuleCreate):
    return await gold_core.create_tp_rule(payload.model_dump(exclude_none=True))


@router.patch("/tp-rules/{rule_id}")
async def api_update_tp_rule(rule_id: int, payload: TpRuleUpdate):
    return await gold_core.update_tp_rule(rule_id, payload.model_dump(exclude_none=True))


# ══════════════════════════════════════════════════════════════════════════════
# Alertes comptes en danger
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cramed-check")
async def api_cramed_check():
    return await gold_core.daily_cramed_check()


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard — vue d'ensemble
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def api_dashboard():
    """
    Statut basé UNIQUEMENT sur MySQL (current_phase) — la même vérité
    quel que soit le process qui répond. Plus de référence à un
    registre RAM du process bot, invisible depuis l'API.
    """
    active_session = await gold_core.get_active_gold_session()
    active_season = await gold_core.get_active_season()
    sim_accounts = await gold_core.get_simulation_accounts(active_only=True)
    price = await gold_core.get_live_gold_price()

    return {
        "active_session": active_session,
        "active_season": active_season,
        "simulation_accounts": sim_accounts,
        "live_price": price,
    }