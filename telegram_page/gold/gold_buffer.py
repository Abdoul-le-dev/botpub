"""
gold_buffer.py — Buffer mémoire + écritures SQL par lots (v6).

Remplace le worker "1 job = 1 requête" de gold_write_queue par un
write-behind par LOTS :

    30 000 actions utilisateur
        ↓  append RAM (< 1 µs, jamais bloquant)
    buffers en mémoire (entries / steps / events)
        ↓  flush toutes les 500 ms ou dès 500 éléments
    1 INSERT multi-lignes par table   ←  au lieu de 30 000 requêtes
        +
    1 UPDATE d'agrégats par session   ←  au lieu d'un SELECT SUM + UPDATE
                                          par confirmation (O(n²) avant)

Propriétés :
  - entries et steps sont des dicts clés (session_id, user_id) → si un
    user reclique/ressaisit avant le flush, seul le DERNIER état est
    écrit ("last write wins"), dédoublonnage gratuit.
  - En cas d'échec MySQL : les données sont re-fusionnées dans les
    buffers, retentées au flush suivant, et l'admin est alerté (throttlé
    à 1 alerte / 60 s pour ne pas spammer).
  - Perte maximale en cas de crash brutal du process : le contenu des
    buffers, soit ≤ 500 ms d'actions — conforme à ta priorité
    "je préfère perdre une écriture que faire attendre un utilisateur".

Intégration (main.py, post_init) :
    from telegram_page.gold.gold_buffer import gold_buffer
    gold_buffer.start(app.bot)
"""

import asyncio
import logging
import time
import json
import traceback
from datetime import datetime

from db import get_db
from telegram_page.gold.gold_state import user_state

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

FLUSH_INTERVAL = 0.5    # secondes
MAX_BATCH      = 500    # flush anticipé au-delà
CHUNK_ROWS     = 500    # lignes max par INSERT multi-lignes


def _multi_insert_sql(prefix: str, n_cols: int, n_rows: int, odku: str | None) -> str:
    """Construit `INSERT ... VALUES (…),(…),… [AS new_vals ON DUPLICATE KEY UPDATE …]`."""
    row = "(" + ",".join(["%s"] * n_cols) + ")"
    sql = f"{prefix} VALUES {','.join([row] * n_rows)}"
    if odku:
        sql += f" AS new_vals ON DUPLICATE KEY UPDATE {odku}"
    return sql


