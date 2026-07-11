"""
gold_v7 — Architecture Gold refondue pour cohérence absolue.
"""
 
from telegram_page.gold.session_registry import session_registry, SessionStatus
from telegram_page.gold.session_snapshot import snapshot_store, build_snapshot, SessionSnapshot
from telegram_page.gold.gold_state import user_state_v7, CalcContext
from telegram_page.gold.gold_buffer import gold_buffer, GoldWriteBuffer
from telegram_page.gold.lifecycle import (
    open_new_session, close_session, mark_broadcast_done,
    current_snapshot, current_version, is_open, is_ready_for_confirmations,
    register_buffer,
)
from telegram_page.gold.callback_guard import guard, make_callback_data, check_callback, GuardResult
from telegram_page.gold.gold_broadcast import register_gold_handlers_v7, build_calc_context, adjust_entry_sl
from telegram_page.gold.broadcast_send import send_teaser_broadcast
from telegram_page.gold.tp_notifier import (
    notify_tp_reached, notify_sl_touched,
    apply_tp_closure_in_db, notify_admin_session_closed,
)
from telegram_page.gold.consistency import run_full_check, ConsistencyReport
from telegram_page.gold.weekly_capital_cache import weekly_capital, WeeklyCapitalCache, CapitalEntry
from telegram_page.gold.capital_campaign import (
    run_campaign, weekly_scheduler_loop, CampaignConfig, CampaignReport,
)

# Alias rétro-compat : d'anciens callers importaient `gold_buffer_v7`
gold_buffer_v7 = gold_buffer

__all__ = [
    "session_registry", "SessionStatus",
    "snapshot_store", "build_snapshot", "SessionSnapshot",
    "user_state_v7", "CalcContext",
    "gold_buffer", "gold_buffer_v7", "GoldWriteBuffer",
    "open_new_session", "close_session", "mark_broadcast_done",
    "current_snapshot", "current_version", "is_open", "is_ready_for_confirmations",
    "register_buffer",
    "guard", "make_callback_data", "check_callback", "GuardResult",
    "register_gold_handlers_v7", "build_calc_context", "adjust_entry_sl",
    "send_teaser_broadcast",
    "notify_tp_reached", "notify_sl_touched",
    "apply_tp_closure_in_db", "notify_admin_session_closed",
    "run_full_check", "ConsistencyReport",
    "weekly_capital", "WeeklyCapitalCache", "CapitalEntry",
    "run_campaign", "weekly_scheduler_loop", "CampaignConfig", "CampaignReport",
]