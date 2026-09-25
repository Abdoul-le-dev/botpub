"""
gold_broadcast.py — Gold v8, qui reçoit le signal et quand.

Fusionne l'ancien disclaimer_gate.py + signal_broadcast.py : les deux
étaient déjà couplés dans les faits (la validation du disclaimer
déclenche l'envoi du signal en attente) — un seul fichier pour une
seule responsabilité : décider qui reçoit quoi, et le leur envoyer.

PRINCIPE (inchangé, déjà en prod)
  1. Consentement hebdomadaire — validé une fois par semaine (campagne
     week-end ou à la demande via /je_valide_mon_engagement), valable
     toute la semaine. Sans consentement à jour : pas de signal, juste
     la demande de validation.
  2. Signal brut — entry/TP/SL uniquement, envoyé IMMÉDIATEMENT à toute
     la catégorie ciblée, sans étape intermédiaire (pas de saisie
     capital, pas de calcul de lot personnalisé à l'envoi). Le calcul
     est un outil à la demande (voir gold_followup.py — "Money
     management").
  3. Dès l'envoi terminé : ouverture des comptes simulation + démarrage
     automatique de la surveillance prix (gold_followup.watch_and_close).
     Plus besoin de déclencher ça à la main depuis l'admin.

Intégration :
    from telegram_page.gold.gold_broadcast import send_signal
    await send_signal(bot, session_id)   # session déjà créée via
                                          # gold_core.create_gold_session()
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden, RetryAfter

from db import get_db
from telegram_page.gold.gold_core import get_session_row, open_simulation_trades

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

CATEGORY_TARGET   = "clients_actifs"
CATEGORY_BLOCKED  = "clients_bloquer"
RESUB_WINDOW_DAYS = 10
RESUB_URL = "https://fdkvip.com/reabonnement"   # TODO: ajuster si besoin
NUM_WORKERS = 40


# ══════════════════════════════════════════════════════════════════════════════
# 1. CONSENTEMENT HEBDOMADAIRE
# ══════════════════════════════════════════════════════════════════════════════

DISCLAIMER_TEXT = (
    "📌 *Validation hebdomadaire*\n\n"
    "Avant de recevoir les signaux de la semaine, confirme que tu as "
    "bien compris ceci :\n\n"
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

DISCLAIMER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS weekly_disclaimer_consents (
    user_id      BIGINT NOT NULL,
    week_start   DATE   NOT NULL,
    consented_at DATETIME NOT NULL,
    PRIMARY KEY (user_id, week_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def ensure_disclaimer_schema():
    async with get_db() as cur:
        await cur.execute(DISCLAIMER_SCHEMA_SQL)
    logger.info("[gold_broadcast] schéma weekly_disclaimer_consents OK")


def _week_start(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())   # lundi de la semaine


class DisclaimerGate:
    """
    Cache RAM en lecture seule (rechargé toutes les RAM_CACHE_TTL
    secondes) — ce n'est PAS un état à synchroniser entre process : si
    l'API et le bot ont chacun leur propre copie légèrement décalée
    dans le temps, la conséquence est au pire un rappel de validation
    envoyé une fois de trop, jamais une incohérence de trade.
    """

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
    suffix = f"_{pending_session_id}" if pending_session_id is not None else ""
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Je valide mon engagement",
        callback_data=f"disclaimer_weekly_ok{suffix}",
    )]])


async def run_weekend_campaign(bot, category: str = CATEGORY_TARGET,
                                 batch_size: int = 300, pause_seconds: float = 20.0):
    """Sollicite uniquement les membres pas encore à jour pour la
    semaine à venir. À brancher sur le scheduler (ex: samedi 09h)."""
    async with get_db() as cur:
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        all_ids = [int(r["id_user"]) for r in await cur.fetchall()]

    consented, pending = await split_by_consent(all_ids)
    logger.info(f"[gold_broadcast] campagne weekend — {len(pending)} à solliciter, "
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
                logger.debug(f"[gold_broadcast] uid={uid}: {e}")
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
            logger.error(f"[gold_broadcast] campagne échouée: {e}", exc_info=True)


async def handle_disclaimer_weekly_ok(update, context):
    """Callback du bouton de validation — weekend OU à la volée."""
    query = update.callback_query
    if query is None:
        return
    uid = query.from_user.id

    parts = query.data.split("_")
    pending_session_id = int(parts[-1]) if parts[-1].isdigit() else None

    try:
        await query.answer("✅ Engagement validé pour cette semaine.")
    except Exception:
        pass
    await disclaimer_gate.record_consent(uid)
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass

    await _deliver_after_consent(context.bot, uid, pending_session_id)


async def _deliver_after_consent(bot, uid: int, pending_session_id: int | None):
    """
    Après validation :
      - un session_id précis était en attente (signal manqué) → on
        envoie CE signal.
      - sinon → on regarde s'il y a un trade en cours (phase teaser/
        open) et on l'envoie s'il y en a un ; sinon confirmation simple.
    """
    from telegram_page.gold.gold_core import get_active_gold_session

    target_session_id = pending_session_id
    if target_session_id is None:
        try:
            active = await get_active_gold_session()
            target_session_id = active["id"] if active else None
        except Exception as e:
            logger.error(f"[gold_broadcast] lookup trade en cours uid={uid}: {e}", exc_info=True)
            target_session_id = None

    if target_session_id is not None:
        try:
            await send_signal_to_user(bot, uid, target_session_id)
            return
        except Exception as e:
            logger.error(f"[gold_broadcast] envoi signal uid={uid} sid={target_session_id}: {e}",
                         exc_info=True)

    try:
        await bot.send_message(
            chat_id=uid,
            text=("✅ *Engagement validé pour cette semaine.*\n\n"
                  "Dès qu'une nouvelle opportunité sera disponible, "
                  "elle te sera envoyée automatiquement."),
            parse_mode="Markdown",
        )
    except Forbidden:
        pass


async def send_consent_request(bot, user_id: int, *,
                                pending_session_id: int | None = None,
                                intro: str | None = None):
    text = f"{intro}\n\n{DISCLAIMER_TEXT}" if intro else DISCLAIMER_TEXT
    try:
        await bot.send_message(chat_id=user_id, text=text,
                                parse_mode="Markdown",
                                reply_markup=_consent_keyboard(pending_session_id))
    except Forbidden:
        pass


async def cmd_je_valide_mon_engagement(update, context):
    """Le membre déclenche lui-même la validation hebdomadaire."""
    uid = update.effective_user.id

    if await disclaimer_gate.is_valid(uid):
        await update.message.reply_text(
            "✅ *Tu as déjà validé ton engagement pour cette semaine.*",
            parse_mode="Markdown",
        )
        return

    intro = (
        "⚠️ *Tu n'as pas encore validé ton engagement hebdomadaire.*\n\n"
        "Tant que ce n'est pas fait, tu ne peux pas recevoir les signaux."
    )
    await send_consent_request(context.bot, uid, intro=intro)


# ══════════════════════════════════════════════════════════════════════════════
# 2. RATE LIMITER ADAPTATIF (AIMD)
# ══════════════════════════════════════════════════════════════════════════════
# Autonome (pas de dépendance à un moteur de diffusion générique) car
# le signal a besoin d'un reply_markup PAR DESTINATAIRE. Instance
# MODULE-LEVEL : le débit appris est conservé d'un envoi à l'autre.

class AdaptiveRateLimiter:
    def __init__(self, start_rate: float = 25.0,
                 min_rate: float = 12.0, max_rate: float = 30.0,
                 ramp_step: float = 0.5, ramp_after_streak: int = 40):
        self.current_rate = start_rate
        self.min_rate = min_rate
        self.max_rate = max_rate
        self._ramp_step = ramp_step
        self._ramp_after_streak = ramp_after_streak
        self._lock = asyncio.Lock()
        self._last_send = 0.0
        self._success_streak = 0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            interval = 1.0 / self.current_rate
            wait = self._last_send + interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_send = time.monotonic()

    def report_success(self):
        self._success_streak += 1
        if self._success_streak >= self._ramp_after_streak and self.current_rate < self.max_rate:
            self.current_rate = min(self.max_rate, self.current_rate + self._ramp_step)
            self._success_streak = 0

    def report_flood(self, retry_after: float):
        self._success_streak = 0
        self.current_rate = max(self.min_rate, self.current_rate * 0.7)


_signal_limiter = AdaptiveRateLimiter()


class _SendContext:
    __slots__ = ("bot", "session_id", "text", "resub_flags", "limiter",
                 "sent", "errors", "blocked_ids")

    def __init__(self, bot, session_id: int, text: str,
                 resub_flags: dict, limiter: AdaptiveRateLimiter):
        self.bot = bot
        self.session_id = session_id
        self.text = text
        self.resub_flags = resub_flags
        self.limiter = limiter
        self.sent = 0
        self.errors = 0
        self.blocked_ids: list = []


async def _signal_worker(queue: asyncio.Queue, ctx: _SendContext):
    while True:
        uid = await queue.get()
        try:
            if uid is None:
                return
            kbd = build_signal_keyboard(ctx.session_id,
                                         show_resub=ctx.resub_flags.get(uid, False))
            await ctx.limiter.acquire()
            try:
                await ctx.bot.send_message(chat_id=uid, text=ctx.text,
                                            parse_mode="Markdown", reply_markup=kbd)
                ctx.sent += 1
                ctx.limiter.report_success()
            except RetryAfter as e:
                ctx.limiter.report_flood(e.retry_after)
                await asyncio.sleep(e.retry_after)
                try:
                    await ctx.bot.send_message(chat_id=uid, text=ctx.text,
                                                parse_mode="Markdown", reply_markup=kbd)
                    ctx.sent += 1
                except Forbidden:
                    ctx.blocked_ids.append(uid)
                except Exception as e2:
                    logger.debug(f"[signal_worker] retry échoué uid={uid}: {e2}")
                    ctx.errors += 1
            except Forbidden:
                ctx.blocked_ids.append(uid)
            except Exception as e:
                logger.debug(f"[signal_worker] uid={uid}: {e}")
                ctx.errors += 1
        finally:
            queue.task_done()


# ══════════════════════════════════════════════════════════════════════════════
# 3. FORMATAGE DU MESSAGE
# ══════════════════════════════════════════════════════════════════════════════

def build_signal_message(session: dict) -> str:
    """Message brut, minimal : paire, sens, niveaux. Rien d'autre."""
    direction = session["direction"]
    dir_label = "BUY 📈" if direction == "buy" else "SELL 📉"

    lines = [
        f"🟡 *XAU/USD*  ·  {dir_label}",
        "",
        f"Entrée   `{session['entry_price']}`",
    ]
    if session.get("tp1"):
        lines.append(f"TP1        `{session['tp1']}`")
    if session.get("tp2"):
        lines.append(f"TP2        `{session['tp2']}`")
    if session.get("tp3"):
        lines.append(f"TP3        `{session['tp3']}`")
    lines.append(f"SL          `{session['sl']}`")
    return "\n".join(lines)


