"""
gold_write_queue.py — File d'attente + worker pour les écritures Gold.

Principe :
  - Les handlers Telegram font le calcul (Python pur, instantané) et
    répondent à l'utilisateur TOUT DE SUITE.
  - L'écriture en base est empilée dans une queue en mémoire et traitée
    par UN SEUL worker, dans l'ordre, une écriture à la fois.
  - Conséquence : même avec 1000 clics simultanés après un broadcast,
    jamais plus d'une connexion DB utilisée pour ce flux → plus de
    saturation du pool, plus de "Query is too old" en cascade.
  - Si un job échoue (DB tombée, deadlock, etc.), l'admin reçoit un
    message Telegram immédiat avec le détail de l'erreur.

Intégration (dans main.py, au démarrage de l'application) :

    from gold_write_queue import start_gold_write_worker
    start_gold_write_worker(bot)   # bot = ton Application.bot, après build()
"""

import asyncio
import logging
import traceback
from datetime import datetime

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

# Queue partagée — une seule instance pour tout le process
gold_write_queue: asyncio.Queue = asyncio.Queue()

_worker_bot = None
_worker_task: asyncio.Task | None = None


def start_gold_write_worker(bot):
    """À appeler une seule fois au démarrage de l'application."""
    global _worker_bot, _worker_task
    _worker_bot = bot
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_gold_write_worker_loop())
        logger.info("[gold_write_queue] Worker démarré.")


async def enqueue_write(job_name: str, fn, *args, **kwargs):
    """
    Empile une écriture DB à exécuter par le worker.

    job_name : nom court pour les logs et les alertes admin (ex: "confirm_entry")
    fn       : fonction async à exécuter
    *args / **kwargs : arguments passés à fn
    """
    await gold_write_queue.put({
        "job_name": job_name,
        "fn": fn,
        "args": args,
        "kwargs": kwargs,
        "queued_at": datetime.now(),
    })


async def _gold_write_worker_loop():
    """
    Boucle infinie : dépile et exécute les jobs un par un.
    Ne s'arrête jamais — si un job plante, on log + alerte admin,
    puis on passe au job suivant sans interrompre la boucle.
    """
    while True:
        job = await gold_write_queue.get()
        job_name  = job["job_name"]
        queued_at = job["queued_at"]

        try:
            await job["fn"](*job["args"], **job["kwargs"])

            wait_time = (datetime.now() - queued_at).total_seconds()
            if wait_time > 5:
                # Le job a attendu longtemps dans la queue — signe que
                # le volume dépasse la capacité de traitement. À surveiller.
                logger.warning(
                    f"[gold_write_queue] job '{job_name}' traité après {wait_time:.1f}s d'attente "
                    f"(queue size actuelle: {gold_write_queue.qsize()})"
                )

        except Exception as e:
            await _alert_admin_job_failed(job_name, e, job["args"], job["kwargs"])

        finally:
            gold_write_queue.task_done()


async def _alert_admin_job_failed(job_name: str, error: Exception, args, kwargs):
    """Notifie l'admin immédiatement si une écriture DB échoue."""
    tb = traceback.format_exc()
    logger.error(f"[gold_write_queue] job '{job_name}' ÉCHOUÉ: {error}\n{tb}")

    if not _worker_bot:
        return

    # On tronque le traceback pour rester dans la limite Telegram (4096 caractères)
    tb_short = tb[-1500:] if len(tb) > 1500 else tb

    text = (
        f"🔴 *Échec écriture DB — Gold*\n\n"
        f"Job : `{job_name}`\n"
        f"Erreur : `{str(error)[:200]}`\n"
        f"Queue restante : {gold_write_queue.qsize()} job(s)\n\n"
        f"```\n{tb_short}\n```"
    )

    try:
        await _worker_bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")
    except Exception as notify_err:
        # Si même la notification admin échoue (texte trop long, markdown invalide...),
        # on retente en texte brut sans formatage pour ne rien perdre.
        logger.error(f"[gold_write_queue] notification admin échouée: {notify_err}")
        try:
            await _worker_bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Échec écriture DB - job '{job_name}' - erreur: {str(error)[:300]}"
            )
        except Exception:
            pass


async def get_queue_status() -> dict:
    """Utilitaire pour un endpoint admin / commande de diagnostic."""
    return {
        "pending_jobs": gold_write_queue.qsize(),
        "worker_running": _worker_task is not None and not _worker_task.done(),
    }