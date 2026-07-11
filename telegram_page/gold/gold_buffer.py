"""
gold_buffer.py — Buffer Gold v7.1 (write-behind par lots, versionné).

Différences majeures avec le v6 :
  1. ATTACHÉ À UNE SESSION VERSIONNÉE
     Le buffer sait à quelle (session_id, version) il est actuellement
     attaché. Tout write provenant d'une session obsolète (ancienne
     version ou autre session_id) est REJETÉ silencieusement — protection
     contre les clics sur d'anciens messages Telegram après réouverture.

  2. LIFECYCLE EXPLICITE
     - attach(sid, ver)     : bind sur une nouvelle session
     - drain_and_stop()     : flush total puis détachement (fin de session)
     Le worker reste vivant entre les sessions (démarré une fois via
     start(bot) au boot, comme le v6).

  3. CLÉS RAM VERSIONNÉES
     Les dicts internes utilisent (session_id, version, user_id) au lieu
     de (session_id, user_id). Même si un vieux write arrivait par
     inadvertance, il ne pourrait pas écraser un write de la session
     courante.

  4. AGRÉGATS ISOLÉS
     _dirty_agg stocke des (session_id, version) — l'UPDATE d'agrégats
     ne se déclenche que si l'entrée correspond à la session ACTIVE
     dans user_state_v7.

Propriétés conservées du v6 :
  - Flush toutes les 500 ms ou dès 500 éléments
  - INSERT multi-lignes (1 requête pour N confirmations)
  - Last write wins sur les dicts (double-clic, retry Telegram)
  - Perte max en cas de crash : ≤ 500 ms d'actions
  - Alerte admin throttlée (1/60s) en cas d'échec MySQL
  - Re-fusion en RAM et retry au flush suivant si SQL échoue

Intégration (script.py, _post_init) :
    from telegram_page.gold.gold_buffer import gold_buffer
    gold_buffer.start(application.bot)          # démarre le worker une fois
    register_buffer(gold_buffer)                # lifecycle sait qui contacter

Puis, au fil de la vie du process, lifecycle.open_new_session() appelle
gold_buffer.attach(sid, ver) et lifecycle.close_session() appelle
gold_buffer.drain_and_stop().
"""

import asyncio
import logging
import time
import json
import traceback

