"""
relance_routes.py — Endpoints FastAPI pour la configuration des relances.

À monter dans api.py :
    from relance_routes import router as relance_router
    app.include_router(relance_router)

Ces routes gèrent uniquement la CONFIGURATION (message, actif, heure).
L'historique d'exécution reste consultable via les routes broadcast_history
existantes (filtrer par tag, ex: tag LIKE 'relance_%').
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from relance.relance import (
    get_relances,
    get_relance_by_categorie,
    upsert_relance,
    update_relance_message,
    set_relance_active,
    set_relance_schedule,
    delete_relance,
)

router = APIRouter(prefix="/relance", tags=["relance"])


# ════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ════════════════════════════════════════════════════════════════════════════

class RelanceUpsert(BaseModel):
    message:   str  = Field(..., min_length=1, max_length=4096)
    is_active: bool = True


class RelanceMessageUpdate(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)


class RelanceActiveUpdate(BaseModel):
    is_active: bool


class RelanceScheduleUpdate(BaseModel):
    heure_envoi: str = Field(..., pattern=r"^\d{2}:\d{2}(:\d{2})?$")


# ════════════════════════════════════════════════════════════════════════════
# ROUTES — LECTURE
# ════════════════════════════════════════════════════════════════════════════

@router.get("")
async def api_get_relances():
    """Liste toutes les relances configurées, avec member_count et créneaux."""
    return await get_relances()


@router.get("/{name_categorie}")
async def api_get_relance(name_categorie: str):
    """Détail d'une relance pour une catégorie donnée."""
    relance = await get_relance_by_categorie(name_categorie)
    if not relance:
        raise HTTPException(status_code=404, detail=f"Aucune relance configurée pour '{name_categorie}'")
    return relance


# ════════════════════════════════════════════════════════════════════════════
# ROUTES — ÉCRITURE
# ════════════════════════════════════════════════════════════════════════════

@router.put("/{name_categorie}")
async def api_upsert_relance(name_categorie: str, payload: RelanceUpsert):
    """
    Crée ou remplace entièrement la config (message + actif) pour une
    catégorie. Utilisé si le dashboard permet de configurer une relance
    pour une catégorie qui n'en a pas encore.
    """
    return await upsert_relance(name_categorie, payload.message, payload.is_active)


@router.patch("/{relance_id}/message")
async def api_update_message(relance_id: int, payload: RelanceMessageUpdate):
    """Modifie uniquement le texte du message (depuis la modal d'édition du dashboard)."""
    ok = await update_relance_message(relance_id, payload.message)
    if not ok:
        raise HTTPException(status_code=404, detail="Relance introuvable")
    return {"id": relance_id, "message": payload.message}


@router.patch("/{relance_id}/active")
async def api_toggle_active(relance_id: int, payload: RelanceActiveUpdate):
    """Active/désactive une relance (toggle dans la liste des segments)."""
    ok = await set_relance_active(relance_id, payload.is_active)
    if not ok:
        raise HTTPException(status_code=404, detail="Relance introuvable")
    return {"id": relance_id, "is_active": payload.is_active}


@router.patch("/{relance_id}/schedule")
async def api_update_schedule(relance_id: int, payload: RelanceScheduleUpdate):
    """
    Définit l'heure d'envoi d'une relance (remplace le créneau existant).
    heure_envoi attendue en heure locale GMT+1 (Europe/Paris), format HH:MM.
    """
    schedule = await set_relance_schedule(relance_id, payload.heure_envoi)
    return schedule


@router.delete("/{relance_id}")
async def api_delete_relance(relance_id: int):
    """
    Supprime une relance et ses créneaux. Préférer PATCH .../active avec
    is_active=false dans la plupart des cas plutôt que la suppression.
    """
    ok = await delete_relance(relance_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Relance introuvable")
    return {"deleted": True, "id": relance_id}