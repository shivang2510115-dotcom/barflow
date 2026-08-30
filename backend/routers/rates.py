"""Meal plans, seasonal rate periods, per-type rates, and GST slabs."""
from fastapi import APIRouter, Depends, HTTPException

from models.hotel import (
    MealPlan, MealPlanIn, Rate, RateIn, RatePeriod, RatePeriodIn, TaxSlab,
)
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, require_configuration

router = APIRouter()

# Rates, rate periods, meal plans and tax slabs are what every tariff on every folio is
# derived from: configuration, admin only.
#
# All four are setup-time: a hotel prices its rooms before it is approved to sell them,
# and a room type with no rate is refused at booking rather than priced at zero.
CONFIG = require_configuration("hotel", setup_time=True)

# All four lists are read by the one Rates screen.
READ = require_access("hotel", permission="hotel.rates", setup_time=True)


# --------------------------- meal plans ---------------------------
@router.get("/meal-plans")
async def list_meal_plans(user: dict = Depends(READ),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.meal_plans.find({}, {"_id": 0}).to_list(50)


@router.post("/meal-plans")
async def create_meal_plan(payload: MealPlanIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    plan = MealPlan(**payload.model_dump()).model_dump()
    await db.meal_plans.insert_one(plan)
    plan.pop("_id", None)
    return plan


@router.put("/meal-plans/{plan_id}")
async def update_meal_plan(plan_id: str, payload: MealPlanIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    result = await db.meal_plans.update_one({"id": plan_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Meal plan not found")
    return await db.meal_plans.find_one({"id": plan_id}, {"_id": 0})


# -------------------------- rate periods --------------------------
@router.get("/rate-periods")
async def list_rate_periods(user: dict = Depends(READ),
                            db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.rate_periods.find({}, {"_id": 0}).to_list(5000)


@router.post("/rate-periods")
async def create_rate_period(payload: RatePeriodIn, user: dict = Depends(CONFIG),
                             db: PropertyScopedDatabase = Depends(tenant_db)):
    if payload.end_date <= payload.start_date:
        raise HTTPException(400, "end_date must be after start_date")

    period = RatePeriod(**payload.model_dump()).model_dump()
    await db.rate_periods.insert_one(period)
    period.pop("_id", None)

    # Overlaps are legal — priority decides — but the desk should know when the outcome
    # is ambiguous, i.e. another active period at the same priority also covers a night
    # in this range.
    others = await db.rate_periods.find(
        {"id": {"$ne": period["id"]}, "active": True}, {"_id": 0}
    ).to_list(5000)
    clashes = [
        p["name"] for p in others
        if p["start_date"] < period["end_date"] and p["end_date"] > period["start_date"]
        and p.get("priority", 0) == period.get("priority", 0)
    ]
    return {**period, "overlap_warning": clashes or None}


@router.put("/rate-periods/{period_id}")
async def update_rate_period(period_id: str, payload: RatePeriodIn, user: dict = Depends(CONFIG),
                             db: PropertyScopedDatabase = Depends(tenant_db)):
    if payload.end_date <= payload.start_date:
        raise HTTPException(400, "end_date must be after start_date")
    result = await db.rate_periods.update_one({"id": period_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Rate period not found")
    return await db.rate_periods.find_one({"id": period_id}, {"_id": 0})


@router.delete("/rate-periods/{period_id}")
async def delete_rate_period(period_id: str, user: dict = Depends(CONFIG),
                             db: PropertyScopedDatabase = Depends(tenant_db)):
    # Deleting a period must not orphan the rates that reference it.
    await db.rates.delete_many({"period_id": period_id})
    await db.rate_periods.delete_one({"id": period_id})
    return {"ok": True}


# ------------------------------ rates -----------------------------
@router.get("/rates")
async def list_rates(user: dict = Depends(READ),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.rates.find({}, {"_id": 0}).to_list(500)


@router.post("/rates")
async def create_rate(payload: RateIn, user: dict = Depends(CONFIG),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    if not await db.room_types.find_one({"id": payload.room_type_id}):
        raise HTTPException(400, "Unknown room_type_id")
    if payload.period_id and not await db.rate_periods.find_one({"id": payload.period_id}):
        raise HTTPException(400, "Unknown period_id")
    # A rate pointing at a package that does not exist would sell an elite room that
    # includes nothing, and the guest would find out at the salon counter.
    if payload.package_id and not await db.packages.find_one({"id": payload.package_id}):
        raise HTTPException(400, "Unknown package_id")

    # A (room_type_id, period_id) pair must resolve to exactly one rate, or pricing
    # becomes non-deterministic — replace any existing row instead of inserting another.
    existing = await db.rates.find_one({
        "room_type_id": payload.room_type_id, "period_id": payload.period_id
    })
    if existing:
        await db.rates.update_one({"id": existing["id"]}, {"$set": payload.model_dump()})
        return await db.rates.find_one({"id": existing["id"]}, {"_id": 0})

    rate = Rate(**payload.model_dump()).model_dump()
    await db.rates.insert_one(rate)
    rate.pop("_id", None)
    return rate


@router.delete("/rates/{rate_id}")
async def delete_rate(rate_id: str, user: dict = Depends(CONFIG),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    await db.rates.delete_one({"id": rate_id})
    return {"ok": True}


# ---------------------------- tax slabs ---------------------------
@router.get("/tax-slabs")
async def list_tax_slabs(user: dict = Depends(READ),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.tax_slabs.find({}, {"_id": 0}).to_list(20)


@router.put("/tax-slabs")
async def replace_tax_slabs(slabs: list[TaxSlab], user: dict = Depends(CONFIG),
                            db: PropertyScopedDatabase = Depends(tenant_db)):
    """Replace the whole band table. Statutory GST rates change; they must never be
    hardcoded, so the entire table is editable data rather than a fixed set of rows."""
    if not slabs:
        raise HTTPException(400, "At least one slab is required")
    await db.tax_slabs.delete_many({})
    await db.tax_slabs.insert_many([s.model_dump() for s in slabs])
    return await db.tax_slabs.find({}, {"_id": 0}).to_list(20)
