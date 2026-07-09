"""
session_snapshot.py — Snapshot immutable des données de session (v7).

PROBLÈME RÉSOLU
Avant la v7, plusieurs composants lisaient/écrivaient sur la session :
  - MySQL (gold_trade_sessions)              → source persistante
  - signal_cache.session                     → copie RAM, muable
  - user_state (via effective_entry/sl)      → copie par user, muable
  - context.user_data                        → copie par user, muable

Résultat : un TP calculé côté handler pouvait ne plus correspondre au TP
stocké en base au moment de la confirmation, parce qu'un composant
intermédiaire avait été rafraîchi entre les deux.

SOLUTION
Une fois la session ouverte, ses données de TRADE (entry, sl, tp1/2/3,
direction, sl_pips, tp_rules attachées) sont FIGÉES dans un objet
immutable (frozen dataclass). Tous les calculs — lot, gain, perte —
prennent ce snapshot en paramètre. Aucun handler, aucun worker, aucun
cache ne peut modifier ces valeurs après ouverture.

Seule la PHASE de la session (teaser/open/tp1_reached/closed…) évolue
au cours de la vie. Elle est stockée dans le registry, pas dans le
snapshot.

CONTRAT
  - build_snapshot() est appelée UNE fois par le lifecycle à l'ouverture.
  - get(session_id, version) renvoie le snapshot correspondant, ou None
    si session/version obsolète.
  - clear(session_id, version) supprime le snapshot à la fermeture.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType

from db import get_db

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TpRule:
    """Une règle TP figée au moment de l'ouverture de session."""
    tp_level:                int
    min_capital:             float
    max_capital:             float | None
    risk_pct:                float
    message_tp1_reached:     str | None
    message_tp2_reached:     str | None
    message_tp3_reached:     str | None
    message_sl_touched:      str | None
    message_teaser:          str | None
    message_confirmation:    str | None


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """
    Données de trade figées pour toute la durée de vie de la session.
    frozen=True → toute tentative de modification lève FrozenInstanceError.
    """
    session_id:   int
    version:      int          # doit correspondre au registry
    season_id:   int | None

    direction:    str          # "buy" | "sell"
    entry_price:  float
    sl:           float
    tp1:          float | None
    tp2:          float | None
    tp3:          float | None
    sl_pips:      float
    tp1_pips:     float | None
    tp2_pips:     float | None
    tp3_pips:     float | None

    confidence_level: int
    note:             str | None
    screenshot_url:   str | None
    timeframe:        str

    # Règles TP figées à l'ouverture — un dict par tp_level. On stocke
    # sous forme de MappingProxyType (dict en lecture seule).
    tp_rules: MappingProxyType   # {1: TpRule, 2: TpRule, 3: TpRule}

    # ── Helpers déterministes ────────────────────────────────────────────
    # Ces méthodes n'ont AUCUN accès externe : mêmes entrées, même sortie,
    # toujours. Pas de get_db, pas de cache, pas de signal_cache.

    def tp_level_for_capital(self, capital: float) -> tuple[int, float]:
        """Détermine niveau TP + risque % pour un capital donné, uniquement
        à partir des règles figées dans le snapshot."""
        best: TpRule | None = None
        for rule in self.tp_rules.values():
            if rule.min_capital <= capital and (
                rule.max_capital is None or capital <= rule.max_capital
            ):
                if best is None or rule.tp_level < best.tp_level:
                    best = rule
        if best:
            return best.tp_level, best.risk_pct
        # Fallback conservateur — mais ne devrait jamais arriver si les
        # règles couvrent bien [0, +∞).
        if capital < 500:   return 1, 1.0
        if capital < 2000:  return 2, 1.5
        return 3, 2.0

    def rule_for(self, tp_level: int) -> TpRule | None:
        return self.tp_rules.get(int(tp_level))


# ══════════════════════════════════════════════════════════════════════════════
# Registre process-wide des snapshots
# ══════════════════════════════════════════════════════════════════════════════

