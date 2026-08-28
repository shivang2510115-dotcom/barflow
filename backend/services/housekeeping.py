"""Housekeeping: what a room's status means, who may change it, and how a request moves.

Pure functions over plain strings — no database, no request — so every rule here is
readable in one place and testable without a server, beside `pricing.py`,
`availability.py` and `folio.py`. The routers in `routers/housekeeping.py` do the reading
and writing; what may happen is decided here.

**`housekeeping_status = "out_of_order"` is not `rooms.out_of_order`.** They are two
different axes and merging them is the one mistake this feature can make that costs a
hotel money:

* `rooms.out_of_order` is a list of **date ranges**. It controls what the booking engine
  will **sell**, it is the manager's to set on the Rooms screen, and
  `services/availability.py` reads it.
* `housekeeping_status = "out_of_order"` means **not usable right now**. It stops the
  desk assigning that room at check-in and does nothing else. It never touches
  availability, and nothing in this module knows what a date is.

An attendant reporting a broken shower must not silently withdraw the room from sale for
a fortnight. Separate fields, separate owners, and neither function below reads the other
one's.
"""

# ------------------------------ room status ------------------------------
CLEAN = "clean"
DIRTY = "dirty"
INSPECTED = "inspected"
OUT_OF_ORDER = "out_of_order"

# The whole vocabulary. Ordered as a room moves through it, and used by the model, the
# migration and the API's validation so none of them can hold a different list.
STATUSES = (CLEAN, DIRTY, INSPECTED, OUT_OF_ORDER)

# What a room is seeded with, and what the startup migration stamps onto every room that
# predates the field. A property switching this on has rooms it has been letting all
# along; calling them all dirty on the morning of the deploy would hand housekeeping a
# list of a hundred rooms that do not need doing.
DEFAULT_STATUS = CLEAN

# Ready means a guest can be sent to it. Both `clean` and `inspected` count, which is what
# lets a small property ignore inspection entirely and a larger one insist on it.
READY_STATUSES = (CLEAN, INSPECTED)

# Everyone whose job can touch a room's status at all. `waiter` and `kitchen` are absent
# on purpose and so is `platform_admin`, who belongs to no hotel.
SETTING_ROLES = ("admin", "manager", "housekeeping", "front_desk")

# The two roles accountable for inventory. An attendant reports a fault; one of these
# confirms it is fixed before the room can be sold again.
SENIOR_ROLES = ("admin", "manager")


def is_ready(status: str | None) -> bool:
    """Whether a room in this state can be handed to an arriving guest.

    One predicate, used by the check-in warning and the housekeeping screen alike, so
    the desk and the attendant cannot disagree about what "ready" means.

    Anything unrecognised — `None` on a record the migration has not reached, an empty
    string, a hand-edited value — is **not** ready. The permissive guess is the wrong one:
    "nobody has said this room is made up" is exactly the case this exists to surface.
    """
    return status in READY_STATUSES


def status_of(room: dict | None) -> str:
    """The status of a stored room, with the seed standing in for a record the migration
    has not reached yet.

    `clean` for such a room, and not because absence is comforting: it is what
    `migrations/backfill_housekeeping.py` is about to write, so the rule has to give the
    same answer whether or not the migration has run. Every caller inside this
    application resolves a room through here, which is why `is_ready` can afford to be
    stricter about a bare `None` handed to it from somewhere else.
    """
    return (room or {}).get("housekeeping_status") or DEFAULT_STATUS


def note_required(to_status: str | None) -> bool:
    """Whether setting this status has to say why.

    Only `out_of_order`. The note is the whole content of that status — a room marked
    broken with no reason is a room nobody can fix and nobody can put back.
    """
    return to_status == OUT_OF_ORDER


def can_set(role: str | None, from_status: str | None, to_status: str | None) -> bool:
    """The transition table: may somebody in this role move a room from here to there?

    | From           | To                 | Who                                          |
    |----------------|--------------------|----------------------------------------------|
    | any            | `dirty`            | housekeeping, front_desk, manager, admin     |
    | `dirty`        | `clean`            | housekeeping, manager, admin                 |
    | `clean`        | `inspected`        | manager, admin only                          |
    | any            | `out_of_order`     | housekeeping, manager, admin (note required) |
    | `out_of_order` | `dirty` or `clean` | manager, admin                               |

    Two rows overlap and the order they are applied in is the decision worth stating:
    **leaving `out_of_order` is judged by the `out_of_order` row, not by the "any" rows.**
    Otherwise "any -> dirty" would let the attendant who reported the broken shower put
    the room straight back into service, which is precisely what the table is arranged to
    prevent. On the housekeeping screen an `out_of_order` room therefore offers an
    attendant nothing at all: it is visibly waiting on someone else.

    **`from_status == to_status` is False, for every role including admin.** Nothing
    moves, so there is nothing to permit. The route answers a repeat tap before it asks
    this — returning the room unchanged and writing no event, so a double-tap on a phone
    in a corridor does not fill the log with noise — and if this said True, the no-op rule
    and this table would be two places that had to agree about the same double-tap.

    An unknown role, or an unknown status on either side, is False. A typo must not be
    the thing that grants a transition.
    """
    if role not in SETTING_ROLES:
        return False
    if from_status not in STATUSES or to_status not in STATUSES:
        return False
    if from_status == to_status:
        return False

    # Ahead of everything below: see the docstring. Clearing a fault is senior work.
    if from_status == OUT_OF_ORDER:
        return role in SENIOR_ROLES and to_status in (DIRTY, CLEAN)

    if to_status == DIRTY:
        # Including a room with a guest asleep in it. Mid-stay mess is normal, and status
        # never blocks anything for the guest already in the room — only the assignment
        # of that room to somebody new.
        return True
    if to_status == CLEAN:
        # From `dirty` and nowhere else. A room is made up after it has been used; an
        # `inspected` room reaching `clean` would be an inspection being undone, which is
        # not a thing anybody does — it gets used, which dirties it, and the cycle runs.
        return from_status == DIRTY and role in ("admin", "manager", "housekeeping")
    if to_status == INSPECTED:
        # The point of an inspection is that somebody other than the cleaner does it.
        return from_status == CLEAN and role in SENIOR_ROLES
    if to_status == OUT_OF_ORDER:
        return role in ("admin", "manager", "housekeeping")
    return False


