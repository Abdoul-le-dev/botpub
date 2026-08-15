"""
broadcast/worker.py — logique d'envoi unitaire + worker asynchrone.

Un worker consomme la queue et envoie un message à la fois. La cadence est
imposée par le rate limiter global (rate_limiter.py) : les workers passent
l'essentiel de leur temps dans `await limiter.acquire()`.

Politique d'erreur (par le brief) :
  - RetryAfter : PAUSE globale + retry de CE message uniquement (pas d'échec).
  - Toute autre erreur : classification + collecte, PAS de retry, passer au suivant.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram.error import RetryAfter, TelegramError

from . import config, media_cache
from .error_classifier import ErrorCategory, classify
from .rate_limiter import AdaptiveRateLimiter
from .recipients import inject_variables

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTE PARTAGÉ PAR TOUS LES WORKERS D'UN BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

class BroadcastContext:
    """
    État mutable partagé entre le feeder et les N workers d'UN broadcast.
    Les compteurs sont modifiés sans lock : Python + asyncio single-threaded
    garantit qu'un `+= 1` est atomique tant qu'on ne yield pas au milieu.
    """

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

        # Media : file_id Telegram réutilisable une fois obtenu
        self.cached_file_id: Optional[str] = None
        # Événement signalant que le premier upload est terminé (workers attendent)
        self.file_id_ready = asyncio.Event()
        # Lock qui sérialise le PREMIER upload d'un fichier local : sans lui,
        # 32 workers uploaderaient le même fichier en parallèle.
        self.first_upload_lock = asyncio.Lock()

        # Compteurs
        self.sent: int = 0
        self.errors: int = 0

        # Collecte d'erreurs par catégorie
        self.errors_by_category: dict[ErrorCategory, list[dict]] = {
            cat: [] for cat in ErrorCategory
        }


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI UNITAIRE
# ══════════════════════════════════════════════════════════════════════════════

# Formats qui portent un média
_MEDIA_FORMATS = {"image", "video", "document", "image+text", "video+text", "document+text"}
_FORMATS_WITH_CAPTION = {"image+text", "video+text"}  # document+text est traité séparément


def _clip_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


async def _do_send(bot, user_id: int, fmt: str, text: str, media) -> Optional[object]:
    """
    Effectue l'appel Telegram brut. Retourne l'objet Message renvoyé par PTB
    (utile pour extraire le file_id après upload), ou None pour un envoi texte.

    Gestion des captions :
      - Si texte ≤ TG_CAPTION_SAFE_LEN → caption sur le média.
      - Sinon → texte envoyé en message séparé + média sans caption.
    Le brief impose de ne jamais dépasser la limite Telegram.
    """
    if fmt == "text":
        await bot.send_message(chat_id=user_id, text=_clip_text(text, config.TG_MAX_MESSAGE_LEN))
        return None

    if fmt == "image":
        return await bot.send_photo(chat_id=user_id, photo=media)

    if fmt == "video":
        return await bot.send_video(chat_id=user_id, video=media)

    if fmt == "document":
        return await bot.send_document(chat_id=user_id, document=media)

    if fmt in _FORMATS_WITH_CAPTION:
        # image+text ou video+text
        if len(text) > config.TG_CAPTION_SAFE_LEN:
            await bot.send_message(
                chat_id=user_id,
                text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
            )
            if fmt == "image+text":
                return await bot.send_photo(chat_id=user_id, photo=media)
            return await bot.send_video(chat_id=user_id, video=media)
        else:
            if fmt == "image+text":
                return await bot.send_photo(chat_id=user_id, photo=media, caption=text)
            return await bot.send_video(chat_id=user_id, video=media, caption=text)

    if fmt == "document+text":
        # Document : historiquement le texte est envoyé séparément (compat v1)
        if text:
            await bot.send_message(
                chat_id=user_id,
                text=_clip_text(text, config.TG_MAX_MESSAGE_LEN),
            )
        return await bot.send_document(chat_id=user_id, document=media)

    # Fallback : format inconnu → traité comme text
    await bot.send_message(chat_id=user_id, text=_clip_text(text, config.TG_MAX_MESSAGE_LEN))
    return None


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


async def _prepare_media_for_send(ctx: BroadcastContext, worker_id: int) -> object:
    """
    Retourne le paramètre à passer à Telegram pour le média :
      - Un file_id str si déjà connu (cache RAM ou déjà uploadé).
      - Une URL http(s) si media_url est une URL externe (Telegram la fetch).
      - Un file handle si c'est le PREMIER upload local (un seul worker le fait,
        les autres attendent l'event).
    """
    if ctx.fmt not in _MEDIA_FORMATS or not ctx.media_url:
        return None

    # Déjà cached en RAM (broadcast en cours)
    if ctx.cached_file_id:
        return ctx.cached_file_id

    if not ctx.is_local_media:
        # URL distante : Telegram la télécharge, pas besoin de gérer localement
        return ctx.media_url

    # Fichier local → premier envoi doit uploader.
    # Un seul worker fait l'upload ; les autres attendent l'événement.
    if not ctx.file_id_ready.is_set():
        # Le worker qui arrive ici en premier va uploader.
        # Le lock implicite : on renvoie None spécial pour signaler
        # "toi tu uploades". Simplification : on utilise une race sur l'event.
        # Approche propre : lock dédié.
        pass

    return None  # signal : uploader depuis ctx.media_url


async def _send_one(ctx: BroadcastContext, user_id: int, worker_id: int) -> None:
    """
    Envoie UN message. Applique le rate limit, gère RetryAfter (retry cette
    fois-ci uniquement, jamais pour les autres erreurs).
    """
    personalized = inject_variables(ctx.message, user_id, ctx.prenoms, ctx.variables)

    # Boucle interne UNIQUEMENT pour re-tenter après RetryAfter (règle brief).
    # Aucun autre type d'erreur ne provoque de retry.
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
                            # Fichier introuvable → on log, on marque en unknown,
                            # on ne retente pas.
                            cat = classify(e)
                            ctx.errors_by_category[cat].append({
                                "telegram_id":   user_id,
                                "error_type":    type(e).__name__,
                                "error_message": f"média local introuvable: {e}",
                                "date":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "broadcast_tag": ctx.tag,
                            })
                            ctx.errors += 1
                            return

                    # Si on est uploader, on GARDE le lock jusqu'à ce que
                    # l'upload soit terminé et le file_id caché : sinon un
                    # autre worker verrait cached_file_id encore vide et
                    # ré-uploaderait aussi.
                    if uploading_locally:
                        try:
                            try:
                                msg = await _do_send(
                                    ctx.bot, user_id, ctx.fmt, personalized, media_param
                                )
                            finally:
                                try:
                                    media_param.close()
                                except Exception:
                                    pass

                            fid = _extract_file_id(msg, ctx.fmt)
                            if fid:
                                ctx.cached_file_id = fid
                                ctx.file_id_ready.set()
                                asyncio.create_task(
                                    media_cache.store_file_id(ctx.media_url, ctx.fmt, fid)
                                )

                            ctx.sent += 1
                            await ctx.limiter.notify_success()
                            return

                        except RetryAfter as e:
                            wait = float(getattr(e, "retry_after", 5) or 5)
                            logger.warning(
                                f"[worker {worker_id}] RetryAfter {wait}s pendant upload initial"
                            )
                            await ctx.limiter.notify_retry_after(wait)
                            # Sort du lock, retentera au prochain tour de boucle
                            continue

                        except Exception as e:
                            cat = classify(e)
                            ctx.errors_by_category[cat].append({
                                "telegram_id":   user_id,
                                "error_type":    type(e).__name__,
                                "error_message": str(e)[:500],
                                "date":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "broadcast_tag": ctx.tag,
                            })
                            ctx.errors += 1
                            return

        # ── Chemin normal : file_id cached OU URL distante OU format text ──
        try:
            msg = await _do_send(ctx.bot, user_id, ctx.fmt, personalized, media_param)
            ctx.sent += 1
            await ctx.limiter.notify_success()
            return

        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", 5) or 5)
            logger.warning(f"[worker {worker_id}] RetryAfter {wait}s (uid={user_id})")
            await ctx.limiter.notify_retry_after(wait)
            continue

        except Exception as e:
            cat = classify(e)
            ctx.errors_by_category[cat].append({
                "telegram_id":   user_id,
                "error_type":    type(e).__name__,
                "error_message": str(e)[:500],
                "date":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "broadcast_tag": ctx.tag,
            })
            ctx.errors += 1
            logger.debug(
                f"[worker {worker_id}] échec uid={user_id} cat={cat.value} err={type(e).__name__}"
            )
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
    Chaque item de la queue est un telegram_id (int).
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
                # Filet de sécurité : ne doit jamais arriver, _send_one
                # capture tout. Mais si oui, on ne veut pas tuer le worker.
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
