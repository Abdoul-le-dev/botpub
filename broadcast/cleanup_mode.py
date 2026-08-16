"""
broadcast/cleanup_mode.py — mode "nettoyage silencieux".

Principe (analogue à +prenom) :
  - Si le message contient le token `+nettoyage`, le broadcast entre en
    "mode nettoyage" : le token est retiré du texte, l'envoi se fait en
    silencieux (disable_notification=True), et le rapport final admin est
    reformulé "Rapport nettoyage" au lieu de "Diffusion terminée".
  - Sinon, comportement broadcast normal — zéro impact.

Le mode nettoyage change UNIQUEMENT :
  * un flag `silent` sur les appels d'envoi
  * le wording du rapport admin
  * la proposition de cleanup (rapport "vérification" au lieu de "broadcast")
  * les erreurs `network` sont EXCLUES de la proposition de suppression
    (un timeout n'implique pas que le user est mort)
"""

from __future__ import annotations

CLEANUP_TOKEN: str = "+nettoyage"


def is_cleanup_mode(text: str) -> bool:
    """True si le message active le mode nettoyage."""
    return bool(text) and CLEANUP_TOKEN in text


def strip_cleanup_token(text: str) -> str:
    """
    Retire le token +nettoyage du message (une ou plusieurs occurrences),
    puis nettoie les espaces/newlines redondants laissés par la suppression.
    """
    if not text or CLEANUP_TOKEN not in text:
        return text
    cleaned = text.replace(CLEANUP_TOKEN, "")
    # Nettoie les doubles espaces et lignes vides consécutives
    lines = [ln.rstrip() for ln in cleaned.split("\n")]
    out_lines: list[str] = []
    prev_empty = False
    for ln in lines:
        empty = not ln.strip()
        if empty and prev_empty:
            continue
        out_lines.append(ln)
        prev_empty = empty
    return "\n".join(out_lines).strip()