def build_signal_keyboard(session_id: int, *, show_resub: bool) -> InlineKeyboardMarkup:
    # Un bouton par ligne — deux boutons côte à côte se font tronquer
    # sur mobile.
    rows = [
        [InlineKeyboardButton("💰 Money management", callback_data=f"mm_open_{session_id}")],
        [InlineKeyboardButton("🆘 Besoin d'aide", callback_data=f"help_request_{session_id}")],
    ]
    if show_resub:
        rows.append([InlineKeyboardButton("🎁 Me réabonner -30%", url=RESUB_URL)])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 4. DESTINATAIRES
# ══════════════════════════════════════════════════════════════════════════════

async def _get_category_user_ids(category: str) -> list:
    async with get_db() as cur:
        if category == "all":
            await cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
            return [r["telegram_id"] for r in await cur.fetchall()]
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        return [r["id_user"] for r in await cur.fetchall()]


async def _preload_resub_flags(user_ids: list) -> dict:
    """Pour chaque destinataire : abonnement expirant dans <= 10 jours ?"""
    if not user_ids:
        return {}
    flags: dict = {}
    chunk_size = 1000
    try:
        async with get_db() as cur:
            for i in range(0, len(user_ids), chunk_size):
                chunk = user_ids[i:i + chunk_size]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(f"""
                    SELECT user_id, MAX(expires_at) AS expires_at
                    FROM subscriptions
                    WHERE user_id IN ({ph})
                    GROUP BY user_id
                """, chunk)
                for r in await cur.fetchall():
                    days_left = (r["expires_at"] - datetime.now()).days if r["expires_at"] else None
                    flags[int(r["user_id"])] = (
                        days_left is not None and 0 <= days_left <= RESUB_WINDOW_DAYS
                    )
    except Exception as e:
        logger.error(f"[gold_broadcast] _preload_resub_flags échoué : {e}")
        return {}
    return flags


