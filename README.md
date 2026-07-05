# DeetsEdu — education policy data

Local corpus for modeling impacts of technology and education policy on US education outcomes. ~1.4 GB in `data/raw/`, pulled via `pull_data.py` (idempotent — re-run anytime, existing files are skipped).

## Project goal

The first thing we'd like to do is a queryable database where each district's
scores are presented as a timeseries via SEDA's data, along with CCD data.
Effectively, we'll visualize the data for folks to see how education is and has
been doing.

See [data.md](data.md) for the build strategy — how the raw files below become
that database (DuckDB as the build engine, a `district_year` fact table, emitted
as both `.duckdb` and portable `.sqlite`).

## Data sources on disk

### SEDA 5.0 — `data/raw/seda/`

Stanford Education Data Archive. District-level academic achievement, **2009–2019**, grades 3–8, math + reading (RLA), on a nationally comparable scale built from every state's standardized tests. The primary **outcome** dataset.

- `seda_geodist_long_cs_5.0_*.csv` — 1.22M rows: mean scores per district-year-grade-subject, overall and by subgroup (race/ethnicity, gender, economic disadvantage), with standard errors and test counts. `cs` = cohort-standardized scale.
- `seda_cov_geodist_long_5.0_*.csv` — district covariates: SES composite, demographics, enrollment shares.
- `seda_crosswalk_5.0_*.csv` — school↔district↔county crosswalk for joining other sources.
- `seda_codebook_*.xlsx` — variable definitions.

Public schools only (incl. charters). Known gap: **California is missing 2014 entirely** (STAR→SBAC test transition). Data-use agreement: non-commercial, cite Reardon et al. (2024). https://purl.stanford.edu/cs829jn7849

### SEDA 2023 — `data/raw/seda2023/`

Pandemic-era companion to SEDA 5.0. District achievement in **2019, 2022, 2023** plus changes between those years, relative to the 2019 national average. Covers **40 states (CA included)** — a subset, not the full universe. Basis of the Education Recovery Scorecard; the dataset for COVID learning-loss/recovery questions. https://purl.stanford.edu/xt779fj2637

### CCD — `data/raw/ccd/`

NCES Common Core of Data district directory, **2009–2024**, one parquet per year (~18–20k districts/year, 69 columns): identifiers, enrollment, locale/urbanicity codes, addresses, grade spans. The universe of US public school districts — the **spine** of the panel and source of control variables. Pulled via the Urban Institute Education Data Portal API. Note: NCES changed the district universe definition in 2018 (~1k district jump). https://educationdata.urban.org/documentation/

### CCD enrollment — `data/raw/ccd_enrollment/`

NCES CCD district enrollment **disaggregated by race**, 2009–2024, via the same Urban Institute API. The directory table (above) carries only *total* enrollment; this endpoint adds the demographic breakdown — the source of race-composition **control variables**. Default pull (`ccd_enroll`) grabs grade-99 (district total across grades), one parquet per year-grade: `ccd_enrollment_{year}_grade99.parquet`, one row per district-race (`race` code, `sex`=99 = both). The opt-in `ccd_enroll_grade` target pulls the same by individual grade (K–12 + ungraded), matching SEDA's district×grade granularity. Joins to everything else on `leaid`. https://educationdata.urban.org/documentation/

### PSS — `data/raw/pss/`

NCES Private School Universe Survey, biennial, **2009-10 through 2021-22** (7 collections). Enrollment, demographics, staffing, religious affiliation for ~22k private schools. The only private-school coverage in the corpus — **no test scores exist for private schools**, so this supports descriptive/context analyses, not outcome modeling. Merge across years on `PIN`; link to public data by geography (county/state), not district. Layout workbooks (`*.xlsx`) are extracted alongside the zips. 2023-24 collection lands spring 2026. https://nces.ed.gov/surveys/pss/pssdata.asp

## Join keys

- `leaid` (NCES district ID) — SEDA (`sedalea`/`sedaadmin`) ↔ CCD
- `fips` — state level, everywhere
- `PIN` — PSS across years; PSS links to the rest only by geography

## Available but not yet pulled (opt-in targets in pull_data.py)

- `crdc` — Civil Rights Data Collection: school-level tech/course access, discipline, AP (biennial 2011–2020). Big.
- `erate` — FCC E-Rate commitments from USAC, FY2016+: the school-connectivity **treatment variable**. Big.
- `ipeds` — college/university directory, if the project goes higher-ed.

## Quick start

```bash
pip install requests pandas pyarrow
python pull_data.py            # interactive checklist: shows what's on disk, pick what to fetch
python pull_data.py --list     # just print download status and exit
python pull_data.py erate      # non-interactive: pull named targets directly
```

Running with no arguments in a terminal opens a checklist that shows each
dataset's on-disk status (`OK` complete / `..` partial / `--` none), a rough
size estimate of what's left to download (e.g. `~25 MB`, `~4.0 GB`), and a
running total for the current selection — so you can gauge the download before
committing. Toggle items by number; no need to remember target names. Passing
names skips the menu. All pulls are idempotent: existing files are skipped.

Convention: `data/raw/` stays byte-for-byte as published (zips stay zipped — pandas reads them directly); anything cleaned/merged goes in `data/processed/`.

## Status & next steps

Current on-disk state and what to build next: see [handoff.md](handoff.md).
