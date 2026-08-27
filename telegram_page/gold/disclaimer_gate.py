"""
disclaimer_gate.py — Consentement disclaimer hebdomadaire (v8).

REMPLACE : le disclaimer cliqué à chaque signal (ancien handle_disclaimer_ok
dans broadcast_v7.py).

PRINCIPE
  Le disclaimer n'est plus un clic par signal. Il est validé UNE FOIS
  par semaine, le week-end (campagne, comme l'ancienne capital_campaign
  mais pour le consentement — la campagne capital elle-même est
  supprimée).

  Tant qu'un membre n'a pas validé pour la semaine en cours :
    - il ne reçoit AUCUN signal (voir signal_broadcast.split_by_consent)
    - il reçoit la demande de validation (campagne weekend, ou à la
      volée s'il rejoint en semaine)

  Dès qu'il valide, il redevient éligible aux signaux suivants
  immédiatement (pas besoin d'attendre le prochain week-end).

SEMAINE DE RÉFÉRENCE
  On utilise le lundi de la semaine courante comme clé (`week_start`),
  pour que la validation faite le samedi/dimanche couvre bien toute la
  semaine à venir (lundi → dimanche suivant).

TABLE
    CREATE TABLE IF NOT EXISTS weekly_disclaimer_consents (
        user_id      BIGINT NOT NULL,
        week_start   DATE   NOT NULL,
        consented_at DATETIME NOT NULL,
        PRIMARY KEY (user_id, week_start)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden

from db import get_db

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

DISCLAIMER_TEXT = (
    "📌 *Validation hebdomadaire*\n\n"
    "Avant de recevoir les signaux de la semaine, confirme que tu as "
    "bien comprend ceci :\n\n"
    "Ce que nous partageons est le fruit de notre propre analyse — "
    "ce n'est pas un conseil financier, ni une recommandation "
    "d'investissement. Le trading comporte des risques réels, y "
    "compris la perte de ton capital. Chaque décision t'appartient "
    "entièrement.\n\n"
    "✅ Je trade avec des fonds que je peux me permettre de perdre\n"
    "✅ Je suis les signaux à titre informatif uniquement\n"
    "✅ Je suis seul responsable de mes positions\n\n"
    "_Valable pour toute la semaine — à refaire chaque semaine._"
)

RAM_CACHE_TTL = 300  # secondes — évite un SELECT à chaque broadcast

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS weekly_disclaimer_consents (
    user_id      BIGINT NOT NULL,
    week_start   DATE   NOT NULL,
    consented_at DATETIME NOT NULL,
    PRIMARY KEY (user_id, week_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def ensure_schema():
    async with get_db() as cur:
        await cur.execute(SCHEMA_SQL)
    logger.info("[disclaimer_gate] schéma weekly_disclaimer_consents OK")


def _week_start(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())   # lundi de la semaine


class DisclaimerGate:

    def __init__(self):
        self._cache: dict[int, date] = {}   # user_id -> dernière week_start validée
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self):
        if time.time() - self._loaded_at < RAM_CACHE_TTL and self._cache:
            return
        async with self._lock:
            if time.time() - self._loaded_at < RAM_CACHE_TTL and self._cache:
                return
            async with get_db() as cur:
                await cur.execute("""
                    SELECT user_id, MAX(week_start) AS week_start
                    FROM weekly_disclaimer_consents
                    GROUP BY user_id
                """)
                rows = await cur.fetchall()
            self._cache = {int(r["user_id"]): r["week_start"] for r in rows}
            self._loaded_at = time.time()

    async def is_valid(self, user_id: int) -> bool:
        await self._ensure_loaded()
        wk = self._cache.get(user_id)
        return wk is not None and wk >= _week_start()

    async def record_consent(self, user_id: int):
        wk = _week_start()
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO weekly_disclaimer_consents (user_id, week_start, consented_at)
                VALUES (%s, %s, NOW())
                AS new_vals
                ON DUPLICATE KEY UPDATE consented_at = new_vals.consented_at
            """, (user_id, wk))
        self._cache[user_id] = wk

    def invalidate_cache(self):
        self._loaded_at = 0.0


disclaimer_gate = DisclaimerGate()