# --------------------------------- jobs ----------------------------------
# A *request*: somebody asks for a room to be dealt with. Separate from the room's status
# and with a life of its own — raised, picked up, done — because "this room is dirty" and
# "the guest in 204 has asked for towels" are different facts and only one of them is
# answered by cleaning the room.
OPEN = "open"
IN_PROGRESS = "in_progress"
DONE = "done"
CANCELLED = "cancelled"

JOB_STATUSES = (OPEN, IN_PROGRESS, DONE, CANCELLED)

# Still waiting on somebody. What the housekeeping screen shows by default, and what a
# second request from the same room merges into rather than duplicating.
LIVE_JOB_STATUSES = (OPEN, IN_PROGRESS)

# Only `open` reaches the alert: acknowledging is what makes it stop appearing.
ALERT_STATUS = OPEN

PRIORITIES = ("low", "normal", "high")
DEFAULT_PRIORITY = "normal"

# Who may raise, acknowledge, finish or call off a job. Anyone working the floor can see
# that a room needs attention. A guest raises one too, through the in-room QR, which
# carries no role at all — see routers/housekeeping.py.
JOB_ROLES = ("admin", "manager", "front_desk", "housekeeping")

# Append-only in spirit: a job is never deleted and every end state is terminal, so "who
# asked for this and when" survives being finished, called off, or both at once.
_JOB_MOVES: dict[str, tuple[str, ...]] = {
    OPEN: (IN_PROGRESS, DONE, CANCELLED),
    # Straight to done without an acknowledgement is the ordinary case for a job an
    # attendant simply did on their way past.
    IN_PROGRESS: (DONE, CANCELLED),
    DONE: (),
    CANCELLED: (),
}


def can_move_job(from_status: str | None, to_status: str | None) -> bool:
    """The transition table for one request.

    **A done job cannot be reopened, and neither can a cancelled one.** Both are
    terminal. The record of who asked and when is the reason cancelling is a status
    rather than a delete, and reusing that record for a later problem would throw the
    same thing away by another route — a second request is a second job, which costs
    nothing and keeps both stories straight.

    Same-status is False, for the same reason it is in `can_set`: two staff acknowledging
    at once is not an error either of them should see, so the route treats the second one
    as a no-op rather than asking this and refusing.
    """
    if from_status not in JOB_STATUSES or to_status not in JOB_STATUSES:
        return False
    return to_status in _JOB_MOVES[from_status]


def job_is_live(status: str | None) -> bool:
    """Whether this job is still waiting on somebody."""
    return status in LIVE_JOB_STATUSES


# ------------------------------ the merge rule ------------------------------
# What separates the lines of a merged reason. A newline rather than "; " because the
# attendant reads this on a phone, one line per thing asked for.
REASON_SEPARATOR = "\n"


def merge_reason(existing: str | None, addition: str | None) -> str:
    """The reason a live job should carry after a second request arrives for its room.

    **A guest pressing twice is a guest unsure it worked, not a second problem.** So the
    second press appends rather than raising a duplicate: one card for one room, holding
    everything that has been said about it.

    Three rules, all of them about what an attendant is handed:

    * nothing new to say leaves the reason exactly as it stands. An empty reason is
      allowed — "something is wrong in 204" is worth knowing — but it must not blank out
      or pad what was already recorded.
    * words already in the thread are not repeated. Compared stripped and case-folded,
      because the guest retyping is the case this exists for, and two identical lines on
      one card read as two problems.
    * anything else is appended on its own line, oldest first, so the order the guest
      said things in survives.
    """
    lines = [ln.strip() for ln in (existing or "").split(REASON_SEPARATOR) if ln.strip()]
    new = (addition or "").strip()
    if not new:
        return REASON_SEPARATOR.join(lines)
    if new.casefold() in [ln.casefold() for ln in lines]:
        return REASON_SEPARATOR.join(lines)
    return REASON_SEPARATOR.join([*lines, new])
