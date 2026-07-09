"""
tests/assertions.py — Vérifications automatiques après simulation v7.1.

Vérifie que la simulation n'a produit AUCUNE incohérence :

  - aucun utilisateur ne reçoit un mauvais trade
  - aucun utilisateur ne reçoit un ancien SL / TP
  - chaque lot correspond bien au trade actif (snapshot)
  - chaque calcul utilise bien le capital du cache (comparaison directe)
  - aucun utilisateur "processed" ne l'est plusieurs fois
  - aucune session mélangée
  - aucun calcul non-déterministe
"""

from __future__ import annotations

from dataclasses import dataclass, field

from  telegram_page.gold.session_registry import session_registry
from telegram_page.gold.session_snapshot import snapshot_store
from telegram_page.gold.gold_state import user_state_v7
from telegram_page.gold.gold_broadcast import build_calc_context, adjust_entry_sl
from telegram_page.gold.weekly_capital_cache import weekly_capital
from tests.user_generator import FakeUser


@dataclass
class AssertionReport:
    ok:        bool = True
    n_checked: int = 0
    failures:  list[str] = field(default_factory=list)

    def fail(self, msg: str):
        self.failures.append(msg)
        self.ok = False

    def summary(self) -> str:
        if self.ok:
            return f"✅ Toutes les assertions OK ({self.n_checked} users vérifiés)"
        lines = [f"❌ {len(self.failures)} assertion(s) échouée(s) "
                 f"sur {self.n_checked} vérifiées"]
        for f in self.failures[:20]:
            lines.append(f"  🔴 {f}")
        if len(self.failures) > 20:
            lines.append(f"  ... et {len(self.failures) - 20} autres")
        return "\n".join(lines)


def check_users_vs_ram(users: list[FakeUser]) -> AssertionReport:
    """
    Pour chaque user 'processed' :
      - présent dans le RAM du state manager
      - CalcContext utilise bien le capital du cache
      - CalcContext utilise bien entry/sl/tp du snapshot actif
      - recalcul déterministe → mêmes valeurs
    Pour chaque user 'blocked'/'stale_rejected' :
      - absent du RAM
    """
    rep = AssertionReport()
    snap = snapshot_store.get_active()
    reg = session_registry.current()
    if reg is None or snap is None:
        rep.fail("Aucune session active en fin de simulation")
        return rep

    processed_ids = user_state_v7.confirmed_ids()
    calcs = user_state_v7.confirmed_calcs()

    for u in users:
        rep.n_checked += 1

        if u.outcome in ("processed_from_cache", "processed_after_input"):
            if u.user_id not in processed_ids:
                rep.fail(f"uid={u.user_id}: outcome={u.outcome} mais absent RAM")
                continue
            calc = calcs[u.user_id]

            # Session correcte
            if calc.session_id != reg.session_id or calc.version != reg.version:
                rep.fail(f"uid={u.user_id}: calc sur #{calc.session_id}v"
                         f"{calc.version} vs courante #{reg.session_id}v{reg.version}")
                continue

            # Le capital utilisé doit provenir du cache (ou avoir été mis
            # en cache par la saisie utilisateur pendant le flow)
            cap_ram = weekly_capital.get_ram(u.user_id)
            if cap_ram is None:
                rep.fail(f"uid={u.user_id}: processed mais capital absent du cache")
            elif abs(cap_ram - calc.capital) > 0.01:
                rep.fail(f"uid={u.user_id}: capital calc={calc.capital} "
                         f"vs cache RAM={cap_ram}")

            # Params techniques doivent venir du snapshot actif
            eff_entry, eff_sl, _ = adjust_entry_sl(snap, None)
            if abs(calc.effective_entry - eff_entry) > 0.01:
                rep.fail(f"uid={u.user_id}: entry calc={calc.effective_entry} "
                         f"vs snapshot={eff_entry}")
            if abs(calc.effective_sl - eff_sl) > 0.01:
                rep.fail(f"uid={u.user_id}: sl calc={calc.effective_sl} "
                         f"vs snapshot={eff_sl}")

            # Recalcul déterministe
            recalc = build_calc_context(snap, u.user_id, calc.capital,
                                        eff_entry, eff_sl)
            if abs(recalc.lot - calc.lot) > 0.001:
                rep.fail(f"uid={u.user_id}: lot RAM={calc.lot} recalc={recalc.lot}")
            if abs(recalc.perte_sl - calc.perte_sl) > 0.01:
                rep.fail(f"uid={u.user_id}: perte_sl RAM={calc.perte_sl} "
                         f"recalc={recalc.perte_sl}")
            if recalc.tp_level != calc.tp_level:
                rep.fail(f"uid={u.user_id}: tp_level RAM={calc.tp_level} "
                         f"recalc={recalc.tp_level}")
            if calc.lot <= 0:
                rep.fail(f"uid={u.user_id}: lot ≤ 0 ({calc.lot})")

        elif u.outcome in ("blocked", "stale_rejected", "session_closed",
                            "invalid_capital_typo", "no_processing"):
            if u.user_id in processed_ids:
                rep.fail(f"uid={u.user_id}: outcome={u.outcome} mais processed en RAM")

    return rep


