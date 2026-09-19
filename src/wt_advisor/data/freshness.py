"""Central stale-data policy."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from wt_advisor.domain.models import DatasetType, Freshness, FrozenModel


class FreshnessPolicy(FrozenModel):
    metadata_fresh_days: int = Field(default=14, ge=0)
    metadata_aging_days: int = Field(default=45, ge=0)
    statistics_fresh_days: int = Field(default=30, ge=0)
    statistics_aging_days: int = Field(default=90, ge=0)


def evaluate_freshness(
    dataset_type: DatasetType,
    observed_on: date | None,
    as_of: date,
    policy: FreshnessPolicy | None = None,
) -> Freshness:
    """Classify age without preventing use of old evidence."""

    if observed_on is None:
        return Freshness.UNKNOWN
    selected = policy or FreshnessPolicy()
    age = max(0, (as_of - observed_on).days)
    if dataset_type == DatasetType.GLOBAL_STATISTICS:
        fresh_days, aging_days = selected.statistics_fresh_days, selected.statistics_aging_days
    else:
        fresh_days, aging_days = selected.metadata_fresh_days, selected.metadata_aging_days
    if age <= fresh_days:
        return Freshness.FRESH
    if age <= aging_days:
        return Freshness.AGING
    return Freshness.STALE
