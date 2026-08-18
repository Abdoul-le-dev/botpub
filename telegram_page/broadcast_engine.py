"""
broadcast_engine.py — v2 (refactor moteur haute performance)

════════════════════════════════════════════════════════════════════════════════
COMPATIBILITÉ
════════════════════════════════════════════════════════════════════════════════

Cette version conserve intégralement la signature publique de la v1 :

    async def broadcast_engine(bot, payload: dict) -> dict

Le dict `payload` accepte exactement les mêmes clés :
    message, format, media_url, category, user_ids, scheduled_at, delay,
    retry, exclude_user_ids, variables, filters, tag, callback_url

Le dict retourné contient toujours au minimum :
    tag, total, sent, errors, started_at, finished_at

Il expose maintenant EN PLUS :
    blocked, deleted, network_errors, flood_errors, unknown_errors,
    duration_seconds, success_rate, average_msg_per_second,
    max_msg_per_second, min_msg_per_second

La constante `ADMIN_ID` est toujours exportée pour les modules externes qui
l'importaient directement.

════════════════════════════════════════════════════════════════════════════════
CE QUI CHANGE EN INTERNE
════════════════════════════════════════════════════════════════════════════════

  * Envoi concurrent via asyncio.Queue + N workers (config.NUM_WORKERS).
  * Rate limiter GLOBAL adaptatif (AIMD) partagé entre broadcasts concurrents :
      - démarre à 29 msg/s
      - descend à 25 msg/s sur RetryAfter (avec pause globale)
      - remonte progressivement vers 30 msg/s sur streak de succès
  * ZERO retry utilisateur (le paramètre `retry` du payload est ignoré,
    seule une info est loguée s'il vaut True).
  * Le seul retry autorisé est post-RetryAfter, sur le message qui l'a
    déclenché.
  * Personnalisation `+prenom` optimisée : pré-chargée en batch en début de
    broadcast si et seulement si le message contient `+prenom`.
  * Cache file_id Telegram persistant en DB (table broadcast_media_cache).
  * Classification centralisée des erreurs (blocked / deleted / network /
    flood / unknown) + génération automatique de CSV par catégorie.
  * Rapport final texte + fichiers CSV envoyés à tous les admins.
  * Proposition automatique de nettoyage DB si blocked/deleted détectés
    (boutons ✅ Supprimer / ❌ Ignorer — handlers à enregistrer via
    `register_broadcast_admin_handlers(app)` dans main.py).

════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ── Imports internes du package broadcast ────────────────────────────────────
from broadcast import config
from broadcast import media_cache
from broadcast.cleanup import propose_cleanup
from broadcast.cleanup_mode import is_cleanup_mode, strip_cleanup_token
from broadcast.error_classifier import ErrorCategory
from broadcast.rate_limiter import get_global_limiter
from broadcast.recipients import (
    batch_fetch_prenoms,
    needs_prenom_lookup,
    resolve_user_ids,
)
from broadcast.reports import (
    delete_csv_reports,
    format_admin_report,
    generate_csv_reports,
    save_broadcast_history,
    save_broadcast_stats,
)
from broadcast.worker import BroadcastContext, worker_loop

# ── Compat : constante exportée que d'anciens modules importent peut-être ────
ADMIN_ID: int = config.ADMIN_ID

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_MEDIA_FORMATS = {"image", "video", "document", "image+text", "video+text", "document+text"}


def _is_local_file(media_url: str) -> bool:
    """Réplique la logique v1 : chemin absolu, ./ ou existant sur disque = local."""
    if not media_url:
        return False
    return (
        media_url.startswith("/")
        or media_url.startswith("./")
        or Path(media_url).exists()
    )


def _normalize_local_path(media_url: str) -> str:
    """
    v1 faisait `media_url.lstrip("/")` — on garde ce comportement pour compat
    (les fichiers étaient stockés en relatif au working dir du bot).
    """
    if not media_url:
        return media_url
    # On garde la logique v1 : strip du "/" initial UNIQUEMENT s'il ne s'agit
    # pas d'un chemin absolu qui existe réellement.
    stripped = media_url.lstrip("/")
    if Path(stripped).exists():
        return stripped
    # Sinon on garde le chemin d'origine
    return media_url


def _limit_text(text: str, max_length: int = config.TG_MAX_MESSAGE_LEN) -> str:
    if not text:
        return text
    return text[: max_length - 1] + "…" if len(text) > max_length else text


async def _notify_admins(bot, text: str) -> None:
    """Envoi info à TOUS les admins configurés (best-effort)."""
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning(f"[notify] échec admin {admin_id} : {e}")


async def _notify_admin(bot, admin_id: int, message: str) -> None:
    """
    COMPAT v1 : ancienne signature à 3 args (bot, admin_id, message).
    Conservée pour les modules externes (subscription.py, etc.) qui
    l'importaient depuis broadcast_engine.
    """
    try:
        await bot.send_message(chat_id=admin_id, text=message)
    except Exception as e:
        logger.warning(f"[notify_admin] échec {admin_id} : {e}")


async def _call_webhook(callback_url: str, report: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(callback_url, json=report)
    except Exception as e:
        logger.error(f"[webhook] échec ({callback_url}) : {e}")


async def _send_csv_files(bot, csv_paths: dict[ErrorCategory, Path], tag: str) -> None:
    """Envoie les CSV à tous les admins, puis les supprime."""
    if not csv_paths:
        return

    tag_prefix = f"[{tag}] " if tag else ""

    for cat, path in csv_paths.items():
        caption = f"📎 {tag_prefix}{cat.value} — {path.name}"
        for admin_id in config.ADMIN_IDS:
            try:
                with path.open("rb") as f:
                    await bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        filename=path.name,
                        caption=caption,
                    )
            except Exception as e:
                logger.warning(f"[csv] échec envoi {path.name} à {admin_id} : {e}")

    # Suppression après envoi (règle : delete after send)
    delete_csv_reports(csv_paths)


# ══════════════════════════════════════════════════════════════════════════════
# MOTEUR PRINCIPAL — POINT D'ENTRÉE PUBLIC
# ══════════════════════════════════════════════════════════════════════════════

async def broadcast_engine(bot, payload: dict) -> dict:
    """
    Point d'entrée public. Signature et contrat inchangés vs v1.
    """
    broadcast_id = uuid.uuid4().hex[:8]

    # ── 1. Extraction & validation du payload ────────────────────────────────
    message: str          = _limit_text(payload.get("message", "") or "")
    fmt: str              = payload.get("format", "text")
    media_url: Optional[str] = payload.get("media_url")
    category: Optional[str]  = payload.get("category")
    user_ids                 = payload.get("user_ids")
    scheduled_at             = payload.get("scheduled_at")
    exclude_user_ids         = payload.get("exclude_user_ids") or []
    variables                = payload.get("variables") or {}
    filters                  = payload.get("filters") or {}
    tag: str                 = payload.get("tag", "") or ""
    callback_url             = payload.get("callback_url")

    # Ces deux clés sont acceptées pour compat mais ignorées : le rate limiter
    # gère la cadence, et le brief impose zéro retry utilisateur.
    if payload.get("retry"):
        logger.info(f"[{tag}] payload.retry=True ignoré (règle : aucun retry user)")
    if payload.get("delay") is not None:
        logger.debug(f"[{tag}] payload.delay ignoré (rate limiter global actif)")

    # ── Détection MODE NETTOYAGE (analogue à +prenom) ────────────────────────
    # Si le message contient +nettoyage, on entre en mode vérification :
    #   * envoi silencieux (disable_notification=True)
    #   * token retiré du message avant envoi
    #   * rapport final formulé "Rapport nettoyage"
    #   * erreurs network exclues de la proposition de suppression
    cleanup_mode = is_cleanup_mode(message)
    if cleanup_mode:
        message = strip_cleanup_token(message)
        logger.info(f"[{tag}] MODE NETTOYAGE activé (+nettoyage détecté)")

    if not message and fmt == "text":
        return {"error": "message vide"}

    if fmt in _MEDIA_FORMATS and not media_url:
        return {"error": "media_url manquant"}

    if not category and not user_ids:
        return {"error": "aucun destinataire défini"}

    # ── 2. Envoi différé ─────────────────────────────────────────────────────
    if scheduled_at:
        try:
            target_dt = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M:%S")
            wait_sec = (target_dt - datetime.now()).total_seconds()
            if wait_sec > 0:
                tag_prefix = f"[{tag}] " if tag else ""
                await _notify_admins(
                    bot,
                    f"⏳ {tag_prefix}Envoi planifié dans {round(wait_sec / 60, 1)} min "
                    f"({scheduled_at})",
                )
                await asyncio.sleep(wait_sec)
        except ValueError:
            return {"error": "format scheduled_at invalide, utilise YYYY-MM-DD HH:MM:SS"}

    # ── 3. Résolution destinataires ──────────────────────────────────────────
    final_ids = await resolve_user_ids(category, user_ids, exclude_user_ids, filters)
    total = len(final_ids)

    if total == 0:
        await _notify_admins(bot, "❌ Aucun destinataire trouvé. Diffusion annulée.")
        return {"error": "aucun destinataire trouvé", "tag": tag}

    # ── 4. Pré-chargement des prénoms (uniquement si +prenom présent) ────────
    prenoms: dict[int, str] = {}
    if needs_prenom_lookup(message):
        logger.info(f"[{tag}] +prenom détecté → pré-chargement des prénoms ({total} users)")
        prenoms = await batch_fetch_prenoms(final_ids)
    else:
        logger.info(f"[{tag}] pas de +prenom → aucune requête SQL de personnalisation")

    # ── 5. Résolution du média : cache file_id Telegram si dispo ─────────────
    is_local_media = False
    effective_media_url = media_url
    initial_cached_file_id: Optional[str] = None

    if fmt in _MEDIA_FORMATS and media_url:
        normalized = _normalize_local_path(media_url)
        is_local_media = _is_local_file(normalized) or _is_local_file(media_url)

        if is_local_media:
            # Le chemin canonique utilisé pour le cache
            effective_media_url = normalized if Path(normalized).exists() else media_url
            initial_cached_file_id = await media_cache.get_cached_file_id(
                effective_media_url, fmt
            )
            if initial_cached_file_id:
                logger.info(
                    f"[{tag}] cache HIT — file_id réutilisé pour {effective_media_url}"
                )
                # touch async, non bloquant
                asyncio.create_task(
                    media_cache.touch_file_id(effective_media_url, fmt)
                )
        else:
            effective_media_url = media_url  # URL externe, telle quelle

    # ── 6. Démarrage : notification admin ────────────────────────────────────
    started_at_dt = datetime.now()
    started_at = started_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    tag_prefix = f"[{tag}] " if tag else ""

    await _notify_admins(
        bot,
        f"📤 {tag_prefix}Broadcast démarré\n"
        f"Destinataires : {total}\nFormat : {fmt}",
    )

    # ── 7. Setup du contexte + rate limiter global ───────────────────────────
    limiter = await get_global_limiter()
    ctx = BroadcastContext(
        bot=bot,
        fmt=fmt,
        message=message,
        media_url=effective_media_url,
        is_local_media=is_local_media,
        prenoms=prenoms,
        variables=variables,
        tag=tag,
        limiter=limiter,
        cleanup_mode=cleanup_mode,  # mode nettoyage → silent + delete-msg + purge live
    )
    if initial_cached_file_id:
        ctx.cached_file_id = initial_cached_file_id
        ctx.file_id_ready.set()

    # ── 8. Démarrage workers + feeder + progress monitor ─────────────────────
    queue: asyncio.Queue = asyncio.Queue(maxsize=config.QUEUE_MAXSIZE)

    workers = [
        asyncio.create_task(worker_loop(i, queue, ctx))
        for i in range(config.NUM_WORKERS)
    ]

    # Progress monitor : notifie l'admin à chaque palier configuré (50%)
    async def _progress_monitor() -> None:
        already_notified: set[int] = set()
        while True:
            await asyncio.sleep(5)
            done = ctx.sent + ctx.errors
            pct = (done * 100) // total if total > 0 else 100
            for threshold in config.PROGRESS_NOTIFY_PERCENTS:
                if pct >= threshold and threshold not in already_notified:
                    already_notified.add(threshold)
                    await _notify_admins(
                        bot,
                        f"📊 {tag_prefix}{threshold}% de la diffusion atteints "
                        f"({done}/{total} — {ctx.sent} OK, {ctx.errors} err — "
                        f"{limiter.current_rate:.1f} msg/s)",
                    )
            if done >= total:
                return

    progress_task = asyncio.create_task(_progress_monitor())

    # Feeder : push tous les IDs dans la queue, puis les sentinels d'arrêt
    for uid in final_ids:
        await queue.put(uid)
    for _ in range(config.NUM_WORKERS):
        await queue.put(None)

    # Attendre la fin de tous les workers
    await asyncio.gather(*workers, return_exceptions=True)
    progress_task.cancel()
    try:
        await progress_task
    except (asyncio.CancelledError, Exception):
        pass

    # ── 9. Calcul des métriques finales ──────────────────────────────────────
    finished_at_dt = datetime.now()
    finished_at = finished_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    duration = max((finished_at_dt - started_at_dt).total_seconds(), 0.001)

    blocked = len(ctx.errors_by_category[ErrorCategory.BLOCKED])
    deleted = len(ctx.errors_by_category[ErrorCategory.DELETED])
    network = len(ctx.errors_by_category[ErrorCategory.NETWORK])
    flood   = len(ctx.errors_by_category[ErrorCategory.FLOOD])
    unknown = len(ctx.errors_by_category[ErrorCategory.UNKNOWN])

    metrics_snap = limiter.metrics.snapshot(duration)
    avg_rate = ctx.sent / duration if duration > 0 else 0.0
    success_rate = (ctx.sent * 100.0 / total) if total > 0 else 0.0

    stats: dict = {
        "tag":                    tag,
        "category":               category or "",
        "format":                 fmt,
        "total":                  total,
        "sent":                   ctx.sent,
        "errors":                 ctx.errors,
        "blocked":                blocked,
        "deleted":                deleted,
        "network_errors":         network,
        "flood_errors":           flood,
        "unknown_errors":         unknown,
        "started_at":             started_at,
        "finished_at":            finished_at,
        "duration_seconds":       int(duration),
        "success_rate":           round(success_rate, 2),
        "average_msg_per_second": round(avg_rate, 2),
        "max_msg_per_second":     metrics_snap["max_rate"],
        "min_msg_per_second":     metrics_snap["min_rate"],
        # Mode nettoyage : nb d'utilisateurs purgés en temps réel
        "purged":                 ctx.purged,
    }

    # ── 10. Génération et envoi des CSV ──────────────────────────────────────
    csv_paths = generate_csv_reports(ctx.errors_by_category, tag, broadcast_id)

    # ── 11. Notification finale admin ────────────────────────────────────────
    await _notify_admins(bot, format_admin_report(stats, cleanup_mode=cleanup_mode))

    # Envoi CSV en tâche de fond (les CSV sont supprimés après envoi)
    if csv_paths:
        asyncio.create_task(_send_csv_files(bot, csv_paths, tag))

    # ── 12. Proposition de nettoyage DB (UNIQUEMENT si PAS en mode nettoyage)
    #     En mode nettoyage, la purge est déjà faite en live par les workers.
    if not cleanup_mode and (blocked > 0 or deleted > 0):
        blocked_ids = [e["telegram_id"] for e in ctx.errors_by_category[ErrorCategory.BLOCKED]]
        deleted_ids = [e["telegram_id"] for e in ctx.errors_by_category[ErrorCategory.DELETED]]
        asyncio.create_task(
            propose_cleanup(bot, blocked_ids, deleted_ids, tag, cleanup_mode=False)
        )

    # ── 13. Persistance DB (historique + stats détaillées) ──────────────────
    await save_broadcast_history(
        tag=tag,
        category=category or "",
        fmt=fmt,
        message=message,
        total=total,
        sent=ctx.sent,
        errors=ctx.errors,
        started_at=started_at,
        finished_at=finished_at,
    )
    await save_broadcast_stats(stats)

    # ── 14. Webhook externe ──────────────────────────────────────────────────
    if callback_url:
        asyncio.create_task(_call_webhook(callback_url, stats))

    # ── 15. Rapport de retour (compat v1 + nouveaux champs) ──────────────────
    return stats