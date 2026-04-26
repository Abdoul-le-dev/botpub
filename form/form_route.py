"""
form_routes.py — Routes FastAPI pour le système de formulaires.

Préfixe : /forms

Endpoints :
  POST   /forms                      → Créer / mettre à jour un formulaire (depuis le builder)
  GET    /forms                      → Lister tous les formulaires
  GET    /forms/{form_id}            → Détail d'un formulaire
  DELETE /forms/{form_id}            → Désactiver un formulaire
  GET    /forms/{form_id}/stats      → Stats (réponses, complétion, score)
  GET    /forms/{form_id}/responses  → Liste des soumissions
  GET    /forms/{form_id}/responses/{telegram_id} → Réponses d'un user
  GET    /forms/scheduler/jobs       → Voir les jobs planifiés

Intégration dans api.py :
  from form_routes import router as forms_router
  app.include_router(forms_router)
"""

from fastapi import APIRouter, HTTPException
from form.form import (
    save_form, get_form_by_id, get_all_forms,
    toggle_form, get_form_stats, get_form_responses,
    get_user_responses_for_form,
)
from form.form_scheduler import schedule_form, unschedule_form, get_scheduled_jobs

router = APIRouter(prefix="/forms", tags=["forms"])


# ════════════════════════════════════════════════════════════════════════════
# CRUD FORMULAIRES
# ════════════════════════════════════════════════════════════════════════════

@router.post("")
async def api_save_form(payload: dict):
    """
    Crée ou met à jour un formulaire depuis le builder frontend.

    Payload attendu (identique au publish() JS du frontend) :
    {
        "name": "Quiz Analyse Technique",
        "command": "/quiz",
        "type": "quiz",
        "trigger": "Commande manuelle",
        "trigger_value": null,
        "intro": "Bonjour +prenom !...",
        "outro": "Score : +score / +total",
        "fields": [...],
        "actions": [...],
        "conditions": [...],
        "quiz_config": { "max": 50, "pts": 10, "penalty": 0 },
        "options": { "resume": true, "progress": true }
    }
    """
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name requis")
    if not payload.get("command"):
        raise HTTPException(status_code=400, detail="command requis")

    form_id = save_form(payload)
    form    = get_form_by_id(form_id)

    # Enregistrer le job si planifié
    if form and form.get("trigger_type") == "scheduled":
        schedule_form(form)

    return {
        "ok":      True,
        "form_id": form_id,
        "message": f"Formulaire '{payload['name']}' sauvegardé (id={form_id})"
    }


@router.get("")
async def api_list_forms(actif_only: bool = True):
    """Retourne tous les formulaires (actifs par défaut)."""
    forms = get_all_forms(actif_only=actif_only)
    # Enrichir avec les stats basiques
    result = []
    for f in forms:
        stats = get_form_stats(f["id"])
        result.append({
            **f,
            "stats": stats,
        })
    return result


@router.get("/scheduler/jobs")
async def api_scheduler_jobs():
    """Retourne tous les jobs planifiés actifs."""
    return get_scheduled_jobs()


@router.get("/{form_id}")
async def api_get_form(form_id: int):
    """Retourne le détail complet d'un formulaire."""
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return form


@router.delete("/{form_id}")
async def api_delete_form(form_id: int):
    """Désactive un formulaire (soft delete) et supprime son job scheduler."""
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    toggle_form(form_id, actif=False)
    unschedule_form(form_id)
    return {"ok": True, "message": f"Formulaire {form_id} désactivé"}


@router.post("/{form_id}/activate")
async def api_activate_form(form_id: int):
    """Réactive un formulaire désactivé."""
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    toggle_form(form_id, actif=True)
    if form.get("trigger_type") == "scheduled":
        schedule_form(form)
    return {"ok": True, "message": f"Formulaire {form_id} réactivé"}


# ════════════════════════════════════════════════════════════════════════════
# STATS & RÉPONSES
# ════════════════════════════════════════════════════════════════════════════

@router.get("/{form_id}/stats")
async def api_form_stats(form_id: int):
    """
    Retourne les stats d'un formulaire.
    {
        form_id, total, completed, completion_pct, avg_score
    }
    """
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return get_form_stats(form_id)


@router.get("/{form_id}/responses")
async def api_form_responses(form_id: int, limit: int = 100):
    """
    Retourne la liste des soumissions complètes pour ce formulaire.
    Utilisé par la vue 'Réponses' du dashboard.
    """
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return get_form_responses(form_id, limit=limit)


@router.get("/{form_id}/responses/{telegram_id}")
async def api_user_responses(form_id: int, telegram_id: int):
    """
    Retourne le détail des réponses d'un utilisateur à un formulaire.
    Utilisé par la modal de détail dans la vue Réponses.
    """
    return get_user_responses_for_form(form_id, telegram_id)


# ════════════════════════════════════════════════════════════════════════════
# ENVOI MANUEL (broadcast depuis l'interface)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/{form_id}/send")
async def api_send_form(form_id: int, payload: dict):
    """
    Envoie manuellement un formulaire à des utilisateurs.

    payload:
    {
        "user_ids": [123, 456],       # liste explicite
        "category": "Prospect Inscrit" # OU une catégorie
    }
    """
    from form.form_engine import broadcast_form as _broadcast
    from form.form_scheduler import _bot, _admin_id
    import sqlite3

    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")

    user_ids = payload.get("user_ids", [])

    # Si catégorie fournie, récupérer les membres
    if not user_ids and payload.get("category"):
        cat = payload["category"]
        conn = sqlite3.connect("preinscriptions.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT DISTINCT u.telegram_id
            FROM users u
            JOIN user_categories uc ON uc.telegram_id = u.telegram_id
            WHERE uc.name_categorie = ?
        """, (cat,)).fetchall()
        conn.close()
        user_ids = [r["telegram_id"] for r in rows]

    if not user_ids:
        raise HTTPException(status_code=400, detail="Aucun utilisateur cible")

    if not _bot:
        raise HTTPException(status_code=503, detail="Bot non initialisé")

    import asyncio
    asyncio.create_task(_broadcast(_bot, form_id, user_ids, _admin_id))

    return {
        "ok":    True,
        "queued": len(user_ids),
        "message": f"Diffusion de '{form['name']}' lancée pour {len(user_ids)} utilisateur(s)"
    }