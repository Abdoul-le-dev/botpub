"""
session_registry.py — Registre unique des sessions Gold (v7).

RÈGLE FONDAMENTALE : à un instant donné, UNE seule session Gold peut
être active dans le process. Toute tentative d'ouverture d'une nouvelle
session pendant qu'une autre est active doit soit :
  - être refusée immédiatement (mode strict)
  - déclencher une fermeture propre de l'ancienne avant d'ouvrir la
    nouvelle (mode replace)

Ce module est LE point d'entrée unique pour :
  - déclarer qu'une session s'ouvre
  - déclarer qu'une session se ferme
  - récupérer la session courante
  - obtenir le verrou d'ouverture (empêche 2 broadcasts simultanés)

Personne d'autre dans le code n'a le droit de manipuler l'état "session
active" directement. Ni le cache, ni les routes, ni le state manager.
Tous les autres composants LISENT depuis le registry, jamais écrivent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    OPENING   = "opening"     # broadcast en cours, pas encore prête à recevoir des confirmations
    ACTIVE    = "active"      # session ouverte, les users peuvent confirmer
    CLOSING   = "closing"     # fermeture en cours, plus de nouvelles confirmations acceptées
    CLOSED    = "closed"      # terminée, gardée un court instant pour le nettoyage


@dataclass(slots=True)
class ActiveSession:
    """Handle sur la session active. Immutable après création."""
    session_id: int
    version:    int                       # incrémenté à chaque nouvelle session — sert de fencing token
    opened_at:  float
    status:     SessionStatus = SessionStatus.OPENING
    # Le snapshot figé des données de session (entry, sl, tp1/2/3, direction...)
    # est stocké séparément dans session_snapshot.py.


class SessionRegistry:
    """Singleton process. Une instance pour tout le bot."""

    def __init__(self):
        self._current: ActiveSession | None = None
        # Verrou d'exclusion mutuelle sur ouverture/fermeture — empêche
        # deux broadcasts concurrents ou une fermeture pendant une ouverture.
        self._lock = asyncio.Lock()
        # Compteur monotone pour les versions — jamais réinitialisé, même
        # après restart (persisté ailleurs si besoin, sinon repart de 1).
        self._next_version = 1

    # ── Lecture (jamais bloquante) ────────────────────────────────────────

    def current(self) -> ActiveSession | None:
        return self._current

    def current_id(self) -> int | None:
        return self._current.session_id if self._current else None

    def current_version(self) -> int | None:
        return self._current.version if self._current else None

    def is_accepting_confirmations(self) -> bool:
        return self._current is not None and self._current.status == SessionStatus.ACTIVE

    def matches(self, session_id: int, version: int) -> bool:
        """
        Test de fencing : le callback/action concerne-t-il bien la session
        courante ET la même version ? Un click sur un vieux message
        renverra session_id correct mais version obsolète → False.
        """
        s = self._current
        return (s is not None
                and s.session_id == session_id
                and s.version == version)

    # ── Ouverture / fermeture (protégées par verrou) ──────────────────────

    async def try_open(self, session_id: int, *, mode: str = "strict") -> ActiveSession:
        """
        Enregistre une nouvelle session comme active.

        mode = "strict"  : lève RuntimeError si une session est déjà active.
        mode = "replace" : ferme proprement l'ancienne, puis ouvre la nouvelle.

        Renvoie le handle ActiveSession créé.
        Le status est initialement OPENING — appeler mark_active() quand
        le broadcast est terminé et que le bot est prêt à accepter les
        clics des utilisateurs.
        """
        async with self._lock:
            if self._current is not None:
                if mode == "strict":
                    raise RuntimeError(
                        f"Session #{self._current.session_id} déjà active "
                        f"(status={self._current.status.value}). "
                        f"Fermer avant d'en ouvrir une autre."
                    )
                # mode replace : on ferme AVANT d'ouvrir
                logger.warning(
                    f"[registry] mode=replace — fermeture forcée de la "
                    f"session #{self._current.session_id} au profit de #{session_id}"
                )
                self._current.status = SessionStatus.CLOSING
                # Le lifecycle.close_session() doit être appelé par
                # l'orchestrateur (broadcast_v7) AVANT try_open en mode
                # replace — try_open ne fait que la mise à jour du registre.
                self._current = None

            version = self._next_version
            self._next_version += 1
            self._current = ActiveSession(
                session_id=session_id,
                version=version,
                opened_at=time.time(),
                status=SessionStatus.OPENING,
            )
            logger.info(
                f"[registry] session #{session_id} enregistrée "
                f"(version={version}, status=opening)"
            )
            return self._current

    def mark_active(self, session_id: int, version: int):
        """
        Appelé par le lifecycle quand le broadcast est fini et que le
        bot est prêt à recevoir les clics.
        """
        s = self._current
        if not s or s.session_id != session_id or s.version != version:
            logger.warning(
                f"[registry] mark_active ignoré — session courante ne "
                f"correspond pas (demandée=#{session_id}v{version}, "
                f"courante={s and (s.session_id, s.version)})"
            )
            return
        s.status = SessionStatus.ACTIVE
        logger.info(f"[registry] session #{session_id} → ACTIVE")

    def mark_closing(self, session_id: int, version: int):
        s = self._current
        if not s or s.session_id != session_id or s.version != version:
            return
        s.status = SessionStatus.CLOSING
        logger.info(f"[registry] session #{session_id} → CLOSING")

    async def finalize_close(self, session_id: int, version: int):
        """
        Retire la session du registre. À n'appeler qu'APRÈS drain complet
        (buffers vidés, workers arrêtés, RAM nettoyée) par le lifecycle.
        """
        async with self._lock:
            s = self._current
            if not s or s.session_id != session_id or s.version != version:
                logger.warning(
                    f"[registry] finalize_close ignoré — "
                    f"session courante ne correspond pas"
                )
                return
            s.status = SessionStatus.CLOSED
            logger.info(
                f"[registry] session #{session_id} fermée définitivement "
                f"(durée: {time.time() - s.opened_at:.1f}s)"
            )
            self._current = None

    # ── Introspection / diagnostic ────────────────────────────────────────

    def snapshot(self) -> dict:
        s = self._current
        if not s:
            return {"active": False}
        return {
            "active":     True,
            "session_id": s.session_id,
            "version":    s.version,
            "status":     s.status.value,
            "opened_at":  s.opened_at,
            "uptime_s":   round(time.time() - s.opened_at, 1),
        }


# Instance unique pour tout le process
session_registry = SessionRegistry()