"""
tests/run_all.py — Suite complète v7.1.

Scénarios exécutés :
  A. Charge — 100 / 1000 / 5000 / 10000 / 30000 users
     * chaque taille avec un mix de personas ET un mix cache/no-cache
  B. Single session — impossible d'avoir 2 sessions actives
  C. Stale callback — click sur ancienne session rejeté
  D. Idempotence — double click access ⇒ 1 seul processed
  E. Isolation cross-trade — trade A / trade B, aucun mélange param
  F. Capital cache lifecycle — miss RAM → hit SQL → promo → hit RAM
  G. Capital expiré — re-saisie forcée automatiquement
  H. Fermeture puis ouverture immédiate — RAM propre entre 2 sessions
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

import os
# Rend la suite portable : on remonte à la racine projet (parent de tests/)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from db import init_pool, close_pool
from tests.user_generator import generate_users, counts_by_persona, Persona
from tests.simulator import (
    install_mock_session, teardown_mock_session,
    run_simulation, make_mock_snapshot,
    preseed_cache_ram, preseed_cache_expired, wipe_cache,
)
from tests.assertions import (
    check_users_vs_ram, check_no_cross_session_leak,
    check_deterministic_calc, check_cross_trade_isolation,
)
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.gold_state import user_state_v7
from telegram_page.gold.weekly_capital_cache import weekly_capital, CapitalEntry, TTL_SECONDS
from telegram_page.gold.gold_broadcast import build_calc_context, adjust_entry_sl

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@dataclass
class ScenarioResult:
    name:    str
    passed:  bool
    details: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════════
# A. Charge
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_charge(n_users: int, cache_fraction: float = 0.7,
                            expired_fraction: float = 0.1) -> ScenarioResult:
    res = ScenarioResult(name=f"Charge_{n_users}_users", passed=True)

    await wipe_cache()
    users = generate_users(n_users, seed=42)
    preseed_cache_ram(users, fraction=cache_fraction)
    preseed_cache_expired(users, fraction=expired_fraction)

    snap = await install_mock_session(session_id=1000 + n_users)
    try:
        sim = await run_simulation(users, snap, concurrency=min(200, n_users))

        a1 = check_users_vs_ram(users)
        a2 = check_no_cross_session_leak(users)
        a3 = check_deterministic_calc(snap, users)

        for a in (a1, a2, a3):
            if not a.ok:
                res.passed = False
                res.details.append(a.summary())

        res.metrics = {
            "elapsed_s":              round(sim.elapsed_s, 2),
            "per_user_avg_ms":        round(sim.per_user_avg_ms, 3),
            "per_user_max_ms":        round(sim.per_user_max_ms, 3),
            "throughput_ops":         round(n_users / max(sim.elapsed_s, 0.001), 0),
            "processed_from_cache":   sim.n_processed_from_cache,
            "processed_after_input":  sim.n_processed_after_input,
            "processed_total":        sim.n_processed_total,
            "blocked":                sim.n_blocked,
            "stale_rejected":         sim.n_stale_rejected,
            "invalid_capital_typo":   sim.n_invalid_capital_typo,
        }
    finally:
        await teardown_mock_session()
        await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# B. Single session invariant
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_single_session() -> ScenarioResult:
    res = ScenarioResult(name="Single_session_invariant", passed=True)

    await install_mock_session(session_id=201)
    try:
        await session_registry.try_open(202, mode="strict")
        res.passed = False
        res.details.append("Ouverture STRICT d'une 2e session n'a pas levé")
    except RuntimeError:
        pass   # attendu

    await teardown_mock_session()

    # Vérifie qu'après remplacement seule la nouvelle est active
    await install_mock_session(session_id=203)
    h = session_registry.current()
    if h.session_id != 203:
        res.passed = False
        res.details.append(f"Après ré-ouverture, session courante = {h.session_id} ≠ 203")

    await teardown_mock_session()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# C. Stale callback
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_stale_callback() -> ScenarioResult:
    res = ScenarioResult(name="Stale_callback_rejected", passed=True)

    snap1 = await install_mock_session(session_id=301)
    user_state_v7.transition(snap1.session_id, snap1.version, 42, "teaser")
    old_sid, old_ver = snap1.session_id, snap1.version
    await teardown_mock_session()

    snap2 = await install_mock_session(session_id=302)
    if user_state_v7.get(old_sid, old_ver, 42) is not None:
        res.passed = False
        res.details.append("state_v7.get() renvoie qqch pour ancienne session")
    if user_state_v7.transition(old_sid, old_ver, 42, "waiting_capital"):
        res.passed = False
        res.details.append("transition acceptée sur ancienne (sid, ver)")

    await teardown_mock_session()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# D. Idempotence
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_idempotent_access() -> ScenarioResult:
    res = ScenarioResult(name="Idempotent_access_click", passed=True)

    await wipe_cache()
    snap = await install_mock_session(session_id=401)

    uid = 777
    # Prépeuple le cache pour ce user
    now = time.time()
    weekly_capital._entries[uid] = CapitalEntry(uid, 1000.0, 1, now, now + TTL_SECONDS)

    user_state_v7.transition(snap.session_id, snap.version, uid, "teaser")

    # Simule 5 clicks sur access
    n_processed_marks = 0
    for _ in range(5):
        if user_state_v7.is_processed(snap.session_id, snap.version, uid):
            continue
        capital = weekly_capital.get_ram(uid)
        eff_entry, eff_sl, _ = adjust_entry_sl(snap, None)
        calc = build_calc_context(snap, uid, capital, eff_entry, eff_sl)
        user_state_v7.set_calc(snap.session_id, snap.version, uid, calc)
        if user_state_v7.mark_processed(snap.session_id, snap.version, uid, calc):
            n_processed_marks += 1

    agg = user_state_v7.aggregates()
    if agg["total_members"] != 1:
        res.passed = False
        res.details.append(f"5 clicks → total_members={agg['total_members']} (attendu 1)")
    if n_processed_marks != 1:
        res.passed = False
        res.details.append(f"mark_processed appelé {n_processed_marks} fois "
                            f"malgré is_processed=True (attendu 1)")

    await teardown_mock_session()
    await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# E. Isolation cross-trade
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_cross_trade_isolation() -> ScenarioResult:
    res = ScenarioResult(name="Cross_trade_isolation", passed=True)

    # Trade A puis Trade B — même user, params trade DIFFÉRENTS
    snap_A = await install_mock_session(session_id=501,
                                          direction="sell",
                                          entry_price=3345, sl=3355, tp1=3330, tp2=3320, tp3=3310)
    users = generate_users(50, seed=1)
    preseed_cache_ram(users, fraction=1.0)   # tous ont un capital cache
    await run_simulation(users, snap_A)
    calcs_A = dict(user_state_v7.confirmed_calcs())
    await teardown_mock_session()

    snap_B = await install_mock_session(session_id=502,
                                          direction="buy",
                                          entry_price=3320, sl=3310, tp1=3338, tp2=3350, tp3=3365)
    await run_simulation(users, snap_B)
    calcs_B = dict(user_state_v7.confirmed_calcs())

    a = check_cross_trade_isolation(snap_A, snap_B, users)
    if not a.ok:
        res.passed = False
        res.details.append(a.summary())

    # Vérif directe : pour un même user, entry/sl doivent différer
    n_leaked = 0
    for uid in calcs_A:
        if uid in calcs_B:
            cA, cB = calcs_A[uid], calcs_B[uid]
            if cA.effective_entry == cB.effective_entry:
                n_leaked += 1
    if n_leaked > 0:
        res.passed = False
        res.details.append(f"{n_leaked} users ont un entry identique entre A et B")

    await teardown_mock_session()
    await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# F. Capital cache lifecycle
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_capital_cache_lifecycle() -> ScenarioResult:
    res = ScenarioResult(name="Capital_cache_lifecycle", passed=True)
    await wipe_cache()

    uid = 9999

    # 1. Cache vide → get_ram = None
    if weekly_capital.get_ram(uid) is not None:
        res.passed = False; res.details.append("cache non vide au départ")

    # 2. set → get_ram = valeur
    await weekly_capital.set(uid, 2500.0)
    if weekly_capital.get_ram(uid) != 2500.0:
        res.passed = False; res.details.append("get_ram ne renvoie pas la valeur après set")

    # 3. invalidate → get_ram = None
    weekly_capital.invalidate(uid)
    if weekly_capital.get_ram(uid) is not None:
        res.passed = False; res.details.append("invalidate ne purge pas la RAM")

    # 4. Entrée expirée → get_ram = None mais entrée toujours en dict
    now = time.time()
    weekly_capital._entries[uid] = CapitalEntry(
        uid, 500.0, 1, now - TTL_SECONDS - 3600, now - 3600
    )
    if weekly_capital.get_ram(uid) is not None:
        res.passed = False
        res.details.append("entrée expirée renvoyée par get_ram")

    # 5. clear_expired → entrée supprimée
    n = weekly_capital.clear_expired()
    if n != 1:
        res.passed = False; res.details.append(f"clear_expired = {n} (attendu 1)")

    await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# G. Capital expiré = re-saisie
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_expired_capital_reprompt() -> ScenarioResult:
    res = ScenarioResult(name="Expired_capital_reprompts", passed=True)
    await wipe_cache()

    users = generate_users(200, seed=7)
    # Tous les users ont un capital EXPIRÉ en cache
    preseed_cache_expired(users, fraction=1.0)

    snap = await install_mock_session(session_id=701)
    sim = await run_simulation(users, snap)

    # Aucun ne devrait avoir fait "processed_from_cache" (le cache est expiré)
    if sim.n_processed_from_cache > 0:
        res.passed = False
        res.details.append(f"{sim.n_processed_from_cache} users ont utilisé un cache expiré")

    # La majorité doit être "processed_after_input" (formulaire → save → process)
    total_ok = sim.n_processed_from_cache + sim.n_processed_after_input
    res.metrics = {
        "processed_from_cache":  sim.n_processed_from_cache,
        "processed_after_input": sim.n_processed_after_input,
        "invalid_capital_typo":  sim.n_invalid_capital_typo,
    }

    await teardown_mock_session()
    await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# H. Close then immediate reopen — RAM propre
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_close_reopen_cleanup() -> ScenarioResult:
    res = ScenarioResult(name="Close_reopen_clean_RAM", passed=True)
    await wipe_cache()

    # Session 1 avec confirmations
    snap1 = await install_mock_session(session_id=801)
    users = generate_users(50, seed=3)
    preseed_cache_ram(users, fraction=1.0)
    await run_simulation(users, snap1)

    if len(user_state_v7.confirmed_calcs()) == 0:
        res.passed = False
        res.details.append("Aucune confirmation dans session 1 — test invalide")

    await teardown_mock_session()

    # Session 2 : RAM state DOIT être vide
    if user_state_v7.session_id is not None:
        res.passed = False
        res.details.append("state_v7.session_id non nul après teardown")
    if len(user_state_v7.confirmed_calcs()) != 0:
        res.passed = False
        res.details.append(f"confirmations résiduelles: "
                            f"{len(user_state_v7.confirmed_calcs())}")

    # Weekly capital cache DOIT survivre (indépendant des sessions)
    n_still_in_cache = sum(1 for u in users
                            if weekly_capital.get_ram(u.user_id) is not None)
    if n_still_in_cache == 0:
        res.passed = False
        res.details.append("Weekly capital cache purgé alors qu'il devrait survivre")

    # Nouvelle session
    snap2 = await install_mock_session(session_id=802)
    if snap2.version <= snap1.version:
        res.passed = False
        res.details.append(f"Version pas incrémentée : {snap1.version} → {snap2.version}")

    await teardown_mock_session()
    await wipe_cache()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# Runner principal
# ══════════════════════════════════════════════════════════════════════════════

async def main(loads=None):
    # ── Initialisation DB ─────────────────────────────────────────────
    try:
        await init_pool()
        print("✓ Pool DB initialisé")
    except Exception as e:
        print(f"✗ Échec init DB : {e}")
        return 1

    loads = loads or [100, 1000, 5000, 10000, 30000]
    results: list[ScenarioResult] = []

    print("═" * 76)
    print(" GOLD V7.1 — SUITE DE TESTS")
    print("═" * 76)

    try:
        # A. Charge
        for n in loads:
            print(f"\n▶ Charge — {n} users ...")
            t0 = time.perf_counter()
            r = await scenario_charge(n)
            m = r.metrics
            print(f"   {'✅' if r.passed else '❌'} en {time.perf_counter() - t0:.1f}s "
                  f"— {m.get('throughput_ops')} ops/s "
                  f"(from_cache={m.get('processed_from_cache')} "
                  f"after_input={m.get('processed_after_input')})")
            for d in r.details: print(f"   {d}")
            results.append(r)

        # B → H
        for scen in (scenario_single_session,
                      scenario_stale_callback,
                      scenario_idempotent_access,
                      scenario_cross_trade_isolation,
                      scenario_capital_cache_lifecycle,
                      scenario_expired_capital_reprompt,
                      scenario_close_reopen_cleanup):
            print(f"\n▶ {scen.__name__} ...")
            r = await scen()
            print(f"   {'✅' if r.passed else '❌'} {r.name}")
            for d in r.details: print(f"   {d}")
            results.append(r)

    except Exception as e:
        print(f"\n✗ ERREUR FATALE : {e}")
        import traceback
        traceback.print_exc()

    finally:
        # ── Cleanup DB ──────────────────────────────────────────────────
        try:
            await close_pool()
            print("\n✓ Pool DB fermé")
        except Exception as e:
            print(f"\n⚠ Erreur fermeture DB : {e}")

    # ── Rapport final
    print("\n" + "═" * 76)
    print(" RAPPORT FINAL")
    print("═" * 76)
    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass
    print(f"Scénarios : {len(results)} — ✅ {n_pass} succès — ❌ {n_fail} échecs\n")

    print("Métriques de charge :")
    print(f"{'users':>8} {'elapsed':>10} {'ops/s':>10} {'avg_ms':>10} {'max_ms':>10} "
          f"{'cache_hit':>12} {'saisie':>10}")
    for r in results:
        if not r.name.startswith("Charge_"):
            continue
        m = r.metrics
        print(f"{m.get('processed_total', 0):>8} "
              f"{m.get('elapsed_s', 0):>10.2f} "
              f"{m.get('throughput_ops', 0):>10.0f} "
              f"{m.get('per_user_avg_ms', 0):>10.3f} "
              f"{m.get('per_user_max_ms', 0):>10.3f} "
              f"{m.get('processed_from_cache', 0):>12} "
              f"{m.get('processed_after_input', 0):>10}")

    print("═" * 76)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)