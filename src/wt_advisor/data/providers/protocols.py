"""Transport-independent provider protocols."""

from typing import Protocol

from wt_advisor.data.models import RawStatisticsDataset, RawVehicleDataset


class VehicleDataProvider(Protocol):
    def fetch_vehicles(self) -> RawVehicleDataset: ...


class StatisticsProvider(Protocol):
    def fetch_vehicle_statistics(self) -> RawStatisticsDataset: ...
