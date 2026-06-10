# api.py — v4 MySQL

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from telegram import Bot
import os
import csv
import io
from subscription_sync import sync_clients_actifs
import asyncio
from db import init_pool, close_pool

load_dotenv()
bot = Bot(token=os.getenv("tokenss"))

# ── Routers ──────────────────────────────────────────────────────────────
from form.form_route                          import router as forms_router
from routes.routes_dashboard                  import router as dashboard_router
from telegram_page.gold.routes_gold           import router as gold_router
from telegram_page.subscription.subscription  import router as subscription_router
from telegram_page.chat_route                 import router as chat_router
from telegram_page.automatisation.routes_growth import router as growth_router
from telegram_page.ia_config_table            import router as ai_router
from telegram_page.routes_trading             import router as trading_router

# ── Gold engine ───────────────────────────────────────────────────────────
from telegram_page.gold.gold_engine import set_bot as set_gold_bot

# ── IA agent ──────────────────────────────────────────────────────────────
from ai_agent import agent_response_router

# ── Chat ──────────────────────────────────────────────────────────────────
from telegram_page.chat import set_bot, get_conversation_stats, get_subscriptions_stats

# ── Broadcast ─────────────────────────────────────────────────────────────
from telegram_page.broadcast_engine import broadcast_engine

# ── Catégories ────────────────────────────────────────────────────────────
from telegram_page.categorie import (
    get_categories_stats,
    get_categories,
    get_category_by_name,
    create_category,
    update_category,
    get_category_members,
    add_members_to_category,
    remove_member_from_category,
    move_members,
    merge_categories,
    import_members_csv,
    get_category_rules,
    add_category_rule,
    delete_category_rule,
    get_category_stats,
    get_category_intersections,
    get_member_profile,
    get_member_categories,
)

# ── DB (MySQL) — lecture directe pour les routes legacy ──────────────────
from db import get_db

# ── Trading ───────────────────────────────────────────────────────────────
from telegram_page.trading_journal import (
    set_bot as set_trading_bot,
    get_dashboard_stats,
)

# ── Scheduler ─────────────────────────────────────────────────────────────
from form.form_scheduler import start_scheduler, stop_scheduler


# ════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    set_bot(bot)
    await start_scheduler(bot, admin_id=571718066)
    set_trading_bot(bot)
    set_gold_bot(bot)
    #await sync_clients_actifs() 
    yield
    stop_scheduler()
    await close_pool()
    


# ════════════════════════════════════════════════════════════════════════
# APP
# ════════════════════════════════════════════════════════════════════════

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory="media"), name="media")

# Routers
app.include_router(chat_router)
app.include_router(gold_router)
app.include_router(forms_router)
app.include_router(trading_router)
app.include_router(growth_router)
app.include_router(ai_router)
app.include_router(subscription_router)
app.include_router(dashboard_router)


# ════════════════════════════════════════════════════════════════════════
# MODÈLES
# ════════════════════════════════════════════════════════════════════════

class RequestBody(BaseModel):
    text: str

class RequestData(BaseModel):
    userId: int


# ════════════════════════════════════════════════════════════════════════
# ROUTES RACINE
# ════════════════════════════════════════════════════════════════════════

@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/broadcast")
async def api_broadcast(payload: dict):
    report = await broadcast_engine(bot, payload)
    return report


@app.get("/broadcast/history")
async def api_get_broadcast_history():
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM broadcast_history ORDER BY id DESC LIMIT 50"
        )
        rows = await cur.fetchall()
    return rows


# ════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════════════════════════

@app.get("/dashboard/stats")
async def api_dashboard_stats():
    chat   = await get_conversation_stats()
    subs   = await get_subscriptions_stats()
    trades = await get_dashboard_stats("month")
    return {
        "total_membres":        chat.get("total_conversations", 0),
        "actifs_7j":            chat.get("active_today", 0),
        "abonnements_actifs":   subs.get("active", 0),
        "trades_journalises":   trades.get("journals_collected", 0),
        "nouveaux_7j":          chat.get("new_7j", 0),
        "expirations_proches":  subs.get("expiring_in_7_days", 0),
        "escalades_ia":         chat.get("requires_admin_count", 0),
        "membres_inactifs_21j": chat.get("inactive_21j", 0),
    }


# ════════════════════════════════════════════════════════════════════════
# CATÉGORIES — routes STATIQUES en premier (avant les dynamiques /{name})
# ════════════════════════════════════════════════════════════════════════

# ── Stats globales ──
@app.get("/categories/stats")
async def api_categories_stats():
    return await get_categories_stats()


# ── Liste legacy (broadcast page) ──
@app.get("/categories")
async def api_get_categorie():
    async with get_db() as cur:
        await cur.execute(
            "SELECT name_categorie, COUNT(*) as total FROM categories GROUP BY name_categorie"
        )
        rows = await cur.fetchall()
    return [{"name": r["name_categorie"], "total": r["total"]} for r in rows]


