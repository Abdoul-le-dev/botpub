"""
broadcast/worker.py — logique d'envoi unitaire + worker asynchrone.

Un worker consomme la queue et envoie un message à la fois. La cadence est
imposée par le rate limiter global (rate_limiter.py) : les workers passent
l'essentiel de leur temps dans `await limiter.acquire()`.

Politique d'erreur (par le brief) :
  - RetryAfter : PAUSE globale + retry de CE message uniquement (pas d'échec).
  - Toute autre erreur : classification + collecte, PAS de retry, passer au suivant.

Mode nettoyage (+nettoyage dans le message, ctx.cleanup_mode=True) :
  - Envoi silencieux (disable_notification=True).
  - Après envoi réussi → suppression IMMÉDIATE du message côté user
    (bot.delete_message pour chaque Message renvoyé par Telegram).
  - Sur erreur BLOCKED ou DELETED → purge IMMÉDIATE du user en DB
    (users + categories) avant de passer au user suivant.
  - Erreurs network → ignorées (ni purge, ni delete-msg).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram.error import RetryAfter, TelegramError

from . import config, media_cache
from .cleanup import delete_user_immediately
from .error_classifier import ErrorCategory, classify
from .rate_limiter import AdaptiveRateLimiter
from .recipients import inject_variables

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTE PARTAGÉ PAR TOUS LES WORKERS D'UN BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

class BroadcastContext:
    """État mutable partagé entre le feeder et les N workers d'UN broadcast."""

    def __init__(
        self,
        *,
        bot,
        fmt: str,
        message: str,
        media_url: Optional[str],
        is_local_media: bool,
        prenoms: dict[int, str],
        variables: Optional[dict],
        tag: str,
        limiter: AdaptiveRateLimiter,
        cleanup_mode: bool = False,
    ):
        self.bot = bot
        self.fmt = fmt
        self.message = message
        self.media_url = media_url
        self.is_local_media = is_local_media
        self.prenoms = prenoms
        self.variables = variables or {}
        self.tag = tag
        self.limiter = limiter

        # Mode nettoyage : envoi silencieux + delete du msg + purge DB live
        self.cleanup_mode = cleanup_mode
        # Silent = True dès qu'on est en cleanup_mode (pas de vibration user)
        self.silent = cleanup_mode

        # Media : file_id Telegram réutilisable une fois obtenu
        self.cached_file_id: Optional[str] = None
        self.file_id_ready = asyncio.Event()
        # Lock qui sérialise le PREMIER upload d'un fichier local
        self.first_upload_lock = asyncio.Lock()

        # Compteurs
        self.sent: int = 0
        self.errors: int = 0
        # Compteur spécifique au mode nettoyage : users purgés en live
        self.purged: int = 0

        # Collecte d'erreurs par catégorie
        self.errors_by_category: dict[ErrorCategory, list[dict]] = {
            cat: [] for cat in ErrorCategory
        }


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI UNITAIRE
# ══════════════════════════════════════════════════════════════════════════════

_MEDIA_FORMATS = {"image", "video", "document", "image+text", "video+text", "document+text"}
_FORMATS_WITH_CAPTION = {"image+text", "video+text"}


def _clip_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


