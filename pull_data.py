"""
pull_data.py — Download all project datasets into ./data/raw/

Usage:
    pip install requests pandas pyarrow
    python pull_data.py                  # interactive checklist (pick what to fetch)
    python pull_data.py crdc erate       # non-interactive: named targets
    python pull_data.py --list           # print download status and exit
    python pull_data.py seda ccd seda2023 pss crdc ipeds erate   # everything

Run with no arguments in a terminal to get a checklist showing what's already
on disk and let you toggle datasets to download. Passing target names skips the
menu (handy for scripts/cron); with no TTY it falls back to the default set.

Targets:
    seda        SEDA 5.0 district achievement 2009-2019 + covariates + crosswalk
    ccd         NCES CCD district directory 2009-2024 (via Urban Institute API)
    ccd_enroll  CCD district enrollment by race, grade totals 2009-2024
    seda2023    SEDA pandemic-era scores: 2019/2022/2023 achievement + changes
    pss         NCES Private School Universe Survey, biennial 2009-10 to 2021-22
    tiger       Census school-district boundary shapefiles (2019, 1:500k)
    crdc        Civil Rights Data Collection school characteristics (opt-in, big)
    ipeds       IPEDS college directory (opt-in)
    erate       FCC E-Rate commitments from USAC (opt-in, big)
    ccd_enroll_grade  CCD enrollment by race for every grade K-12 (opt-in, big)

Idempotent: skips files that already exist. Delete a file to re-download.
"""

import sys
import time
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data" / "raw"
CHUNK = 1 << 20  # 1 MB

# ---------------------------------------------------------------- SEDA 5.0
# Stanford Education Data Archive v5.0 (2009-2019, published 2024)
# Docs & citation: https://purl.stanford.edu/cs829jn7849
# NOTE: SEDA is free but has a data-use agreement (non-commercial,
# no re-identification, cite Reardon et al. 2024). Read it at the PURL above.
SEDA_BASE = "https://stacks.stanford.edu/file/druid:cs829jn7849"
SEDA_FILES = [
    # District-level achievement, one row per district-year-grade-subject.
    # "cs" = cohort-standardized scale (comparable across districts/years).
    "seda_geodist_long_cs_5.0_updated_20240319.csv",
    # District covariates (demographics, SES, enrollment) by year.
    "seda_cov_geodist_long_5.0_updated_20240319.csv",
    # School <-> district <-> county crosswalk for joins to CCD/CRDC.
    "seda_crosswalk_5.0_updated_20240319.csv",
    # Codebooks
    "seda_codebook_geodist_5.0.xlsx",
    "seda_codebook_cov_geodist_5.0.xlsx",
]

# ---------------------------------------------------------------- SEDA 2023
# Pandemic-era district achievement (2019, 2022, 2023 + changes), 40 states.
# Same data-use agreement as SEDA 5.0. https://purl.stanford.edu/xt779fj2637
SEDA23_BASE = "https://stacks.stanford.edu/file/druid:xt779fj2637"
SEDA23_FILES = [
    "seda2023_admindist_poolsub_ys_updated_20240205.csv",   # by year-subject
    "seda2023_admindist_poolsub_gys_updated_20240205.csv",  # by grade-year-subject
    "seda2023_cov_admindist_annual.csv",                    # district covariates
    "seda2023_codebook_admindist.xlsx",
]

# ---------------------------------------------------------------- Urban Institute API
# Wraps NCES CCD, CRDC, and IPEDS. Docs: https://educationdata.urban.org/documentation/
UI_BASE = "https://educationdata.urban.org/api/v1"
CCD_YEARS = range(2009, 2025)      # through SY 2024-25 (latest available)
CRDC_YEARS = [2011, 2013, 2015, 2017, 2020]  # biennial; labels = fall year
IPEDS_YEARS = range(2009, 2024)

# CCD enrollment endpoint (disaggregated by race) — the directory table has
# only total enrollment, so this is the source of demographic controls that
# match SEDA's district x grade granularity. The endpoint requires a grade in
# the path; grade 99 = total across all grades, 0 = kindergarten, 1-12 = grades,
# 13/14/15 = grade 13 / ungraded / adult ed (rare). The /race/ path returns one
# row per district-race with sex=99 (both sexes). Row counts ~145-170k/year.
CCD_ENROLL_TOTAL_GRADE = 99                 # district total across all grades
CCD_ENROLL_GRADES = list(range(0, 16))      # K(0), 1-12, plus 13/14/15

