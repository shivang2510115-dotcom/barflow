# HR and payroll

**Status:** design, awaiting review
**Date:** 2026-08-30
**Piece 5** of `2026-08-30-outlets-and-entitlements-design.md`, which deliberately left
this unspecified.

---

## What this is not

**It does not compute PF, ESI, professional tax or TDS.** Stated first because it is the
most important boundary in the document and the easiest one to erode later.

Those rates change, several are state-specific, and their thresholds move with headcount —
PF becomes mandatory above twenty employees, ESI applies below a wage ceiling. A statutory
figure this software computed *wrongly* would become a compliance problem the hotel
carries, not us, and it would be discovered by an inspector rather than by a test.

Every property of this size already has a CA who supplies those numbers. So deductions are
**named lines somebody enters** — "PF ₹1,800", "Professional tax ₹200". The software does
the arithmetic and keeps the record; it does not pretend to know tax law.

Statutory calculation is a deliberate second project, done with someone who can verify the
rates against the law of the state the property is in. It is not a follow-up task to be
squeezed in later.

Also out of scope: shift rostering, appraisals, recruitment, and anything resembling a
document vault. A staff record carries a document *number*, not a scan.

---

## Where it sits

Today a staff record is a login: name, phone, role, domains, outlet_ids, permissions. It
says what somebody may *do* and nothing about their *employment*. This adds the second
half, and keeps them in one record — `users` — because splitting them would mean every
screen that shows a person joins two collections and every leaver has to be deactivated
twice.

`users` stands outside tenancy (a login must be findable before its hotel is known), so
the employment fields live there too and every query says `_mine(user)` out loud, exactly
as `routers/staff.py` already does.

**Everything else is property-scoped and ordinary:** attendance, advances, salary runs and
payslips are all collections behind the bound handle, so no route grows a `property_id`
filter and `test_isolation.py` gains no allowlist entry.

---

## Piece A: the staff record grows up

Added to the user document:

```
joined_on          date the employment started
designation        free text — "Front Office Executive", "Commis II"
salary_monthly     the figure the payslip starts from
paid_leave_days    how many days a month are paid without being worked
emergency_contact  name and number
document_number    Aadhaar or PAN, as typed — a number, never a scan
```

All optional, all defaulted, because eighty-eight staff records already exist without
them and an account missing a joining date must keep working exactly as it does today.

**`salary_monthly` is not a permission.** It is money, and the roster is already
admin-only, so it rides on the existing `admin.staff` gate rather than inventing a key.
But it is stripped from the projection every non-admin route returns — a manager who can
see the roster must not see what everybody earns, and that is a projection change, not a
new screen.

---

## Piece B: attendance

One row per person per day:

```
attendance
  id · property_id · user_id · on (date)
  status     present | absent | leave | week_off | half_day
  note       free text, for the exception that needs explaining
  marked_by · marked_at
```

**The id is deterministic:** `uuid5(NAMESPACE, f"{user_id}|{on}")`, written with
`upsert=True`. Marking the same person twice for the same day corrects the row rather than
creating a second one — the same trick room nights and entitlement uses use, and for the
same reason: a double-tapped Save must not produce two answers to "was Priya in on
Tuesday".

**A day with no row is not an absence.** It is a day nobody marked, and the salary run
must treat it as present rather than deduct for it. Deducting for unmarked days would mean
that a manager who forgets to open the screen for a week silently cuts everybody's pay,
which is the kind of bug that is discovered on payday.

**Half day** counts as half a present day, and exists because it is what actually happens
when somebody leaves at lunch and the alternative is marking them absent for a full day.

---

## Piece C: advances

Money handed over before payday, recovered from the next run.

```
advances
  id · property_id · user_id
  amount · given_on · reason
  given_by · given_at
  recovered_in   the salary run that took it back, or null
```

Append-only. An advance given by mistake is reversed by a second row, never edited — this
is money that left the till and the folio ledger's reasoning applies unchanged.

An advance is recovered **in full by the next run**, not spread across months. Partial
recovery is a schedule, a schedule needs a plan, and a plan needs a screen; a hotel that
wants to spread one gives two smaller advances instead. If that turns out to be wrong, it
is an addition rather than a rewrite.

---

## Piece D: the salary run

A month, a property, and one payslip per person.

```
salary_runs
  id · property_id · month (YYYY-MM)
  status      draft | paid
  created_by · created_at · paid_at
  reversed_by · reversal_of      corrections, never edits

payslips
  id · property_id · run_id · user_id
  name · designation                     copied, not referenced
  salary_monthly · days_in_month
  present · half_days · paid_leave · unpaid_absence · week_offs
  gross
  additions[]    { label, amount }       overtime, incentive, tips
  deductions[]   { label, amount }       PF, professional tax — entered, never computed
  advance_recovered
  net
```

### The arithmetic, in one place

```
per_day        = salary_monthly / days_in_month
worked_credit  = present + (half_days / 2) + paid_leave + week_offs
unpaid         = days_in_month - worked_credit          (never below zero)
gross          = salary_monthly - (unpaid * per_day)
net            = gross + sum(additions) - sum(deductions) - advance_recovered
```

**A week-off is paid.** It is a day the person was not required, not a day they failed to
turn up, and treating it as unpaid would dock everybody four days a month.

**`unpaid` is floored at zero.** Attendance can exceed the month — a correction, a double
marking that predates the deterministic id, a manager marking a 31st in a 30-day month.
Paying somebody *more* because the arithmetic went negative is worse than paying them the
figure they were promised.

**Names and designations are copied onto the payslip, not referenced.** A payslip is a
record of what somebody was paid and what they were called at the time; a person who is
promoted in October must not find their August payslip retitled.

### Draft, then paid

A run is created as a **draft** and can be recomputed while it is. Marking it **paid**
freezes it — the same snapshot rule the guest bill follows, and for the same reason: the
payslip somebody was handed and the record in the system must not be able to disagree.

**A paid run is corrected by reversing it and creating another.** There is no edit route
and there is not going to be one. "What did we pay Priya in August" has exactly one
answer, forever, and it survives the argument.

---

## What the screens say

**Staff** gains the employment fields, admin-only, with salary visible only to an admin.

**Attendance** is a month grid: people down, days across, one tap to cycle
present → absent → leave → week-off → half-day. It is the screen a manager opens daily, so
it has to be fast and forgiving — marking is idempotent, and a mis-tap is corrected by
tapping again rather than by finding an edit button.

**Payroll** lists runs by month. Opening one shows every payslip with its arithmetic
visible — not a total to be trusted, but the working shown, because the first question
anybody asks about a payslip is "why is it this much".

---

## Testing

Beyond the ordinary, four cases earn named tests because each one is a way this goes wrong
quietly:

**An unmarked day does not dock pay.** A manager who never opens the attendance screen
must produce full salaries, not zero ones.

**Marking twice writes one row.** The deterministic id, exercised the way a double tap
exercises it.

**A paid run does not move.** Change attendance after marking a run paid; the payslip is
unchanged. This is the bill's snapshot test in a second costume.

**An advance is recovered exactly once.** Two runs in the same month, or a run reversed and
re-created, must not take the same ₹5,000 back twice.

Plus the standing one: a payslip in property A is invisible and unfetchable from property
B — 404, not 403.

---

## Order

1. **The staff record** — additive, unlocks everything, ships alone.
2. **Attendance** — usable on its own even before payroll exists; a manager gets a record
   of who worked.
3. **Advances** — small, and needed before a run can recover one.
4. **The salary run** — needs all three.