class _SnapshotStore:
    """
    Un seul snapshot actif à la fois. On garde aussi les snapshots récents
    fermés (LRU court) pour permettre à des callbacks très en retard de
    valider proprement leur validité (et être rejetés avec un message
    clair) sans crasher.
    """

    def __init__(self):
        self._active: SessionSnapshot | None = None
        # historique récent (session_id, version) -> snapshot
        self._recent: dict[tuple[int, int], SessionSnapshot] = {}
        self._max_recent = 8

    def set_active(self, snap: SessionSnapshot):
        # Rétrograde l'ancien snapshot actif dans l'historique
        if self._active is not None:
            self._recent[(self._active.session_id, self._active.version)] = self._active
            self._trim()
        self._active = snap
        logger.info(
            f"[snapshot] session #{snap.session_id}v{snap.version} figée "
            f"(entry={snap.entry_price}, sl={snap.sl}, "
            f"tp1={snap.tp1}, tp2={snap.tp2}, tp3={snap.tp3})"
        )

    def get_active(self) -> SessionSnapshot | None:
        return self._active

    def get(self, session_id: int, version: int) -> SessionSnapshot | None:
        a = self._active
        if a and a.session_id == session_id and a.version == version:
            return a
        return self._recent.get((session_id, version))

    def clear_active(self):
        if self._active is not None:
            self._recent[(self._active.session_id, self._active.version)] = self._active
            self._trim()
            logger.info(f"[snapshot] active clear (session #{self._active.session_id})")
            self._active = None

    def _trim(self):
        while len(self._recent) > self._max_recent:
            # supprime le plus ancien inséré (dicts py3.7+ conservent l'ordre d'insertion)
            key = next(iter(self._recent))
            self._recent.pop(key, None)


snapshot_store = _SnapshotStore()


# ══════════════════════════════════════════════════════════════════════════════
# Construction du snapshot
# ══════════════════════════════════════════════════════════════════════════════

async def build_snapshot(session_id: int, version: int) -> SessionSnapshot:
    """
    Charge la session + toutes les règles TP actives depuis MySQL et
    construit un SessionSnapshot immutable.

    À appeler UNE fois par le lifecycle, à l'ouverture. Après cet appel,
    les données de trade ne peuvent plus changer, même si quelqu'un
    modifie la table gold_tp_rules pendant que la session est ouverte
    (garantit que tous les users du même broadcast voient les mêmes
    seuils et messages).
    """
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"Session #{session_id} introuvable en base")
        s = dict(row)

        await cur.execute(
            "SELECT * FROM gold_tp_rules WHERE is_active = 1"
        )
        rules_rows = [dict(r) for r in await cur.fetchall()]

    rules_by_level: dict[int, TpRule] = {}
    for r in rules_rows:
        lvl = int(r["tp_level"])
        # Une seule règle par tp_level pour l'instant (celle qui matche
        # via min/max_capital). On garde toutes celles vues et le
        # tp_level_for_capital du snapshot choisira la bonne.
        # → on indexe par un id unique pour éviter les collisions.
        pass

    # Indexation propre : on garde TOUTES les règles, keyées par leur
    # (tp_level, min_capital) pour permettre la sélection par capital.
    all_rules: dict = {}
    for i, r in enumerate(rules_rows):
        all_rules[i] = TpRule(
            tp_level=int(r["tp_level"]),
            min_capital=float(r["min_capital"]),
            max_capital=float(r["max_capital"]) if r["max_capital"] is not None else None,
            risk_pct=float(r["risk_pct"]),
            message_tp1_reached=r.get("message_tp1_reached"),
            message_tp2_reached=r.get("message_tp2_reached"),
            message_tp3_reached=r.get("message_tp3_reached"),
            message_sl_touched=r.get("message_sl_touched"),
            message_teaser=r.get("message_teaser"),
            message_confirmation=r.get("message_confirmation"),
        )

    snap = SessionSnapshot(
        session_id=int(s["id"]),
        version=version,
        season_id=int(s["season_id"]) if s.get("season_id") is not None else None,
        direction=s["direction"],
        entry_price=float(s["entry_price"]),
        sl=float(s["sl"]),
        tp1=float(s["tp1"]) if s.get("tp1") is not None else None,
        tp2=float(s["tp2"]) if s.get("tp2") is not None else None,
        tp3=float(s["tp3"]) if s.get("tp3") is not None else None,
        sl_pips=float(s["sl_pips"]),
        tp1_pips=float(s["tp1_pips"]) if s.get("tp1_pips") is not None else None,
        tp2_pips=float(s["tp2_pips"]) if s.get("tp2_pips") is not None else None,
        tp3_pips=float(s["tp3_pips"]) if s.get("tp3_pips") is not None else None,
        confidence_level=int(s.get("confidence_level") or 3),
        note=s.get("note"),
        screenshot_url=s.get("screenshot_url"),
        timeframe=s.get("timeframe") or "M15",
        tp_rules=MappingProxyType(all_rules),
    )
    snapshot_store.set_active(snap)
    return snap