# ---------------------------------------------------------------- PSS (private schools)
# NCES Private School Universe Survey — biennial universe of US private schools.
# Small files (~4 MB each). https://nces.ed.gov/surveys/pss/pssdata.asp
# 2023-24 collection expected spring 2026; add it here when posted.
PSS_BASE = "https://nces.ed.gov/surveys/pss/zip"
PSS_FILES = [
    # 2009-10 and 2011-12 are tab-delimited TXT; 2013-14 onward are CSV.
    "TXT_PSS0910.zip",
    "pss1112_pu_txt.zip",
    "pss1314_pu_csv.zip",
    "pss1516_pu_csv.zip",
    "pss1718_pu_csv.zip",
    "pss1920_pu_csv.zip",
    "pss2122_pu_csv.zip",
    # File layouts (column definitions)
    "layout2021-22.zip",
    "layout2019-20.zip",
]

# ---------------------------------------------------------------- TIGER boundaries
# Census cartographic boundary shapefiles for school districts, 2019 vintage
# (matches SEDA 5.0's anchor year), 1:500k generalized — made for thematic
# mapping, far smaller than full TIGER/Line. One zip per state per district
# type; each polygon's GEOID equals the NCES leaid used by SEDA/CCD.
#   unsd = unified K-12 districts (most of the country)
#   elsd/scsd = separate elementary/secondary districts (overlap each other;
#               only some states have them)
# Directory listing: https://www2.census.gov/geo/tiger/GENZ2019/shp/
TIGER_BASE = "https://www2.census.gov/geo/tiger/GENZ2019/shp"
TIGER_SD_TYPES = {
    # State FIPS codes that have a file of each type on the server (incl.
    # DC=11 and territories 60/66/69/72/78 — filter territories in
    # processing if the map stays continental-US only).
    "unsd": ["01", "02", "04", "05", "06", "08", "09", "10", "11", "12",
             "13", "15", "16", "17", "18", "19", "20", "21", "22", "23",
             "24", "25", "26", "27", "28", "29", "30", "31", "32", "33",
             "34", "35", "36", "37", "38", "39", "40", "41", "42", "44",
             "45", "46", "47", "48", "49", "50", "51", "53", "54", "55",
             "56", "60", "66", "69", "72", "78"],
    "elsd": ["01", "04", "06", "09", "13", "17", "21", "23", "25", "26",
             "27", "29", "30", "33", "34", "36", "38", "40", "41", "44",
             "45", "47", "48", "50", "51", "55", "56"],
    "scsd": ["04", "06", "09", "13", "17", "21", "23", "25", "27", "30",
             "33", "34", "36", "40", "41", "44", "45", "47", "48", "55"],
}
TIGER_FILES = [
    f"cb_2019_{fips}_{sdtype}_500k.zip"
    for sdtype, fips_list in TIGER_SD_TYPES.items()
    for fips in fips_list
] + [
    # State outlines (one national file) for the zoomed-out map view.
    "cb_2019_us_state_5m.zip",
]

# ---------------------------------------------------------------- E-Rate (USAC)
# "E-Rate Recipient Details and Commitments" — Socrata dataset avi8-svp9.
# Docs: https://dev.socrata.com/foundry/opendata.usac.org/avi8-svp9
ERATE_URL = "https://opendata.usac.org/resource/avi8-svp9.csv"
ERATE_YEARS = range(2016, 2027)  # modern (post-2015) E-Rate program only
SOCRATA_PAGE = 50_000


