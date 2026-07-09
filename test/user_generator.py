"""
tests/user_generator.py — Génère des utilisateurs fictifs pour la simulation.

Chaque utilisateur a un COMPORTEMENT (persona) qui détermine comment il
va réagir au broadcast. Les personas couvrent les cas problématiques
identifiés dans la refonte :

  - fast_clicker    : clique très vite, parfois double-clic
  - slow_reader     : met du temps à répondre
  - indecisive      : change d'avis (cancel puis re-clique)
  - typo_maker      : entre parfois un capital invalide
  - retry_confirm   : clique 2-3 fois sur "confirmer"
  - late_arriver    : clique bien après le broadcast (session peut être fermée)
  - stale_clicker   : clique sur un vieux message d'une session précédente
  - normal          : parcours nominal
  - blocker         : a bloqué le bot
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum


class Persona(str, Enum):
    NORMAL         = "normal"
    FAST_CLICKER   = "fast_clicker"
    SLOW_READER    = "slow_reader"
    INDECISIVE     = "indecisive"
    TYPO_MAKER     = "typo_maker"
    RETRY_CONFIRM  = "retry_confirm"
    LATE_ARRIVER   = "late_arriver"
    STALE_CLICKER  = "stale_clicker"
    BLOCKER        = "blocker"


@dataclass
class FakeUser:
    user_id:  int
    persona:  Persona
    capital:  float
    # Config comportementale
    click_delay_ms:      int = 0          # temps entre teaser et click
    double_click_prob:   float = 0.0      # probabilité de double-clic
    change_mind_prob:    float = 0.0      # probabilité de canceller puis reprendre
    typo_prob:           float = 0.0      # probabilité de taper un capital invalide
    retry_confirm_prob:  float = 0.0
    late_delay_s:        int = 0          # attend N secondes après broadcast
    stale_session_id:    int | None = None  # simule un click sur ce vieux sid
    is_blocked:          bool = False

    # État observé pendant la simulation (rempli par le simulateur)
    outcome: str = "pending"
    result_calc: dict = field(default_factory=dict)


def _capital_realistic() -> float:
    """
    Distribution de capitaux réaliste :
      50% ≤ 500$   (petit compte)
      30% 500-2000$ (moyen)
      15% 2000-10000$ (grand)
      5%  > 10000$   (très grand)
    """
    r = random.random()
    if r < 0.50:
        return round(random.uniform(30, 499), 2)
    if r < 0.80:
        return round(random.uniform(500, 1999), 2)
    if r < 0.95:
        return round(random.uniform(2000, 9999), 2)
    return round(random.uniform(10000, 50000), 2)


def _persona_config(persona: Persona) -> dict:
    if persona == Persona.NORMAL:
        return dict(click_delay_ms=random.randint(500, 5000))
    if persona == Persona.FAST_CLICKER:
        return dict(click_delay_ms=random.randint(50, 300),
                    double_click_prob=0.5)
    if persona == Persona.SLOW_READER:
        return dict(click_delay_ms=random.randint(20000, 120000))
    if persona == Persona.INDECISIVE:
        return dict(click_delay_ms=random.randint(1000, 8000),
                    change_mind_prob=0.7)
    if persona == Persona.TYPO_MAKER:
        return dict(click_delay_ms=random.randint(1000, 6000),
                    typo_prob=0.6)
    if persona == Persona.RETRY_CONFIRM:
        return dict(click_delay_ms=random.randint(500, 3000),
                    retry_confirm_prob=0.8)
    if persona == Persona.LATE_ARRIVER:
        return dict(click_delay_ms=random.randint(1000, 3000),
                    late_delay_s=random.randint(300, 1800))
    if persona == Persona.STALE_CLICKER:
        return dict(click_delay_ms=random.randint(500, 3000))
    if persona == Persona.BLOCKER:
        return dict(is_blocked=True)
    return {}


def generate_users(n: int, *,
                   persona_mix: dict[Persona, float] | None = None,
                   seed: int | None = None,
                   uid_start: int = 100_000_000) -> list[FakeUser]:
    """
    Génère n utilisateurs fictifs avec un mix de personas.

    persona_mix : proportions (doit sommer à 1.0). Défaut = réaliste.
    """
    if seed is not None:
        random.seed(seed)

    if persona_mix is None:
        persona_mix = {
            Persona.NORMAL:        0.55,
            Persona.FAST_CLICKER:  0.10,
            Persona.SLOW_READER:   0.10,
            Persona.INDECISIVE:    0.08,
            Persona.TYPO_MAKER:    0.05,
            Persona.RETRY_CONFIRM: 0.05,
            Persona.LATE_ARRIVER:  0.03,
            Persona.STALE_CLICKER: 0.02,
            Persona.BLOCKER:       0.02,
        }

    # Cumul pour choix pondéré
    personas, cumweights = [], []
    acc = 0.0
    for p, w in persona_mix.items():
        acc += w
        personas.append(p)
        cumweights.append(acc)

    users = []
    for i in range(n):
        r = random.random() * acc
        persona = personas[next(j for j, cw in enumerate(cumweights) if r <= cw)]
        cfg = _persona_config(persona)
        u = FakeUser(
            user_id=uid_start + i,
            persona=persona,
            capital=_capital_realistic(),
            **cfg,
        )
        users.append(u)
    return users


def counts_by_persona(users: list[FakeUser]) -> dict:
    counts = {}
    for u in users:
        counts[u.persona.value] = counts.get(u.persona.value, 0) + 1
    return counts