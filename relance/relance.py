"""
relance.py — Accès DB pour la configuration des relances automatiques.

Une "relance" = la config éditable pour une catégorie donnée :
    - message (avec variables +prenom, +jours_restants)
    - is_active (la catégorie reçoit-elle des relances ?)
    - un ou plusieurs créneaux d'envoi (relance_schedule)

Ce module ne fait QUE de la configuration. L'exécution (envoi réel via
broadcast_engine, et le log sent/errors) est gérée par relance_scheduler.py
et broadcast_history — pas ici.
"""

from datetime import datetime, timedelta
from typing import Optional

from db import get_db


# ════════════════════════════════════════════════════════════════════════════
# LECTURE
# ════════════════════════════════════════════════════════════════════════════

async def get_relances() -> list[dict]:
    """
    Retourne toutes les relances configurées, avec :
      - member_count : nombre de membres actuels dans la catégorie
        (jointe sur `categories`, même principe que get_categories())
      - schedules : liste des créneaux d'envoi (relance_schedule)

    Format de retour, une entrée par relance :
        {
            "id": 2, "name_categorie": "clients_j7", "message": "...",
            "is_active": 1, "member_count": 34,
            "schedules": [{"id": 7, "heure_envoi": "08:00:00", "is_active": 1}]
        }
    """
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                r.id, r.name_categorie, r.message, r.is_active,
                r.created_at, r.updated_at,
                COALESCE(COUNT(DISTINCT c.id), 0) AS member_count
            FROM relance r
            LEFT JOIN categories c ON c.name_categorie = r.name_categorie
            GROUP BY r.id
            ORDER BY r.id ASC
        """)
        relances = [dict(row) for row in await cur.fetchall()]

        for relance in relances:
            await cur.execute("""
                SELECT id, heure_envoi, is_active
                FROM relance_schedule
                WHERE relance_id = %s
                ORDER BY heure_envoi ASC
            """, (relance["id"],))
            relance["schedules"] = [dict(row) for row in await cur.fetchall()]

    return relances


async def get_relance_by_categorie(name_categorie: str) -> Optional[dict]:
    """Retourne une seule relance (même format que get_relances), ou None."""
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                r.id, r.name_categorie, r.message, r.is_active,
                r.created_at, r.updated_at,
                COALESCE(COUNT(DISTINCT c.id), 0) AS member_count
            FROM relance r
            LEFT JOIN categories c ON c.name_categorie = r.name_categorie
            WHERE r.name_categorie = %s
            GROUP BY r.id
        """, (name_categorie,))
        row = await cur.fetchone()
        if not row:
            return None

        relance = dict(row)
        await cur.execute("""
            SELECT id, heure_envoi, is_active
            FROM relance_schedule
            WHERE relance_id = %s
            ORDER BY heure_envoi ASC
        """, (relance["id"],))
        relance["schedules"] = [dict(r) for r in await cur.fetchall()]

    return relance


# ════════════════════════════════════════════════════════════════════════════
# ÉCRITURE — CONFIG (message / actif)
# ════════════════════════════════════════════════════════════════════════════

