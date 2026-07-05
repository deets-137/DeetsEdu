"""
pull_data.py — Download all project datasets into ./data/raw/

Usage:
    pip install requests pandas pyarrow
    python pull_data.py                  # default: seda + ccd + seda2023 + pss
    python pull_data.py crdc erate       # opt-in big pulls by name
    python pull_data.py seda ccd seda2023 pss crdc ipeds erate   # everything

Targets:
    seda      SEDA 5.0 district achievement 2009-2019 + covariates + crosswalk
    ccd       NCES CCD district directory 2009-2024 (via Urban Institute API)
    seda2023  SEDA pandemic-era scores: 2019/2022/2023 achievement + changes
    pss       NCES Private School Universe Survey, biennial 2009-10 to 2021-22
    crdc      Civil Rights Data Collection school characteristics (opt-in, big)
    ipeds     IPEDS college directory (opt-in)
    erate     FCC E-Rate commitments from USAC (opt-in, big)

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


def fetch_paged_json(url: str) -> list[dict]:
    """Follow Urban Institute API 'next' links, return all rows."""
    rows = []
    while url:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
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
                resp = requests.get(
                    ERATE_URL,
                    params={
                        "$where": f"funding_year = '{year}'",
                        "$limit": SOCRATA_PAGE,
                        "$offset": offset,
                        "$order": ":id",
                    },
                    timeout=300,
                )
                resp.raise_for_status()
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
    "seda2023": lambda: pull_files("seda2023", SEDA23_BASE, SEDA23_FILES),
    "pss": lambda: pull_files("pss", PSS_BASE, PSS_FILES),
    "crdc": lambda: pull_ui_endpoint(
        "crdc", "schools/crdc/school-characteristics", CRDC_YEARS
    ),
    "ipeds": lambda: pull_ui_endpoint(
        "ipeds", "college-university/ipeds/directory", IPEDS_YEARS
    ),
    "erate": pull_erate,
}
DEFAULT_TARGETS = ["seda", "ccd", "seda2023", "pss"]

if __name__ == "__main__":
    requested = sys.argv[1:] or DEFAULT_TARGETS
    unknown = [t for t in requested if t not in TARGETS]
    if unknown:
        sys.exit(f"Unknown target(s): {unknown}. Options: {list(TARGETS)}")
    for t in requested:
        TARGETS[t]()
    print("\nDone. Raw data in", DATA)