# ================================================================ helpers
def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (exists): {dest.name}")
        return
    print(f"  downloading: {dest.name}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
    tmp.rename(dest)
    print(f"    done ({dest.stat().st_size / 1e6:.1f} MB)")


def get_with_retry(url: str, tries: int = 5, timeout: int = 120,
                   **kwargs) -> requests.Response:
    """GET with retries on timeouts/connection drops (the UI API is flaky)."""
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == tries:
                raise
            wait = 10 * attempt
            print(f"    {type(e).__name__}, retry {attempt}/{tries - 1} "
                  f"in {wait}s...")
            time.sleep(wait)
        except requests.HTTPError as e:
            # retry server-side errors; 4xx are permanent, let caller handle
            if attempt == tries or e.response.status_code < 500:
                raise
            wait = 10 * attempt
            print(f"    HTTP {e.response.status_code}, retry "
                  f"{attempt}/{tries - 1} in {wait}s...")
            time.sleep(wait)


def fetch_paged_json(url: str) -> list[dict]:
    """Follow Urban Institute API 'next' links, return all rows."""
    rows = []
    while url:
        payload = get_with_retry(url).json()
        rows.extend(payload["results"])
        url = payload.get("next")
        time.sleep(0.2)  # be polite
    return rows


def pull_ui_endpoint(name: str, endpoint: str, years) -> None:
    """Generic Urban Institute API puller -> one parquet per year."""
    import pandas as pd

    out = DATA / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"{name} (Urban Institute API) -> {out}")
    for year in years:
        dest = out / f"{name}_{year}.parquet"
        if dest.exists():
            print(f"  skip (exists): {dest.name}")
            continue
        print(f"  fetching {year}...")
        try:
            rows = fetch_paged_json(f"{UI_BASE}/{endpoint}/{year}/")
        except requests.HTTPError as e:
            print(f"    {year} not available ({e.response.status_code}), skipping")
            continue
        pd.DataFrame(rows).to_parquet(dest, index=False)
        print(f"    done ({len(rows):,} rows)")


def pull_files(name: str, base: str, files: list[str]) -> None:
    out = DATA / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"{name} -> {out}")
    for fname in files:
        download(f"{base}/{fname}", out / fname)


# ================================================================ targets
def pull_ccd() -> None:
    """CCD parquets named ccd_directory_{year} (matches original pull naming)."""
    import pandas as pd

    out = DATA / "ccd"
    out.mkdir(parents=True, exist_ok=True)
    print("ccd (Urban Institute API) ->", out)
    for year in CCD_YEARS:
        dest = out / f"ccd_directory_{year}.parquet"
        if dest.exists():
            print(f"  skip (exists): {dest.name}")
            continue
        print(f"  fetching {year}...")
        rows = fetch_paged_json(f"{UI_BASE}/school-districts/ccd/directory/{year}/")
        pd.DataFrame(rows).to_parquet(dest, index=False)
        print(f"    done ({len(rows):,} districts)")


def pull_ccd_enrollment(grades, disagg: str = "race") -> None:
    """CCD district enrollment by {disagg}, one parquet per year-grade.

    Files are always grade-tagged (ccd_enrollment_{year}_grade{g}.parquet) so
    the grade-99 totals and individual-grade pulls coexist without collision.
    Writes to data/raw/ccd_enrollment/ — separate from the ccd/ directory
    files, so nothing already on disk is re-pulled. Existing files are skipped.
    """
    import pandas as pd

    out = DATA / "ccd_enrollment"
    out.mkdir(parents=True, exist_ok=True)
    print(f"ccd_enrollment (Urban Institute API, by {disagg}) ->", out)
    for year in CCD_YEARS:
        for g in grades:
            dest = out / f"ccd_enrollment_{year}_grade{g}.parquet"
            if dest.exists():
                print(f"  skip (exists): {dest.name}")
                continue
            url = f"{UI_BASE}/school-districts/ccd/enrollment/{year}/grade-{g}/{disagg}/"
            print(f"  fetching {year} grade-{g} by {disagg}...")
            try:
                rows = fetch_paged_json(url)
            except requests.HTTPError as e:
                print(f"    {year} grade-{g} not available "
                      f"({e.response.status_code}), skipping")
                continue
            pd.DataFrame(rows).to_parquet(dest, index=False)
            print(f"    done ({len(rows):,} rows)")


def pull_erate() -> None:
    """E-Rate commitments, one CSV per funding year, paged via Socrata API."""
    out = DATA / "erate"
    out.mkdir(parents=True, exist_ok=True)
    print("erate (USAC Socrata) ->", out)
    for year in ERATE_YEARS:
        dest = out / f"erate_commitments_{year}.csv"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  skip (exists): {dest.name}")
            continue
        print(f"  fetching FY{year}...")
        tmp = dest.with_suffix(".csv.part")
        offset, wrote_header = 0, False
        with open(tmp, "wb") as f:
            while True:
                resp = get_with_retry(
                    ERATE_URL,
                    params={
                        "$where": f"funding_year = '{year}'",
                        "$limit": SOCRATA_PAGE,
                        "$offset": offset,
                        "$order": ":id",
                    },
                    timeout=300,
                )
                lines = resp.content.splitlines(keepends=True)
                if len(lines) <= 1:  # header only = no more rows
                    break
                f.write(b"".join(lines if not wrote_header else lines[1:]))
                wrote_header = True
                offset += SOCRATA_PAGE
                time.sleep(0.5)
        if wrote_header:
            tmp.rename(dest)
            print(f"    done ({dest.stat().st_size / 1e6:.1f} MB)")
        else:
            tmp.unlink()
            print(f"    no data for FY{year}")


