# Changelog

All notable changes to `sftocf` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.1] - 2026-11-24

### Added
- `starfish_zone_created` signal, sent from `update_zones` when a new per-project
  Starfish zone is created, so organization-specific logic (e.g. an institution's
  own Globus-UUID push) can live in a separate plugin instead of in sftocf itself.
- `starfish_set_zone_userparam` signal, a generic hook for setting a zone's
  `zone_user_param`, with a receiver that forwards to `StarfishClient`.
- `starfish_update_volume_information` signal and receiver, updating a
  Resource's capacity/usage attributes from Starfish.
- kwargs support on tasks' management command calls.

### Changed
- Renamed `StarfishClient.update_zone_user_param` to `set_zone_user_param`.
- `update_zones` no longer hard-depends on `coldfront.core.department`
  (an institution-specific concept); it degrades gracefully and skips
  department-level zone management when that app isn't installed.
- Removed an unnecessary conditional from the REST pipeline.

### Fixed
- Fixed an `allocation_attribute_type` reference in `import_allocation_filepaths`.
- Fixed the "missing users detected" log message so it only fires when there
  are actually missing users.

## [1.0.0] - 2026-11-19

First tagged release. Packages the recent internal refactor of the
Starfish REST/Redash integration:

### Changed
- Split the former monolithic `utils.py` into separate `client`, `conf`,
  `receivers`, and `pipelines` modules.
- Renamed `StarfishServer` to `StarfishClient`.
- Replaced the ad hoc JSON-file cache of API results with
  `django.core.cache`-backed caching for both lab usage data and zone
  lookups.
- Deduplicated `collect_sf_user_data` / `collect_sf_usage_data` logic.
- Added a timeout to async Starfish queries.
- Improved logging around data collection.

### Fixed
- Fixed a stale `STORAGE_RESOURCES` reference that could return stale
  results.

[Unreleased]: https://github.com/fasrc/sftocf/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/fasrc/sftocf/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/fasrc/sftocf/releases/tag/v1.0.0
