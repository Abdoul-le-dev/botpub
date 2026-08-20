"""
form_scheduler.py — Déclenchement automatique des formulaires planifiés.

Utilise APScheduler pour :
  - Exécuter un formulaire à une date/heure fixe vers une catégorie
  - Recharger les jobs au démarrage depuis la DB
  - Ajouter/supprimer des jobs dynamiquement depuis l'API

Intégration dans api.py (lifespan) :
    from form_scheduler import start_scheduler, stop_scheduler
    await start_scheduler(bot)
    ...
    stop_scheduler()

Dépendance :
    pip install apscheduler
"""

import json
import re
import asyncio
from datetime import datetime
from subscription_sync import sync_clients_actifs
from relance.relance      import get_due_relances, count_members_in_categorie
from telegram_page.broadcast_engine import broadcast_engine


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from form.form import get_form_by_id, get_all_forms
from form.form_engine import broadcast_form
from db import get_db

_scheduler: BackgroundScheduler | None = None
_bot       = None
_admin_id: int | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def get_bot():
    return _bot

def get_admin_id():
    return _admin_id


# ════════════════════════════════════════════════════════════════════════════
# INIT
# ════════════════════════════════════════════════════════════════════════════

async def start_scheduler(bot, admin_id: int = None):
    """
    Démarre APScheduler et charge les formulaires planifiés depuis la DB.
    À appeler une seule fois au démarrage (lifespan FastAPI).
    """
    global _scheduler, _bot, _admin_id, _main_loop
    _bot       = bot
    _admin_id  = admin_id
    _main_loop = asyncio.get_running_loop()  # loop principal d'uvicorn, capturé ici

    _scheduler = BackgroundScheduler(timezone="Europe/Paris")
    _scheduler.start()
    _scheduler.add_job(
        _run_sync_clients_actifs,
        trigger="cron",
        hour=12,
        minute=48,
        id="sync_clients_actifs",
        replace_existing=True,
    )
    print("[form_scheduler] Job sync_clients_actifs enregistré → 10h20 chaque jour")

    _scheduler.add_job(
        _run_relances_check,
        trigger="cron",
        minute="*",
        id="relances_check",
        replace_existing=True,
        misfire_grace_time=50,
    )
    print("[form_scheduler] Job relances_check enregistré → chaque minute")

    await _reload_scheduled_forms()
    print("[form_scheduler] Scheduler démarré.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[form_scheduler] Scheduler arrêté.")


# ════════════════════════════════════════════════════════════════════════════
# JOB : SYNC CLIENTS ACTIFS
# ════════════════════════════════════════════════════════════════════════════

def _run_sync_clients_actifs():
    """
    Appelé par APScheduler (thread séparé, distinct du loop uvicorn).

    Le pool aiomysql (_pool dans db.py) est créé une seule fois sur le loop
    principal d'uvicorn via init_pool(). Si on fait asyncio.run(...) ici,
    un second event loop est créé dans ce thread, et toute tentative
    d'utiliser _pool depuis ce loop étranger lève :
        RuntimeError: ... attached to a different loop

    On planifie donc la coroutine sur le loop principal via
    run_coroutine_threadsafe, qui est le mécanisme thread-safe prévu par
    asyncio pour ce cas précis.
    """
    print("[form_scheduler] Lancement job sync_clients_actifs")

    if _main_loop and _main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(sync_clients_actifs(), _main_loop)
        try:
            future.result()  # bloque le thread scheduler, propage les exceptions
        except Exception as e:
            print(f"[form_scheduler] Erreur sync_clients_actifs: {e}")
    else:
        # Fallback : ne devrait pas arriver en prod (loop principal toujours
        # actif tant qu'uvicorn tourne), mais évite un échec silencieux.
        print("[form_scheduler] _main_loop indisponible, fallback asyncio.run()")
        try:
            asyncio.run(sync_clients_actifs())
        except Exception as e:
            print(f"[form_scheduler] Erreur sync_clients_actifs (fallback): {e}")


# ════════════════════════════════════════════════════════════════════════════
# JOB : RELANCES AUTOMATIQUES
# ════════════════════════════════════════════════════════════════════════════