TARGETS = {
    "seda": lambda: pull_files("seda", SEDA_BASE, SEDA_FILES),
    "ccd": pull_ccd,
    "ccd_enroll": lambda: pull_ccd_enrollment([CCD_ENROLL_TOTAL_GRADE], "race"),
    "ccd_enroll_grade": lambda: pull_ccd_enrollment(CCD_ENROLL_GRADES, "race"),
    "seda2023": lambda: pull_files("seda2023", SEDA23_BASE, SEDA23_FILES),
    "pss": lambda: pull_files("pss", PSS_BASE, PSS_FILES),
    "tiger": lambda: pull_files("tiger", TIGER_BASE, TIGER_FILES),
    "crdc": lambda: pull_ui_endpoint(
        "crdc", "schools/crdc/school-characteristics", CRDC_YEARS
    ),
    "ipeds": lambda: pull_ui_endpoint(
        "ipeds", "college-university/ipeds/directory", IPEDS_YEARS
    ),
    "erate": pull_erate,
}
DEFAULT_TARGETS = ["seda", "ccd", "ccd_enroll", "seda2023", "pss", "tiger"]

# One-line descriptions for the interactive checklist (order = menu order).
DESCRIPTIONS = {
    "seda": "SEDA 5.0 district achievement 2009-2019 + covariates",
    "ccd": "CCD district directory 2009-2024",
    "ccd_enroll": "CCD enrollment by race, district totals 2009-2024",
    "ccd_enroll_grade": "CCD enrollment by race, every grade K-12 (big)",
    "seda2023": "SEDA pandemic scores 2019/2022/2023 + changes",
    "pss": "Private School Universe Survey 2009-10 to 2021-22",
    "tiger": "Census school-district boundaries, 2019 cartographic 1:500k",
    "crdc": "Civil Rights Data Collection, school characteristics (big)",
    "ipeds": "IPEDS college/university directory",
    "erate": "FCC E-Rate connectivity commitments 2016+ (big)",
}

# Rough full-download size per target, in MB — measured on disk where available,
# extrapolated from row counts otherwise. Only used to warn how heavy a pull is;
# treat as order-of-magnitude ("~"), not exact. erate is by far the largest
# (~1.4M rows *per* funding year); ipeds is tiny (~6k institutions/year).
SIZES_MB = {
    "seda": 1300,        # measured
    "ccd": 45,           # measured
    "ccd_enroll": 7,     # ~0.4 MB/year x 16
    "ccd_enroll_grade": 90,   # ~0.35 MB x 256 year-grade files
    "seda2023": 75,      # measured
    "pss": 28,           # measured
    "tiger": 40,         # 103 state-level 500k zips, mostly well under 1 MB
    "crdc": 300,         # estimate, school-level x 5 collections
    "ipeds": 25,         # ~6.4k rows/year x 15
    "erate": 4000,       # estimate, ~1.4M rows/year CSV x ~10 years
}


def fmt_size(mb: float) -> str:
    """Human-rounded size with a '~' to signal it's an estimate."""
    if mb <= 0:
        return "-"
    if mb >= 1000:
        return f"~{mb / 1000:.1f} GB"
    return f"~{mb:.0f} MB"


