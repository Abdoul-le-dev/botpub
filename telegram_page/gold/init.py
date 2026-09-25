"""
telegram_page/gold — v8.

3 fichiers de logique + 1 fichier d'erreurs génériques :
    gold_core.py       données & calcul (sessions, simulation, TP rules,
                        prix live, capital opt-in) — aucun état RAM
                        partagé entre process.
    gold_broadcast.py  qui reçoit le signal et quand (consentement
                        hebdo + envoi brut).
    gold_followup.py   ce qui se passe après l'envoi (money management,
                        aide, surveillance prix, notifs TP opt-in,
                        fermeture manuelle admin).
    error_handler.py   error handler générique pour l'Application
                        Telegram (sans rapport avec la logique Gold).

SUPPRIMÉS (v6/v7, plus aucun appelant en v8) :
    gold_state.py, session_snapshot.py, session_registry.py,
    lifecycle.py, tp_notifier.py, gold_cache.py, gold_buffer.py.
"""

from telegram_page.gold.gold_core import (
    set_bot,
    init_gold_tables,
    ensure_member_capital_schema,
    create_season, get_active_season, get_seasons, reset_season, get_season_stats,
    create_gold_session, get_session_row, get_active_gold_session,
    get_gold_session_detail, get_gold_sessions,
    get_live_gold_price, watch_interval,
    create_simulation_account, get_simulation_accounts, get_simulation_account_detail,
    open_simulation_trades, close_simulation_trades,
    check_cramed_accounts, daily_cramed_check,
    get_tp_rules, create_tp_rule, update_tp_rule, get_tp_level_for_capital,
    calculate_lot, calculate_gains_losses,
    save_capital, get_all_capitals,
)

from telegram_page.gold.gold_broadcast import (
    ensure_disclaimer_schema,
    disclaimer_gate, split_by_consent,
    run_weekend_campaign, weekend_scheduler_loop,
    handle_disclaimer_weekly_ok, cmd_je_valide_mon_engagement,
    send_signal, send_signal_to_user,
    build_signal_message, build_signal_keyboard,
)

from telegram_page.gold.gold_followup import (
    set_bot as set_followup_bot,
    register_gold_followup_handlers,
    watch_and_close,
    notify_opted_in_members,
    admin_force_close,
)

__all__ = [
    # gold_core
    "set_bot", "init_gold_tables", "ensure_member_capital_schema",
    "create_season", "get_active_season", "get_seasons", "reset_season", "get_season_stats",
    "create_gold_session", "get_session_row", "get_active_gold_session",
    "get_gold_session_detail", "get_gold_sessions",
    "get_live_gold_price", "watch_interval",
    "create_simulation_account", "get_simulation_accounts", "get_simulation_account_detail",
    "open_simulation_trades", "close_simulation_trades",
    "check_cramed_accounts", "daily_cramed_check",
    "get_tp_rules", "create_tp_rule", "update_tp_rule", "get_tp_level_for_capital",
    "calculate_lot", "calculate_gains_losses",
    "save_capital", "get_all_capitals",
    # gold_broadcast
    "ensure_disclaimer_schema", "disclaimer_gate", "split_by_consent",
    "run_weekend_campaign", "weekend_scheduler_loop",
    "handle_disclaimer_weekly_ok", "cmd_je_valide_mon_engagement",
    "send_signal", "send_signal_to_user",
    "build_signal_message", "build_signal_keyboard",
    # gold_followup
    "set_followup_bot", "register_gold_followup_handlers", "watch_and_close",
    "notify_opted_in_members", "admin_force_close",
]