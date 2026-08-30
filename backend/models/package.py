"""Packages and what they include."""
from typing import Optional

from pydantic import BaseModel


class PackageIn(BaseModel):
    """A named bundle a rate can point at. 'Elite', 'Bed & breakfast', 'Spa retreat'."""
    name: str


class InclusionIn(BaseModel):
    """One thing a package includes, in one outlet, so often.

    `ref_id` means different things by scope and is deliberately one field rather than
    three optional ones: it is a menu item's id for `item`, a category name for
    `category`, and unused for `outlet`. Three nullable columns would let two of them be
    filled at once, and then the rule that reads them has to decide which wins.
    """
    outlet_id: str
    scope: str
    ref_id: Optional[str] = None
    quantity: int = 1
    period: str
