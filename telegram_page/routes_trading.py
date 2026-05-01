"""
routes_trading.py — Routes FastAPI pour le Journal de Trading.

À intégrer dans api.py :
    from trading.routes_trading import router as trading_router
    app.include_router(trading_router)

Et dans le lifespan :
    from trading.trading_journal import init_trading_tables, set_bot as set_trading_bot
    init_trading_tables()
    set_trading_bot(bot)
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from telegram_page.trading_journal import (
    # Signaux
    publish_signal,
    get_signals,
    get_signal_detail,
    close_signal,
    record_participation,
    send_followup_comment,
    # Journal membres
    submit_trade_result,
    get_history,
    get_crossed_performance,
    # Performances
    get_member_performance,
    get_performances_list,
    # Classement
    get_leaderboard,
    # Paires & Pip
    get_pairs,
    create_pair,
    update_pair,
    delete_pair,
    calculate_lot,
    get_suggested_lot_for_signal,
    # Formulaires & Collecte
    get_form_stats,
    get_forms_list,
    get_form_field_mapping,
    update_form_field_mapping,
    get_collected_data_summary,
    # Bilan IA
    generate_weekly_bilans,
    get_bilan_history,
    generate_member_bilan,
    _build_member_bilan_context,
    # Dashboard & Capital
    get_dashboard_stats,
    declare_member_capital,
)

router = APIRouter(prefix="/trading", tags=["trading"])


# ════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def api_dashboard_stats(period: str = "month"):
    """
    Stats globales pour les 5 cartes du header Vue Signaux.
    period: week | month | all
    Retourne : trades_published, win_rate_admin, engagement_rate,
               journals_collected, open_trades_count, avg_member_capital,
               weekly_performance
    """
    if period not in ("week", "month", "all"):
        raise HTTPException(400, "period doit être week | month | all")
    return await get_dashboard_stats(period)


# ════════════════════════════════════════════════════════════════════════
# SIGNAUX
# ════════════════════════════════════════════════════════════════════════

@router.get("/signals")
async def api_get_signals(
    status:    str           = "all",
    pair:      Optional[str] = None,
    limit:     int           = 20,
    offset:    int           = 0,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
):
    """
    Liste des signaux avec stats de participation temps réel.
    status: open | closed | cancelled | all
    Retourne aussi pips_to_tp1, pips_to_sl (placeholder pour WebSocket).
    """
    return await get_signals({
        "status": status, "pair": pair,
        "limit": limit, "offset": offset,
        "date_from": date_from, "date_to": date_to,
    })


@router.get("/signals/{signal_id}")
async def api_signal_detail(signal_id: int):
    """
    Détail complet d'un signal :
    - niveaux, participations, behaviors, followup_comments, journal_stats
    """
    signal = await get_signal_detail(signal_id)
    if not signal:
        raise HTTPException(404, "Signal introuvable")
    return signal


@router.post("/signals")
async def api_publish_signal(payload: dict):
    """
    Publie un nouveau signal et déclenche le broadcast Telegram.

    payload: {
        pair*,  direction*,  timeframe?,  entry_price*,
        tp1?,   tp2?,        sl?,         note?,
        screenshot_url?,     category?,   lot_suggested?
    }
    """
    for field in ("pair", "direction", "entry_price"):
        if not payload.get(field):
            raise HTTPException(400, f"{field} requis")
    if payload["direction"] not in ("long", "short"):
        raise HTTPException(400, "direction doit être long | short")
    return await publish_signal(payload)


@router.patch("/signals/{signal_id}/close")
async def api_close_signal(signal_id: int, payload: dict):
    """
    Clôture un signal et envoie le formulaire de collecte.

    payload: {
        close_price*,
        close_result*: tp | sl | partial | cancelled,
        close_screenshot?,
        form_id?,
        send_form_to?: participated | all
    }
    """
    for field in ("close_price", "close_result"):
        if payload.get(field) is None:
            raise HTTPException(400, f"{field} requis")
    if payload["close_result"] not in ("tp", "sl", "partial", "cancelled"):
        raise HTTPException(400, "close_result invalide")
    return await close_signal(signal_id, payload)


@router.post("/signals/{signal_id}/followup")
async def api_followup_comment(signal_id: int, payload: dict):
    """
    Envoie un commentaire de suivi (trade ouvert).
    Ciblé uniquement aux membres 'Je suis dedans'.

    payload: {
        type*: update | invalidation | secure | encourage,
        message*,
        screenshot_url?
    }
    """
    if not payload.get("type") or payload["type"] not in ("update", "invalidation", "secure", "encourage"):
        raise HTTPException(400, "type invalide (update|invalidation|secure|encourage)")
    if not payload.get("message"):
        raise HTTPException(400, "message requis")
    return await send_followup_comment(signal_id, payload)


@router.get("/signals/{signal_id}/lot-suggested")
async def api_signal_lot(signal_id: int, risk_pct: float = 2.0):
    """
    Calcule le lot suggéré par membre pour ce signal.
    Retourne avg_lot, avg_capital, per_member [{user_id, name, capital, lot}]
    Utilisé dans le modal 'Publier un trade' et le message de gestion du risque.
    """
    return await get_suggested_lot_for_signal(signal_id, risk_pct)


# ════════════════════════════════════════════════════════════════════════
# PARTICIPATIONS (webhook bot Telegram)
# ════════════════════════════════════════════════════════════════════════

@router.post("/signals/{signal_id}/participate")
async def api_record_participation(signal_id: int, payload: dict):
    """
    Enregistre la réponse d'un membre aux boutons Telegram.
    Appelé par le bot Python uniquement.

    payload: { user_id*, response*: in | out }
    """
    if not payload.get("user_id"):
        raise HTTPException(400, "user_id requis")
    if payload.get("response") not in ("in", "out"):
        raise HTTPException(400, "response doit être in | out")
    return await record_participation(signal_id, payload["user_id"], payload["response"])


# ════════════════════════════════════════════════════════════════════════
# JOURNAL MEMBRES
# ════════════════════════════════════════════════════════════════════════

@router.post("/signals/{signal_id}/journal/{user_id}")
async def api_submit_journal(signal_id: int, user_id: int, payload: dict):
    """
    Enregistre le résultat réel d'un membre.
    Appelé par form_engine après soumission du formulaire de clôture.

    payload: {
        entry_price?,  exit_price?,   lot_used?,
        screenshot_url?, capital_before?, capital_after?,
        behavior?: disciplined | early_exit | sl_skip | passive
    }
    """
    return await submit_trade_result(signal_id, user_id, payload)


@router.get("/history")
async def api_get_history(
    member_id:  Optional[int] = None,
    signal_id:  Optional[int] = None,
    pair:       Optional[str] = None,
    status:     str           = "all",
    search:     Optional[str] = None,
    date_from:  Optional[str] = None,
    limit:      int           = 50,
    offset:     int           = 0,
):
    """
    Historique croisé membres × signaux pour la Vue Historique.
    status: all | took | skip
    Retourne lignes : member, signal, entrée, sortie, pips,
                      gain$, capital_after, comportement, capture_url
    """
    return await get_history({
        "member_id": member_id, "signal_id": signal_id,
        "pair": pair, "status": status, "search": search,
        "date_from": date_from, "limit": limit, "offset": offset,
    })


@router.get("/history/performance-chart")
async def api_crossed_perf(
    period:    str           = "day",
    pair:      Optional[str] = None,
    member_id: Optional[int] = None,
):
    """
    Données pour le graphique SVG performance croisée.
    Retourne : admin_curve, members_curve, capital_curve
    Chaque courbe : [{period, cumulative_pct, trades|journals}]
    period: day | week | month
    """
    if period not in ("day", "week", "month"):
        raise HTTPException(400, "period doit être day | week | month")
    return await get_crossed_performance({"period": period, "pair": pair, "member_id": member_id})


# ════════════════════════════════════════════════════════════════════════
# PERFORMANCES MEMBRES
# ════════════════════════════════════════════════════════════════════════

@router.get("/performances")
async def api_performances_list(
    search:  Optional[str] = None,
    sort_by: str           = "win_rate",
    limit:   int           = 50,
    offset:  int           = 0,
):
    """
    Liste des membres pour la Vue Performances.
    sort_by: win_rate | discipline | engagement | capital | perf
    Chaque ligne : name, capital (+évol%), trades, engagement%, win_rate,
                   perf_totale, comportement, badge suivi
    """
    if sort_by not in ("win_rate", "discipline", "engagement", "capital", "perf"):
        raise HTTPException(400, "sort_by invalide")
    return await get_performances_list({
        "search": search, "sort_by": sort_by,
        "limit": limit, "offset": offset,
    })


@router.get("/performances/{user_id}")
async def api_member_performance(user_id: int):
    """
    Profil complet d'un membre pour le drawer Performances.

    Retourne :
      stats, capital_initial/actuel/theorique/manque_a_gagner,
      capital_21j [{date, capital, type: up|down|flat}],
      performance_curve [{signal_id, pair, result_pct, behavior, cumulative_pct, date}],
      behaviors, lot_respect_rate, suivi_capital_actif
    """
    perf = await get_member_performance(user_id)
    if not perf:
        raise HTTPException(404, "Membre introuvable")
    return perf


# ════════════════════════════════════════════════════════════════════════
# CLASSEMENT
# ════════════════════════════════════════════════════════════════════════

@router.get("/leaderboard")
async def api_leaderboard(
    period:     str = "all",
    min_trades: int = 3,
    limit:      int = 50,
    offset:     int = 0,
):
    """
    Classement des membres par performance.
    period: week | month | all
    Minimum min_trades journalisés pour figurer.
    Retourne : rank, user_id, name, total_trades, win_rate,
               perf_totale, engagement_rate, capital_actuel, suivi_off
    """
    if period not in ("week", "month", "all"):
        raise HTTPException(400, "period invalide")
    return await get_leaderboard({
        "period": period, "min_trades": min_trades,
        "limit": limit, "offset": offset,
    })


# ════════════════════════════════════════════════════════════════════════
# CAPITAL MEMBRES
# ════════════════════════════════════════════════════════════════════════

@router.post("/capital/{user_id}")
async def api_declare_capital(user_id: int, payload: dict):
    """
    Enregistre une déclaration de capital.
    Appelé par form_engine après réception du formulaire 'Capital membres'.

    payload: {
        capital*,
        type?: gains | withdrawal | loss | initial,
        source?: form | manual | trade_result
    }
    """
    if not payload.get("capital"):
        raise HTTPException(400, "capital requis")
    payload["source"] = payload.get("source", "form")
    return await declare_member_capital(user_id, payload)


# ════════════════════════════════════════════════════════════════════════
# PAIRES & PIP
# ════════════════════════════════════════════════════════════════════════

@router.get("/pairs")
async def api_get_pairs(active_only: bool = False):
    """
    Liste toutes les paires avec paramètres pip.
    Retourne : symbol, category, pip_value, decimals, binance_symbol, is_active
    """
    return await get_pairs(active_only)


@router.post("/pairs")
async def api_create_pair(payload: dict):
    """
    Crée une nouvelle paire.
    payload: { symbol*, category?, pip_value*, decimals?, binance_symbol?, note? }
    """
    for f in ("symbol", "pip_value"):
        if not payload.get(f):
            raise HTTPException(400, f"{f} requis")
    return await create_pair(payload)


@router.patch("/pairs/{pair_id}")
async def api_update_pair(pair_id: int, payload: dict):
    """
    Met à jour une paire existante.
    payload: champs à modifier parmi symbol, category, pip_value,
             decimals, binance_symbol, is_active, note
    """
    return await update_pair(pair_id, payload)


@router.delete("/pairs/{pair_id}")
async def api_delete_pair(pair_id: int):
    """Désactive une paire (soft delete — is_active = 0)."""
    return await delete_pair(pair_id)


@router.get("/pairs/calculate-lot")
async def api_calculate_lot(
    capital:  float           = Query(...),
    risk_pct: float           = Query(2.0),
    sl_pips:  float           = Query(...),
    pair:     str             = Query(...),
    tp1_pips: Optional[float] = Query(0),
):
    """
    Calculateur de lot.
    Retourne : risk_usd, lot_suggested, max_loss, gain_tp1, rr_ratio, pip_value_used
    """
    if sl_pips <= 0:
        raise HTTPException(400, "sl_pips doit être > 0")
    return calculate_lot(capital, risk_pct, sl_pips, pair, tp1_pips or 0)


# ════════════════════════════════════════════════════════════════════════
# FORMULAIRES & COLLECTE
# ════════════════════════════════════════════════════════════════════════

@router.get("/forms/stats")
async def api_form_stats():
    """Stats globales : total_forms, total_responses, completion_rate."""
    return await get_form_stats()


@router.get("/forms")
async def api_forms_list():
    """
    Liste des formulaires avec stats.
    Retourne : id, name, command, type (system|custom), is_active,
               respondents, total_responses, last_response_at
    """
    return await get_forms_list()


@router.get("/forms/{form_id}/mapping")
async def api_form_mapping(form_id: int):
    """
    Mapping champ→statistique d'un formulaire.
    Retourne chaque champ avec : field_id, label, type, maps_to_stat,
                                  aggregation, data_type, sample_values
    """
    result = await get_form_field_mapping(form_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.patch("/forms/{form_id}/mapping")
async def api_update_mapping(form_id: int, payload: dict):
    """
    Met à jour le mapping champ→statistique.
    payload: { fields: [{field_id, maps_to_stat, aggregation, data_type}] }
    """
    if not payload.get("fields"):
        raise HTTPException(400, "fields requis")
    return await update_form_field_mapping(form_id, payload)


@router.get("/forms/summary")
async def api_forms_summary():
    """
    Tableau récapitulatif complet :
    formulaire → données collectées → stats produites.
    Retourne : [{form_name, form_type, fields_collected, stats_produced,
                 total_responses, last_response_at}]
    """
    return await get_collected_data_summary()


# ════════════════════════════════════════════════════════════════════════
# BILAN IA
# ════════════════════════════════════════════════════════════════════════

@router.post("/ia/bilans/generate")
async def api_generate_bilans(payload: dict):
    """
    Génère les bilans hebdomadaires IA (avec Claude Sonnet).

    payload: {
        week_start*:   '2026-04-14T00:00:00',
        week_end*:     '2026-04-20T23:59:59',
        week_label?:   'Semaine du 14 au 20 avril 2026',
        target?:       journalised | all | clients_actifs,
        send?:         bool,
        admin_config?: {
            include_perf: bool,
            include_behavior: bool,
            include_recommendations: bool,
            include_comparison: bool,
        }
    }

    Retourne : {
        bilan_id, total, generated, sent, errors,
        preview (1er bilan texte), preview_user, week_label
    }
    """
    for f in ("week_start", "week_end"):
        if not payload.get(f):
            raise HTTPException(400, f"{f} requis")
    return await generate_weekly_bilans(payload)


@router.post("/ia/bilans/preview")
async def api_preview_bilan(payload: dict):
    """
    Génère le bilan d'un seul membre pour prévisualisation admin.

    payload: {
        user_id*,
        week_start*, week_end*, week_label*,
        admin_config?: {...}
    }
    Retourne : { user_id, name, message (texte Telegram Markdown) }
    """
    for f in ("user_id", "week_start", "week_end"):
        if not payload.get(f):
            raise HTTPException(400, f"{f} requis")

    admin_config = payload.get("admin_config", {
        "include_perf":            True,
        "include_behavior":        True,
        "include_recommendations": True,
        "include_comparison":      False,
    })

    context = await _build_member_bilan_context(
        user_id    = payload["user_id"],
        week_start = payload["week_start"],
        week_end   = payload["week_end"],
        week_label = payload.get("week_label", "Cette semaine"),
        admin_config = admin_config,
    )
    if not context:
        raise HTTPException(404, "Membre introuvable")

    message = await generate_member_bilan(context)
    return {
        "user_id": payload["user_id"],
        "name":    context["member"]["name"],
        "message": message,
        "context": context,  # permet d'afficher les données sources dans le dashboard
    }


@router.get("/ia/bilans/history")
async def api_bilan_history():
    """
    Historique des 20 derniers bilans IA envoyés.
    Retourne : [{id, week_label, target, total_sent, generated_at}]
    """
    return await get_bilan_history()