# ── Liste complète (page catégories) ──
@app.get("/categorie")
async def api_get_categories():
    return await get_categories()


# ── Créer une catégorie ──
@app.post("/categories")
async def api_create_category(payload: dict):
    if not payload.get("name_categorie"):
        raise HTTPException(status_code=400, detail="name_categorie requis")
    return await create_category(payload)


# ── Déplacer des membres (statique — AVANT /{name_categorie}/members) ──
@app.post("/categories/members/move")
async def api_move_members(payload: dict):
    for field in ["source", "destination", "user_ids"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"{field} requis")
    return await move_members(payload)


# ── Fusionner (statique — AVANT /{name_categorie}) ──
@app.post("/categories/merge")
async def api_merge_categories(payload: dict):
    if not payload.get("target") or not payload.get("sources"):
        raise HTTPException(status_code=400, detail="target et sources requis")
    return await merge_categories(payload["target"], payload["sources"])


# ── Supprimer une règle (statique — AVANT DELETE /{name_categorie}) ──
@app.delete("/categories/rules/{rule_id}")
async def api_delete_rule(rule_id: int):
    return await delete_category_rule(rule_id)


# ════════════════════════════════════════════════════════════════════════
# CATÉGORIES — routes DYNAMIQUES /{name_categorie}
# ════════════════════════════════════════════════════════════════════════

@app.get("/categories/{name_categorie}")
async def api_get_category(name_categorie: str):
    cat = await get_category_by_name(name_categorie)
    if not cat:
        raise HTTPException(status_code=404, detail="Catégorie introuvable")
    return cat


@app.put("/categories/{name_categorie}")
async def api_update_category(name_categorie: str, payload: dict):
    return await update_category(name_categorie, payload)


@app.delete("/categories/{name_categorie}")
async def api_delete_category(name_categorie: str):
    from telegram_page.categorie import delete_category
    return await delete_category(name_categorie)


# ── Membres ──

@app.get("/categories/{name_categorie}/members")
async def api_get_members(
    name_categorie: str,
    search:         str  = None,
    active_only:    bool = False,
    inactive_only:  bool = False,
    limit:          int  = 50,
    offset:         int  = 0,
):
    return await get_category_members(name_categorie, {
        "search": search, "active_only": active_only,
        "inactive_only": inactive_only, "limit": limit, "offset": offset,
    })


@app.post("/categories/{name_categorie}/members")
async def api_add_members(name_categorie: str, payload: dict):
    if not payload.get("user_ids"):
        raise HTTPException(status_code=400, detail="user_ids requis")
    return await add_members_to_category(
        name_categorie, payload["user_ids"], payload.get("added_by", "manual")
    )


@app.delete("/categories/{name_categorie}/members/{telegram_id}")
async def api_remove_member(name_categorie: str, telegram_id: int):
    return await remove_member_from_category(name_categorie, telegram_id)


# ── Import CSV ──

@app.post("/categories/{name_categorie}/import")
async def api_import_csv(name_categorie: str, file: UploadFile = File(...)):
    content  = await file.read()
    decoded  = content.decode("utf-8")
    reader   = csv.DictReader(io.StringIO(decoded))
    user_ids = []
    for row in reader:
        uid = row.get("user_id") or row.get("telegram_id")
        if uid:
            try:
                user_ids.append(int(uid.strip()))
            except ValueError:
                pass
    if not user_ids:
        raise HTTPException(status_code=400, detail="Aucun user_id valide trouvé dans le CSV")
    return await import_members_csv(name_categorie, user_ids)


# ── Règles ──

@app.get("/categories/{name_categorie}/rules")
async def api_get_rules(name_categorie: str):
    return await get_category_rules(name_categorie)


@app.post("/categories/{name_categorie}/rules")
async def api_add_rule(name_categorie: str, payload: dict):
    if not payload.get("trigger_type"):
        raise HTTPException(status_code=400, detail="trigger_type requis")
    return await add_category_rule(name_categorie, payload)


# ── Stats & intersections ──

@app.get("/categories/{name_categorie}/stats")
async def api_category_stats(name_categorie: str):
    return await get_category_stats(name_categorie)


@app.get("/categories/{name_categorie}/intersections")
async def api_intersections(name_categorie: str):
    return await get_category_intersections(name_categorie)


# ════════════════════════════════════════════════════════════════════════
# PROFIL MEMBRE
# ════════════════════════════════════════════════════════════════════════

@app.get("/members/{telegram_id}/profile")
async def api_member_profile(telegram_id: int):
    profile = await get_member_profile(telegram_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Membre introuvable")
    return profile


@app.get("/members/{telegram_id}/categories")
async def api_member_categories(telegram_id: int):
    return await get_member_categories(telegram_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)