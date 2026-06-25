"""
error_handler.py — Error handler global pour l'Application Telegram.

À importer et enregistrer une seule fois dans le fichier principal
(main.py ou équivalent) qui construit l'Application :

    from error_handler import error_handler
    app.add_handler(...)
    ...
    app.add_error_handler(error_handler)

Effet :
  - Les BadRequest "Query is too old" / "query id is invalid" sont
    ignorées silencieusement (déjà gérées localement par _safe_answer
    dans gold_broadcast.py et compagnie) — elles ne polluent plus les
    logs avec un traceback complet à chaque occurrence.
  - Toute autre exception non gérée est loggée proprement, avec
    traceback, au lieu de remonter comme "No error handlers are
    registered, logging exception."
"""

import logging
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error

    if isinstance(err, BadRequest):
        msg = str(err)
        if "too old" in msg or "query id is invalid" in msg or "query ID is invalid" in msg:
            # Callback expiré — déjà géré localement avec _safe_answer.
            # On log en debug seulement, pas la peine de spammer en warning/error.
            logger.debug(f"[error_handler] callback expiré ignoré: {msg}")
            return

    update_repr = update.update_id if isinstance(update, Update) else repr(update)
    logger.error(f"[error_handler] Exception non gérée sur update={update_repr}: {err}", exc_info=err)