async def _do_send(
    bot,
    user_id: int,
    fmt: str,
    text: str,
    media,
    silent: bool = False,
) -> list:
    """
    Effectue l'appel Telegram brut. Retourne la LISTE des objets Message
    envoyés (0, 1 ou 2 selon le format).

    Le mode nettoyage utilise cette liste pour supprimer chaque message
    envoyé.  Le cache file_id utilise le dernier élément (le média).

    Gestion des captions :
      - Si texte ≤ TG_CAPTION_SAFE_LEN → caption sur le média.
      - Sinon → texte envoyé en message séparé + média sans caption.

    Paramètre `silent` : si True, tous les envois utilisent
    disable_notification=True (mode nettoyage : ping muet).
    """
    kw = {"disable_notification": True} if silent else {}
    sent: list = []

    if fmt == "text":
        m = await bot.send_message(
            chat_id=user_id,
            text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
            **kw,
        )
        sent.append(m)
        return sent

    if fmt == "image":
        m = await bot.send_photo(chat_id=user_id, photo=media, **kw)
        sent.append(m)
        return sent

    if fmt == "video":
        m = await bot.send_video(chat_id=user_id, video=media, **kw)
        sent.append(m)
        return sent

    if fmt == "document":
        m = await bot.send_document(chat_id=user_id, document=media, **kw)
        sent.append(m)
        return sent

    if fmt in _FORMATS_WITH_CAPTION:
        if len(text) > config.TG_CAPTION_SAFE_LEN:
            m1 = await bot.send_message(
                chat_id=user_id,
                text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
                **kw,
            )
            sent.append(m1)
            if fmt == "image+text":
                m2 = await bot.send_photo(chat_id=user_id, photo=media, **kw)
            else:
                m2 = await bot.send_video(chat_id=user_id, video=media, **kw)
            sent.append(m2)
        else:
            if fmt == "image+text":
                m = await bot.send_photo(chat_id=user_id, photo=media, caption=text, **kw)
            else:
                m = await bot.send_video(chat_id=user_id, video=media, caption=text, **kw)
            sent.append(m)
        return sent

    if fmt == "document+text":
        if text:
            m1 = await bot.send_message(
                chat_id=user_id,
                text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
                **kw,
            )
            sent.append(m1)
        m2 = await bot.send_document(chat_id=user_id, document=media, **kw)
        sent.append(m2)
        return sent

    # Fallback : format inconnu → traité comme text
    m = await bot.send_message(
        chat_id=user_id,
        text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
        **kw,
    )
    sent.append(m)
    return sent


def _extract_file_id(msg, fmt: str) -> Optional[str]:
    """Récupère le file_id Telegram après upload initial."""
    if msg is None:
        return None
    try:
        if fmt in ("image", "image+text") and getattr(msg, "photo", None):
            return msg.photo[-1].file_id
        if fmt in ("video", "video+text") and getattr(msg, "video", None):
            return msg.video.file_id
        if fmt in ("document", "document+text") and getattr(msg, "document", None):
            return msg.document.file_id
    except Exception:
        pass
    return None


async def _delete_sent_messages(bot, user_id: int, messages: list) -> None:
    """
    Supprime tous les messages que l'on vient d'envoyer côté user.
    Best-effort : chaque échec est loggé en debug (non bloquant).
    """
    for m in messages:
        try:
            await bot.delete_message(chat_id=user_id, message_id=m.message_id)
        except Exception as e:
            logger.debug(
                f"[cleanup] delete_message échoué uid={user_id} "
                f"mid={getattr(m, 'message_id', '?')} : {e}"
            )


async def _handle_user_error(
    ctx: BroadcastContext,
    user_id: int,
    exc: Exception,
    worker_id: int,
) -> None:
    """
    Classifie l'erreur, l'enregistre, et en mode nettoyage :
    purge immédiate du user en DB si blocked ou deleted.
    """
    cat = classify(exc)
    ctx.errors_by_category[cat].append({
        "telegram_id":   user_id,
        "error_type":    type(exc).__name__,
        "error_message": str(exc)[:500],
        "date":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "broadcast_tag": ctx.tag,
    })
    ctx.errors += 1
    logger.debug(
        f"[worker {worker_id}] échec uid={user_id} cat={cat.value} "
        f"err={type(exc).__name__}"
    )

    # ── MODE NETTOYAGE : purge immédiate si user injoignable ──────────
    if ctx.cleanup_mode and cat in (ErrorCategory.BLOCKED, ErrorCategory.DELETED):
        users_del, cats_del = await delete_user_immediately(user_id)
        if users_del > 0:
            ctx.purged += 1
            logger.info(
                f"[worker {worker_id}] purge uid={user_id} "
                f"(cat={cat.value}, cats_rows={cats_del})"
            )


