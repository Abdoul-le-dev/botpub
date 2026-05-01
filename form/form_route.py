"""
form_routes.py — Routes FastAPI pour le système de formulaires.
Préfixe : /forms
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse
import httpx

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
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name requis")
    if not payload.get("command"):
        raise HTTPException(status_code=400, detail="command requis")

    form_id = save_form(payload)
    form    = get_form_by_id(form_id)

    if form and form.get("trigger_type") == "scheduled":
        schedule_form(form)

    return {
        "ok":      True,
        "form_id": form_id,
        "message": f"Formulaire '{payload['name']}' sauvegardé (id={form_id})"
    }


@router.get("")
async def api_list_forms(actif_only: bool = True):
    forms  = get_all_forms(actif_only=actif_only)
    result = []
    for f in forms:
        stats = get_form_stats(f["id"])
        result.append({ **f, "stats": stats })
    return result


@router.get("/scheduler/jobs")
async def api_scheduler_jobs():
    return get_scheduled_jobs()


@router.get("/{form_id}")
async def api_get_form(form_id: int):
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return form


@router.delete("/{form_id}")
async def api_delete_form(form_id: int):
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    toggle_form(form_id, actif=False)
    unschedule_form(form_id)
    return {"ok": True, "message": f"Formulaire {form_id} désactivé"}


@router.post("/{form_id}/activate")
async def api_activate_form(form_id: int):
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
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return get_form_stats(form_id)


@router.get("/{form_id}/responses")
async def api_form_responses(form_id: int, limit: int = 100):
    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")
    return get_form_responses(form_id, limit=limit)


@router.get("/{form_id}/responses/{telegram_id}")
async def api_user_responses(form_id: int, telegram_id: int):
    return get_user_responses_for_form(form_id, telegram_id)


# ════════════════════════════════════════════════════════════════════════════
# MÉDIAS — Proxy Telegram → Navigateur
# Endpoint : GET /forms/media/{file_id}
#
# Telegram ne permet pas d'accéder aux fichiers directement depuis le
# navigateur (auth requise). Ce proxy :
#   1. Appelle bot.get_file(file_id) pour obtenir le file_path
#   2. Construit l'URL de téléchargement Telegram
#   3. Streame le contenu vers le client (navigateur)
#
# Le file_id est celui stocké dans form_responses.value pour les
# types photo / video / audio / document.
# ════════════════════════════════════════════════════════════════════════════

@router.get("/media/{file_id:path}")
async def api_get_media(file_id: str):
    """
    Proxy qui récupère un fichier Telegram par son file_id et le renvoie
    au navigateur pour affichage natif (image, vidéo, audio) ou téléchargement.

    Usage depuis le front :
        <img src="http://127.0.0.1:8000/forms/media/{file_id}">
        <video src="http://127.0.0.1:8000/forms/media/{file_id}">
        <audio src="http://127.0.0.1:8000/forms/media/{file_id}">
        <a href="http://127.0.0.1:8000/forms/media/{file_id}">Télécharger</a>
    """
    import os
    from dotenv import load_dotenv
    load_dotenv()

    bot_token = os.getenv("token")
    if not bot_token:
        raise HTTPException(status_code=503, detail="Token Telegram non configuré")

    # ── 1. Obtenir le file_path via l'API Telegram ──────────────────────
    get_file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(get_file_url)
            resp.raise_for_status()
            tg_data = resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Telegram getFile échoué : {e}")

    if not tg_data.get("ok") or not tg_data.get("result", {}).get("file_path"):
        raise HTTPException(status_code=404, detail="Fichier Telegram introuvable ou expiré")

    file_path = tg_data["result"]["file_path"]
    file_size = tg_data["result"].get("file_size", 0)

    # ── 2. URL de téléchargement Telegram ───────────────────────────────
    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

    # ── 3. Déduire le Content-Type depuis l'extension ────────────────────
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    MIME = {
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "png":  "image/png",
        "gif":  "image/gif",
        "webp": "image/webp",
        "mp4":  "video/mp4",
        "mov":  "video/quicktime",
        "avi":  "video/x-msvideo",
        "ogg":  "audio/ogg",
        "oga":  "audio/ogg",
        "mp3":  "audio/mpeg",
        "m4a":  "audio/mp4",
        "wav":  "audio/wav",
        "pdf":  "application/pdf",
        "zip":  "application/zip",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    content_type = MIME.get(ext, "application/octet-stream")

    # ── 4. Détecter si c'est un fichier vocal Telegram (toujours .ogg) ──
    # Les voice notes Telegram ont un file_path contenant "voice/"
    if "voice/" in file_path:
        content_type = "audio/ogg"

    # ── 5. Stream du fichier vers le navigateur ──────────────────────────
    async def _stream():
        async with httpx.AsyncClient(timeout=60) as dl_client:
            async with dl_client.stream("GET", download_url) as dl_resp:
                dl_resp.raise_for_status()
                async for chunk in dl_resp.aiter_bytes(chunk_size=8192):
                    yield chunk

    # Nom de fichier pour le téléchargement
    filename = file_path.split("/")[-1]

    headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": "private, max-age=3600",
        "Access-Control-Allow-Origin": "*",
    }
    if file_size:
        headers["Content-Length"] = str(file_size)

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers=headers,
    )


# ════════════════════════════════════════════════════════════════════════════
# ENVOI MANUEL (broadcast depuis l'interface)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/{form_id}/send")
async def api_send_form(form_id: int, payload: dict):
    """
    Envoie manuellement un formulaire à des utilisateurs.
    payload: { "user_ids": [123, 456] } OU { "category": "Prospect Inscrit" }
    """
    from form.form_engine import broadcast_form as _broadcast
    from form.form_scheduler import get_bot, get_admin_id
    import sqlite3
    import asyncio

    form = get_form_by_id(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable")

    user_ids = payload.get("user_ids", [])

    if not user_ids and payload.get("category"):
        cat  = payload["category"]
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

    bot      = get_bot()
    admin_id = get_admin_id()

    if not bot:
        raise HTTPException(status_code=503, detail="Bot non initialisé")

    asyncio.create_task(_broadcast(bot, form_id, user_ids, admin_id))

    return {
        "ok":     True,
        "queued": len(user_ids),
        "message": f"Diffusion de '{form['name']}' lancée pour {len(user_ids)} utilisateur(s)"
    }