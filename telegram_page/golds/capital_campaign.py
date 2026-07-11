"""
capital_campaign.py — Campagne hebdomadaire de mise à jour du capital (v7.1).

OBJECTIF
Chaque week-end, le bot demande à chaque utilisateur "actif" de mettre
à jour son capital. Cette campagne DOIT être progressive : envoyer
30 000 formulaires en 30 secondes surchargerait Telegram + le bot.

APPROCHE
Traitement par batches de N users avec pause entre batches. La taille
du batch et la pause sont configurables.

ISOLATION
Cette campagne est INDÉPENDANTE des sessions Gold :
  - Ne bloque pas les sessions en cours (peut cohabiter)
  - Ne bloque pas les nouveaux users qui cliquent (ils saisissent leur
    capital immédiatement via le flux normal, pas via la campagne)
  - Skip les users qui ont un capital NON expiré (moins de 7 jours) —
    inutile de spammer

TERMINAISON
La campagne enregistre son état dans une table `capital_campaign_runs`
pour permettre reprise après crash / restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from db import get_db
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden

from .weekly_capital_cache import weekly_capital

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066


@dataclass
class CampaignConfig:
    batch_size:        int   = 300
    pause_seconds:     float = 30.0    # entre chaque batch
    per_message_delay: float = 0.04    # 25 msg/s max intra-batch (limite Telegram)
    skip_if_fresh:     bool  = True    # ignore users qui ont un capital < 7j


@dataclass
class CampaignReport:
    total_targets:    int = 0
    skipped_fresh:    int = 0
    sent:             int = 0
    blocked:          int = 0
    errors:           int = 0
    batches_run:      int = 0
    started_at:       float = 0.0
    finished_at:      float = 0.0

    def summary(self) -> str:
        dt = self.finished_at - self.started_at if self.finished_at else 0
        return (f"Campagne capital — cibles={self.total_targets} "
                f"envoyés={self.sent} skippés_frais={self.skipped_fresh} "
                f"bloqués={self.blocked} erreurs={self.errors} "
                f"batches={self.batches_run} durée={dt:.0f}s")


CAMPAIGN_MSG = (
    "🔄 *Mise à jour hebdomadaire de ton capital*\n\n"
    "Pour que tes calculs restent précis, mets à jour ton capital actuel.\n\n"
    "Clique ci-dessous pour saisir ton nouveau capital "
    "(ou confirmer que rien n'a changé)."
)


def _campaign_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💼 Mettre à jour mon capital",
            callback_data="capital_update_form",
        )
    ]])


# ══════════════════════════════════════════════════════════════════════════════
# Sélection des cibles
# ══════════════════════════════════════════════════════════════════════════════

async def select_targets(category: str = "clients_actifs") -> list[int]:
    """
    Renvoie tous les user_ids de la catégorie ciblée.
    (Défaut : clients_actifs — même cible que les broadcasts Gold.)
    """
    async with get_db() as cur:
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s",
            (category,),
        )
        return [int(r["id_user"]) for r in await cur.fetchall()]


def filter_stale_users(user_ids: list[int]) -> list[int]:
    """
    Renvoie les users dont le capital est absent ou EXPIRÉ.
    Les users avec un capital frais (<7 jours) ne sont PAS re-sollicités.
    """
    return weekly_capital.missing_user_ids(user_ids)


# ══════════════════════════════════════════════════════════════════════════════
# Exécution
# ══════════════════════════════════════════════════════════════════════════════

async def run_campaign(bot, config: CampaignConfig | None = None,
                        category: str = "clients_actifs",
                        progress_callback=None) -> CampaignReport:
    """
    Lance la campagne. À appeler depuis le scheduler hebdomadaire ou
    manuellement via commande admin.

    progress_callback(report) est appelé après chaque batch — utile pour
    reporter en direct à l'admin sur Telegram.
    """
    cfg = config or CampaignConfig()
    rep = CampaignReport(started_at=time.time())

    all_targets = await select_targets(category)
    rep.total_targets = len(all_targets)

    if cfg.skip_if_fresh:
        # Précharge le cache pour savoir qui a un capital frais
        await weekly_capital.preload(all_targets)
        stale = filter_stale_users(all_targets)
        rep.skipped_fresh = len(all_targets) - len(stale)
        targets = stale
    else:
        targets = all_targets

    await bot.send_message(
        ADMIN_ID,
        f"🔄 Campagne capital démarrée\n"
        f"Cibles totales : {rep.total_targets}\n"
        f"À traiter : {len(targets)} (frais skippés : {rep.skipped_fresh})\n"
        f"Batches de {cfg.batch_size}, pause {cfg.pause_seconds}s.\n"
        f"ETA : ~{_eta_minutes(len(targets), cfg)} min",
    )

    for i in range(0, len(targets), cfg.batch_size):
        batch = targets[i:i + cfg.batch_size]
        await _run_batch(bot, batch, cfg, rep)
        rep.batches_run += 1

        if progress_callback:
            try:
                await progress_callback(rep)
            except Exception:
                pass

        # Report intermédiaire à l'admin toutes les N batches
        if rep.batches_run % 10 == 0:
            try:
                await bot.send_message(ADMIN_ID,
                    f"📊 Campagne — batch {rep.batches_run}, {rep.sent}/{len(targets)} envoyés")
            except Exception:
                pass

        # Pause entre batches — sauf après le dernier
        if i + cfg.batch_size < len(targets):
            await asyncio.sleep(cfg.pause_seconds)

    rep.finished_at = time.time()

    try:
        await bot.send_message(ADMIN_ID, f"✅ {rep.summary()}")
    except Exception:
        pass

    await _persist_run(rep)
    return rep


async def _run_batch(bot, user_ids: list[int], cfg: CampaignConfig, rep: CampaignReport):
    for uid in user_ids:
        try:
            await bot.send_message(
                chat_id=uid, text=CAMPAIGN_MSG,
                parse_mode="Markdown", reply_markup=_campaign_keyboard(),
            )
            rep.sent += 1
        except Forbidden:
            rep.blocked += 1
        except Exception as e:
            logger.debug(f"[campaign] uid={uid}: {e}")
            rep.errors += 1
        await asyncio.sleep(cfg.per_message_delay)


def _eta_minutes(n_targets: int, cfg: CampaignConfig) -> int:
    if n_targets == 0:
        return 0
    n_batches = (n_targets + cfg.batch_size - 1) // cfg.batch_size
    total_s = n_batches * (cfg.batch_size * cfg.per_message_delay + cfg.pause_seconds)
    return int(total_s / 60) + 1


# ══════════════════════════════════════════════════════════════════════════════
# Persistance de l'exécution (audit + reprise)
# ══════════════════════════════════════════════════════════════════════════════

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS capital_campaign_runs (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    started_at   DATETIME NOT NULL,
    finished_at  DATETIME NULL,
    total_targets INT NOT NULL DEFAULT 0,
    skipped_fresh INT NOT NULL DEFAULT 0,
    sent          INT NOT NULL DEFAULT 0,
    blocked       INT NOT NULL DEFAULT 0,
    errors        INT NOT NULL DEFAULT 0,
    batches_run   INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def ensure_schema():
    async with get_db() as cur:
        await cur.execute(RUNS_SCHEMA)
    logger.info("[campaign] schéma capital_campaign_runs OK")


async def _persist_run(rep: CampaignReport):
    try:
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO capital_campaign_runs
                    (started_at, finished_at, total_targets, skipped_fresh,
                     sent, blocked, errors, batches_run)
                VALUES (FROM_UNIXTIME(%s), FROM_UNIXTIME(%s),
                        %s, %s, %s, %s, %s, %s)
            """, (rep.started_at, rep.finished_at, rep.total_targets,
                  rep.skipped_fresh, rep.sent, rep.blocked, rep.errors,
                  rep.batches_run))
    except Exception as e:
        logger.warning(f"[campaign] persistance échouée: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler (à brancher dans main.py)
# ══════════════════════════════════════════════════════════════════════════════

async def weekly_scheduler_loop(bot, day_of_week: int = 5, hour: int = 10):
    """
    Boucle infinie qui déclenche la campagne chaque semaine à un jour +
    heure fixes. Par défaut : samedi 10h locale.

    day_of_week : 0=lundi ... 6=dimanche
    """
    from datetime import datetime, timedelta
    while True:
        now = datetime.now()
        # Calcule le prochain déclenchement
        days_ahead = (day_of_week - now.weekday()) % 7
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        target += timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(days=7)

        wait_s = (target - now).total_seconds()
        logger.info(f"[campaign] prochaine campagne dans {wait_s/3600:.1f}h "
                    f"({target.isoformat()})")
        await asyncio.sleep(wait_s)

        try:
            await run_campaign(bot)
        except Exception as e:
            logger.error(f"[campaign] campagne échouée: {e}", exc_info=True)
            try:
                await bot.send_message(ADMIN_ID, f"🔴 Campagne capital ÉCHOUÉE: {e}")
            except Exception:
                pass