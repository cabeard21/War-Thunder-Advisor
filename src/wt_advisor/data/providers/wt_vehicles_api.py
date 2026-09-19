"""Independent adapter for the community War Thunder Vehicles API.

The adapter deliberately stops at provider observations. Persisting snapshots and
applying curated corrections belong to storage/normalization layers.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from wt_advisor.data.models import (
    RawDatasetMetadata,
    RawVehicleDataset,
    RawVehicleRecord,
    canonical_bytes,
)
from wt_advisor.domain.models import (
    AvailabilityType,
    DatasetType,
    Freshness,
    Nation,
    VehicleClass,
)


class ByteCache(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...


class ResponseTooLargeError(ValueError):
    """The provider response exceeded the configured safety limit."""


class ProviderPayloadError(ValueError):
    """The upstream response could not be mapped unambiguously."""


CLASS_ALIASES = {
    "light_tank": VehicleClass.LIGHT_TANK,
    "light tank": VehicleClass.LIGHT_TANK,
    "lighttank": VehicleClass.LIGHT_TANK,
    "medium_tank": VehicleClass.MEDIUM_TANK,
    "medium tank": VehicleClass.MEDIUM_TANK,
    "mediumtank": VehicleClass.MEDIUM_TANK,
    "heavy_tank": VehicleClass.HEAVY_TANK,
    "heavy tank": VehicleClass.HEAVY_TANK,
    "heavytank": VehicleClass.HEAVY_TANK,
    "tank_destroyer": VehicleClass.TANK_DESTROYER,
    "tank destroyer": VehicleClass.TANK_DESTROYER,
    "tankdestroyer": VehicleClass.TANK_DESTROYER,
    "spaa": VehicleClass.SPAA,
    "anti_air": VehicleClass.SPAA,
}

# Provider-scoped aliases preserve stable internal identities. Source IDs remain
# stored verbatim alongside the canonical IDs as provenance.
CANONICAL_ID_ALIASES = {
    "us_m10": "us_m10_gmc",
    "us_halftrack_m13": "us_m13_mgmc",
    "us_halftrack_m15": "us_m15_cgmc",
    "us_halftrack_m16": "us_m16_mgmc",
    "us_m22_locust": "us_m22",
    "us_m24_chaffee": "us_m24",
    "us_m2a4_1st_armor_div": "us_m2a4_first_tank_div",
    "us_halftrack_m3_75mm_gmc": "us_m3_gmc",
    "us_m4_sherman": "us_m4",
    "us_m4a1_1942_sherman": "us_m4a1",
    "us_m4a3_105_sherman": "us_m4a3_105",
    "us_m5a1_stuart": "us_m5a1",
}


class WarThunderVehiclesApiProvider:
    """Fetch bounded/paginated vehicle records with retry and cache hooks."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: ByteCache | None = None,
        base_url: str = "https://wtvehiclesapi.duckdns.org/api/vehicles",
        timeout_seconds: float = 10,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        max_response_bytes: int = 5_000_000,
        page_size: int = 200,
        max_pages: int = 100,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be between one and 200")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        parsed_url = urlsplit(base_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an HTTPS URL without credentials or fragments")
        if parsed_url.hostname.lower() == "localhost":
            raise ValueError("base_url must not target localhost")
        try:
            address = ipaddress.ip_address(parsed_url.hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("base_url must target a public address")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._cache = cache
        self._base_url = base_url
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay_seconds
        self._max_bytes = max_response_bytes
        self._page_size = page_size
        self._max_pages = max_pages

    def _request(self, page: int) -> tuple[bytes, str | None]:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            try:
                with self._client.stream(
                    "GET",
                    self._base_url,
                    params={
                        "page": page,
                        "limit": self._page_size,
                        "country": "usa",
                        "excludeEventVehicles": "false",
                        "excludeKillstreak": "true",
                    },
                ) as response:
                    last_response = response
                    if response.status_code != 429 and response.status_code < 500:
                        response.raise_for_status()
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            try:
                                declared_size = int(declared)
                            except ValueError as exc:
                                raise ProviderPayloadError(
                                    "vehicle API returned an invalid Content-Length"
                                ) from exc
                            if declared_size > self._max_bytes:
                                raise ResponseTooLargeError(
                                    "vehicle API response exceeds configured limit"
                                )
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            if len(content) + len(chunk) > self._max_bytes:
                                raise ResponseTooLargeError(
                                    "vehicle API response exceeds configured limit"
                                )
                            content.extend(chunk)
                        return bytes(content), response.headers.get("etag")
            except httpx.TransportError:
                if attempt + 1 == self._max_attempts:
                    raise
            if self._retry_delay:
                time.sleep(self._retry_delay * (attempt + 1))
        assert last_response is not None
        last_response.raise_for_status()
        raise RuntimeError("unreachable")

    @staticmethod
    def _decode_page(content: bytes) -> tuple[list[dict[str, Any]], int | None]:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderPayloadError(f"vehicle API returned invalid JSON: {exc}") from exc
        if isinstance(payload, list):
            records, total_pages = payload, None
        elif isinstance(payload, dict):
            candidate = payload.get("vehicles", payload.get("data"))
            if not isinstance(candidate, list):
                raise ProviderPayloadError("vehicle API object has no vehicles/data array")
            records = candidate
            pagination = payload.get("pagination", {})
            raw_total = payload.get("totalPages", payload.get("total_pages"))
            if raw_total is None and isinstance(pagination, dict):
                raw_total = pagination.get("totalPages", pagination.get("total_pages", 1))
            total_pages = int(raw_total or 1)
        else:
            raise ProviderPayloadError("vehicle API response must be an array or object")
        if not all(isinstance(row, dict) for row in records):
            raise ProviderPayloadError("vehicle API records must be objects")
        return records, total_pages

    @staticmethod
    def _vehicle_id(raw: dict[str, Any]) -> str:
        source_id = raw.get("identifier", raw.get("id"))
        if not isinstance(source_id, str) or not source_id.strip():
            raise ProviderPayloadError("vehicle record is missing identifier/id")
        normalized = re.sub(r"[^a-z0-9]+", "_", source_id.lower()).strip("_")
        if not normalized:
            raise ProviderPayloadError("vehicle identifier cannot be normalized")
        return CANONICAL_ID_ALIASES.get(normalized, normalized)

    @staticmethod
    def _br_tenths(raw_value: Any) -> int:
        try:
            value = Decimal(str(raw_value)) * 10
        except (InvalidOperation, ValueError) as exc:
            raise ProviderPayloadError("vehicle record has invalid realistic BR") from exc
        if value != value.to_integral_value():
            raise ProviderPayloadError("realistic BR must have at most one decimal place")
        return int(value)

    @staticmethod
    def _rank(raw_value: Any) -> int:
        if isinstance(raw_value, int):
            return raw_value
        text = str(raw_value).strip().upper()
        roman = {
            "I": 1,
            "II": 2,
            "III": 3,
            "IV": 4,
            "V": 5,
            "VI": 6,
            "VII": 7,
            "VIII": 8,
        }
        if text in roman:
            return roman[text]
        try:
            return int(text)
        except ValueError as exc:
            raise ProviderPayloadError(f"invalid vehicle rank/era: {raw_value!r}") from exc

    @classmethod
    def _normalize(cls, raw: dict[str, Any]) -> RawVehicleRecord:
        source_id = raw.get("identifier", raw.get("id"))
        country = str(raw.get("country", raw.get("nation", ""))).lower()
        if country not in {"usa", "us", "united states", "united_states"}:
            raise ProviderPayloadError(f"unsupported nation in Milestone 1 adapter: {country!r}")
        raw_class = str(
            raw.get(
                "vehicle_class",
                raw.get("vehicleClass", raw.get("vehicle_type", raw.get("type", ""))),
            )
        ).lower()
        try:
            vehicle_class = CLASS_ALIASES[raw_class]
        except KeyError as exc:
            raise ProviderPayloadError(f"unsupported vehicle class: {raw_class!r}") from exc
        realistic_br = raw.get(
            "realistic_ground_br",
            raw.get("realistic_br", raw.get("realisticBR", raw.get("br_realistic"))),
        )
        if realistic_br is None:
            raise ProviderPayloadError("vehicle record is missing a Ground Realistic BR")
        premium = bool(raw.get("is_premium", raw.get("premium", False)))
        if bool(raw.get("is_pack", False)):
            availability = AvailabilityType.PACK
        elif bool(raw.get("squadron_vehicle", False)):
            availability = AvailabilityType.SQUADRON
        elif bool(raw.get("event", False)):
            availability = AvailabilityType.EVENT
        elif premium:
            availability = AvailabilityType.PREMIUM
        else:
            availability = AvailabilityType.RESEARCH_TREE
        return RawVehicleRecord(
            vehicle_id=cls._vehicle_id(raw),
            source_vehicle_id=str(source_id),
            name=str(raw.get("name") or source_id),
            nation=Nation.USA,
            vehicle_class=vehicle_class,
            rank=cls._rank(raw.get("rank", raw.get("era"))),
            ground_realistic_br=cls._br_tenths(realistic_br),
            research_cost=raw.get("req_exp"),
            purchase_cost=raw.get("value"),
            availability_type=availability,
        )

    def fetch_vehicles(self) -> RawVehicleDataset:
        cache_key = f"wt-vehicles-api:{self._base_url}"
        cached = self._cache.get(cache_key) if self._cache else None
        source_revision: str | None = None
        if cached is not None:
            if len(cached) > self._max_bytes:
                raise ResponseTooLargeError("cached vehicle API payload exceeds configured limit")
            try:
                decoded = json.loads(cached)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderPayloadError("cached vehicle API payload is invalid") from exc
            if not isinstance(decoded, list):
                raise ProviderPayloadError("cached vehicle API payload must be an array")
            all_records = decoded
        else:
            first_content, source_revision = self._request(0)
            all_records, total_pages = self._decode_page(first_content)
            total_size = len(first_content)
            if total_pages is None:
                pages = range(1, self._max_pages) if len(all_records) == self._page_size else ()
            else:
                if total_pages > self._max_pages:
                    raise ProviderPayloadError(
                        "vehicle API pagination exceeds configured page limit"
                    )
                pages = range(1, total_pages)
            for page in pages:
                content, page_revision = self._request(page)
                total_size += len(content)
                if total_size > self._max_bytes:
                    raise ResponseTooLargeError(
                        "combined paginated response exceeds configured limit"
                    )
                page_records, _ = self._decode_page(content)
                all_records.extend(page_records)
                source_revision = source_revision or page_revision
                if total_pages is None and len(page_records) < self._page_size:
                    break
            else:
                if total_pages is None and len(all_records) >= self._page_size * self._max_pages:
                    raise ProviderPayloadError(
                        "vehicle API pagination exceeded configured page limit"
                    )
            cached = canonical_bytes(all_records)
            if self._cache:
                self._cache.set(cache_key, cached)
        ground_records = tuple(
            record
            for record in all_records
            if str(
                record.get(
                    "vehicle_class",
                    record.get(
                        "vehicleClass", record.get("vehicle_type", record.get("type", ""))
                    ),
                )
            ).lower()
            in CLASS_ALIASES
        )
        rows = tuple(self._normalize(record) for record in ground_records)
        if not rows:
            raise ProviderPayloadError("vehicle API returned no supported USA ground vehicles")
        retrieved_at = datetime.now(UTC)
        content = canonical_bytes(all_records)
        metadata = RawDatasetMetadata(
            snapshot_id=f"wt-vehicles-api-{retrieved_at:%Y%m%dT%H%M%SZ}",
            dataset_type=DatasetType.VEHICLE_METADATA,
            provider="war_thunder_vehicles_community_api",
            retrieved_at=retrieved_at,
            source_revision=source_revision,
        )
        return RawVehicleDataset(
            snapshot=metadata.snapshot(content, Freshness.FRESH),
            records=rows,
            raw_content=content,
        )
