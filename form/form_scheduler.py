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
import asyncio
from datetime import datetime
from subscription_sync import sync_clients_actifs


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from form.form import get_form_by_id, get_all_forms
from form.form_engine import broadcast_form
from db import get_db

_scheduler: BackgroundScheduler | None = None
_bot       = None
_admin_id: int | None = None


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
    global _scheduler, _bot, _admin_id
    _bot      = bot
    _admin_id = admin_id

    _scheduler = BackgroundScheduler(timezone="Europe/Paris")
    _scheduler.start()
    _scheduler.add_job(
    lambda: asyncio.run(sync_clients_actifs()),
    trigger="cron",
    hour=23,
    minute=50,
    id="sync_clients_actifs",
    replace_existing=True,
    )
    print("[form_scheduler] Job sync_clients_actifs enregistré → 23h50 chaque soir")

    await _reload_scheduled_forms()
    print("[form_scheduler] Scheduler démarré.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[form_scheduler] Scheduler arrêté.")


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
# EXÉCUTION D'UN JOB
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