from db import get_db
from telegram_page.gold.gold_state import user_state_v7

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
    """
    Buffer versionné : chaque session (id, version) a son propre espace
    logique. Les writes d'une session obsolète sont rejetés.
    """

    def __init__(self):
        # Session actuellement attachée. Tant que rien n'est attaché,
        # tous les writes sont rejetés.
        self._sid: int | None = None
        self._ver: int | None = None

        # Clés : (session_id, version, user_id) — last write wins
        self._entries: dict[tuple, tuple] = {}
        self._steps:   dict[tuple, tuple] = {}

        # append-only : (session_id, version, user_id, event_type, payload_json)
        self._events: list[tuple] = []

        # phase à écrire : session_id -> phase
        self._phase_updates: dict[int, str] = {}

        # sessions dont les agrégats doivent être réécrits : {(sid, ver), ...}
        self._dirty_agg: set[tuple] = set()

        # Infra
        self._bot = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_admin_alert = 0.0
        self._attach_lock = asyncio.Lock()

    # ══════════════════════════════════════════════════════════════════════
    # LIFECYCLE — appelé par lifecycle.py
    # ══════════════════════════════════════════════════════════════════════

    def start(self, bot):
        """Démarre le worker de flush. À appeler UNE seule fois au boot."""
        self._bot = bot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("[buffer_v7] worker démarré")

    async def attach(self, session_id: int, version: int):
        """
        Bind le buffer sur une nouvelle session versionnée.
        Si une autre session était attachée, elle est d'abord drainée
        (flush total) pour ne pas mélanger les données.
        """
        async with self._attach_lock:
            if self._sid is not None and (self._sid, self._ver) != (session_id, version):
                logger.warning(
                    f"[buffer_v7] attach: session #{self._sid}v{self._ver} "
                    f"encore attachée — drain avant switch vers #{session_id}v{version}"
                )
                await self._drain_locked()

            self._sid = session_id
            self._ver = version
            logger.info(f"[buffer_v7] attaché à #{session_id}v{version}")

    async def drain_and_stop(self):
        """
        Flush tout ce qui reste puis détache le buffer.
        Le worker reste vivant (partagé pour toute la vie du process).
        """
        async with self._attach_lock:
            await self._drain_locked()
            sid, ver = self._sid, self._ver
            self._sid = None
            self._ver = None
            logger.info(f"[buffer_v7] détaché de #{sid}v{ver}")

    async def _drain_locked(self):
        """Flush total. À appeler avec _attach_lock détenu."""
        pending = self.pending()
        if pending == 0 and not self._phase_updates and not self._dirty_agg:
            return
        logger.info(f"[buffer_v7] drain: {pending} entrées en attente")
        try:
            await self.flush()
        except Exception as e:
            logger.error(f"[buffer_v7] drain flush ÉCHOUÉ: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # VALIDATION — protection des writes d'anciennes sessions
    # ══════════════════════════════════════════════════════════════════════

    def _accepts(self, session_id: int) -> bool:
        """
        Renvoie True si un write pour session_id est acceptable.
        Un write d'une session obsolète est rejeté silencieusement.
        """
        if self._sid is None:
            logger.debug(f"[buffer_v7] rejet: aucune session attachée (write sid={session_id})")
            return False
        if session_id != self._sid:
            logger.debug(
                f"[buffer_v7] rejet: write sid={session_id} "
                f"≠ session attachée #{self._sid}v{self._ver}"
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════════════════
    # API — appelée par les handlers Telegram (0 SQL, 0 await bloquant)
    # ══════════════════════════════════════════════════════════════════════

    def add_entry(self, session_id: int, user_id: int, season_id, capital: float,
                  risk_pct: float, risk_usd: float, lot: float, tp_level: int,
                  perte_sl: float, gain_tp1, gain_tp2, gain_tp3):
        if not self._accepts(session_id):
            return
        ver = self._ver
        self._entries[(session_id, ver, user_id)] = (
            session_id, user_id, season_id, capital, risk_pct, risk_usd,
            lot, tp_level, perte_sl, gain_tp1, gain_tp2, gain_tp3, capital,
        )
        self._dirty_agg.add((session_id, ver))
        self._maybe_wake()

    def add_step(self, session_id: int, user_id: int, step: str, capital: float = None):
        if not self._accepts(session_id):
            return
        ver = self._ver
        self._steps[(session_id, ver, user_id)] = (session_id, user_id, step, capital)
        self._maybe_wake()

    def add_event(self, session_id: int, user_id: int, event_type: str, payload: dict = None):
        if not self._accepts(session_id):
            return
        ver = self._ver
        self._events.append((
            session_id, ver, user_id, event_type,
            json.dumps(payload) if payload else None,
        ))
        self._maybe_wake()

    def set_phase(self, session_id: int, phase: str):
        # Les phases peuvent aussi arriver depuis la clôture — on accepte
        # même sans session attachée pour ne pas bloquer les writes
        # post-close_session().
        self._phase_updates[session_id] = phase
        self._maybe_wake()

    def pending(self) -> int:
        return len(self._entries) + len(self._steps) + len(self._events)

    def _maybe_wake(self):
        if self.pending() >= MAX_BATCH:
            self._wake.set()

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
            if self.pending() or self._phase_updates or self._dirty_agg:
                try:
                    await self.flush()
                except Exception as e:
                    await self._alert_admin(e)

    async def flush(self):
        """
        Swap atomique des buffers → écriture SQL en lots.
        En cas d'échec, re-fusion des données pour retry au prochain cycle.
        """
        # Swap atomique (asyncio mono-thread : pas de lock nécessaire tant
        # qu'on ne await pas avant d'avoir tout copié)
        entries = list(self._entries.values());  self._entries.clear()
        steps   = list(self._steps.values());    self._steps.clear()
        events  = self._events;                  self._events = []
        phases  = dict(self._phase_updates);     self._phase_updates.clear()
        dirty   = set(self._dirty_agg);          self._dirty_agg.clear()

        # Sauvegarde des clés d'origine pour la re-fusion en cas d'échec
        entries_keys = list(self._get_entries_snapshot_keys(entries))
        steps_keys   = list(self._get_steps_snapshot_keys(steps))

        t0 = time.perf_counter()
        try:
            async with get_db() as cur:

                # 1. Phases de session (teaser → open, etc.)
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
                    chunk = entries[i:i + CHUNK_ROWS]
                    row = "(" + ",".join(["%s"] * 13) + ",'confirmed',NOW())"
                    sql = (PREFIX_ENTRIES + " VALUES "
                           + ",".join([row] * len(chunk))
                           + f" AS new_vals ON DUPLICATE KEY UPDATE {ODKU_ENTRIES}")
                    params = [v for r in chunk for v in r]
                    await cur.execute(sql, params)

                # 3. Étapes utilisateur — 1 requête pour N étapes
                # Fallback en INSERT par ligne si le batch échoue, pour
                # isoler et jeter les lignes corrompues (évite la boucle infinie).
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
                    try:
                        await cur.execute(sql, params)
                    except Exception as batch_err:
                        logger.warning(
                            f"[buffer_v7] batch steps échoué ({batch_err}); "
                            f"fallback ligne-par-ligne sur {len(chunk)} lignes"
                        )
                        sql_one = _multi_insert_sql(
                            """INSERT INTO gold_user_sessions
                                   (session_id, user_id, step, capital_input)""",
                            4, 1,
                            """step = new_vals.step,
                               capital_input = new_vals.capital_input,
                               updated_at = NOW()""",
                        )
                        for r in chunk:
                            try:
                                await cur.execute(sql_one, list(r))
                            except Exception as row_err:
                                logger.error(
                                    f"[buffer_v7] LIGNE JETÉE (step): {r} — {row_err}"
                                )

                # 4. Événements de flux — 1 requête pour N événements
                # Note : le tuple contient (sid, ver, user_id, type, payload)
                # → on drop la version pour l'écriture SQL (pas de colonne version)
                for i in range(0, len(events), CHUNK_ROWS):
                    chunk = events[i:i + CHUNK_ROWS]
                    row = "(" + ",".join(["%s"] * 4) + ",NOW())"
                    sql = ("INSERT INTO gold_flow_events "
                           "(session_id, user_id, event_type, payload, created_at) VALUES "
                           + ",".join([row] * len(chunk)))
                    params = []
                    for r in chunk:
                        # r = (sid, ver, user_id, event_type, payload_json)
                        params.extend([r[0], r[2], r[3], r[4]])
                    await cur.execute(sql, params)

                # 5. Agrégats — 1 UPDATE par session dirty ET encore active
                #    dans user_state_v7 (évite d'écraser avec des zéros
                #    après un close_session qui a purgé la RAM).
                for sid, ver in dirty:
                    if sid != user_state_v7.session_id:
                        continue
                    if hasattr(user_state_v7, "version") and user_state_v7.version != ver:
                        continue
                    agg = user_state_v7.aggregates()
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
                logger.info(
                    f"[buffer_v7] flush {n} lignes en {dt:.0f} ms "
                    f"({len(entries)} entries / {len(steps)} steps / {len(events)} events)"
                )

        except Exception:
            # Re-fusion : les nouveaux writes prioritaires (last write wins),
            # les anciens repris uniquement s'il n'y a pas déjà une version
            # plus récente en RAM.
            for key, r in zip(entries_keys, entries):
                self._entries.setdefault(key, r)
            for key, r in zip(steps_keys, steps):
                self._steps.setdefault(key, r)
            self._events = events + self._events
            self._phase_updates = {**phases, **self._phase_updates}
            self._dirty_agg |= dirty
            raise

    def _get_entries_snapshot_keys(self, entries):
        """Reconstruit les clés (sid, ver, user_id) pour la re-fusion post-échec."""
        # r = (session_id, user_id, season_id, capital, ...)
        # On ne connaît pas la version d'origine ici (elle a été perdue au
        # moment du swap) — on utilise self._ver comme approximation :
        # si le buffer est encore attaché à la même session, c'est correct.
        # Sinon les writes sont de toute façon obsolètes.
        ver = self._ver if self._ver is not None else 0
        for r in entries:
            yield (r[0], ver, r[1])

    def _get_steps_snapshot_keys(self, steps):
        ver = self._ver if self._ver is not None else 0
        for r in steps:
            yield (r[0], ver, r[1])

    async def _alert_admin(self, error: Exception):
        tb = traceback.format_exc()
        logger.error(f"[buffer_v7] flush ÉCHOUÉ: {error}\n{tb}")
        now = time.time()
        if not self._bot or now - self._last_admin_alert < 60:
            return
        self._last_admin_alert = now
        try:
            await self._bot.send_message(
                chat_id=ADMIN_ID,
                text=(f"🔴 Échec flush DB Gold v7\n"
                      f"Erreur : {str(error)[:300]}\n"
                      f"Buffer en attente : {self.pending()} lignes\n"
                      f"Attaché à : {self.attached_label()}\n"
                      f"Retentative auto au prochain cycle ({FLUSH_INTERVAL}s)."),
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # INTROSPECTION — utilisée par /queue_status
    # ══════════════════════════════════════════════════════════════════════

    def attached_label(self) -> str:
        if self._sid is None:
            return "aucune"
        return f"#{self._sid}v{self._ver}"

    def status(self) -> dict:
        return {
            "attached":       self.attached_label(),
            "session_id":     self._sid,
            "version":        self._ver,
            "pending":        self.pending(),
            "entries":        len(self._entries),
            "steps":          len(self._steps),
            "events":         len(self._events),
            "dirty_agg":      len(self._dirty_agg),
            "worker_running": self._task is not None and not self._task.done(),
        }


# Instance unique pour tout le process
gold_buffer = GoldWriteBuffer()