# ================================================================ status / CLI
def expected_files(target: str) -> list[Path]:
    """The files a target is expected to produce, for download-status checks."""
    d = DATA
    if target == "seda":
        return [d / "seda" / f for f in SEDA_FILES]
    if target == "ccd":
        return [d / "ccd" / f"ccd_directory_{y}.parquet" for y in CCD_YEARS]
    if target == "ccd_enroll":
        g = CCD_ENROLL_TOTAL_GRADE
        return [d / "ccd_enrollment" / f"ccd_enrollment_{y}_grade{g}.parquet"
                for y in CCD_YEARS]
    if target == "ccd_enroll_grade":
        return [d / "ccd_enrollment" / f"ccd_enrollment_{y}_grade{g}.parquet"
                for y in CCD_YEARS for g in CCD_ENROLL_GRADES]
    if target == "seda2023":
        return [d / "seda2023" / f for f in SEDA23_FILES]
    if target == "pss":
        return [d / "pss" / f for f in PSS_FILES]
    if target == "tiger":
        return [d / "tiger" / f for f in TIGER_FILES]
    if target == "crdc":
        return [d / "crdc" / f"crdc_{y}.parquet" for y in CRDC_YEARS]
    if target == "ipeds":
        return [d / "ipeds" / f"ipeds_{y}.parquet" for y in IPEDS_YEARS]
    if target == "erate":
        return [d / "erate" / f"erate_commitments_{y}.csv" for y in ERATE_YEARS]
    return []


def status(target: str) -> tuple[str, int, int]:
    """Return (label, have, total) where label is none|partial|complete."""
    files = expected_files(target)
    have = sum(1 for f in files if f.exists() and f.stat().st_size > 0)
    total = len(files)
    if total and have == total:
        return "complete", have, total
    if have == 0:
        return "none", have, total
    return "partial", have, total


def remaining_mb(target: str) -> float:
    """Estimated MB left to download (full size scaled by missing fraction)."""
    _, have, total = status(target)
    full = SIZES_MB.get(target, 0)
    if total == 0:
        return full
    return full * (total - have) / total


def print_status() -> None:
    for t in TARGETS:
        label, have, total = status(t)
        mark = {"complete": "OK", "partial": "..", "none": "--"}[label]
        left = "done" if label == "complete" else fmt_size(remaining_mb(t))
        print(f"  {mark}  {have:>3}/{total:<3}  {left:>8}  {t:<17} "
              f"{DESCRIPTIONS[t]}")


def interactive_select() -> list[str]:
    """Show a checklist of targets with on-disk status; return chosen targets."""
    order = list(TARGETS)
    # Pre-select default targets that aren't fully downloaded yet.
    selected = {t for t in DEFAULT_TARGETS if status(t)[0] != "complete"}
    while True:
        print("\n  DeetsEdu data downloader - select datasets to fetch")
        print("  (size column = rough estimate of what's left to download)\n")
        for i, t in enumerate(order, 1):
            label, have, total = status(t)
            mark = {"complete": "OK", "partial": "..", "none": "--"}[label]
            box = "[x]" if t in selected else "[ ]"
            left = "done" if label == "complete" else fmt_size(remaining_mb(t))
            print(f"  {i:>2}  {box}  {mark} {have:>3}/{total:<3}  {left:>8}  "
                  f"{t:<17} {DESCRIPTIONS[t]}")
        sel_total = sum(remaining_mb(t) for t in selected)
        print(f"\n  selected: {len(selected)} dataset(s), "
              f"~{fmt_size(sel_total).lstrip('~')} to download")
        print("  toggle: numbers (e.g. '2 3')   a=all  n=none  d=defaults")
        print("  Enter=download selected   q=quit")
        try:
            raw = input("  > ").strip().lower()
        except EOFError:
            return []
        if raw == "q":
            return []
        if raw == "a":
            selected = set(order)
            continue
        if raw == "n":
            selected = set()
            continue
        if raw == "d":
            selected = set(DEFAULT_TARGETS)
            continue
        if raw == "":
            chosen = [t for t in order if t in selected]
            if not chosen:
                print("  nothing selected — pick some or 'q' to quit")
                continue
            return chosen
        for tok in raw.replace(",", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= len(order):
                selected ^= {order[int(tok) - 1]}
            else:
                print(f"  ? ignoring '{tok}'")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--list", "-l"):
        print_status()
        sys.exit(0)
    if args:
        requested = args
        unknown = [t for t in requested if t not in TARGETS]
        if unknown:
            sys.exit(f"Unknown target(s): {unknown}. Options: {list(TARGETS)}")
    elif sys.stdin.isatty():
        requested = interactive_select()
        if not requested:
            print("Nothing selected. Bye.")
            sys.exit(0)
        print(f"\nDownloading: {', '.join(requested)}\n")
    else:
        requested = DEFAULT_TARGETS
    for t in requested:
        TARGETS[t]()
    print("\nDone. Raw data in", DATA)
