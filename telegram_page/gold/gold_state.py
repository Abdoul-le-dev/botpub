"""
gold_state.py — Machine d'état utilisateur en mémoire (v6).

Chaque utilisateur a un état RAM : étape courante, capital, entry/SL
effectifs (ajustés au prix live). La DB (gold_user_sessions) ne sert
qu'à la persistance write-behind et à la restauration après redémarrage.

Ce module porte AUSSI l'idempotence :
  - try_begin(uid, action) : verrou en RAM contre les doubles clics et
    les callbacks dupliqués renvoyés par Telegram. Un seul traitement
    par (user, action) à la fois.
  - un utilisateur déjà 'confirmed' ne peut pas relancer une confirmation
    (transition d'état refusée), même après restart du bot.

Mémoire : ~200 octets/user → 30 000 users ≈ 6 Mo. Négligeable.
"""

import logging
import time
from dataclasses import dataclass, field

from db import get_db

logger = logging.getLogger(__name__)

# Transitions autorisées de la machine d'état.
# "retour en arrière" volontairement permis : un user peut re-saisir son
# capital tant qu'il n'a pas confirmé.
_ALLOWED = {
    None:               {"teaser"},
    "teaser":           {"teaser", "waiting_capital", "cancelled"},
    "waiting_capital":  {"waiting_capital", "trade_shown", "cancelled"},
    "trade_shown":      {"waiting_capital", "trade_shown", "confirmed", "cancelled"},
    "cancelled":        {"waiting_capital", "trade_shown", "cancelled"},  # droit de changer d'avis
    "confirmed":        set(),  # état final — plus aucune transition
}


@dataclass(slots=True)
class UserState:
    step: str | None = None
    capital: float | None = None
    effective_entry: float | None = None
    effective_sl: float | None = None
    updated_at: float = field(default_factory=time.time)


class StateManager:

    def __init__(self):
        self.session_id: int | None = None
        self._states: dict[int, UserState] = {}
        # Résultats des confirmations, gardés en RAM pour calculer les
        # agrégats de session SANS relire la DB (voir gold_buffer).
        self.confirmed_entries: dict[int, dict] = {}
        # Verrous anti double-clic : (user_id, action) en cours
        self._inflight: set[tuple[int, str]] = set()

    # ── Cycle de vie ──────────────────────────────────────────────────────

    def reset(self, session_id: int):
        """Nouveau broadcast → nouvel espace d'états."""
        self.session_id = session_id
        self._states.clear()
        self.confirmed_entries.clear()
        self._inflight.clear()

    async def restore(self, session_id: int):
        """
        Après redémarrage du bot : recharge les étapes et les confirmations
        de la session active. 2 requêtes, une fois, au démarrage.
        """
        self.reset(session_id)
        async with get_db() as cur:
            await cur.execute("""
                SELECT user_id, step, capital_input
                FROM gold_user_sessions WHERE session_id = %s
            """, (session_id,))
            for r in await cur.fetchall():
                st = UserState(
                    step=r["step"],
                    capital=float(r["capital_input"]) if r["capital_input"] is not None else None,
                )
                self._states[int(r["user_id"])] = st

            await cur.execute("""
                SELECT user_id, lot_calculated, perte_sl,
                       gain_tp1, gain_tp2, gain_tp3
                FROM gold_member_entries
                WHERE session_id = %s AND step_reached = 'confirmed'
            """, (session_id,))
            for r in await cur.fetchall():
                uid = int(r["user_id"])
                self.confirmed_entries[uid] = {
                    "lot":      float(r["lot_calculated"] or 0),
                    "perte_sl": float(r["perte_sl"] or 0),
                    "gain_tp1": float(r["gain_tp1"] or 0),
                    "gain_tp2": float(r["gain_tp2"]) if r["gain_tp2"] is not None else None,
                    "gain_tp3": float(r["gain_tp3"]) if r["gain_tp3"] is not None else None,
                }
                st = self._states.setdefault(uid, UserState())
                st.step = "confirmed"

        logger.info(
            f"[gold_state] restore session {session_id} — "
            f"{len(self._states)} états, {len(self.confirmed_entries)} confirmés"
        )

    # ── Accès état ────────────────────────────────────────────────────────

    def get(self, user_id: int) -> UserState:
        st = self._states.get(user_id)
        if st is None:
            st = UserState()
            self._states[user_id] = st
        return st

    def transition(self, user_id: int, new_step: str) -> bool:
        """
        Tente une transition. Retourne False si interdite (ex : l'utilisateur
        a déjà confirmé). C'est CE test qui rend le flux idempotent même
        quand Telegram renvoie plusieurs fois le même callback.
        """
        st = self.get(user_id)
        if new_step not in _ALLOWED.get(st.step, set()):
            return False
        st.step = new_step
        st.updated_at = time.time()
        return True

    def is_confirmed(self, user_id: int) -> bool:
        return self.get(user_id).step == "confirmed"

    def mark_confirmed(self, user_id: int, entry: dict):
        st = self.get(user_id)
        st.step = "confirmed"
        st.updated_at = time.time()
        self.confirmed_entries[user_id] = entry

    # ── Agrégats en RAM (remplace le SELECT SUM par confirmation) ────────

    def aggregates(self) -> dict:
        e = self.confirmed_entries.values()
        return {
            "total_members":  len(self.confirmed_entries),
            "total_lots":     round(sum(x["lot"] for x in e), 4),
            "total_loss_sl":  round(sum(abs(x["perte_sl"]) for x in self.confirmed_entries.values()), 2),
            "total_gain_tp1": round(sum(x["gain_tp1"] or 0 for x in self.confirmed_entries.values()), 2),
            "total_gain_tp2": round(sum(x["gain_tp2"] or 0 for x in self.confirmed_entries.values()), 2),
            "total_gain_tp3": round(sum(x["gain_tp3"] or 0 for x in self.confirmed_entries.values()), 2),
        }

    # ── Idempotence / anti double-clic ────────────────────────────────────

    def try_begin(self, user_id: int, action: str) -> bool:
        """
        À appeler en TOUT PREMIER dans chaque handler :

            if not user_state.try_begin(uid, "confirm"):
                await _safe_answer(query, "⏳ Déjà en cours...")
                return
            try:
                ...traitement...
            finally:
                user_state.end(uid, "confirm")

        Deux callbacks identiques qui arrivent à 2 ms d'intervalle →
        le second est ignoré instantanément, zéro DB, zéro double envoi.
        """
        key = (user_id, action)
        if key in self._inflight:
            return False
        self._inflight.add(key)
        return True

    def end(self, user_id: int, action: str):
        self._inflight.discard((user_id, action))


# Instance unique pour tout le process
user_state = StateManager()