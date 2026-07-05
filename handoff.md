# DeetsEdu — handoff

Where things stand and what to do next. Start with the [README](README.md) for
the data, then [data.md](data.md) for the build strategy.

## On disk now

- `seda` — 5 files, ~1.2 GB (2009–2019).
- `ccd` — district directory, 2009–2024.
- `ccd_enrollment` — by race, 2009–2023 (the 2024 grade-99 file is still pending).
- `seda2023` and `pss` — default targets, **not yet fetched**.

Run `python pull_data.py --list` for the live status.

## Raw pull

May still be in progress. `pull_data.py` is idempotent, so re-running finishes
any partial or missing targets — existing files are skipped.

## Next step

Write `build_db.py` — the cleaning + join + `district_year` fact table that emits
`data/processed/deetsedu.{duckdb,sqlite}`. Design, storage rationale, and the
build footguns (zero-padded `leaid`, `.`-nulls, CA-2014 gap, 2018 universe jump,
weight-by-`tot_asmt`) are documented in [data.md](data.md).