async def _handle_blocked_users(blocked_ids: list, source_category: str) -> dict:
    result = {"blocked": len(blocked_ids), "removed": 0, "added": 0, "already_in": 0}
    if not blocked_ids:
        return result
    try:
        from telegram_page.categorie import (
            add_members_to_category, remove_member_from_category,
        )
        add_res = await add_members_to_category(
            CATEGORY_BLOCKED, blocked_ids, added_by="signal_broadcast_blocked"
        )
        result["added"] = add_res.get("added", 0)
        result["already_in"] = add_res.get("ignored", 0)
        if source_category and source_category != "all":
            for uid in blocked_ids:
                try:
                    await remove_member_from_category(source_category, uid)
                    result["removed"] += 1
                except Exception as e:
                    logger.warning(f"[blocked] retrait uid={uid} de '{source_category}' échoué: {e}")
    except Exception as e:
        logger.error(f"[blocked] traitement échoué: {e}", exc_info=True)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 5. ENVOI
# ══════════════════════════════════════════════════════════════════════════════

async def _notify_pending(bot, pending_ids: list, session_id: int):
    for uid in pending_ids:
        try:
            await send_consent_request(bot, uid, pending_session_id=session_id)
        except Exception as e:
            logger.debug(f"[gold_broadcast] consent request uid={uid}: {e}")
        await asyncio.sleep(0.04)


