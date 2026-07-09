"""
consistency.py — Vérifie la cohérence RAM ⇄ Buffer ⇄ SQL (v7).

Trois usages :

  1. Diagnostic à la demande (commande admin /gold_check)
     → renvoie un rapport lisible sur l'état actuel.

  2. Vérification automatique après un broadcast et après une fermeture
     → invoqué par lifecycle.close_session() en fin de vie.

  3. Assertions dans la suite de tests (tests/simulator.py)
     → utilisées pour vérifier qu'aucune simulation ne produit
       d'incohérence.

Ce module NE MODIFIE JAMAIS aucun état — lecture seule partout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from db import get_db
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.session_snapshot import snapshot_store
from telegram_page.gold.gold_state import user_state_v7
from telegram_page.gold.gold_buffer import gold_buffer

logger = logging.getLogger(__name__)


@dataclass
class Discrepancy:
    kind:     str
    detail:   str
    severity: str = "error"     # "error" | "warning" | "info"


@dataclass
class ConsistencyReport:
    ok:            bool = True
    session_id:    int | None = None
    version:       int | None = None
    checks_run:    int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)
    metrics:       dict = field(default_factory=dict)

    def add(self, kind: str, detail: str, severity: str = "error"):
        self.discrepancies.append(Discrepancy(kind, detail, severity))
        if severity == "error":
            self.ok = False

    def summary(self) -> str:
        if self.ok and not self.discrepancies:
            return f"✅ Cohérence OK ({self.checks_run} vérifications)"
        lines = [f"{'✅' if self.ok else '❌'} Cohérence: {self.checks_run} checks, "
                 f"{len(self.discrepancies)} anomalie(s)"]
        for d in self.discrepancies:
            emo = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(d.severity, "•")
            lines.append(f"  {emo} [{d.kind}] {d.detail}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Checks unitaires
# ══════════════════════════════════════════════════════════════════════════════

async def check_registry_snapshot_state_alignment(rep: ConsistencyReport):
    """Registry, snapshot store et state manager doivent parler de la MÊME session."""
    rep.checks_run += 1
    reg = session_registry.current()
    snap = snapshot_store.get_active()

    if reg is None and snap is None and user_state_v7.session_id is None:
        return    # tout est vide, cohérent

    if reg is None:
        rep.add("registry_empty",
                f"snapshot ou state actifs alors que registry vide "
                f"(snap={snap and snap.session_id}, state={user_state_v7.session_id})")
        return

    if snap is None:
        rep.add("snapshot_missing",
                f"registry a session #{reg.session_id}v{reg.version} mais snapshot absent")
    elif snap.session_id != reg.session_id or snap.version != reg.version:
        rep.add("snapshot_version_mismatch",
                f"registry=#{reg.session_id}v{reg.version} "
                f"snapshot=#{snap.session_id}v{snap.version}")

    if user_state_v7.session_id != reg.session_id or user_state_v7.version != reg.version:
        rep.add("state_version_mismatch",
                f"registry=#{reg.session_id}v{reg.version} "
                f"state=#{user_state_v7.session_id}v{user_state_v7.version}")


async def check_buffer_attached_to_current(rep: ConsistencyReport):
    rep.checks_run += 1
    reg = session_registry.current()
    status = gold_buffer.status()
    attached = status["attached"]

    if reg is None:
        if attached is not None and status["pending"] > 0:
            rep.add("buffer_leaks",
                    f"registry vide mais buffer attaché à {attached} "
                    f"avec {status['pending']} lignes en attente")
        return

    if attached != (reg.session_id, reg.version):
        rep.add("buffer_wrong_session",
                f"registry=#{reg.session_id}v{reg.version} "
                f"buffer attaché à {attached}",
                severity="error")


async def check_state_confirmations_calc_integrity(rep: ConsistencyReport):
    """
    Chaque CalcContext des utilisateurs confirmés doit avoir été calculé
    avec les valeurs entry/sl du snapshot ACTIF (à ~epsilon près pour
    l'ajustement au prix live).
    """
    rep.checks_run += 1
    snap = snapshot_store.get_active()
    if snap is None:
        return
    for uid, calc in user_state_v7.confirmed_calcs().items():
        if calc.session_id != snap.session_id or calc.version != snap.version:
            rep.add("calc_session_mismatch",
                    f"uid={uid} calc pour #{calc.session_id}v{calc.version} "
                    f"vs session courante #{snap.session_id}v{snap.version}")
            continue
        # SL_pips figé dans le snapshot doit être conservé, MÊME si entry
        # a été ajusté au prix live (l'ajustement préserve l'écart).
        sl_pips_effective = round(abs(calc.effective_entry - calc.effective_sl), 2)
        if abs(sl_pips_effective - snap.sl_pips) > 0.05:
            rep.add("sl_pips_drift",
                    f"uid={uid} sl_pips effectifs={sl_pips_effective} "
                    f"vs snapshot={snap.sl_pips}",
                    severity="warning")


async def check_sql_matches_state_aggregates(rep: ConsistencyReport):
    """
    Compare les agrégats en RAM avec ce qui est en SQL. Si le buffer
    n'a pas encore flushé, un écart est ATTENDU — ne remonter que si
    l'écart persiste (i.e. le buffer est vide mais SQL diverge encore).
    """
    rep.checks_run += 1
    reg = session_registry.current()
    if reg is None:
        return

    # Si le buffer a des lignes en attente, la comparaison n'est pas
    # pertinente — SQL sera à jour après le prochain flush.
    if gold_buffer.status()["pending"] > 0:
        return

    agg = user_state_v7.aggregates()
    async with get_db() as cur:
        await cur.execute("""
            SELECT total_members_in, total_lots_engaged,
                   estimated_loss_sl, estimated_gain_tp1,
                   estimated_gain_tp2, estimated_gain_tp3
            FROM gold_trade_sessions WHERE id = %s
        """, (reg.session_id,))
        row = await cur.fetchone()
    if not row:
        rep.add("sql_session_missing",
                f"session #{reg.session_id} absente en SQL")
        return

    def _close(a, b, tol=0.01):
        a = float(a or 0); b = float(b or 0)
        return abs(a - b) <= tol

    pairs = [
        ("total_members",  agg["total_members"],  row["total_members_in"]),
        ("total_lots",     agg["total_lots"],     row["total_lots_engaged"]),
        ("total_loss_sl",  agg["total_loss_sl"],  row["estimated_loss_sl"]),
        ("total_gain_tp1", agg["total_gain_tp1"], row["estimated_gain_tp1"]),
        ("total_gain_tp2", agg["total_gain_tp2"], row["estimated_gain_tp2"]),
        ("total_gain_tp3", agg["total_gain_tp3"], row["estimated_gain_tp3"]),
    ]
    for name, ram_val, sql_val in pairs:
        if not _close(ram_val, sql_val):
            rep.add("agg_ram_sql_drift",
                    f"{name}: RAM={ram_val} SQL={sql_val}",
                    severity="warning")

    rep.metrics["agg_ram"] = agg
    rep.metrics["agg_sql"] = {k: float(v or 0) for k, v in dict(row).items()}


async def check_no_confirm_for_wrong_session(rep: ConsistencyReport):
    """Aucun confirmé en RAM ne doit pointer vers une session ≠ session courante."""
    rep.checks_run += 1
    reg = session_registry.current()
    if reg is None:
        if user_state_v7.confirmed_ids():
            rep.add("ghost_confirmations",
                    f"{len(user_state_v7.confirmed_ids())} confirmations en RAM "
                    f"alors qu'aucune session active")
        return
    for uid, calc in user_state_v7.confirmed_calcs().items():
        if calc.session_id != reg.session_id:
            rep.add("confirm_wrong_session",
                    f"uid={uid} confirmé pour #{calc.session_id} "
                    f"mais session active #{reg.session_id}")


# ══════════════════════════════════════════════════════════════════════════════
# Entrée publique
# ══════════════════════════════════════════════════════════════════════════════

async def run_full_check() -> ConsistencyReport:
    rep = ConsistencyReport()
    reg = session_registry.current()
    if reg is not None:
        rep.session_id = reg.session_id
        rep.version    = reg.version

    await check_registry_snapshot_state_alignment(rep)
    await check_buffer_attached_to_current(rep)
    await check_state_confirmations_calc_integrity(rep)
    await check_no_confirm_for_wrong_session(rep)
    await check_sql_matches_state_aggregates(rep)

    if not rep.ok:
        logger.error(f"[consistency] {rep.summary()}")
    return rep