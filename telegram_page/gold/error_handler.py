"""
error_handler.py — Error handler global pour l'Application Telegram.

Inchangé par rapport à la version précédente : ce fichier est générique
(gestion des exceptions Telegram non traitées), sans rapport avec la
logique métier Gold — il n'a donc pas été touché par la bascule v8.

À importer et enregistrer une seule fois dans script.py :

    from telegram_page.gold.error_handler import error_handler
    app.add_error_handler(error_handler)
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
            logger.debug(f"[error_handler] callback expiré ignoré: {msg}")
            return

    update_repr = update.update_id if isinstance(update, Update) else repr(update)
    logger.error(f"[error_handler] Exception non gérée sur update={update_repr}: {err}", exc_info=err)