async def upsert_relance(name_categorie: str, message: str, is_active: bool) -> dict:
    """
    Crée la relance si elle n'existe pas encore pour cette catégorie,
    sinon met à jour message + is_active. Idempotent.
    Retourne la relance résultante via get_relance_by_categorie.
    """
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO relance (name_categorie, message, is_active)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                message   = VALUES(message),
                is_active = VALUES(is_active)
        """, (name_categorie, message, int(is_active)))

    return await get_relance_by_categorie(name_categorie)


async def update_relance_message(relance_id: int, message: str) -> bool:
    """Met à jour uniquement le texte du message. Retourne True si une ligne a été modifiée."""
    async with get_db() as cur:
        await cur.execute(
            "UPDATE relance SET message = %s WHERE id = %s",
            (message, relance_id)
        )
        return cur.rowcount > 0


async def set_relance_active(relance_id: int, is_active: bool) -> bool:
    """Active/désactive une relance (toggle depuis le dashboard)."""
    async with get_db() as cur:
        await cur.execute(
            "UPDATE relance SET is_active = %s WHERE id = %s",
            (int(is_active), relance_id)
        )
        return cur.rowcount > 0


async def delete_relance(relance_id: int) -> bool:
    """
    Supprime une relance et ses créneaux (cascade FK).
    À utiliser avec précaution — préférer set_relance_active(False) dans
    la plupart des cas (désactiver plutôt que supprimer).
    """
    async with get_db() as cur:
        await cur.execute("DELETE FROM relance WHERE id = %s", (relance_id,))
        return cur.rowcount > 0


# ════════════════════════════════════════════════════════════════════════════
# ÉCRITURE — CRÉNEAUX (relance_schedule)
# ════════════════════════════════════════════════════════════════════════════

async def set_relance_schedule(relance_id: int, heure_envoi: str) -> dict:
    """
    Définit l'UNIQUE créneau d'une relance (remplace tout créneau existant).
    heure_envoi au format 'HH:MM' ou 'HH:MM:SS'.

    Pour l'instant une relance n'a qu'un créneau ; cette fonction supprime
    les créneaux existants puis insère le nouveau. add_relance_schedule()
    ci-dessous permet d'ajouter des créneaux supplémentaires à l'avenir
    sans toucher à cette fonction.
    """
    if len(heure_envoi.split(":")) == 2:
        heure_envoi += ":00"

    async with get_db() as cur:
        await cur.execute("DELETE FROM relance_schedule WHERE relance_id = %s", (relance_id,))
        await cur.execute("""
            INSERT INTO relance_schedule (relance_id, heure_envoi, is_active)
            VALUES (%s, %s, 1)
        """, (relance_id, heure_envoi))
        new_id = cur.lastrowid

    return {"id": new_id, "relance_id": relance_id, "heure_envoi": heure_envoi, "is_active": 1}


async def add_relance_schedule(relance_id: int, heure_envoi: str) -> dict:
    """
    Ajoute un créneau supplémentaire à une relance qui en a déjà un ou
    plusieurs (contrairement à set_relance_schedule qui remplace tout).
    Prêt pour le futur multi-créneaux, pas encore exposé en UI.
    """
    if len(heure_envoi.split(":")) == 2:
        heure_envoi += ":00"

    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO relance_schedule (relance_id, heure_envoi, is_active)
            VALUES (%s, %s, 1)
        """, (relance_id, heure_envoi))
        new_id = cur.lastrowid

    return {"id": new_id, "relance_id": relance_id, "heure_envoi": heure_envoi, "is_active": 1}


async def delete_relance_schedule(schedule_id: int) -> bool:
    async with get_db() as cur:
        await cur.execute("DELETE FROM relance_schedule WHERE id = %s", (schedule_id,))
        return cur.rowcount > 0


# ════════════════════════════════════════════════════════════════════════════
# LECTURE — POUR LE SCHEDULER (relance_scheduler.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_due_relances(heure_courante: str) -> list[dict]:
    """
    Retourne les relances actives dont un créneau actif tombe sur
    heure_courante (format 'HH:MM', comparé à la minute — les secondes
    de relance_schedule.heure_envoi sont ignorées dans la comparaison).

    Utilisé par le scheduler pour savoir quoi déclencher à chaque tick.
    Ne filtre PAS sur member_count > 0 ici — c'est au scheduler de décider
    s'il déclenche broadcast_engine quand 0 membre (broadcast_engine gère
    déjà ce cas en renvoyant {"error": "aucun destinataire trouvé"}).
    """
    async with get_db() as cur:
        await cur.execute("""
            SELECT r.id AS relance_id, r.name_categorie, r.message,
                   rs.id AS schedule_id, rs.heure_envoi
            FROM relance r
            JOIN relance_schedule rs ON rs.relance_id = r.id
            WHERE r.is_active = 1
              AND rs.is_active = 1
              AND TIME_FORMAT(rs.heure_envoi, '%%H:%%i') = %s
        """, (heure_courante,))
        return [dict(row) for row in await cur.fetchall()]


async def count_members_in_categorie(name_categorie: str) -> int:
    """
    Compte les membres actuels d'une catégorie. Utilisé par le scheduler
    pour décider de déclencher ou non un broadcast (pas la peine d'appeler
    broadcast_engine si 0 membre).
    """
    async with get_db() as cur:
        await cur.execute(
            "SELECT COUNT(*) AS n FROM categories WHERE name_categorie = %s",
            (name_categorie,)
        )
        row = await cur.fetchone()
        return row["n"] if row else 0