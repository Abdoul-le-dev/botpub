"""
state_v7.py — Machine d'état utilisateur isolée par session (v7).

DIFFÉRENCES AVEC v6 (gold_state.py)
  1. Toutes les clés sont (session_id, user_id) — jamais user_id seul.
     Un vieux click sur une session terminée ne peut PAS lire l'état d'un
     user pour une nouvelle session.
  2. Chaque UserState porte session_id + version. Toute action passe par
     validate() qui vérifie que la version correspond à la session courante.
     Sinon, l'action est rejetée silencieusement.
  3. reset(new_session_id) supprime TOUT l'état de l'ancienne session
     (states, confirmed_entries, inflight, calc_context) avant d'accepter
     de nouvelles données.
  4. Le calcul (lot, gain, perte, tp_level) est capturé au moment du
     "trade_shown" et stocké dans un CalcContext figé pour cet user.
     Au moment du "confirmed", on relit ce contexte figé au lieu de
     recalculer — impossible qu'un TP soit calculé avec des données
     d'une autre session.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Machine d'état v7.1 : simplifiée. L'étape "confirmer" est supprimée.
# Ouvrir le trade = le prendre. Un état "processed" final regroupe ce
# qui s'appelait "trade_shown + confirmed" en v7.0.
#
# Deux chemins :
#   None → teaser → processed                              (capital en cache)
#   None → teaser → waiting_capital → processed            (nouveau user / expiré)
#
# "cancelled" reste un état terminal pour les users qui ferment le trade
# via /skip ou équivalent — mais n'est plus atteignable par un bouton
# systématique dans le nouveau workflow.
_ALLOWED = {
    None:              {"teaser", "waiting_capital", "processed", "cancelled"},
    "teaser":          {"teaser", "waiting_capital", "processed", "cancelled"},
    "waiting_capital": {"waiting_capital", "processed", "cancelled"},
    "processed":       set(),   # état terminal — plus aucune transition
    "cancelled":       {"waiting_capital", "processed", "cancelled"},

    # Rétro-compatibilité : les états v7.0 restent tolérés pour ne pas
    # casser une session en cours au moment du déploiement.
    "trade_shown":     {"processed", "cancelled"},
    "confirmed":       set(),
}


@dataclass(slots=True)
class CalcContext:
    """
    Capture immutable après création (on n'y touche plus).
    Contient TOUT ce qui a été calculé pour montrer le détail du trade
    à un user. La confirmation relit ces mêmes valeurs — jamais de
    recalcul avec d'autres inputs.
    """
    session_id:       int
    version:          int
    effective_entry:  float
    effective_sl:     float
    capital:          float
    lot:              float
    tp_level:         int
    risk_pct:         float
    risk_usd:         float
    perte_sl:         float
    gain_tp1:         float | None
    gain_tp2:         float | None
    gain_tp3:         float | None
    computed_at:      float = field(default_factory=time.time)


@dataclass(slots=True)
class UserState:
    session_id: int
    version:    int
    step:       str | None = None
    updated_at: float = field(default_factory=time.time)
    calc:       CalcContext | None = None


class StateManagerV7:
    """
    Un StateManager n'existe QUE pour la session courante.
    Toute donnée d'une ancienne session est purgée à reset().
    """

    def __init__(self):
        self.session_id: int | None = None
        self.version:    int | None = None
        self._states: dict[int, UserState] = {}
        self._confirmed: dict[int, CalcContext] = {}
        self._inflight: set[tuple[int, str]] = set()

    # ── Cycle de vie ──────────────────────────────────────────────────────

    def bind(self, session_id: int, version: int):
        """
        Attache le state manager à une nouvelle session. Écrase toutes
        les données précédentes. Après cet appel, seule cette session est
        connue par le state manager.
        """
        self.session_id = session_id
        self.version    = version
        self._states.clear()
        self._confirmed.clear()
        self._inflight.clear()
        logger.info(f"[state_v7] bind session #{session_id}v{version} — RAM purgée")

    def unbind(self):
        """Détache toute session. Le bot n'accepte plus aucune action user."""
        old = (self.session_id, self.version)
        self.session_id = None
        self.version    = None
        self._states.clear()
        self._confirmed.clear()
        self._inflight.clear()
        logger.info(f"[state_v7] unbind {old} — RAM purgée")

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self, session_id: int, version: int) -> bool:
        """
        Toute action publique doit passer par ce test AVANT toute lecture
        ou écriture sur l'état. Renvoie False si la session/version ne
        correspond pas à celle actuellement bindée.
        """
        return (self.session_id is not None
                and self.session_id == session_id
                and self.version == version)

    # ── Accès état ────────────────────────────────────────────────────────

    def _get_or_create(self, user_id: int) -> UserState | None:
        if self.session_id is None or self.version is None:
            return None
        st = self._states.get(user_id)
        if st is None:
            st = UserState(session_id=self.session_id, version=self.version)
            self._states[user_id] = st
        return st

    def get(self, session_id: int, version: int, user_id: int) -> UserState | None:
        if not self.validate(session_id, version):
            return None
        return self._states.get(user_id)

    def step(self, session_id: int, version: int, user_id: int) -> str | None:
        st = self.get(session_id, version, user_id)
        return st.step if st else None

    def transition(self, session_id: int, version: int, user_id: int,
                   new_step: str) -> bool:
        if not self.validate(session_id, version):
            return False
        st = self._get_or_create(user_id)
        if st is None:
            return False
        if new_step not in _ALLOWED.get(st.step, set()):
            return False
        st.step = new_step
        st.updated_at = time.time()
        return True

    def is_processed(self, session_id: int, version: int, user_id: int) -> bool:
        """État terminal v7.1 : le trade a été calculé + affiché + enregistré."""
        st = self.get(session_id, version, user_id)
        return st is not None and st.step in ("processed", "confirmed")

    # Alias rétro-compat v7.0
    is_confirmed = is_processed

    # ── Contexte de calcul (figé au trade_shown) ─────────────────────────

    def set_calc(self, session_id: int, version: int, user_id: int,
                 calc: CalcContext) -> bool:
        if not self.validate(session_id, version):
            return False
        if calc.session_id != session_id or calc.version != version:
            logger.warning(
                f"[state_v7] set_calc REJETÉ pour uid={user_id} — "
                f"calc pour #{calc.session_id}v{calc.version} mais "
                f"session courante #{session_id}v{version}"
            )
            return False
        st = self._get_or_create(user_id)
        if st is None:
            return False
        st.calc = calc
        return True

    def get_calc(self, session_id: int, version: int, user_id: int) -> CalcContext | None:
        st = self.get(session_id, version, user_id)
        return st.calc if st else None

    def mark_processed(self, session_id: int, version: int, user_id: int,
                        calc: CalcContext) -> bool:
        """
        État terminal v7.1 : calcul figé + trade affiché + persisté.
        Remplace l'ancienne combinaison trade_shown + confirmed.
        """
        if not self.validate(session_id, version):
            return False
        if calc.session_id != session_id or calc.version != version:
            return False
        st = self._get_or_create(user_id)
        if st is None:
            return False
        st.step = "processed"
        st.updated_at = time.time()
        st.calc = calc
        self._confirmed[user_id] = calc   # même dict d'agrégats — pas de rupture
        return True

    # Alias rétro-compat v7.0
    mark_confirmed = mark_processed

    # ── Idempotence anti double-clic ──────────────────────────────────────

    def try_begin(self, session_id: int, version: int, user_id: int,
                  action: str) -> bool:
        if not self.validate(session_id, version):
            return False
        key = (user_id, action)
        if key in self._inflight:
            return False
        self._inflight.add(key)
        return True

    def end(self, user_id: int, action: str):
        self._inflight.discard((user_id, action))

    # ── Agrégats depuis les CalcContext confirmés ────────────────────────

    def aggregates(self) -> dict:
        entries = list(self._confirmed.values())
        return {
            "total_members":  len(entries),
            "total_lots":     round(sum(c.lot for c in entries), 4),
            "total_loss_sl":  round(sum(abs(c.perte_sl) for c in entries), 2),
            "total_gain_tp1": round(sum(c.gain_tp1 or 0 for c in entries), 2),
            "total_gain_tp2": round(sum(c.gain_tp2 or 0 for c in entries), 2),
            "total_gain_tp3": round(sum(c.gain_tp3 or 0 for c in entries), 2),
        }

    def dump(self) -> dict:
        """Introspection pour le module consistency et pour les tests."""
        return {
            "session_id":     self.session_id,
            "version":        self.version,
            "n_states":       len(self._states),
            "n_confirmed":    len(self._confirmed),
            "n_inflight":     len(self._inflight),
            "step_counts":    self._count_by_step(),
        }

    def _count_by_step(self) -> dict:
        counts = {}
        for st in self._states.values():
            counts[st.step] = counts.get(st.step, 0) + 1
        return counts

    def confirmed_ids(self) -> set[int]:
        return set(self._confirmed.keys())

    def confirmed_calcs(self) -> dict[int, CalcContext]:
        # copie superficielle pour ne pas exposer la structure interne
        return dict(self._confirmed)


user_state_v7 = StateManagerV7()