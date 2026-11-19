# Changelog

All notable changes to `sftocf` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/fasrc/sftocf/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/fasrc/sftocf/releases/tag/v1.0.0