async def send_signal_to_user(bot, uid: int, session_id: int):
    """Envoie le signal brut à UN seul membre — utilisé quand un membre
    valide son disclaimer après coup et doit recevoir le signal en cours."""
    session = await get_session_row(session_id)
    if session is None:
        return
    resub = await _preload_resub_flags([uid])
    kbd = build_signal_keyboard(session_id, show_resub=resub.get(uid, False))
    try:
        await bot.send_message(chat_id=uid, text=build_signal_message(session),
                                parse_mode="Markdown", reply_markup=kbd)
    except Forbidden:
        pass


async def send_signal(bot, session_id: int, *, category: str = None) -> dict:
    """
    Envoi brut et immédiat du signal à toute la catégorie ciblée.

    - Les membres SANS consentement disclaimer valide cette semaine ne
      reçoivent PAS le signal (voir split_by_consent) ; ils reçoivent/
      ont reçu la demande de validation à part.
    - Aucun calcul de lot, aucune saisie de capital ici.
    - À la fin : ouvre les comptes simulation et démarre automatiquement
      la surveillance prix (gold_followup.watch_and_close).
    """
    category = category or CATEGORY_TARGET
    session = await get_session_row(session_id)
    if session is None:
        raise RuntimeError(f"Session #{session_id} introuvable.")

    all_ids = await _get_category_user_ids(category)
    consented_ids, pending_ids = await split_by_consent(all_ids)

    total = len(consented_ids)
    if pending_ids:
        logger.info(
            f"[gold_broadcast] {len(pending_ids)} membres en attente de "
            f"validation disclaimer — reçoivent la demande à la place du signal."
        )
        asyncio.create_task(_notify_pending(bot, pending_ids, session_id))

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0,
                "pending_consent": len(pending_ids), "session_id": session_id}

    resub_flags = await _preload_resub_flags(consented_ids)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Envoi signal Gold démarré*\n"
                  f"Session : #{session_id}\n"
                  f"Cible : {category} | Destinataires : {total} "
                  f"(en attente disclaimer : {len(pending_ids)})"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    text = build_signal_message(session)
    queue: asyncio.Queue = asyncio.Queue()
    ctx = _SendContext(bot, session_id, text, resub_flags, _signal_limiter)

    workers = [asyncio.create_task(_signal_worker(queue, ctx)) for _ in range(NUM_WORKERS)]
    t0 = time.monotonic()
    for uid in consented_ids:
        queue.put_nowait(uid)
    for _ in range(NUM_WORKERS):
        queue.put_nowait(None)
    await asyncio.gather(*workers, return_exceptions=True)
    elapsed = time.monotonic() - t0

    sent, errors = ctx.sent, ctx.errors
    blocked_report = await _handle_blocked_users(ctx.blocked_ids, category)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"✅ *Signal Gold terminé — session #{session_id}*\n\n"
                  f"Envoyés : {sent}/{total} en {elapsed:.1f}s "
                  f"({sent / elapsed:.1f} msg/s)\n"
                  f"Erreurs : {errors}\n"
                  f"En attente disclaimer : {len(pending_ids)}\n"
                  f"🚫 Bloqués : {blocked_report['blocked']}"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # teaser → open (écriture directe, une seule fois par session —
    # plus besoin de write-behind pour un seul UPDATE non-bloquant).
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_trade_sessions
            SET current_phase = 'open', opened_at = COALESCE(opened_at, NOW())
            WHERE id = %s AND current_phase = 'teaser'
        """, (session_id,))

    # Comptes simulation (paper trading pour stats/dashboard).
    try:
        await open_simulation_trades(session_id, session)
    except Exception as e:
        logger.error(f"[gold_broadcast] open_simulation_trades sid={session_id}: {e}", exc_info=True)

    # Surveillance prix + fermeture auto SL/TP3 (import local pour
    # éviter un cycle d'import avec gold_followup, qui importe des
    # helpers de ce fichier).
    try:
        from telegram_page.gold.gold_followup import watch_and_close
        asyncio.create_task(watch_and_close(session_id))
    except Exception as e:
        logger.error(f"[gold_broadcast] démarrage watch_and_close: {e}")

    return {"total": total, "sent": sent, "errors": errors,
            "pending_consent": len(pending_ids),
            "blocked": blocked_report, "session_id": session_id}