class GoldWriteBuffer:

    def __init__(self):
        # (session_id, user_id) -> tuple de colonnes — last write wins
        self._entries: dict[tuple, tuple] = {}
        self._steps:   dict[tuple, tuple] = {}
        # append-only
        self._events: list[tuple] = []
        # session_id -> phase à écrire
        self._phase_updates: dict[int, str] = {}
        # sessions dont les agrégats doivent être réécrits
        self._dirty_agg: set[int] = set()

        self._bot = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_admin_alert = 0.0

    # ── API côté handlers (0 SQL, 0 await bloquant) ──────────────────────

    def add_entry(self, session_id: int, user_id: int, season_id, capital: float,
                  risk_pct: float, risk_usd: float, lot: float, tp_level: int,
                  perte_sl: float, gain_tp1, gain_tp2, gain_tp3):
        self._entries[(session_id, user_id)] = (
            session_id, user_id, season_id, capital, risk_pct, risk_usd,
            lot, tp_level, perte_sl, gain_tp1, gain_tp2, gain_tp3, capital,
        )
        self._dirty_agg.add(session_id)
        self._maybe_wake()

    def add_step(self, session_id: int, user_id: int, step: str, capital: float = None):
        self._steps[(session_id, user_id)] = (session_id, user_id, step, capital)
        self._maybe_wake()

    def add_event(self, session_id: int, user_id: int, event_type: str, payload: dict = None):
        self._events.append((
            session_id, user_id, event_type,
            json.dumps(payload) if payload else None,
        ))
        self._maybe_wake()

    def set_phase(self, session_id: int, phase: str):
        self._phase_updates[session_id] = phase
        self._maybe_wake()

    def pending(self) -> int:
        return len(self._entries) + len(self._steps) + len(self._events)

    def _maybe_wake(self):
        if self.pending() >= MAX_BATCH:
            self._wake.set()

    # ── Worker ────────────────────────────────────────────────────────────

    def start(self, bot):
        self._bot = bot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("[gold_buffer] Flusher démarré.")

    async def _loop(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=FLUSH_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self.pending() or self._phase_updates or self._dirty_agg:
                try:
                    await self.flush()
                except Exception as e:
                    await self._alert_admin(e)

    async def flush(self):
        # Swap atomique (asyncio mono-thread : aucun lock nécessaire entre
        # deux await — on prend tout d'un coup avant le premier await).
        entries = list(self._entries.values());  self._entries.clear()
        steps   = list(self._steps.values());    self._steps.clear()
        events  = self._events;                  self._events = []
        phases  = dict(self._phase_updates);     self._phase_updates.clear()
        dirty   = set(self._dirty_agg);          self._dirty_agg.clear()

        t0 = time.perf_counter()
        try:
            async with get_db() as cur:

                # 1. Phases de session (teaser → open, etc.)
                for sid, phase in phases.items():
                    if phase == "open":
                        await cur.execute("""
                            UPDATE gold_trade_sessions
                            SET current_phase = 'open', opened_at = COALESCE(opened_at, NOW())
                            WHERE id = %s AND current_phase = 'teaser'
                        """, (sid,))
                    else:
                        await cur.execute(
                            "UPDATE gold_trade_sessions SET current_phase = %s WHERE id = %s",
                            (phase, sid),
                        )

                # 2. Confirmations membres — 1 requête pour N confirmations
                ODKU_ENTRIES = """
                    capital_declared  = new_vals.capital_declared,
                    risk_pct          = new_vals.risk_pct,
                    risk_usd          = new_vals.risk_usd,
                    lot_calculated    = new_vals.lot_calculated,
                    tp_level_assigned = new_vals.tp_level_assigned,
                    perte_sl          = new_vals.perte_sl,
                    gain_tp1          = new_vals.gain_tp1,
                    gain_tp2          = new_vals.gain_tp2,
                    gain_tp3          = new_vals.gain_tp3,
                    capital_before    = new_vals.capital_before,
                    confirmed_at      = NOW()
                """
                PREFIX_ENTRIES = """
                    INSERT INTO gold_member_entries
                        (session_id, user_id, season_id, capital_declared, risk_pct,
                         risk_usd, lot_calculated, tp_level_assigned,
                         perte_sl, gain_tp1, gain_tp2, gain_tp3,
                         capital_before, step_reached, confirmed_at)
                """
                for i in range(0, len(entries), CHUNK_ROWS):
                    chunk  = entries[i:i + CHUNK_ROWS]
                    # 13 colonnes de données + 'confirmed' + NOW() injectés par ligne
                    row    = "(" + ",".join(["%s"] * 13) + ",'confirmed',NOW())"
                    sql    = (PREFIX_ENTRIES + " VALUES "
                              + ",".join([row] * len(chunk))
                              + f" AS new_vals ON DUPLICATE KEY UPDATE {ODKU_ENTRIES}")
                    params = [v for r in chunk for v in r]
                    await cur.execute(sql, params)

                # 3. Étapes utilisateur — 1 requête pour N étapes
                for i in range(0, len(steps), CHUNK_ROWS):
                    chunk = steps[i:i + CHUNK_ROWS]
                    sql = _multi_insert_sql(
                        """INSERT INTO gold_user_sessions
                               (session_id, user_id, step, capital_input)""",
                        4, len(chunk),
                        """step = new_vals.step,
                           capital_input = new_vals.capital_input,
                           updated_at = NOW()""",
                    )
                    params = [v for r in chunk for v in r]
                    await cur.execute(sql, params)

                # 4. Événements de flux — 1 requête pour N événements
                for i in range(0, len(events), CHUNK_ROWS):
                    chunk = events[i:i + CHUNK_ROWS]
                    row = "(" + ",".join(["%s"] * 4) + ",NOW())"
                    sql = ("INSERT INTO gold_flow_events "
                           "(session_id, user_id, event_type, payload, created_at) VALUES "
                           + ",".join([row] * len(chunk)))
                    params = [v for r in chunk for v in r]
                    await cur.execute(sql, params)

                # 5. Agrégats — calculés depuis la RAM (StateManager),
                #    1 seul UPDATE par session et par flush.
                for sid in dirty:
                    if sid != user_state.session_id:
                        continue
                    agg = user_state.aggregates()
                    await cur.execute("""
                        UPDATE gold_trade_sessions SET
                            total_members_in      = %s,
                            total_lots_engaged    = %s,
                            estimated_loss_sl     = %s,
                            estimated_gain_tp1    = %s,
                            estimated_gain_tp2    = %s,
                            estimated_gain_tp3    = %s,
                            aggregates_updated_at = NOW()
                        WHERE id = %s
                    """, (agg["total_members"], agg["total_lots"],
                          agg["total_loss_sl"], agg["total_gain_tp1"],
                          agg["total_gain_tp2"], agg["total_gain_tp3"], sid))

            dt = (time.perf_counter() - t0) * 1000
            n  = len(entries) + len(steps) + len(events)
            if n:
                logger.info(f"[gold_buffer] flush {n} lignes en {dt:.0f} ms "
                            f"({len(entries)} entries / {len(steps)} steps / {len(events)} events)")

        except Exception:
            # Re-fusionne pour retenter au prochain flush (les nouveaux
            # écrasent les anciens sur les dicts — cohérent : last write wins)
            for r in entries:
                self._entries.setdefault((r[0], r[1]), r)
            for r in steps:
                self._steps.setdefault((r[0], r[1]), r)
            self._events = events + self._events
            self._phase_updates = {**phases, **self._phase_updates}
            self._dirty_agg |= dirty
            raise

    async def _alert_admin(self, error: Exception):
        tb = traceback.format_exc()
        logger.error(f"[gold_buffer] flush ÉCHOUÉ: {error}\n{tb}")
        now = time.time()
        if not self._bot or now - self._last_admin_alert < 60:
            return
        self._last_admin_alert = now
        try:
            await self._bot.send_message(
                chat_id=ADMIN_ID,
                text=(f"🔴 Échec flush DB Gold\n"
                      f"Erreur : {str(error)[:300]}\n"
                      f"Buffer en attente : {self.pending()} lignes\n"
                      f"Retentative automatique au prochain cycle ({FLUSH_INTERVAL}s)."),
            )
        except Exception:
            pass

    async def status(self) -> dict:
        return {
            "pending": self.pending(),
            "entries": len(self._entries),
            "steps": len(self._steps),
            "events": len(self._events),
            "worker_running": self._task is not None and not self._task.done(),
        }


# Instance unique pour tout le process
gold_buffer = GoldWriteBuffer()