async def split_by_consent(user_ids: list[int]) -> tuple[list[int], list[int]]:
    """Renvoie (consentis_valides, en_attente) pour la semaine courante."""
    await disclaimer_gate._ensure_loaded()
    wk = _week_start()
    consented, pending = [], []
    for uid in user_ids:
        last = disclaimer_gate._cache.get(uid)
        (consented if (last is not None and last >= wk) else pending).append(uid)
    return consented, pending


def _consent_keyboard(pending_session_id: int | None = None) -> InlineKeyboardMarkup:
    # Le session_id éventuel voyage dans le callback_data pour que le
    # handler sache quel signal renvoyer juste après validation.
    suffix = f"_{pending_session_id}" if pending_session_id is not None else ""
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Je valide pour cette semaine",
        callback_data=f"disclaimer_weekly_ok{suffix}",
    )]])


# ══════════════════════════════════════════════════════════════════════════════
# Campagne week-end — sollicite les membres qui n'ont pas encore validé
# ══════════════════════════════════════════════════════════════════════════════

async def run_weekend_campaign(bot, category: str = "clients_actifs",
                                 batch_size: int = 300, pause_seconds: float = 20.0):
    """
    À brancher sur le scheduler (ex: samedi 09h). Sollicite uniquement
    les membres n'ayant pas encore validé pour la semaine à venir —
    les membres déjà à jour ne sont pas re-sollicités.
    """
    async with get_db() as cur:
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        all_ids = [int(r["id_user"]) for r in await cur.fetchall()]

    consented, pending = await split_by_consent(all_ids)
    logger.info(f"[disclaimer_gate] campagne weekend — {len(pending)} à solliciter, "
                f"{len(consented)} déjà à jour")

    sent = blocked = errors = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        for uid in batch:
            try:
                await bot.send_message(chat_id=uid, text=DISCLAIMER_TEXT,
                                        parse_mode="Markdown", reply_markup=_consent_keyboard())
                sent += 1
            except Forbidden:
                blocked += 1
            except Exception as e:
                logger.debug(f"[disclaimer_gate] uid={uid}: {e}")
                errors += 1
            await asyncio.sleep(0.04)
        if i + batch_size < len(pending):
            await asyncio.sleep(pause_seconds)

    try:
        await bot.send_message(ADMIN_ID,
            f"📌 Campagne disclaimer hebdo terminée — envoyés={sent} "
            f"bloqués={blocked} erreurs={errors} déjà_à_jour={len(consented)}")
    except Exception:
        pass


async def weekend_scheduler_loop(bot, day_of_week: int = 5, hour: int = 9):
    """Boucle hebdomadaire — défaut : samedi 09h locale."""
    while True:
        now = datetime.now()
        days_ahead = (day_of_week - now.weekday()) % 7
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(days=7)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await run_weekend_campaign(bot)
        except Exception as e:
            logger.error(f"[disclaimer_gate] campagne échouée: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# Handler du bouton de validation (weekend OU à la volée en semaine)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_disclaimer_weekly_ok(update, context):
    query = update.callback_query
    if query is None:
        return
    uid = query.from_user.id

    # callback_data = "disclaimer_weekly_ok" ou "disclaimer_weekly_ok_<session_id>"
    parts = query.data.split("_")
    pending_session_id = int(parts[-1]) if parts[-1].isdigit() else None

    try:
        await query.answer("✅ Validé pour cette semaine.")
    except Exception:
        pass
    await disclaimer_gate.record_consent(uid)
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=uid,
        text="✅ *C'est validé.*",
        parse_mode="Markdown",
    )

    # Si la validation faisait suite à un signal manqué (weekend non fait
    # ou premier signal), on lui envoie ce signal maintenant.
    if pending_session_id is not None:
        try:
            from signal_broadcast import send_signal_to_user
            await send_signal_to_user(context.bot, uid, pending_session_id)
        except Exception as e:
            logger.error(f"[disclaimer_gate] envoi signal différé uid={uid}: {e}", exc_info=True)


async def send_consent_request(bot, user_id: int, *, pending_session_id: int | None = None):
    """
    Demande de validation hebdomadaire.

    - Sans pending_session_id : campagne weekend classique.
    - Avec pending_session_id : le membre a raté un signal (pas encore
      validé cette semaine, ou tout premier signal reçu) — dès qu'il
      valide, ce signal précis lui est envoyé.
    """
    try:
        await bot.send_message(chat_id=user_id, text=DISCLAIMER_TEXT,
                                parse_mode="Markdown",
                                reply_markup=_consent_keyboard(pending_session_id))
    except Forbidden:
        pass