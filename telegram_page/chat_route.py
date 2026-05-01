# routes_chat.py — Routes FastAPI pour le Chat Direct
# Même pattern que routes_categories.py
# À intégrer dans main.py : app.include_router(chat_router)

import io, os
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from typing import Optional

from telegram_page.chat import (
    # Abonnements
    create_subscription, get_subscriptions, get_subscription_summary,
    cancel_subscription, expire_subscriptions, get_subscriptions_stats,
    # Conversations
    get_conversations, get_conversation, get_conversation_stats,
    set_ia_enabled, set_conversation_blocked, mark_as_read,
    pin_conversation, set_admin_note, search_conversations,
    # Messages
    get_messages, send_message, receive_message, receive_ia_message,
    update_message_status, delete_message, get_conversation_timeline,
    # IA
    get_ia_stats,
    # Upload
    upload_media,
    # Profil
    get_chat_profile, get_received_broadcasts,
    # Exports
    export_conversation,mark_requires_admin, mark_testimonial
)

router = APIRouter(prefix="/chat", tags=["chat"])


# ════════════════════════════════════════════════════════════════════════
# ABONNEMENTS
# ════════════════════════════════════════════════════════════════════════

@router.get("/subscriptions/stats")
async def api_subscriptions_stats():
    """Stats globales : actifs, expirés, expirent dans 7 jours."""
    return await get_subscriptions_stats()


@router.post("/subscriptions/expire")
async def api_expire_subscriptions():
    """
    Passe les abonnements expirés à status='expired'.
    À appeler via cron quotidien.
    """
    return await expire_subscriptions()


@router.get("/conversations/{user_id}/subscriptions")
async def api_get_subscriptions(user_id: int):
    """Tous les abonnements d'un membre — actifs + historique complet."""
    return await get_subscriptions(user_id)


@router.get("/conversations/{user_id}/subscriptions/summary")
async def api_subscription_summary(user_id: int):
    """
    Résumé abonnement pour le panneau profil du chat.
    Retourne : has_active, plans_active, max_expiry, days_remaining.
    """
    return await get_subscription_summary(user_id)


@router.post("/conversations/{user_id}/subscriptions")
async def api_create_subscription(user_id: int, payload: dict):
    """
    Crée un abonnement. Les durées s'additionnent si un abonnement actif existe.
    payload: { plan, note? }
    plan: mensuel (30j) | trimestriel (90j) | semestriel (180j) | annuel (270j)
    """
    if not payload.get("plan"):
        raise HTTPException(status_code=400, detail="plan requis")
    payload["user_id"] = user_id
    result = await create_subscription(payload)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/subscriptions/{sub_id}/cancel")