def check_no_cross_session_leak(users: list[FakeUser]) -> AssertionReport:
    rep = AssertionReport()
    reg = session_registry.current()
    for uid, calc in user_state_v7.confirmed_calcs().items():
        rep.n_checked += 1
        if reg is None or calc.session_id != reg.session_id or calc.version != reg.version:
            rep.fail(f"uid={uid}: calc leak — session={calc.session_id}v"
                     f"{calc.version} vs courante="
                     f"{reg and (reg.session_id, reg.version)}")
    return rep


def check_deterministic_calc(snap, users: list[FakeUser],
                              n_repeats: int = 3) -> AssertionReport:
    rep = AssertionReport()
    sample = [u for u in users
              if u.outcome in ("processed_from_cache", "processed_after_input")][:100]
    for u in sample:
        rep.n_checked += 1
        eff_entry, eff_sl, _ = adjust_entry_sl(snap, None)
        first = build_calc_context(snap, u.user_id, u.capital, eff_entry, eff_sl)
        for _ in range(n_repeats):
            again = build_calc_context(snap, u.user_id, u.capital, eff_entry, eff_sl)
            if (again.lot != first.lot or
                again.perte_sl != first.perte_sl or
                again.gain_tp1 != first.gain_tp1 or
                again.tp_level != first.tp_level):
                rep.fail(f"uid={u.user_id}: calcul non-déterministe")
                break
    return rep


def check_cross_trade_isolation(snap_A, snap_B,
                                 sample_users: list[FakeUser]) -> AssertionReport:
    """
    Vérifie qu'un calcul fait sur snap_A ne partage AUCUN paramètre
    technique avec snap_B. Le capital peut être identique (c'est le
    même user) mais entry/sl/tp/lot/perte doivent différer.
    """
    rep = AssertionReport()
    for u in sample_users[:20]:
        rep.n_checked += 1
        eA_entry, eA_sl, _ = adjust_entry_sl(snap_A, None)
        eB_entry, eB_sl, _ = adjust_entry_sl(snap_B, None)
        cA = build_calc_context(snap_A, u.user_id, u.capital, eA_entry, eA_sl)
        cB = build_calc_context(snap_B, u.user_id, u.capital, eB_entry, eB_sl)
        # Params techniques du calc doivent correspondre au snapshot correspondant
        if cA.effective_entry == cB.effective_entry and snap_A.entry_price != snap_B.entry_price:
            rep.fail(f"uid={u.user_id}: entry identique entre 2 trades différents")
        if cA.effective_sl == cB.effective_sl and snap_A.sl != snap_B.sl:
            rep.fail(f"uid={u.user_id}: sl identique entre 2 trades différents")
        # Le capital utilisé doit être identique (même user)
        if cA.capital != cB.capital:
            rep.fail(f"uid={u.user_id}: capital divergent {cA.capital} vs {cB.capital}")
    return rep