def _extract_jours_restants(name_categorie: str) -> str | None:
    """
    Dérive jours_restants à partir du nom de la catégorie plutôt que d'un
    mapping en dur : 'clients_j7' -> '7', 'clients_j3' -> '3', etc.
    Reste valide si une catégorie clients_j14 ou similaire est ajoutée
    plus tard, sans modifier ce fichier.
    Retourne None si le nom ne contient pas de motif jN (ex: clients_actifs,
    clients_expires) — la variable +jours_restants est alors simplement
    absente du message final si elle n'est pas utilisée par le texte.
    """
    match = re.search(r"_j(\d+)$", name_categorie)
    return match.group(1) if match else None


async def _check_and_send_relances():
    """
    Appelée chaque minute par APScheduler (via _run_relances_check).

    1. Demande à la DB quelles relances ont un créneau actif tombant sur
       l'heure courante (HH:MM, Europe/Paris).
    2. Pour chacune, vérifie qu'il y a au moins un membre dans la
       catégorie (évite un appel broadcast_engine inutile sur un segment
       vide — broadcast_engine gérerait ce cas mais autant filtrer tôt).
    3. Calcule jours_restants selon le nom de catégorie et déclenche
       broadcast_engine avec tag='relance_<categorie>' pour que
       l'historique (broadcast_history) reste filtrable par relance.
    """
    heure_courante = datetime.now().strftime("%H:%M")
    relances = await get_due_relances(heure_courante)

    if not relances:
        return

    for r in relances:
        name_categorie = r["name_categorie"]
        member_count   = await count_members_in_categorie(name_categorie)

        if member_count == 0:
            print(f"[form_scheduler] Relance '{name_categorie}' due à {heure_courante} mais 0 membre, ignorée.")
            continue

        jours_restants = _extract_jours_restants(name_categorie)
        variables = {"+jours_restants": jours_restants} if jours_restants else {}

        print(f"[form_scheduler] Relance '{name_categorie}' déclenchée à {heure_courante} → {member_count} membre(s)")

        payload = {
            "message":  r["message"],
            "format":   "text",
            "category": name_categorie,
            "variables": variables,
            "tag":      f"relance_{name_categorie}",
        }

        try:
            report = await broadcast_engine(_bot, payload)
            print(f"[form_scheduler] Relance '{name_categorie}' terminée : {report}")
        except Exception as e:
            print(f"[form_scheduler] Erreur lors de la relance '{name_categorie}': {e}")


def _run_relances_check():
    """
    Appelé par APScheduler (thread séparé, distinct du loop uvicorn).
    Même mécanisme que _run_sync_clients_actifs : la coroutine est
    planifiée sur _main_loop via run_coroutine_threadsafe pour éviter
    la collision de event loop avec le pool aiomysql.
    """
    if _main_loop and _main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_check_and_send_relances(), _main_loop)
        try:
            future.result()
        except Exception as e:
            print(f"[form_scheduler] Erreur relances_check: {e}")
    else:
        print("[form_scheduler] _main_loop indisponible, fallback asyncio.run() (relances_check)")
        try:
            asyncio.run(_check_and_send_relances())
        except Exception as e:
            print(f"[form_scheduler] Erreur relances_check (fallback): {e}")


# ════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DES FORMULAIRES PLANIFIÉS
# ════════════════════════════════════════════════════════════════════════════

async def _reload_scheduled_forms():
    """Recharge tous les formulaires actifs de type 'scheduled' depuis la DB."""
    forms = await get_all_forms(actif_only=True)
    for form in forms:
        if form.get("trigger_type") == "scheduled" and form.get("trigger_value"):
            _register_form_job(form)


def _register_form_job(form: dict):
    """
    Enregistre un job APScheduler pour un formulaire planifié.

    trigger_value peut être :
      - Une date ISO : "2025-09-15T09:00:00"  → exécution unique
      - Un cron      : "0 9 * * 1"            → chaque lundi à 9h
      - Cron lisible : "lundi 09:00"          → converti en cron
    """
    tv     = form.get("trigger_value", "")
    job_id = f"form_{form['id']}"

    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

    trigger = _parse_trigger(tv)
    if not trigger:
        print(f"[form_scheduler] trigger invalide pour form {form['id']}: '{tv}'")
        return

    _scheduler.add_job(
        func=_run_form_job,
        trigger=trigger,
        id=job_id,
        kwargs={"form_id": form["id"]},
        replace_existing=True,
        misfire_grace_time=300,
    )
    print(f"[form_scheduler] Job '{job_id}' enregistré → trigger: {tv}")


