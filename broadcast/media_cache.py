"""
broadcast/media_cache.py — cache persistant des file_id Telegram.

Objectif : ne JAMAIS réuploader un média déjà connu, y compris entre broadcasts
successifs. Un même fichier local peut avoir des file_id différents selon le
format (photo vs document), donc la clé est (path, format).

Deux niveaux :
  - RAM (dict process-level) : lookup O(1), rechargé à chaud
  - DB (table broadcast_media_cache) : persistance entre redémarrages

Utilisation typique dans le moteur :
    cached = await get_cached_file_id(path, fmt)
    if not cached:
        # premier envoi : upload du fichier local
        file_id = await do_upload(...)
        await store_file_id(path, fmt, file_id)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from db import get_db

logger = logging.getLogger(__name__)


# Cache RAM — chargé paresseusement depuis la DB au premier accès.
_ram_cache: dict[tuple[str, str], str] = {}
_ram_lock = asyncio.Lock()
_ram_loaded: bool = False


async def _load_from_db() -> None:
    """Charge tout le cache DB en RAM. Idempotent."""
    global _ram_loaded
    async with _ram_lock:
        if _ram_loaded:
            return
        try:
            async with get_db() as cur:
                await cur.execute(
                    "SELECT local_path, format, telegram_file_id FROM broadcast_media_cache"
                )
                rows = await cur.fetchall()
            for r in rows:
                key = (r["local_path"], r["format"])
                _ram_cache[key] = r["telegram_file_id"]
            logger.info(f"[media_cache] {len(_ram_cache)} entrée(s) chargée(s) depuis la DB")
        except Exception as e:
            # Table peut ne pas exister encore (migration non appliquée) : on log
            # et on continue avec un cache vide. Le moteur uploadera normalement.
            logger.warning(f"[media_cache] échec chargement DB (cache vide) : {e}")
        _ram_loaded = True


async def get_cached_file_id(local_path: str, fmt: str) -> Optional[str]:
    """
    Retourne le file_id Telegram déjà connu pour (path, format), ou None.
    Charge le cache DB à la volée si pas encore fait.
    """
    if not local_path:
        return None
    await _load_from_db()
    return _ram_cache.get((local_path, fmt))


async def store_file_id(local_path: str, fmt: str, file_id: str) -> None:
    """Enregistre un nouveau file_id en RAM ET en DB (upsert)."""
    if not local_path or not file_id:
        return

    key = (local_path, fmt)
    # RAM d'abord (jamais bloquant)
    _ram_cache[key] = file_id

    # DB ensuite (best-effort, on ne bloque pas le broadcast si ça foire)
    try:
        now = datetime.now()
        async with get_db() as cur:
            await cur.execute(
                """
                INSERT INTO broadcast_media_cache
                    (local_path, format, telegram_file_id, created_at, last_used_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    telegram_file_id = VALUES(telegram_file_id),
                    last_used_at     = VALUES(last_used_at)
                """,
                (local_path, fmt, file_id, now, now),
            )
        logger.info(f"[media_cache] file_id persisté : {local_path} [{fmt}] → {file_id[:20]}…")
    except Exception as e:
        logger.warning(f"[media_cache] échec persistance ({local_path}) : {e}")


async def touch_file_id(local_path: str, fmt: str) -> None:
    """Met à jour last_used_at (best-effort, non bloquant)."""
    if not local_path:
        return
    try:
        async with get_db() as cur:
            await cur.execute(
                "UPDATE broadcast_media_cache SET last_used_at=%s "
                "WHERE local_path=%s AND format=%s",
                (datetime.now(), local_path, fmt),
            )
    except Exception:
        pass  # silencieux : c'est juste de la télémétrie
