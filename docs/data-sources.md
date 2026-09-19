# Milestone 1 data-source findings

Reviewed 2026-09-18. These notes distinguish provider evidence from the
independently curated acceptance fixture. Fixture values are test inputs, not a
claim that they are the current live-game values.

## Milestone 2 acquisition boundary

The War Thunder Vehicles community API remains the only automated remote
vehicle-data source. Milestone 2 may use its list and per-vehicle detail routes,
but does not crawl Gaijin or War Thunder Wiki pages. Manual JSON, YAML, and CSV
imports may cite official pages when every record retains its own source and
reference.

The upstream API source was rechecked at revision
`ac07d5364ee4dc09af91514be0c1b80e0c6699ad`. Its per-vehicle response exposes
`required_vehicle`, modification names/icons, `gun_stabilizer`, `has_ess`, and
weapon/ammunition details. Automated capability normalization is deliberately
narrow:

- scouting, artillery, smoke, and vertical stabilization require explicit
  provider fields or modification identifiers;
- a missing field is unknown, not false;
- high-caliber HE is not inferred from a locally invented caliber threshold;
- prerequisite edges are imported only when the provider supplies an explicit
  required vehicle.

The upstream implementation is GPL-3.0, but its data ultimately derives from
game files. Gaijin's current terms restrict database and AI-related uses of its
services/content without permission. This project therefore records terms and
source revisions, does not automate official-site extraction, and keeps manual
evidence auditable. This is an engineering boundary, not legal advice.

StatShark remains manual-import-only for Milestone 2. A documented user export
may be inspected and imported, but the application will not automate browser
scraping, bypass anti-bot controls, or call undocumented endpoints.

## War Thunder Vehicles community API

- Project: <https://github.com/Sgambe33/WarThunder-Vehicles-API>
- Current advertised host: <https://wtvehiclesapi.duckdns.org/>. The repository
  says the service moved hosts, is rate limited to 10,000 requests per 72 hours
  per IP/domain, and recommends caching.
- Source basis: automated extraction from public game-file datamines.
- Upstream implementation license: GPL-3.0. This project uses the remote public
  interface through an independent `httpx` adapter; it does not copy or vendor
  the upstream implementation or its database.
- Adapter safeguards: explicit timeout, bounded retry for transport/429/5xx
  failures, pagination, response-size limits, and injectable byte-cache hooks.
- Provider fields are observations. Storage retains the raw snapshot, maps provider IDs to stable
  internal IDs, and applies corrections separately. The adapter sends the provider's case-sensitive
  `country=usa` filter, rejects an empty supported-ground result, and ignores non-ground classes.
- Provider source IDs remain immutable aliases. A small explicit provider-scoped alias table maps
  known live IDs such as `us_m10` and `us_m22_locust` to stable canonical IDs such as
  `us_m10_gmc` and `us_m22`; display names are never identity keys.
- Redistribution of a bulk upstream data dump was not established by this
  review. Therefore Milestone 1 commits only a small, independently curated
  fixture and does not check in a response captured from the service.

The service is under active development, so its documentation and payload must
be revalidated before enabling a scheduled live import. Network smoke tests are
opt-in; CI uses an injected HTTP transport.

Milestone 1 adapter mapping:

| Upstream list field | Internal observation |
| --- | --- |
| `identifier` | `source_vehicle_id` and normalized `vehicle_id` |
| `country` | `nation` (request is explicitly filtered to USA) |
| `vehicle_type` | `vehicle_class` through an explicit accepted-value map |
| `era` | `rank` |
| `realistic_ground_br` | `ground_realistic_br` as integer tenths |
| `req_exp`, `value` | research and purchase cost |
| `is_premium`, `is_pack`, `squadron_vehicle`, `event` | availability type |

Unsupported nations/classes and missing Ground RB values fail explicitly; the
adapter does not guess. The upstream list route does not currently include a
research-parent field in its selected attributes, so research edges must come
from a separately validated source or curated fixture.

## StatShark feasibility

- Product page examined: <https://statshark.net/globalstats>
- The page returned HTTP 403 to a non-browser retrieval on 2026-09-18.
- No public, documented, permitted statistics API or downloadable export was
  found during this review. Community discussions demonstrate that global
  vehicle cards exist, but do not establish a supported acquisition contract.
- Decision: no HTML/Cloudflare bypass and no brittle page scraper. Milestone 1
  accepts strict JSON and explicit-column CSV exports through the statistics
  import boundary. Automatic acquisition remains blocked until StatShark offers
  or authorizes a stable interface/export.

The statistics fixture is transparent synthetic test data. Its provider is
`committed_acceptance_fixture`, period is 2026-08-01 through 2026-08-31, and one
vehicle intentionally has all metrics missing to exercise `null` handling. It
must never be presented as observed StatShark data. Both committed fixture snapshots are stamped
with purpose `acceptance` and the same frozen compatibility key. Operational snapshots use purpose
`operational`, preventing the synthetic statistics from attaching to a live vehicle refresh.

## WT Roster Manager

- Repository: <https://github.com/IamQbcle/wt-roster-manager>
- The repository describes local ownership/lineup storage, community API
  imports, and manually maintained availability corrections.
- No root license file or explicit reusable-code license was visible in the
  repository review on 2026-09-18. Describing a project as “open-source” is not
  a substitute for license terms.
- Decision: concepts only. No source code, bundled data, assets, or schemas were
  copied. Reuse requires an explicit license or permission from the author.

## Committed acceptance fixture

`src/wt_advisor/data/fixtures/usa_ground_rb_m1.json` covers the early USA Ground
RB tree through BR 3.7 and stamps:

- vehicle snapshot `fixture-usa-ground-rb-vehicles-m1`;
- statistics snapshot `fixture-usa-ground-rb-statistics-m1`;
- revision `usa-ground-rb-m1-2026-09-01`;
- a five-crew USA Ground RB profile;
- ordinary Rank I vehicles owned, M3 Lee researching, and premium content
  excluded by profile policy;
- explicit research edges and a premium vehicle used to test exclusion.

Snapshot checksums are calculated from canonical JSON record content. Re-import
of identical content therefore yields the same checksum. The fixture provider
returns a complete immutable bundle via `FixtureProvider.load()`.

## Stored versus active evidence

`data status` distinguishes four concepts:

- newest stored vehicle snapshot;
- newest stored statistics snapshot;
- active operational vehicle evidence;
- compatible optional statistical evidence.

The newest stored statistics snapshot may be incompatible with the active vehicle snapshot and is
still inspectable with a reason and missing canonical IDs. No compatible statistics means unknown
statistical strength, not zero, and does not prevent structural lineup analysis. Stale compatible
evidence remains usable with freshness metadata and warnings.

## Overrides and freshness

Overrides are YAML records with a revision plus `vehicle_id`, `field`, typed
`value`, `reason`, `reference`, and `added_at`. Duplicate targets in one revision
are rejected. Provider observations remain untouched. The initial override file
is intentionally empty rather than fabricating a correction.

Default freshness policy:

| Dataset | Fresh | Aging | Stale |
| --- | ---: | ---: | ---: |
| Metadata, BR, tech tree | 0–14 days | 15–45 days | over 45 days |
| Statistics (`sample_end`) | 0–30 days | 31–90 days | over 90 days |

A missing observation date produces `unknown`. Stale/unknown evidence remains
usable but must be surfaced to callers.
