"""Validated runtime configuration; private dashboard access is an explicit opt-in."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "testserver", "::1"})
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_AUTHORITY = re.compile(r"(?:\[([0-9a-fA-F:.]+)\]|([a-zA-Z0-9.-]+))(?::([0-9]+))?\Z")


def database_path(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve the shared durable path without requiring dashboard configuration."""
    environment = os.environ if environment is None else environment
    value = environment.get("WT_ADVISOR_DB", "wt-advisor.sqlite")
    if not value.strip() or "\x00" in value:
        raise ValueError("WT_ADVISOR_DB must be a nonempty filesystem path")
    return Path(value)


def normalize_host(value: str) -> str:
    """Accept an exact DNS hostname or IP, without URL/authority syntax or wildcards."""
    value = value.lower()
    if not value or any(character.isspace() for character in value) or "%" in value:
        raise ValueError("invalid host")
    try:
        address = ip_address(value)
    except ValueError:
        if len(value) > 253 or all(part.isdigit() for part in value.split(".")):
            raise ValueError("invalid host") from None
        if not all(_DNS_LABEL.fullmatch(part) for part in value.split(".")):
            raise ValueError("invalid host") from None
        return value
    if address.is_unspecified:
        raise ValueError("unspecified address is not an allowed host")
    return address.compressed


def authority_host(authority: str) -> str:
    """Parse a request Host, including IPv6 brackets, and reject malformed authorities."""
    matched = _AUTHORITY.fullmatch(authority)
    if matched is None:
        raise ValueError("invalid authority")
    ipv6, hostname, port = matched.groups()
    if ipv6 is not None and ip_address(ipv6).version != 6:
        raise ValueError("brackets require an IPv6 address")
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("invalid authority port")
    return normalize_host(ipv6 or hostname)


@dataclass(frozen=True)
class RuntimeSettings:
    database: Path
    bind_address: str
    port: int
    allowed_hosts: frozenset[str]

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        port: int | None = None,
    ) -> RuntimeSettings:
        environment = os.environ if environment is None else environment
        database = database_path(environment)
        bind_address = environment.get("WT_ADVISOR_BIND_ADDRESS", "127.0.0.1")
        if bind_address not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("WT_ADVISOR_BIND_ADDRESS must be 127.0.0.1 or 0.0.0.0")
        try:
            resolved_port = (
                int(environment.get("WT_ADVISOR_PORT", "8765")) if port is None else port
            )
        except ValueError:
            raise ValueError("WT_ADVISOR_PORT must be an integer between 1024 and 65535") from None
        if not 1024 <= resolved_port <= 65535:
            raise ValueError("WT_ADVISOR_PORT must be between 1024 and 65535")
        hosts = LOCAL_HOSTS
        if "WT_ADVISOR_ALLOWED_HOSTS" in environment:
            try:
                configured = frozenset(
                    normalize_host(host.strip())
                    for host in environment["WT_ADVISOR_ALLOWED_HOSTS"].split(",")
                )
            except ValueError:
                raise ValueError(
                    "WT_ADVISOR_ALLOWED_HOSTS must contain comma-separated exact hostnames or IPs; "
                    "no URLs, ports, or wildcards"
                ) from None
            hosts = hosts | configured
        if bind_address == "0.0.0.0" and not hosts - LOCAL_HOSTS:
            raise ValueError(
                "WT_ADVISOR_ALLOWED_HOSTS must include a private dashboard hostname or IP "
                "when binding to 0.0.0.0"
            )
        return cls(database, bind_address, resolved_port, hosts)
