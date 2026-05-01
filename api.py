#api.py 
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database.database import migrate_categories_to_meta, get_data, get_data_users, get_categories_user, init_broadcast_history,  get_broadcast_history
import os
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from telegram import Bot
from fastapi import HTTPException, UploadFile, File
import csv  
from form.form_route import router as forms_router
from form.form import init_forms_db
from form.form_scheduler import start_scheduler, stop_scheduler
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
from telegram_page.broadcast_engine import broadcast_engine

from telegram_page.chat_route import router as chat_router
from telegram_page.chat import init_chat_tables, set_bot 

from contextlib import asynccontextmanager

from telegram_page.routes_trading import router as trading_router
from telegram_page.trading_journal import (
    init_trading_tables, reset_problem_tables,
    set_bot as set_trading_bot,
)
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_chat_tables() 
    set_bot(bot)
    init_forms_db()                          
    start_scheduler(bot, admin_id=571718066)
    set_trading_bot(bot)
    #reset_problem_tables()
    #init_trading_tables()    
    #init_broadcast_history()
   
    #migrate_categories_to_meta()      # crée conversations, subscriptions, migre messages
    # init_broadcast_history()  # si tu l'as déjà ailleurs, garde-le ici aussi
    yield
    stop_scheduler() 
 

load_dotenv()
bot = Bot(token=os.getenv("token"))

app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)
app.include_router(forms_router) 
app.include_router(trading_router) 


origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory="media"), name="media")


class RequestBody(BaseModel):
    text: str

class RequestData(BaseModel):
    userId: int


@app.get("/")
def health():
    return {"status": "ok"}


@app.post('/process')
async def getdata(data: RequestBody):
    return await get_data()


@app.post('/user')
async def get_data_user(data: RequestData):
    return await get_data_users(data.userId)

@app.post("/broadcast")
async def api_broadcast(payload: dict):
    print('yes')
    report = await broadcast_engine(bot, payload)
    return report

@app.get("/categories")
async def api_get_categorie():

    categorie = await  get_categories_user()

    return categorie

@app.get("/broadcast/history")
def api_get_broadcast_history():

    return  get_broadcast_history()



# ────────────────────────────────────────────────────────────────────────
# STATS GLOBALES
# ────────────────────────────────────────────────────────────────────────
 
@app.get("/categories/stats")
async def api_categories_stats():
    return await get_categories_stats()
 
 
# ────────────────────────────────────────────────────────────────────────
# CRUD CATÉGORIES
# ────────────────────────────────────────────────────────────────────────
 
@app.get("/categorie")
async def api_get_categories():
    return await get_categories()
 
 
@app.get("/categories/{name_categorie}")
async def api_get_category(name_categorie: str):
    cat = await get_category_by_name(name_categorie)
    if not cat:
        raise HTTPException(status_code=404, detail="Catégorie introuvable")
    return cat
 
 
@app.post("/categories")
async def api_create_category(payload: dict):
    """
    payload: {
        name_categorie, color?, description?,
        rule?: { trigger_type, trigger_value },
        member_ids?: [123, 456]
    }
    """
    if not payload.get("name_categorie"):
        raise HTTPException(status_code=400, detail="name_categorie requis")
    return await create_category(payload)
 
 
@app.put("/categories/{name_categorie}")
async def api_update_category(name_categorie: str, payload: dict):
    """
    payload: { new_name?, color?, description? }
    """
    return await update_category(name_categorie, payload)
 
 
@app.delete("/categories/{name_categorie}")
async def api_delete_category(name_categorie: str):
    """
    Supprime la catégorie + tous ses membres + ses règles.
    """
    return await drop_category(name_categorie)
 
 
# ────────────────────────────────────────────────────────────────────────
# MEMBRES
# ────────────────────────────────────────────────────────────────────────
 
