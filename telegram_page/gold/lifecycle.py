"""
lifecycle.py — Ouverture / fermeture propres des sessions Gold (v7).

C'est le SEUL module qui a le droit d'appeler :
  - session_registry.try_open() / finalize_close()
  - snapshot_store.set_active() / clear_active()
  - user_state_v7.bind() / unbind()
  - gold_buffer_v7.attach() / drain_and_stop()

Toute autre partie du code (routes, broadcast, handlers) passe par les
fonctions publiques de ce module :
  - open_new_session(session_id, mode="strict"|"replace")
  - close_session(session_id, version, close_type)
  - is_ready() / current_snapshot() / current_version()

Cela garantit que l'ordre des opérations est TOUJOURS le même :

  OUVERTURE :
    1. Verrou global (impossible d'ouvrir 2 sessions en parallèle)
    2. Si session déjà active :
         - mode strict  → refus
         - mode replace → close_session() complète d'abord
    3. Enregistrement dans registry (version bumpée) → status=OPENING
    4. Construction du snapshot immutable depuis SQL
    5. Bind du state manager (RAM purgée)
    6. Attach du buffer à la nouvelle session (buffer purgé)
    7. Ready pour le broadcast — l'appelant enverra les messages
    8. Après broadcast terminé, registry.mark_active() → status=ACTIVE

  FERMETURE :
    1. Verrou global
    2. registry.mark_closing() → plus aucune confirmation acceptée
    3. buffer.drain_and_stop() : flush tout ce qui reste + arrêt worker
    4. Réécriture SQL des agrégats finaux
    5. snapshot_store.clear_active()
    6. user_state_v7.unbind() — RAM purgée
    7. registry.finalize_close() → retire du registre
"""

from __future__ import annotations

import asyncio
import logging

from db import get_db

from telegram_page.gold.session_registry import session_registry, SessionStatus
from telegram_page.gold.session_snapshot import snapshot_store, build_snapshot, SessionSnapshot
from telegram_page.gold.gold_state import user_state_v7

logger = logging.getLogger(__name__)

# Le buffer v7 est importé "lazy" pour éviter les imports circulaires
# (le buffer utilise le state manager qui est ici).
_buffer = None


def register_buffer(buffer):
    """À appeler UNE fois au démarrage (main.py) avec l'instance du buffer v7."""
    global _buffer
    _buffer = buffer


# ══════════════════════════════════════════════════════════════════════════════
# LECTURE — jamais bloquante
# ══════════════════════════════════════════════════════════════════════════════

def current_snapshot() -> SessionSnapshot | None:
    """Renvoie le snapshot de la session courante, ou None."""
    return snapshot_store.get_active()


def current_version() -> int | None:
    return session_registry.current_version()


def is_ready_for_confirmations() -> bool:
    return session_registry.is_accepting_confirmations()


def is_open() -> bool:
    """True si une session est OPENING ou ACTIVE (broadcast en cours ou fini)."""
    s = session_registry.current()
    return s is not None and s.status in (SessionStatus.OPENING, SessionStatus.ACTIVE)


# ══════════════════════════════════════════════════════════════════════════════
# OUVERTURE
# ══════════════════════════════════════════════════════════════════════════════

