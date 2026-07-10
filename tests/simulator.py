"""
tests/simulator.py — Simulateur en mémoire du flux Gold v7.1.

WORKFLOW SIMULÉ (v7.1)
  Broadcast → click access → capital cache ? →
    - Oui → processed direct (auto-calc)
    - Non → capital saisi → processed

Ce que le simulateur peut reproduire :
  - user avec capital déjà en cache (fast path, ~1 étape)
  - user avec capital en SQL uniquement (mid path : promotion cache)
  - user sans capital nulle part (slow path : formulaire)
  - user avec capital expiré (re-saisie forcée)
  - double clic sur "access" → idempotence
  - callback stale (vieille session)
  - ouverture / fermeture successive de sessions
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from types import MappingProxyType

from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.session_snapshot import snapshot_store, SessionSnapshot, TpRule
from telegram_page.gold.gold_state import user_state_v7, CalcContext
from telegram_page.gold.gold_broadcast import build_calc_context, adjust_entry_sl
from telegram_page.gold.weekly_capital_cache import weekly_capital, CapitalEntry, TTL_SECONDS
from tests.user_generator import FakeUser, Persona

from db import get_db

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Session mock (sans MySQL)
# ══════════════════════════════════════════════════════════════════════════════

def make_mock_snapshot(session_id: int, version: int, *,
                       direction: str = "buy",
                       entry_price: float = 2000.0,
                       sl: float = 1990.0,
                       tp1: float = 2010.0,
                       tp2: float = 2020.0,
                       tp3: float = 2030.0) -> SessionSnapshot:
    rules = {
        0: TpRule(1, 0, 499.99, 1.0, None, None, None, None, None, None),
        1: TpRule(2, 500, 1999.99, 1.5, None, None, None, None, None, None),
        2: TpRule(3, 2000, None, 2.0, None, None, None, None, None, None),
    }
    return SessionSnapshot(
        session_id=session_id, version=version, season_id=1,
        direction=direction, entry_price=entry_price, sl=sl,
        tp1=tp1, tp2=tp2, tp3=tp3,
        sl_pips=round(abs(entry_price - sl), 2),
        tp1_pips=round(abs(tp1 - entry_price), 2) if tp1 else None,
        tp2_pips=round(abs(tp2 - entry_price), 2) if tp2 else None,
        tp3_pips=round(abs(tp3 - entry_price), 2) if tp3 else None,
        confidence_level=4, note=None, screenshot_url=None, timeframe="M15",
        tp_rules=MappingProxyType(rules),
    )


async def install_mock_session(session_id: int, **snap_kwargs) -> SessionSnapshot:
    handle = await session_registry.try_open(session_id, mode="replace")
    snap = make_mock_snapshot(session_id, handle.version, **snap_kwargs)
    snapshot_store.set_active(snap)
    user_state_v7.bind(session_id, handle.version)
    session_registry.mark_active(session_id, handle.version)
    return snap


async def teardown_mock_session():
    handle = session_registry.current()
    if handle is None:
        return
    snapshot_store.clear_active()
    user_state_v7.unbind()
    await session_registry.finalize_close(handle.session_id, handle.version)


# ══════════════════════════════════════════════════════════════════════════════
# Prépopulation du Weekly Capital Cache
# ══════════════════════════════════════════════════════════════════════════════

def preseed_cache_ram(users: list[FakeUser], fraction: float = 0.7):
    """
    Pour simuler l'état "milieu de semaine" : une fraction des users a
    déjà son capital en cache RAM. Les autres devront le saisir.
    """
    for u in users:
        if random.random() < fraction:
            now = time.time()
            weekly_capital._entries[u.user_id] = CapitalEntry(
                user_id=u.user_id, capital=u.capital,
                version=1, updated_at=now, expire_at=now + TTL_SECONDS,
            )


def preseed_cache_expired(users: list[FakeUser], fraction: float = 0.1):
    """Marque une fraction des users comme "capital expiré" (au-delà TTL)."""
    for u in users:
        if random.random() < fraction:
            now = time.time()
            weekly_capital._entries[u.user_id] = CapitalEntry(
                user_id=u.user_id, capital=u.capital,
                version=1,
                updated_at=now - TTL_SECONDS - 3600,
                expire_at=now - 3600,   # expiré il y a 1h
            )


async def wipe_cache():
    weekly_capital._entries.clear()
    weekly_capital._load_locks.clear()
    async with get_db() as cur:
        await cur.execute("DELETE FROM user_capital_weekly")


# ══════════════════════════════════════════════════════════════════════════════
# Simulation du parcours d'un user (v7.1)
# ══════════════════════════════════════════════════════════════════════════════

async def _simulate_flow(user: FakeUser, snap: SessionSnapshot,
                          buffer=None) -> str:
    """
    Retourne :
      processed_from_cache | processed_after_input |
      blocked | stale_rejected | session_closed |
      invalid_capital_typo | no_processing
    """
    sid, ver = snap.session_id, snap.version

    if user.is_blocked or user.persona == Persona.BLOCKER:
        return "blocked"

    # STALE : click sur ancienne session — doit être rejeté
    if user.persona == Persona.STALE_CLICKER and user.stale_session_id is not None:
        ok = user_state_v7.transition(user.stale_session_id, 999_999,
                                       user.user_id, "waiting_capital")
        if not ok:
            return "stale_rejected"

    if user.late_delay_s > 0 and session_registry.current() is None:
        return "session_closed"

    # ── disclaimer_ok
    if not user_state_v7.try_begin(sid, ver, user.user_id, "disclaimer"):
        return "no_processing"
    ok = user_state_v7.transition(sid, ver, user.user_id, "teaser")
    user_state_v7.end(user.user_id, "disclaimer")
    if not ok:
        return "no_processing"

    # ── access — potentiel double clic (test idempotence)
    n_clicks = 2 if random.random() < user.double_click_prob else 1

    processed_from_cache = False
    needs_capital = False

    for i in range(n_clicks):
        # Idempotence : si déjà processed, tout le reste doit être no-op
        if user_state_v7.is_processed(sid, ver, user.user_id):
            continue

        if not user_state_v7.try_begin(sid, ver, user.user_id, "access"):
            continue
        try:
            capital = weekly_capital.get_ram(user.user_id)
            if capital is None:
                capital = await weekly_capital.get_or_load(user.user_id)

            if capital is None:
                # Formulaire à afficher
                user_state_v7.transition(sid, ver, user.user_id, "waiting_capital")
                if buffer:
                    buffer.add_step(sid, ver, user.user_id, "waiting_capital")
                needs_capital = True
            else:
                # Fast path : traitement complet
                eff_entry, eff_sl, _ = adjust_entry_sl(snap, None)
                calc = build_calc_context(snap, user.user_id, capital,
                                            eff_entry, eff_sl)
                if user_state_v7.set_calc(sid, ver, user.user_id, calc) \
                    and user_state_v7.mark_processed(sid, ver, user.user_id, calc):
                    if buffer:
                        buffer.add_entry(sid, ver, user.user_id, snap.season_id,
                                          calc.capital, calc.risk_pct, calc.risk_usd,
                                          calc.lot, calc.tp_level, calc.perte_sl,
                                          calc.gain_tp1, calc.gain_tp2, calc.gain_tp3)
                        buffer.add_step(sid, ver, user.user_id, "processed", capital)
                    processed_from_cache = True
        finally:
            user_state_v7.end(user.user_id, "access")

    if processed_from_cache:
        return "processed_from_cache"

    if not needs_capital:
        return "no_processing"

    # ── slow path : saisie du capital
    if random.random() < user.typo_prob:
        # tape "abc" ou "-100" — reste bloqué en waiting_capital
        return "invalid_capital_typo"

    # Enregistre le nouveau capital (RAM + SQL simulé)
    try:
        await weekly_capital.set(user.user_id, user.capital)
    except Exception:
        return "no_processing"

    # Traite le trade immédiatement
    eff_entry, eff_sl, _ = adjust_entry_sl(snap, None)
    calc = build_calc_context(snap, user.user_id, user.capital, eff_entry, eff_sl)
    if user_state_v7.set_calc(sid, ver, user.user_id, calc) \
        and user_state_v7.mark_processed(sid, ver, user.user_id, calc):
        if buffer:
            buffer.add_entry(sid, ver, user.user_id, snap.season_id,
                              calc.capital, calc.risk_pct, calc.risk_usd,
                              calc.lot, calc.tp_level, calc.perte_sl,
                              calc.gain_tp1, calc.gain_tp2, calc.gain_tp3)
            buffer.add_step(sid, ver, user.user_id, "processed", user.capital)
        return "processed_after_input"

    return "no_processing"


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SimulationResult:
    n_users:                   int
    n_processed_from_cache:    int = 0
    n_processed_after_input:   int = 0
    n_blocked:                 int = 0
    n_stale_rejected:          int = 0
    n_session_closed:          int = 0
    n_invalid_capital_typo:    int = 0
    n_no_processing:           int = 0
    elapsed_s:                 float = 0.0
    per_user_max_ms:           float = 0.0
    per_user_avg_ms:           float = 0.0

    @property
    def n_processed_total(self) -> int:
        return self.n_processed_from_cache + self.n_processed_after_input

    def summary(self) -> str:
        return (f"Users={self.n_users} "
                f"processed_total={self.n_processed_total} "
                f"(from_cache={self.n_processed_from_cache} "
                f"after_input={self.n_processed_after_input}) "
                f"blocked={self.n_blocked} "
                f"stale_rejected={self.n_stale_rejected} "
                f"elapsed={self.elapsed_s:.2f}s "
                f"per_user_max={self.per_user_max_ms:.2f}ms")


async def run_simulation(users: list[FakeUser], snap: SessionSnapshot,
                          buffer=None, concurrency: int = 100) -> SimulationResult:
    sem = asyncio.Semaphore(concurrency)
    result = SimulationResult(n_users=len(users))
    per_user_times = []
    lock = asyncio.Lock()

    async def _one(u: FakeUser):
        async with sem:
            t0 = time.perf_counter()
            outcome = await _simulate_flow(u, snap, buffer)
            dt = (time.perf_counter() - t0) * 1000
            u.outcome = outcome
            async with lock:
                per_user_times.append(dt)
                attr = f"n_{outcome}"
                if hasattr(result, attr):
                    setattr(result, attr, getattr(result, attr) + 1)

    t0 = time.perf_counter()
    await asyncio.gather(*[_one(u) for u in users])
    result.elapsed_s = time.perf_counter() - t0
    if per_user_times:
        result.per_user_max_ms = max(per_user_times)
        result.per_user_avg_ms = sum(per_user_times) / len(per_user_times)
    return result