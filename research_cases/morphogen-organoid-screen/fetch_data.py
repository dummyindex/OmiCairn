#!/usr/bin/env python3
"""
Fetcher for morphogen-organoid-screen (Sanchís-Calleja et al. Nature Methods
2026, DOI 10.1038/s41592-025-02927-5 — morphogen organoid screen
reproduction).

Sources (per RESEARCH.md):
  E-MTAB-15667    morphogen patterning condition screen (ArrayExpress)
                  ~100,538 cells / 97 conditions / 3 hPSC lines
  E-MTAB-15622    morphogen reproducibility screen (ArrayExpress)
                  ~209,902 cells / 192 samples
  Zenodo          processed Seurat / h5ad bundle (10.5281/zenodo.17225179)
  braun_2023      scANVI human-brain reference (auxiliary)
  hnoca           HNOCA atlas (auxiliary)

This fetcher downloads:
  - ArrayExpress SDRF metadata for both E-MTAB studies (small,
    immediately useful for sample-condition mapping)
  - Zenodo processed h5ad bundle (the primary input the OmiCairn agent
    consumes — analysis is on the authors' processed count matrix
    per `## Approach: Raw FASTQ re-alignment` in RESEARCH.md)

Heavier raw-FASTQ data is documented but NOT auto-downloaded:
  - Per-sample 10x cellranger output is hundreds of GB
  - The reproduction analysis runs on processed data, not raw counts
  - Run `--with-raw` flag explicitly to opt in (currently a no-op
    placeholder; raw fetch path is documented in
    DATA_SOURCES.md emitted into ./data/)

Auxiliary datasets (braun_2023, hnoca, scenic_v10) are referenced
during analysis but the OmiCairn scope/plan stage decides which to
fetch on-the-way via the data-fetch protocol in `prompts/execute.md`.

Output (saved to ./data/ and to ../../datasets/ per
COLLAB_PROTOCOL § 3.1):
  data/E-MTAB-15667.sdrf.tsv       sample-condition mapping
  data/E-MTAB-15622.sdrf.tsv       sample-condition mapping
  data/zenodo_17225179_manifest.json    record listing
  data/DATA_SOURCES.md             provenance doc + raw-fetch
                                    instructions
  ../../datasets/E-MTAB-15667/     symlink target for fetched .mtx
                                    (lazy: empty if --skip-large)
  ../../datasets/E-MTAB-15622/     same
  ../../datasets/zenodo_17225179/  Zenodo h5ad bundle

Concurrency: takes data/.fetch.lock per COLLAB_PROTOCOL § 9.

Requires: requests. ~5 min for metadata, +30 min if Zenodo fetch
opted in (--with-zenodo).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ARRAYEXPRESS_BASE = "https://www.ebi.ac.uk/biostudies"
ZENODO_BASE = "https://zenodo.org/api/records"
ZENODO_RECORD = "17225179"

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATASETS_DIR = HERE.parent.parent / "datasets"  # repo-level ./datasets/
LOCK_PATH = DATA_DIR / ".fetch.lock"
TIMEOUT = 60


def acquire_lock() -> bool:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            holder = int(LOCK_PATH.read_text().strip())
        except ValueError:
            holder = 0
        if holder > 0:
            try:
                os.kill(holder, 0)
                print(f"  fetch lock held by live PID {holder} — abort")
                return False
            except ProcessLookupError:
                print(f"  stale lock from dead PID {holder} — clearing")
        LOCK_PATH.unlink(missing_ok=True)
    LOCK_PATH.write_text(f"{os.getpid()}\n")
    return True


def release_lock():
    LOCK_PATH.unlink(missing_ok=True)


def http_get(url: str, retries: int = 3, **kw):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    rate-limited, waiting {wait}s ...")
                time.sleep(wait)
                continue
            print(f"    HTTP {r.status_code} for {url}")
            return None
        except requests.RequestException as exc:
            print(f"    request error (attempt {attempt+1}): {exc}")
            time.sleep(2)
    return None


def fetch_arrayexpress_sdrf(accession: str) -> Path | None:
    """Download the SDRF (Sample-Data Relationship Format) file for
    an ArrayExpress study. SDRF is a TSV with one row per sample
    and columns describing the experimental conditions."""
    url = f"{ARRAYEXPRESS_BASE}/files/{accession}/{accession}.sdrf"
    print(f"  fetching SDRF: {url}")
    r = http_get(url)
    if not r:
        # Try alternate path.
        url2 = f"{ARRAYEXPRESS_BASE}/files/{accession}/{accession}.sdrf.txt"
        print(f"    trying alternate: {url2}")
        r = http_get(url2)
    if not r:
        print(f"    SDRF not available for {accession}")
        return None
    out = DATA_DIR / f"{accession}.sdrf.tsv"
    tmp = out.with_suffix(".tsv.part")
    tmp.write_bytes(r.content)
    tmp.replace(out)
    print(f"    wrote {out.name} ({len(r.content)} bytes)")
    return out


def fetch_zenodo_manifest(record_id: str) -> dict | None:
    """Fetch Zenodo record metadata (file listing + DOIs)."""
    url = f"{ZENODO_BASE}/{record_id}"
    print(f"  fetching Zenodo manifest: {url}")
    r = http_get(url)
    if not r:
        return None
    return r.json()


def fetch_zenodo_files(manifest: dict, target_dir: Path) -> int:
    """Stream-download every file in the Zenodo record."""
    target_dir.mkdir(parents=True, exist_ok=True)
    files = manifest.get("files", [])
    print(f"  Zenodo record has {len(files)} files")
    n_ok = 0
    for f in files:
        name = f.get("key") or f.get("filename")
        if not name:
            continue
        link = f.get("links", {}).get("self") or f.get("links", {}).get(
            "download"
        )
        size = f.get("size", 0)
        out = target_dir / name
        if out.exists() and out.stat().st_size == size:
            print(f"    skip (already on disk): {name} ({size} B)")
            n_ok += 1
            continue
        print(f"    downloading: {name} ({size} B)")
        r = http_get(link, stream=True)
        if not r:
            print(f"      FAILED")
            continue
        tmp = out.with_suffix(out.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
        tmp.replace(out)
        n_ok += 1
        print(f"      done")
    return n_ok


def write_data_sources_doc():
    """Provenance + manual-fetch instructions for the heavy raw
    data the auto-fetcher does NOT pull by default."""
    doc = """# morphogen-organoid-screen data sources

