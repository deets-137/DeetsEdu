# DeetsEdu — data strategy

How raw files become the queryable, visualizable database described in the
[README](README.md#project-goal): *a queryable database where each district's
scores are presented as a timeseries via SEDA's data, along with CCD data.*

## The pipeline in one line

```
data/raw/  ──(DuckDB: read + clean + join)──►  district_year fact table  ──►  data/processed/deetsedu.sqlite
  (CSV + parquet, byte-for-byte)                  (in-memory / build)            (central store, portable)
```

Raw stays untouched. A build step cleans and joins. A single central database
file is the output the viz layer queries.

## Storage decision: DuckDB to *build*, SQLite to *store*

We split the two jobs a database engine does — reading/transforming vs.
persisting/serving — across the two tools, each doing what it's best at.

- **DuckDB is the build/ETL engine.** It reads our raw CSVs *and* parquets in
  place (`read_csv_auto`, `read_parquet`), handles SEDA's quirks (`.`-as-null,
  zero-padded string IDs, ~50 wide subgroup columns), and does the joins — with
  almost no import boilerplate. Columnar + vectorized, so the heavy raw scans
  are fast.
- **SQLite is the central store / deliverable.** One portable, self-contained
  file (`data/processed/deetsedu.sqlite`) that opens in essentially any tool,
  language, or hosting environment. DuckDB writes it directly at the end of the
  build (`INSTALL sqlite; LOAD sqlite; ATTACH ... (TYPE SQLITE)`), and can also
  read it back later when we want DuckDB's query ergonomics.

### Why not just one file for both?

Because **physical storage layout picks a side — a single file can't be optimal
for both row and column access:**

- A **SQLite file stores rows together.** Reading one column across all
  districts still walks every row's full record — there is no columnar shortcut
  in the file.
- **DuckDB's analytical speed comes from two things:** a vectorized execution
  engine *and* columnar storage (touching only the columns queried, per-column
  compression).

When DuckDB reads a SQLite file it gets the vectorized engine but **not** the
columnar storage benefit — the bytes are still row-laid-out. So "SQLite file +
DuckDB reader = best of both layouts" is **not** true; the layout is decided when
the file is written.

### Why SQLite still wins here anyway

**The data is small.** The district-year fact table is ~1M rows kept granular
(district × year × grade × subject), tens of thousands collapsed to district ×
year. At that scale, columnar-vs-row is milliseconds either way — imperceptible.
For a public-facing viz deliverable, **portability, format stability, and
"opens anywhere" matter more** than a speed difference nobody will feel.

If we ever *do* want maximum analytical speed (big scans, exploratory analysis),
we emit a native `.duckdb` from the same build — see below.

## Regenerability is the safety net

The central DB is a **derived artifact**, fully rebuildable from `data/raw/` at
any time. This is what makes the storage choice low-stakes:

- DuckDB's native on-disk format is not guaranteed stable across major versions —
  fine for a throwaway-and-rebuild artifact, which is exactly what this is.
- Because one build script reconstructs everything, we can emit **either or both**
  outputs without lock-in: `deetsedu.sqlite` (portable central store) and/or
  `deetsedu.duckdb` (fast analytical scratch). Same pipeline, pick the output.

Convention (from the README): `data/raw/` stays byte-for-byte as published;
everything cleaned/merged lands in `data/processed/`.

## Target shape: a `district_year` fact table

The spine of the queryable DB, keyed for timeseries-per-district lookups:

- **Grain:** one row per `leaid` × `year` (× `grade` × `subject` if we keep SEDA
  granular; collapsed otherwise).
- **Outcome columns** from SEDA: cohort-standardized means (`cs_mn_all` and
  subgroup variants), standard errors, test counts (`tot_asmt_*`).
- **Control columns** from CCD directory: total `enrollment`, locale/urbanicity,
  geography (`fips`, county), staffing, grade span — joined on `leaid` + `year`.
- **Demographic controls** from CCD enrollment-by-race, joined on `leaid` + `year`.

### Join keys and known footguns to handle in the build

- `leaid` (NCES district ID, **zero-padded string** — keep as text, don't let it
  parse to int) ↔ SEDA `sedalea` / `sedaadmin`; `fips` at state level.
- SEDA nulls arrive as `.` and empty string → load with explicit `na_values`.
- **California is missing 2014** in SEDA 5.0 (STAR→SBAC transition) — expect the
  gap, don't treat it as an error.
- **NCES changed the district universe in 2018** (~1k district jump) — watch
  merge coverage across that boundary.
- Weight aggregations by `tot_asmt` (test counts), not naive means, when
  collapsing grades/subjects.

## Build outputs

**One script emits both formats in a single run** — they're not two pipelines,
just the same finished tables written out twice, so they can never drift.

- `data/processed/deetsedu.duckdb` — native columnar copy for heavy/fast
  analysis. Comes for free by connecting the build to this file path.
- `data/processed/deetsedu.sqlite` — central portable store the viz layer
  queries. DuckDB attaches it and copies every table across (`INSTALL sqlite;
  LOAD sqlite; ATTACH ... (TYPE SQLITE)`) — ~5 lines at the end of the build.

Build into DuckDB *first* (it's the engine, does typed work best), then derive
SQLite from it. Note SQLite's looser type system: a few types (dates, decimals)
land as TEXT/REAL — a non-issue for a numeric/string/int fact table, just keep
`leaid` as TEXT.

Both are git-ignored (derived) and regenerable via the build script.
