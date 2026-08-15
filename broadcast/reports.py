"""
broadcast/reports.py — génération CSV + persistance stats + rapport texte final.

Deux tables sont écrites à la fin d'un broadcast :
  - `broadcast_history`  (existante — conservée pour compat externe)
  - `broadcast_stats`    (nouvelle — métriques détaillées)

Et jusqu'à 4 fichiers CSV sont générés dans REPORTS_DIR puis envoyés à l'admin
avant d'être supprimés.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from db import get_db

from . import config
from .error_classifier import ErrorCategory

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURE D'UNE ENTRÉE D'ERREUR
# ══════════════════════════════════════════════════════════════════════════════
# Chaque erreur est stockée en RAM pendant le broadcast sous la forme :
# {
#     "telegram_id":    int,
#     "error_type":     str (nom de l'exception : "Forbidden", "BadRequest", ...)
#     "error_message":  str,
#     "date":           "YYYY-MM-DD HH:MM:SS",
#     "broadcast_tag":  str,
# }
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# CSV
# ══════════════════════════════════════════════════════════════════════════════

_CSV_HEADERS = ["telegram_id", "error_type", "error_message", "date", "broadcast_tag"]

# Mapping catégorie → nom de fichier (pas de fichier pour FLOOD : c'est du bruit
# transitoire géré par le rate limiter, pas une info admin).
_CSV_FILE_BY_CATEGORY: dict[ErrorCategory, str] = {
    ErrorCategory.BLOCKED: "blocked_users.csv",
    ErrorCategory.DELETED: "deleted_users.csv",
    ErrorCategory.NETWORK: "network_errors.csv",
    ErrorCategory.UNKNOWN: "unknown_errors.csv",
}


def _safe_tag_slug(tag: str) -> str:
    """Rend un tag safe pour un nom de fichier."""
    if not tag:
        return "no_tag"
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in tag)[:60]


def generate_csv_reports(
    errors_by_category: dict[ErrorCategory, list[dict]],
    tag: str,
    broadcast_id: str,
) -> dict[ErrorCategory, Path]:
    """
    Écrit un CSV par catégorie NON vide (hors FLOOD) et retourne le mapping
    {catégorie: chemin_fichier}. Les fichiers vont dans config.REPORTS_DIR
    dans un sous-dossier unique par broadcast, pour ne pas mélanger deux
    diffusions concurrentes.
    """
    slug = _safe_tag_slug(tag)
    subdir = config.REPORTS_DIR / f"{slug}_{broadcast_id}"
    subdir.mkdir(parents=True, exist_ok=True)

    generated: dict[ErrorCategory, Path] = {}

    for cat, filename in _CSV_FILE_BY_CATEGORY.items():
        entries = errors_by_category.get(cat) or []
        if not entries:
            continue
        path = subdir / filename
        try:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
                writer.writeheader()
                for row in entries:
                    writer.writerow({h: row.get(h, "") for h in _CSV_HEADERS})
            generated[cat] = path
        except Exception as e:
            logger.exception(f"[csv] échec écriture {path} : {e}")

    return generated


def delete_csv_reports(paths: dict[ErrorCategory, Path]) -> None:
    """Supprime tous les CSV listés. Best-effort, silencieux."""
    parent_dirs = set()
    for p in paths.values():
        try:
            if p.exists():
                p.unlink()
            parent_dirs.add(p.parent)
        except Exception as e:
            logger.warning(f"[csv] échec suppression {p} : {e}")
    # Supprime aussi le sous-dossier s'il est vide
    for d in parent_dirs:
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTANCE DB
# ══════════════════════════════════════════════════════════════════════════════

async def save_broadcast_history(
    *,
    tag: str,
    category: str,
    fmt: str,
    message: str,
    total: int,
    sent: int,
    errors: int,
    started_at: str,
    finished_at: str,
) -> None:
    """Écrit la ligne dans broadcast_history (table existante, compat)."""
    try:
        async with get_db() as cur:
            await cur.execute(
                """
                INSERT INTO broadcast_history
                    (tag, category, format, message, total, sent, errors, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tag, category, fmt, message, total, sent, errors, started_at, finished_at),
            )
    except Exception as e:
        logger.exception(f"[history] échec insertion : {e}")


async def save_broadcast_stats(stats: dict) -> None:
    """
    Écrit la ligne détaillée dans la nouvelle table broadcast_stats.
    Silencieux si la table n'existe pas encore (migration non appliquée).
    """
    try:
        async with get_db() as cur:
            await cur.execute(
                """
                INSERT INTO broadcast_stats (
                    tag, category, format,
                    started_at, finished_at, duration_seconds,
                    total, sent, errors,
                    blocked, deleted, network_errors, flood_errors, unknown_errors,
                    success_rate, average_msg_per_second,
                    max_msg_per_second, min_msg_per_second
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s
                )
                """,
                (
                    stats["tag"],
                    stats["category"],
                    stats["format"],
                    stats["started_at"],
                    stats["finished_at"],
                    stats["duration_seconds"],
                    stats["total"],
                    stats["sent"],
                    stats["errors"],
                    stats["blocked"],
                    stats["deleted"],
                    stats["network_errors"],
                    stats["flood_errors"],
                    stats["unknown_errors"],
                    stats["success_rate"],
                    stats["average_msg_per_second"],
                    stats["max_msg_per_second"],
                    stats["min_msg_per_second"],
                ),
            )
    except Exception as e:
        logger.exception(f"[stats] échec insertion broadcast_stats : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT TEXTE ADMIN
# ══════════════════════════════════════════════════════════════════════════════

def format_admin_report(stats: dict) -> str:
    """
    Formate le rapport final envoyé à l'admin selon le brief.
    """
    tag_prefix = f"[{stats['tag']}] " if stats.get("tag") else ""
    minutes = round(stats["duration_seconds"] / 60, 1)

    return (
        f"✅ {tag_prefix}Diffusion terminée\n"
        f"\n"
        f"Total : {stats['total']}\n"
        f"\n"
        f"Succès : {stats['sent']}\n"
        f"\n"
        f"Erreurs : {stats['errors']}\n"
        f"\n"
        f"Blocked : {stats['blocked']}\n"
        f"\n"
        f"Deleted : {stats['deleted']}\n"
        f"\n"
        f"Network : {stats['network_errors']}\n"
        f"\n"
        f"Unknown : {stats['unknown_errors']}\n"
        f"\n"
        f"Durée : {minutes} minutes\n"
        f"\n"
        f"Débit moyen : {stats['average_msg_per_second']} msg/s\n"
        f"\n"
        f"Débit max : {stats['max_msg_per_second']} msg/s"
    )