Reproduction of Sanchís-Calleja et al., Nature Methods 2026
(DOI 10.1038/s41592-025-02927-5).

## Auto-fetched by fetch_data.py

- E-MTAB-15667.sdrf.tsv — sample/condition mapping for the
  morphogen patterning screen (ArrayExpress).
- E-MTAB-15622.sdrf.tsv — sample/condition mapping for the
  reproducibility screen.
- ../../datasets/zenodo_17225179/ — processed Seurat / h5ad bundle
  (Zenodo 10.5281/zenodo.17225179). This is the primary input
  the OmiCairn agent consumes per RESEARCH.md "Approach".

## Heavy raw data — opt-in or fetch on-the-way

The OmiCairn agent does NOT need raw FASTQs to reproduce the paper's
quantitative claims; the deposited processed objects suffice.
Per RESEARCH.md "Raw FASTQ re-alignment is allowed only if the
processed objects are unavailable."

If the OmiCairn scope/plan stage decides raw alignment IS needed:

- E-MTAB-15667 raw FASTQs:
  https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667
  (download via biostudies REST + per-sample manifest; ~hundreds of
  GB; honors ./datasets/E-MTAB-15667/.fetch.lock)
- E-MTAB-15622 raw FASTQs:
  https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15622
  (same pattern; ~hundreds of GB)

## Auxiliary references (fetched on-the-way per execute.md)

- braun_2023_human_brain: scANVI model + h5ad
- hnoca: human neural organoid cell atlas h5ad
- scenic_v10_clustered: pre-computed regulons

The OmiCairn execute.md prompt's data-fetch protocol handles these
when the agent's plan names them.
"""
    out = DATA_DIR / "DATA_SOURCES.md"
    out.write_text(doc)
    print(f"  wrote {out.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-zenodo", action="store_true",
                    help="Also fetch Zenodo processed-data bundle (~few GB)")
    ap.add_argument("--with-raw", action="store_true",
                    help="(reserved) fetch raw FASTQs — not implemented; "
                         "see DATA_SOURCES.md for manual instructions")
    args = ap.parse_args()

    if not acquire_lock():
        return 2
    try:
        print("=== ArrayExpress SDRF metadata ===")
        fetch_arrayexpress_sdrf("E-MTAB-15667")
        fetch_arrayexpress_sdrf("E-MTAB-15622")

        print("\n=== Zenodo manifest ===")
        manifest = fetch_zenodo_manifest(ZENODO_RECORD)
        if manifest:
            mpath = DATA_DIR / "zenodo_17225179_manifest.json"
            mpath.write_text(json.dumps(manifest, indent=2))
            print(f"  wrote {mpath.name}")
            if args.with_zenodo:
                print("\n=== Zenodo file download (--with-zenodo) ===")
                target = DATASETS_DIR / "zenodo_17225179"
                fetch_zenodo_files(manifest, target)
            else:
                print("  (--with-zenodo not set; skipping bulk download)")
        else:
            print("  could not fetch Zenodo manifest")

        if args.with_raw:
            print("\n--with-raw is a placeholder; see DATA_SOURCES.md")

        write_data_sources_doc()
        print("\nDone.")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
