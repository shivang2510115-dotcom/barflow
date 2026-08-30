"""What a place that serves a guest looks like on disk."""
from typing import Optional

from pydantic import BaseModel


class OutletIn(BaseModel):
    """What a hotel admin sends when adding an outlet.

    `domain`, `id` and `property_id` are deliberately absent. The domain is derived from
    the kind — a client that could choose it could create a salon nobody on staff is
    able to reach — and the other two belong to the server and the scoped handle.
    """
    name: str
    kind: str
    charges_to_folio: bool = True
    takes_direct_payment: bool = True


class OutletPatch(BaseModel):
    """A partial edit. Every field optional; absent means unchanged.

    `kind` is not editable. Changing it would move the outlet's domain out from under
    the staff already assigned to it, silently revoking their access to a place they
    work in. Deactivate this one and create the right one instead.
    """
    name: Optional[str] = None
    charges_to_folio: Optional[bool] = None
    takes_direct_payment: Optional[bool] = None
    active: Optional[bool] = None
