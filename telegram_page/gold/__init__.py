"""
gold_v7 — Architecture Gold refondue pour cohérence absolue.

Exports publics :
  - open_new_session, close_session, mark_broadcast_done, current_snapshot
  - session_registry, snapshot_store, user_state_v7, gold_buffer_v7
  - register_gold_handlers_v7
  - run_full_check (consistency)

Usage minimal (main.py) :

    from gold_v7 import (
        gold_buffer_v7, register_gold_handlers_v7,
        register_buffer,
    )

    async def _post_init(application):
        gold_buffer_v7.bind_bot(application.bot)
        register_buffer(gold_buffer_v7)
        # NE PAS auto-attach au démarrage : les sessions s'ouvrent
        # explicitement via lifecycle.open_new_session() au broadcast.

    register_gold_handlers_v7(app)
"""

from telegram_page.gold.session_registry import session_registry, SessionStatus
from telegram_page.gold.session_snapshot import snapshot_store, build_snapshot, SessionSnapshot
from telegram_page.gold.gold_state import user_state_v7, CalcContext
from telegram_page.gold.gold_buffer import gold_buffer_v7
from telegram_page.gold.lifecycle import (
    open_new_session, close_session, mark_broadcast_done,
    current_snapshot, current_version, is_open, is_ready_for_confirmations,
    register_buffer,
)
from telegram_page.gold.callback_guard import guard, make_callback_data, check_callback, GuardResult
from telegram_page.gold.gold_broadcast import register_gold_handlers_v7, build_calc_context, adjust_entry_sl
from telegram_page.gold.consistency import run_full_check, ConsistencyReport
from telegram_page.gold.weekly_capital_cache import weekly_capital, WeeklyCapitalCache, CapitalEntry
from telegram_page.gold.capital_campaign import capital_campaign

__all__ = [
    "session_registry", "SessionStatus",
    "snapshot_store", "build_snapshot", "SessionSnapshot",
    "user_state_v7", "CalcContext",
    "gold_buffer_v7",
    "open_new_session", "close_session", "mark_broadcast_done",
    "current_snapshot", "current_version", "is_open", "is_ready_for_confirmations",
    "register_buffer",
    "guard", "make_callback_data", "check_callback", "GuardResult",
    "register_gold_handlers_v7", "build_calc_context", "adjust_entry_sl",
    "run_full_check", "ConsistencyReport",
]