@app.get("/categories/{name_categorie}/members")
async def api_get_members(
    name_categorie: str,
    search:         str  = None,
    active_only:    bool = False,
    inactive_only:  bool = False,
    limit:          int  = 50,
    offset:         int  = 0
):
    filters = {
        "search":        search,
        "active_only":   active_only,
        "inactive_only": inactive_only,
        "limit":         limit,
        "offset":        offset
    }
    return await get_category_members(name_categorie, filters)
 
 
@app.post("/categories/{name_categorie}/members")
async def api_add_members(name_categorie: str, payload: dict):
    """
    payload: { user_ids: [123, 456], added_by?: 'manual' }
    """
    if not payload.get("user_ids"):
        raise HTTPException(status_code=400, detail="user_ids requis")
    return await add_members_to_category(
        name_categorie,
        payload["user_ids"],
        payload.get("added_by", "manual")
    )
 
 
@app.delete("/categories/{name_categorie}/members/{telegram_id}")
async def api_remove_member(name_categorie: str, telegram_id: int):
    return await remove_member_from_category(name_categorie, telegram_id)
 
 
@app.post("/categories/members/move")
async def api_move_members(payload: dict):
    """
    payload: {
        source, destination,
        user_ids: [123] | 'all',
        action: 'move' | 'copy'
    }
    """
    required = ["source", "destination", "user_ids"]
    for field in required:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"{field} requis")
    return await move_members(payload)
 
 
@app.post("/categories/merge")
async def api_merge_categories(payload: dict):
    """
    payload: { target: str, sources: [str, str] }
    """
    if not payload.get("target") or not payload.get("sources"):
        raise HTTPException(status_code=400, detail="target et sources requis")
    return await merge_categories(payload["target"], payload["sources"])
 
 
@app.post("/categories/{name_categorie}/import")
async def api_import_csv(name_categorie: str, file: UploadFile = File(...)):
    """
    Import CSV — colonne attendue : user_id
    """
    content = await file.read()
    decoded = content.decode("utf-8")
    reader  = csv.DictReader(io.StringIO(decoded))
 
    user_ids = []
    for row in reader:
        uid = row.get("user_id") or row.get("telegram_id")
        if uid:
            try:
                user_ids.append(int(uid.strip()))
            except ValueError:
                pass  # ignorer les lignes invalides
 
    if not user_ids:
        raise HTTPException(status_code=400, detail="Aucun user_id valide trouvé dans le CSV")
 
    return await import_members_csv(name_categorie, user_ids)
 
 
# ────────────────────────────────────────────────────────────────────────
# RÈGLES
# ────────────────────────────────────────────────────────────────────────
 
@app.get("/categories/{name_categorie}/rules")
async def api_get_rules(name_categorie: str):
    return await get_category_rules(name_categorie)
 
 
@app.post("/categories/{name_categorie}/rules")
async def api_add_rule(name_categorie: str, payload: dict):
    """
    payload: { trigger_type, trigger_value? }
    trigger_type: link | inactivity | survey | subscription | trade_perf | keyword | no_open
    """
    if not payload.get("trigger_type"):
        raise HTTPException(status_code=400, detail="trigger_type requis")
    return await add_category_rule(name_categorie, payload)
 
 
@app.delete("/categories/rules/{rule_id}")
async def api_delete_rule(rule_id: int):
    return await delete_category_rule(rule_id)
 
 
# ────────────────────────────────────────────────────────────────────────
# STATS & INTERSECTIONS
# ────────────────────────────────────────────────────────────────────────
 
@app.get("/categories/{name_categorie}/stats")
async def api_category_stats(name_categorie: str):
    return await get_category_stats(name_categorie)
 
 
@app.get("/categories/{name_categorie}/intersections")
async def api_intersections(name_categorie: str):
    return await get_category_intersections(name_categorie)
 
 
# ────────────────────────────────────────────────────────────────────────
# PROFIL MEMBRE (drawer)
# ────────────────────────────────────────────────────────────────────────
 
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