async def api_cancel_subscription(sub_id: int):
    """Annule un abonnement (status → cancelled)."""
    result = await cancel_subscription(sub_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ════════════════════════════════════════════════════════════════════════
# CONVERSATIONS
# ════════════════════════════════════════════════════════════════════════

@router.get("/conversations/stats")
async def api_conversation_stats():
    """Stats globales : total, non lus, IA actives, bloquées, actives aujourd'hui."""
    return await get_conversation_stats()


@router.get("/conversations/search")
async def api_search_conversations(q: str = Query(..., min_length=1)):
    """Recherche fulltext sur nom, username et contenu des messages."""
    return await search_conversations(q)


@router.get("/conversations")
async def api_get_conversations(
    tab:    str = "all",
    search: str = "",
    limit:  int = 50,
    offset: int = 0,
):
    """
    Liste paginée avec preview du dernier message.
    tab: all | unread | ia | blocked
    """
    return await get_conversations({
        "tab": tab, "search": search, "limit": limit, "offset": offset
    })


@router.get("/conversations/{user_id}")
async def api_get_conversation(user_id: int):
    """État complet d'une conversation (header du chat)."""
    conv = await get_conversation(user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return conv


@router.patch("/conversations/{user_id}/ia")
async def api_set_ia(user_id: int, payload: dict):
    """
    Active / désactive l'agent IA sur une conversation.
    payload: { enabled: bool }
    """
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled requis")
    return await set_ia_enabled(user_id, payload["enabled"])


@router.patch("/conversations/{user_id}/read")
async def api_mark_read(user_id: int):
    """Remet unread_count à 0 quand l'admin ouvre la conversation."""
    return await mark_as_read(user_id)


@router.patch("/conversations/{user_id}/pin")
async def api_pin(user_id: int, payload: dict):
    """
    Épingle / désépingle une conversation.
    payload: { pinned: bool }
    """
    if "pinned" not in payload:
        raise HTTPException(status_code=400, detail="pinned requis")
    return await pin_conversation(user_id, payload["pinned"])


@router.patch("/conversations/{user_id}/block")
async def api_set_blocked(user_id: int, payload: dict):
    """
    Marque une conversation comme bloquée.
    Appelé par le bot Python via webhook quand un membre bloque le bot.
    payload: { blocked: bool }
    """
    if "blocked" not in payload:
        raise HTTPException(status_code=400, detail="blocked requis")
    return await set_conversation_blocked(user_id, payload["blocked"])


@router.patch("/conversations/{user_id}/note")
async def api_set_note(user_id: int, payload: dict):
    """
    Note interne admin sur une conversation (invisible du membre).
    payload: { note: str }
    """
    if "note" not in payload:
        raise HTTPException(status_code=400, detail="note requis")
    return await set_admin_note(user_id, payload["note"])


# ════════════════════════════════════════════════════════════════════════
# MESSAGES
# ════════════════════════════════════════════════════════════════════════

@router.get("/conversations/{user_id}/messages")
async def api_get_messages(
    user_id:   int,
    limit:     int = 50,
    before_id: Optional[int] = None,
    after_id:  Optional[int] = None,
):
    """
    Fil de messages paginé.
    before_id : charger les messages plus anciens (scroll vers le haut)
    after_id  : charger les messages plus récents (polling)
    """
    return await get_messages(user_id, {
        "limit": limit, "before_id": before_id, "after_id": after_id
    })


def get_extension(media_url):
    if not media_url:
        return "text"
    
    ext = os.path.splitext(media_url)[1].lower()

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
    VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
    DOC_EXTS   = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".zip", ".rar", ".7z", ".txt", ".csv", ".json", ".xml"}

    if ext in IMAGE_EXTS:
        return "image"
    elif ext in VIDEO_EXTS:
        return "video"
    elif ext in DOC_EXTS:
        return "doc"
    else:
        return "text"
    

@router.post("/conversations/{user_id}/messages")
async def api_send_message(user_id: int, payload: dict):
    """
    Envoie un message depuis le dashboard admin.
    payload: {
        message_text?    : texte du message,
        message_type     : text | image | video | pdf | word | excel | powerpoint | archive,
        media_url?       : chemin local /media/uuid.ext (si média),
        replied_to_id?   : ID du message cité
    }
    Le fichier est lu depuis le disque et envoyé en bytes au bot Python.
    """
    

    if not payload.get("message_type"):
        raise HTTPException(status_code=400, detail="message_type requis")
    payload["user_id"] = user_id

    ext = get_extension(payload.get("media_url"))

    if ext == "text" and payload.get("message_text") =="":

        raise HTTPException(status_code=400, detail="rien a envoyer")


    if ext != "text":
        payload["message_type"] = ext

    print(payload)
    return await send_message(payload)


@router.patch("/messages/{message_id}/status")
async def api_update_status(message_id: int, payload: dict):
    """
    Met à jour le statut de livraison Telegram.
    payload: { status: sent | delivered | read | error, timestamp? }
    Appelé par le bot Python via webhook Telegram.
    """
    if not payload.get("status"):
        raise HTTPException(status_code=400, detail="status requis")
    return await update_message_status(
        message_id, payload["status"], payload.get("timestamp")
    )


@router.delete("/messages/{message_id}")
async def api_delete_message(message_id: int, payload: dict):
    """
    Suppression logique d'un message (admin seulement).
    payload: { user_id: int }
    """
    if not payload.get("user_id"):
        raise HTTPException(status_code=400, detail="user_id requis")
    return await delete_message(message_id, payload["user_id"])


@router.get("/conversations/{user_id}/timeline")
async def api_timeline(user_id: int):
    """
    Fil unifié groupé par date.
    Inclut messages + broadcasts reçus.
    """
    return await get_conversation_timeline(user_id)


# ════════════════════════════════════════════════════════════════════════
# UPLOAD MÉDIAS
# ════════════════════════════════════════════════════════════════════════

@router.post("/media/upload")
async def api_upload_media(
    user_id: int = Form(...),
    file:    UploadFile = File(...),
):
    """
    Upload d'un fichier média depuis le dashboard admin.
    Stocke dans /media/{uuid}{ext}.
    Génère une miniature pour les images (Pillow) et vidéos (ffmpeg).

    Types acceptés :
      images    : jpeg, png, gif, webp               — max 10 MB
      vidéos    : mp4, mov, avi, mkv, webm           — max 50 MB
      documents : pdf, doc/x, xls/x, ppt/x, txt      — max 20 MB
      archives  : zip, rar                           — max 50 MB

    Retourne : { filename, url, thumbnail, mime_type, type, size_bytes, size_mb }
    """
    file_bytes = await file.read()
    result     = await upload_media(file_bytes, file.filename, file.content_type, user_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result



# ════════════════════════════════════════════════════════════════════════
# WEBHOOKS BOT → API
# Appelés par le bot Python uniquement.
# Sécuriser via middleware X-Bot-Secret sur ce préfixe.
# ════════════════════════════════════════════════════════════════════════

@router.post("/webhook/message")
async def api_webhook_receive(payload: dict):
    """
    Message texte entrant d'un membre.
    payload: { user_id, message_id, message_text, message_type, media_url? }
    Si ia_enabled=1 sur la conversation, déclenche trigger_ia_response() automatiquement.
    """
    if not payload.get("user_id") or not payload.get("message_id"):
        raise HTTPException(status_code=400, detail="user_id et message_id requis")
    return await receive_message(payload)


@router.post("/webhook/media")
async def api_webhook_media(
    user_id:      int      = Form(...),
    message_id:   int      = Form(...),
    message_type: str      = Form(...),
    message_text: str      = Form(""),
    file:         UploadFile = File(...),
):
    """
    Média entrant d'un membre (photo, vidéo, document).
    Le bot Python télécharge le fichier depuis Telegram
    et l'envoie ici en multipart.
    Le fichier est stocké dans /media/ puis le message est enregistré.

    form fields : user_id, message_id, message_type, message_text?
    file        : le fichier binaire
    """
    file_bytes = await file.read()

    # Stocker le fichier
    media_result = await upload_media(
        file_bytes, file.filename, file.content_type, user_id
    )
    if "error" in media_result:
        raise HTTPException(status_code=400, detail=media_result["error"])

    # Enregistrer le message avec l'URL locale
    return await receive_message({
        "user_id":      user_id,
        "message_id":   message_id,
        "message_text": message_text,
        "message_type": message_type,
        "media_url":    media_result["url"],
    })


@router.post("/webhook/ia-message")
async def api_webhook_ia(payload: dict):
    """
    Réponse générée par l'agent IA, envoyée par le bot Python.
    payload: { user_id, message_text, message_type? }
    """
    if not payload.get("user_id") or not payload.get("message_text"):
        raise HTTPException(status_code=400, detail="user_id et message_text requis")
    return await receive_ia_message(payload)


@router.post("/webhook/message-status")
async def api_webhook_status(payload: dict):
    """
    Mise à jour statut de livraison Telegram (envoyé → livré → lu).
    payload: { message_id, status: sent|delivered|read|error, timestamp? }
    """
    if not payload.get("message_id") or not payload.get("status"):
        raise HTTPException(status_code=400, detail="message_id et status requis")
    return await update_message_status(
        payload["message_id"], payload["status"], payload.get("timestamp")
    )


@router.post("/webhook/blocked")
async def api_webhook_blocked(payload: dict):
    """
    Membre a bloqué ou débloqué le bot sur Telegram.
    payload: { user_id, blocked: bool }
    """
    if "user_id" not in payload or "blocked" not in payload:
        raise HTTPException(status_code=400, detail="user_id et blocked requis")
    return await set_conversation_blocked(payload["user_id"], payload["blocked"])


# ════════════════════════════════════════════════════════════════════════
# AGENT IA
# ════════════════════════════════════════════════════════════════════════

@router.get("/conversations/{user_id}/ia/stats")
async def api_ia_stats(user_id: int):
    """
    Stats IA sur une conversation :
    total messages IA, taux de lecture, première et dernière réponse.
    """
    return await get_ia_stats(user_id)


# ════════════════════════════════════════════════════════════════════════
# PROFIL MEMBRE
# ════════════════════════════════════════════════════════════════════════

@router.get("/conversations/{user_id}/profile")
async def api_chat_profile(user_id: int):
    """
    Vue enrichie du panneau profil droit du chat.
    Retourne en un seul appel :
      - infos user (prenom, username, date inscription)
      - état conversation (ia_enabled, is_blocked, note_admin)
      - catégories
      - résumé abonnements (has_active, days_remaining, max_expiry)
      - stats trading (trades, win_rate, avg_result)
      - 5 derniers broadcasts reçus
    """
    profile = await get_chat_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Membre introuvable")
    return profile


@router.get("/conversations/{user_id}/broadcasts")
async def api_received_broadcasts(user_id: int, limit: int = 5):
    """Dernières campagnes broadcast reçues par un membre."""
    return await get_received_broadcasts(user_id, limit)


# ════════════════════════════════════════════════════════════════════════
# EXPORTS
# ════════════════════════════════════════════════════════════════════════

@router.get("/conversations/{user_id}/export")
async def api_export(user_id: int, fmt: str = "json"):
    """
    Export complet d'une conversation.
    fmt: json | csv | txt
    Retourne un fichier téléchargeable.
    """
    if fmt not in ("json", "csv", "txt"):
        raise HTTPException(status_code=400, detail="fmt doit être json, csv ou txt")
    result = await export_conversation(user_id, fmt)
    return StreamingResponse(
        io.StringIO(result["content"]),
        media_type=result["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'}
    )

@router.patch("/messages/{message_id}/requires-admin")
async def api_mark_requires_admin(message_id: int, payload: dict):
    """payload: { value: 0 | 1 }"""
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="value requis")
    return await mark_requires_admin(message_id, payload["value"])


@router.patch("/messages/{message_id}/testimonial")
async def api_mark_testimonial(message_id: int, payload: dict):
    """payload: { value: 0 | 1 }"""
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="value requis")
    return await mark_testimonial(message_id, payload["value"])