def _parse_trigger(tv: str):
    """Convertit trigger_value en un trigger APScheduler."""
    if not tv:
        return None

    tv = tv.strip()

    # Date ISO (ex: "2025-09-15T09:00:00")
    try:
        dt = datetime.fromisoformat(tv)
        if dt > datetime.now():
            return DateTrigger(run_date=dt)
        else:
            print(f"[form_scheduler] Date passée : {tv}")
            return None
    except ValueError:
        pass

    # Cron standard (ex: "0 9 * * 1")
    parts = tv.split()
    if len(parts) == 5:
        try:
            return CronTrigger.from_crontab(tv, timezone="Europe/Paris")
        except Exception:
            pass

    # Format lisible français (ex: "lundi 09:00")
    day_map = {
        "lundi": "mon", "mardi": "tue", "mercredi": "wed",
        "jeudi": "thu", "vendredi": "fri", "samedi": "sat", "dimanche": "sun",
    }
    parts = tv.lower().split()
    if len(parts) == 2 and parts[0] in day_map and ":" in parts[1]:
        try:
            h, m = parts[1].split(":")
            return CronTrigger(
                day_of_week=day_map[parts[0]],
                hour=int(h), minute=int(m),
                timezone="Europe/Paris"
            )
        except Exception:
            pass

    return None


# ════════════════════════════════════════════════════════════════════════════
# EXÉCUTION D'UN JOB (FORMULAIRES)
# ════════════════════════════════════════════════════════════════════════════

def _run_form_job(form_id: int):
    """
    Appelé par APScheduler (thread séparé).
    Encapsule la logique async dans une coroutine interne.
    """
    print(f"[form_scheduler] Lancement job form_id={form_id}")

    async def _inner():
        form = await get_form_by_id(form_id)
        if not form or not form.get("actif"):
            print(f"[form_scheduler] Formulaire {form_id} inactif ou introuvable.")
            return

        user_ids = await _get_target_users(form)
        if not user_ids:
            print(f"[form_scheduler] Aucun utilisateur cible pour le formulaire {form_id}.")
            return

        await broadcast_form(_bot, form_id, user_ids, _admin_id)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_inner(), loop)
        else:
            asyncio.run(_inner())
    except RuntimeError:
        asyncio.run(_inner())


async def _get_target_users(form: dict) -> list[int]:
    """
    Retourne la liste des telegram_id cibles.
    Lit l'option 'target_category' dans form['options'] ou prend tous les users.
    """
    options    = form.get("options", {})
    target_cat = options.get("target_category")

    async with get_db() as cur:
        if target_cat:
            await cur.execute("""
                SELECT DISTINCT u.telegram_id FROM users u
                JOIN categories c ON c.id_user = u.telegram_id
                WHERE c.name_categorie = %s AND u.telegram_id IS NOT NULL
            """, (target_cat,))
        else:
            await cur.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            )
        rows = await cur.fetchall()

    return [r["telegram_id"] for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE
# ════════════════════════════════════════════════════════════════════════════

def schedule_form(form: dict):
    """Appelé après save_form() pour enregistrer/mettre à jour le job."""
    if form.get("trigger_type") == "scheduled":
        _register_form_job(form)


def unschedule_form(form_id: int):
    """Supprime le job d'un formulaire."""
    job_id = f"form_{form_id}"
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        print(f"[form_scheduler] Job form_{form_id} supprimé.")


def get_scheduled_jobs() -> list[dict]:
    """Retourne tous les jobs planifiés (pour debug/dashboard)."""
    if not _scheduler:
        return []
    return [
        {
            "id":       job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger":  str(job.trigger),
        }
        for job in _scheduler.get_jobs()
    ]