async def open_new_session(session_id: int, *, mode: str = "replace") -> SessionSnapshot:
    """
    Ouvre une nouvelle session Gold.

    mode = "strict"  : lève RuntimeError si une session est déjà active.
    mode = "replace" : ferme proprement l'ancienne d'abord.

    À la fin de cette fonction :
      - la session est enregistrée avec un nouveau numéro de version
      - le snapshot immutable est construit et disponible
      - le state manager est bindé sur cette session (RAM propre)
      - le buffer est attaché à cette session (RAM propre)
      - le status est OPENING → l'appelant (broadcast) doit envoyer les
        messages puis appeler mark_broadcast_done() pour passer ACTIVE
    """
    # 1. Si mode=replace et session existante → fermeture propre AVANT
    current = session_registry.current()
    if current is not None:
        if mode == "strict":
            raise RuntimeError(
                f"Session #{current.session_id} déjà active. "
                f"Utilise mode='replace' pour la remplacer."
            )
        logger.warning(
            f"[lifecycle] mode=replace — fermeture de session #{current.session_id} "
            f"avant ouverture de #{session_id}"
        )
        await close_session(
            current.session_id, current.version,
            close_type="replaced",
            skip_notifications=True,
        )

    # 2. Enregistrement dans le registry
    handle = await session_registry.try_open(session_id, mode="strict")

    # 3. Snapshot immutable
    try:
        snap = await build_snapshot(session_id, handle.version)
    except Exception:
        # Rollback : la session ne peut pas être snapshot → on la retire du registre
        await session_registry.finalize_close(session_id, handle.version)
        raise

    # 4. Bind du state manager
    user_state_v7.bind(session_id, handle.version)

    # 5. Attach du buffer
    if _buffer is not None:
        await _buffer.attach(session_id, handle.version)

    logger.info(
        f"[lifecycle] session #{session_id}v{handle.version} OUVERTE "
        f"(entry={snap.entry_price}, sl={snap.sl})"
    )
    return snap


def mark_broadcast_done(session_id: int, version: int):
    """Passe le status de OPENING à ACTIVE — les confirmations sont ouvertes."""
    session_registry.mark_active(session_id, version)


# ══════════════════════════════════════════════════════════════════════════════
# FERMETURE
# ══════════════════════════════════════════════════════════════════════════════

async def close_session(session_id: int, version: int, *,
                        close_type: str,
                        skip_notifications: bool = False) -> dict:
    """
    Ferme proprement une session.
    close_type : "tp1" | "tp2" | "tp3" | "sl" | "manual" | "replaced"
    """
    handle = session_registry.current()
    if handle is None or handle.session_id != session_id or handle.version != version:
        logger.warning(
            f"[lifecycle] close_session ignoré — session courante ne "
            f"correspond pas à #{session_id}v{version}"
        )
        return {"closed": False, "reason": "session_mismatch"}

    # 1. Plus aucune confirmation acceptée
    session_registry.mark_closing(session_id, version)

    # 2. Drain complet du buffer — attend que TOUTES les écritures
    #    précédentes soient persistées avant de continuer.
    if _buffer is not None:
        try:
            await _buffer.drain_and_stop()
        except Exception as e:
            logger.error(f"[lifecycle] drain buffer échoué: {e}", exc_info=True)

    # 3. Écriture SQL de l'état final : phase de session + agrégats
    try:
        agg = user_state_v7.aggregates()
        phase_map = {
            "tp1": "tp1_reached", "tp2": "tp2_reached", "tp3": "tp3_reached",
            "sl": "sl_touched", "manual": "closed", "replaced": "closed",
        }
        new_phase = phase_map.get(close_type, "closed")
        async with get_db() as cur:
            await cur.execute("""
                UPDATE gold_trade_sessions SET
                    current_phase         = %s,
                    closed_at             = NOW(),
                    total_members_in      = %s,
                    total_lots_engaged    = %s,
                    estimated_loss_sl     = %s,
                    estimated_gain_tp1    = %s,
                    estimated_gain_tp2    = %s,
                    estimated_gain_tp3    = %s,
                    aggregates_updated_at = NOW()
                WHERE id = %s
            """, (
                new_phase,
                agg["total_members"], agg["total_lots"], agg["total_loss_sl"],
                agg["total_gain_tp1"], agg["total_gain_tp2"], agg["total_gain_tp3"],
                session_id,
            ))
    except Exception as e:
        logger.error(f"[lifecycle] écriture finale échouée: {e}", exc_info=True)

    # 4. Purge RAM
    snapshot_store.clear_active()
    user_state_v7.unbind()

    # 5. Retire du registre
    await session_registry.finalize_close(session_id, version)

    logger.info(
        f"[lifecycle] session #{session_id}v{version} FERMÉE proprement "
        f"(close_type={close_type})"
    )
    return {"closed": True, "close_type": close_type, "aggregates": agg}