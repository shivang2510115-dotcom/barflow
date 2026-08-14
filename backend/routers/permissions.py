"""The screen catalogue: every key the ticks on the staff screen can set.

Served rather than duplicated in the frontend, so the staff screen and the sidebar
cannot disagree about what exists — a key present in one and missing from the other is
either a screen nobody can grant or a tick that grants nothing.

Declared `shared` and readable by any signed-in user: the list of screen names is not
sensitive, it says nothing about who holds what, and the staff screen needs it.
"""
from fastapi import APIRouter, Depends

from security import require_access
from services.access import SCREENS, SHARED

router = APIRouter()

# Built once at import: the catalogue is a constant in code, not a query.
CATALOGUE = [
    {"key": key, "label": screen["label"], "section": screen["section"],
     # The domains a person must hold for this tick to do anything. Sent so the staff
     # screen can show a screen outside somebody's domains as unavailable, rather than
     # letting it be ticked and then refused with a 400 on save.
     "domains": list(screen["domains"])}
    for key, screen in SCREENS.items()
]


@router.get("/permissions")
async def list_permissions(user: dict = Depends(require_access(SHARED))):
    return CATALOGUE
