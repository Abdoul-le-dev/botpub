"""
gold_buffer.py — Buffer Gold v8 (write-behind, phases uniquement).

REMPLACE l'ancien gold_buffer.py (v7.1) qui gérait aussi les
confirmations par membre (add_entry/add_step/add_event + agrégats
RAM/SQL). Depuis le passage au signal brut (plus de capital/lot
personnalisé stocké par membre), ces write-behind par membre n'ont
plus aucun appelant : signal_broadcast.py et interactive_tools.py
n'écrivent rien pour un membre individuel.

CE QUI RESTE
  set_phase(session_id, phase)
    Seule écriture encore nécessaire dans le flux chaud : marquer
    qu'une session est passée de 'teaser' à 'open', ou qu'un TP/SL a
    été touché, ou que la session est fermée. Toujours en write-behind
    (flush toutes les 500 ms) pour ne jamais bloquer un handler
    Telegram sur une écriture SQL.

INTERFACE PUBLIQUE INCHANGÉE (pour ne pas casser lifecycle.py, qui
n'a pas été fourni pour ce refactor et qui appelle vraisemblablement
attach()/drain_and_stop() à l'ouverture/fermeture d'une session) :
    gold_buffer.start(bot)                    # une fois, au boot
    gold_buffer.attach(session_id, version)    # à l'ouverture
    gold_buffer.set_phase(session_id, phase)   # au fil de l'eau
    await gold_buffer.drain_and_stop()         # à la fermeture
    gold_buffer.status()                       # introspection /queue_status

attach()/drain_and_stop() sont conservés pour compatibilité d'API
mais ne font plus de rejet versionné ni de purge RAM par membre —
il n'y a plus rien à isoler entre sessions côté buffer.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback

from db import get_db

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

FLUSH_INTERVAL = 0.5    # secondes


class GoldPhaseBuffer:
    """Write-behind minimal : uniquement les mises à jour de phase."""

    def __init__(self):
        self._sid: int | None = None
        self._ver: int | None = None

        # session_id -> phase (dernière valeur gagne — last write wins)
        self._phase_updates: dict[int, str] = {}

        self._bot = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_admin_alert = 0.0

    # ══════════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ══════════════════════════════════════════════════════════════════════

    def start(self, bot):
        """Démarre le worker de flush. À appeler UNE seule fois au boot."""
        self._bot = bot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("[gold_buffer] worker démarré")

    def attach(self, session_id: int, version: int | None = None):
        """Conservé pour compat API — juste un repère d'affichage désormais."""
        self._sid = session_id
        self._ver = version
        logger.info(f"[gold_buffer] attaché à #{session_id}v{version}")

    async def drain_and_stop(self):
        """Flush ce qui reste puis détache (repère d'affichage seulement)."""
        await self._drain()
        sid, ver = self._sid, self._ver
        self._sid = None
        self._ver = None
        logger.info(f"[gold_buffer] détaché de #{sid}v{ver}")

    async def _drain(self):
        if not self._phase_updates:
            return
        try:
            await self.flush()
        except Exception as e:
            logger.error(f"[gold_buffer] drain flush ÉCHOUÉ: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # ÉCRITURE
    # ══════════════════════════════════════════════════════════════════════

    def set_phase(self, session_id: int, phase: str):
        self._phase_updates[session_id] = phase
        self._wake.set()

    def pending(self) -> int:
        return len(self._phase_updates)

    # ══════════════════════════════════════════════════════════════════════
    # WORKER
    # ══════════════════════════════════════════════════════════════════════

    async def _loop(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=FLUSH_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._phase_updates:
                try:
                    await self.flush()
                except Exception as e:
                    await self._alert_admin(e)

    async def flush(self):
        """Swap atomique puis écriture SQL. Re-fusion en RAM si échec."""
        phases = dict(self._phase_updates)
        self._phase_updates.clear()
        if not phases:
            return

        t0 = time.perf_counter()
        try:
            async with get_db() as cur:
                for sid, phase in phases.items():
                    if phase == "open":
                        await cur.execute("""
                            UPDATE gold_trade_sessions
                            SET current_phase = 'open',
                                opened_at = COALESCE(opened_at, NOW())
                            WHERE id = %s AND current_phase = 'teaser'
                        """, (sid,))
                    else:
                        await cur.execute(
                            "UPDATE gold_trade_sessions SET current_phase = %s WHERE id = %s",
                            (phase, sid),
                        )
            dt = (time.perf_counter() - t0) * 1000
            logger.info(f"[gold_buffer] flush {len(phases)} phase(s) en {dt:.0f} ms")
        except Exception:
            # Re-fusion : les nouveaux writes (arrivés pendant le flush)
            # sont prioritaires — on ne réinjecte que ce qui n'a pas déjà
            # été mis à jour entre-temps.
            for sid, phase in phases.items():
                self._phase_updates.setdefault(sid, phase)
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
                text=(f"🔴 Échec flush DB Gold (phases)\n"
                      f"Erreur : {str(error)[:300]}\n"
                      f"En attente : {self.pending()} phase(s)\n"
                      f"Retentative auto au prochain cycle ({FLUSH_INTERVAL}s)."),
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # INTROSPECTION — /queue_status
    # ══════════════════════════════════════════════════════════════════════

    def attached_label(self) -> str:
        if self._sid is None:
            return "aucune"
        return f"#{self._sid}v{self._ver}"

    async def stop(self):
        """
        Arrêt propre — à appeler depuis le hook post_shutdown de
        l'Application. Flush ce qui reste puis annule le worker,
        pour éviter le warning asyncio "Task was destroyed but it is
        pending!" quand le service est redémarré.
        """
        try:
            await self._drain()
        except Exception:
            logger.warning("[gold_buffer] flush final au shutdown échoué")
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[gold_buffer] worker arrêté proprement")

    def status(self) -> dict:
        return {
            "attached":       self.attached_label(),
            "session_id":     self._sid,
            "version":        self._ver,
            "pending":        self.pending(),
            "entries":        0,
            "steps":          0,
            "events":         0,
            "dirty_agg":      0,
            "worker_running": self._task is not None and not self._task.done(),
        }


# Instance unique pour tout le process
gold_buffer = GoldPhaseBuffer()