"""
broadcast/rate_limiter.py — rate limiter global adaptatif (AIMD).

Principe :
  - Un seul limiteur pour tout le process : quelle que soit le nombre de
    broadcasts concurrents, la somme des envois ne dépasse jamais le débit
    autorisé par Telegram (~30 msg/s en broadcast).
  - Stratégie AIMD (Additive Increase / Multiplicative Decrease) :
      * En vitesse de croisière → maintient RATE_TARGET (29 msg/s).
      * Sur RetryAfter → chute immédiate à RATE_MIN (25 msg/s), pause globale
        de tous les workers pendant `retry_after` secondes.
      * Récupération → +RATE_RECOVERY_INCREMENT tous les
        RATE_RECOVERY_STEP_SUCCESSES envois OK, jusqu'à RATE_MAX (30).

Concurrency-safe : un seul asyncio.Lock() sérialise l'accès au timing. Les
workers ne se marchent pas dessus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


@dataclass
class RateMetrics:
    """Métriques observées, exposées pour les rapports finaux."""
    total_sends: int = 0
    max_observed_rate: float = 0.0
    min_observed_rate: float = float("inf")
    # Timestamps des N derniers envois pour calcul de vitesse instantanée
    _recent: list[float] = field(default_factory=list)

    def record_send(self, ts: float) -> None:
        self.total_sends += 1
        self._recent.append(ts)
        # Fenêtre glissante de 2 secondes
        cutoff = ts - 2.0
        while self._recent and self._recent[0] < cutoff:
            self._recent.pop(0)
        # Vitesse instantanée = envois dans la fenêtre / durée fenêtre
        if len(self._recent) >= 2:
            span = self._recent[-1] - self._recent[0]
            if span > 0:
                rate = (len(self._recent) - 1) / span
                if rate > self.max_observed_rate:
                    self.max_observed_rate = rate
                if rate < self.min_observed_rate:
                    self.min_observed_rate = rate

    def snapshot(self, elapsed_seconds: float) -> dict:
        avg = (self.total_sends / elapsed_seconds) if elapsed_seconds > 0 else 0.0
        return {
            "total_sends":        self.total_sends,
            "avg_rate":           round(avg, 2),
            "max_rate":           round(self.max_observed_rate, 2),
            "min_rate":           round(self.min_observed_rate, 2) if self.min_observed_rate != float("inf") else 0.0,
        }


class AdaptiveRateLimiter:
    """
    Limiteur async, thread-unsafe mais coroutine-safe. Un seul asyncio.Lock
    protège l'état interne — les workers appellent `acquire()` avant chaque
    envoi et `notify_success()` / `notify_retry_after()` après.
    """

    def __init__(
        self,
        target: float = config.RATE_TARGET,
        rate_min: float = config.RATE_MIN,
        rate_max: float = config.RATE_MAX,
    ):
        self._target = target
        self._min = rate_min
        self._max = rate_max
        self._current = target
        self._last_send_ts: float = 0.0
        self._pause_until: float = 0.0
        self._success_streak: int = 0
        self._lock = asyncio.Lock()
        self.metrics = RateMetrics()

    # ── API publique ─────────────────────────────────────────────────────────

    @property
    def current_rate(self) -> float:
        return self._current

    async def acquire(self) -> None:
        """Bloque jusqu'à ce qu'il soit permis d'envoyer un message."""
        async with self._lock:
            now = time.monotonic()

            # 1) Pause globale suite à un RetryAfter ?
            if now < self._pause_until:
                wait = self._pause_until - now
                logger.debug(f"[rate] pause globale RetryAfter — {wait:.2f}s")
                await asyncio.sleep(wait)
                now = time.monotonic()

            # 2) Espacement entre 2 envois selon le débit courant
            interval = 1.0 / self._current
            elapsed_since_last = now - self._last_send_ts
            if elapsed_since_last < interval:
                await asyncio.sleep(interval - elapsed_since_last)
                now = time.monotonic()

            self._last_send_ts = now
            self.metrics.record_send(now)

    async def notify_success(self) -> None:
        """
        À appeler après chaque envoi réussi. Fait remonter progressivement le
        débit vers RATE_MAX si on tient la cadence sans erreur.
        """
        async with self._lock:
            self._success_streak += 1
            if self._success_streak >= config.RATE_RECOVERY_STEP_SUCCESSES:
                self._success_streak = 0
                new_rate = min(self._current + config.RATE_RECOVERY_INCREMENT, self._max)
                if new_rate != self._current:
                    logger.info(f"[rate] recovery : {self._current:.2f} → {new_rate:.2f} msg/s")
                    self._current = new_rate

    async def notify_retry_after(self, seconds: float) -> None:
        """
        Telegram nous a envoyé RetryAfter. Tous les workers doivent stopper,
        et on redescend au débit plancher pour laisser la file s'écouler côté
        Telegram avant de retenter.
        """
        async with self._lock:
            now = time.monotonic()
            # +0.5s de marge pour ne pas retomber pile à l'échéance
            self._pause_until = max(self._pause_until, now + seconds + 0.5)
            self._current = self._min
            self._success_streak = 0
            logger.warning(
                f"[rate] RetryAfter {seconds}s → pause globale, débit rabaissé à {self._min} msg/s"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Singleton global partagé entre tous les broadcasts concurrents du process.
# ══════════════════════════════════════════════════════════════════════════════

_global_limiter: Optional[AdaptiveRateLimiter] = None
_global_lock = asyncio.Lock()


async def get_global_limiter() -> AdaptiveRateLimiter:
    """Retourne (et crée si besoin) le rate limiter partagé du process."""
    global _global_limiter
    if _global_limiter is None:
        async with _global_lock:
            if _global_limiter is None:
                _global_limiter = AdaptiveRateLimiter()
                logger.info(
                    f"[rate] limiter global initialisé — "
                    f"target={config.RATE_TARGET} min={config.RATE_MIN} max={config.RATE_MAX}"
                )
    return _global_limiter