async def _send_one(ctx: BroadcastContext, user_id: int, worker_id: int) -> None:
    """
    Envoie UN message. Applique le rate limit, gère RetryAfter (retry cette
    fois-ci uniquement, jamais pour les autres erreurs).
    """
    personalized = inject_variables(ctx.message, user_id, ctx.prenoms, ctx.variables)

    # Boucle interne UNIQUEMENT pour re-tenter après RetryAfter (règle brief).
    while True:
        await ctx.limiter.acquire()

        # ── Détermine si CE worker doit uploader (le premier arrivé sur un
        #    média local sans cache), et prépare le paramètre media. ────────
        uploading_locally = False
        media_param = None

        if ctx.fmt in _MEDIA_FORMATS and ctx.media_url:
            if ctx.cached_file_id:
                media_param = ctx.cached_file_id
            elif not ctx.is_local_media:
                media_param = ctx.media_url  # URL distante, Telegram la fetch
            else:
                # Fichier local + pas de cache : on sérialise, un seul worker
                # uploade, les autres attendent puis récupèrent le file_id.
                async with ctx.first_upload_lock:
                    if ctx.cached_file_id:
                        media_param = ctx.cached_file_id
                    else:
                        try:
                            media_param = open(ctx.media_url, "rb")
                            uploading_locally = True
                        except OSError as e:
                            await _handle_user_error(ctx, user_id, e, worker_id)
                            return

                    if uploading_locally:
                        try:
                            try:
                                sent_messages = await _do_send(
                                    ctx.bot, user_id, ctx.fmt, personalized,
                                    media_param, silent=ctx.silent,
                                )
                            finally:
                                try:
                                    media_param.close()
                                except Exception:
                                    pass

                            # Extract file_id du dernier message (le média)
                            fid = None
                            for m in reversed(sent_messages):
                                fid = _extract_file_id(m, ctx.fmt)
                                if fid:
                                    break
                            if fid:
                                ctx.cached_file_id = fid
                                ctx.file_id_ready.set()
                                asyncio.create_task(
                                    media_cache.store_file_id(ctx.media_url, ctx.fmt, fid)
                                )

                            # Mode nettoyage : supprime le message après confirmation
                            if ctx.cleanup_mode and sent_messages:
                                await _delete_sent_messages(ctx.bot, user_id, sent_messages)

                            ctx.sent += 1
                            await ctx.limiter.notify_success()
                            return

                        except RetryAfter as e:
                            wait = float(getattr(e, "retry_after", 5) or 5)
                            logger.warning(
                                f"[worker {worker_id}] RetryAfter {wait}s pendant upload initial"
                            )
                            await ctx.limiter.notify_retry_after(wait)
                            continue

                        except Exception as e:
                            await _handle_user_error(ctx, user_id, e, worker_id)
                            return

        # ── Chemin normal : file_id cached OU URL distante OU format text ──
        try:
            sent_messages = await _do_send(
                ctx.bot, user_id, ctx.fmt, personalized, media_param,
                silent=ctx.silent,
            )

            # Mode nettoyage : supprime le message après confirmation d'envoi
            if ctx.cleanup_mode and sent_messages:
                await _delete_sent_messages(ctx.bot, user_id, sent_messages)

            ctx.sent += 1
            await ctx.limiter.notify_success()
            return

        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", 5) or 5)
            logger.warning(f"[worker {worker_id}] RetryAfter {wait}s (uid={user_id})")
            await ctx.limiter.notify_retry_after(wait)
            continue

        except Exception as e:
            await _handle_user_error(ctx, user_id, e, worker_id)
            return


# ══════════════════════════════════════════════════════════════════════════════
# WORKER LOOP
# ══════════════════════════════════════════════════════════════════════════════

async def worker_loop(
    worker_id: int,
    queue: asyncio.Queue,
    ctx: BroadcastContext,
) -> None:
    """
    Boucle worker : consomme la queue jusqu'à recevoir None (sentinel).
    """
    while True:
        try:
            item = await queue.get()
        except asyncio.CancelledError:
            return
        try:
            if item is None:
                return
            try:
                await _send_one(ctx, int(item), worker_id)
            except Exception as e:
                # Filet de sécurité : _send_one capture normalement tout.
                logger.exception(f"[worker {worker_id}] erreur non gérée uid={item} : {e}")
                ctx.errors += 1
                ctx.errors_by_category[ErrorCategory.UNKNOWN].append({
                    "telegram_id":   int(item),
                    "error_type":    type(e).__name__,
                    "error_message": str(e)[:500],
                    "date":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "broadcast_tag": ctx.tag,
                })
        finally:
            queue.task_done()