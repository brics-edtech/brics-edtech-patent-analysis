#!/usr/bin/env python
"""
This script recursively reads CSV files (matched by gp-search-20*.csv) from a given input directory,
deduplicates them by patent ID, skips those already in the JSON_OUTPUT folder, and scrapes each new
patent via google_patent_scraper. Results are written in chunked JSON files.
"""

import os
import re
import json
import glob
import time
import logging
import multiprocessing as mp
from urllib.error import HTTPError

import pandas as pd
from tqdm import tqdm

from google_patent_scraper import scraper_class

# ------------------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------------------
INPUT_DIR        = "patents_csvs"
CSV_PATTERN      = os.path.join(INPUT_DIR, "**", "gp-search-20*.csv")
OUTPUT_FOLDER    = os.path.join(INPUT_DIR, "json_output")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

OUTPUT_TEMPLATE  = "all_patents_{:03d}.json"
CHUNK_SIZE       = 1000

NUM_PROCESSES    = 7
MAX_RETRIES      = 3
RETRY_DELAY      = 2    # seconds, will exponential backoff

# ------------------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ------------------------------------------------------------------------------
# HTML Parser SELECTION
# ------------------------------------------------------------------------------
try:
    import lxml  # noqa: F401
    DEFAULT_PARSER = "lxml"
except ImportError:
    logging.warning(
        "lxml not installed; falling back to html.parser. "
        "For best results, pip install lxml."
    )
    DEFAULT_PARSER = "html.parser"

# ------------------------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------------------------

def extract_patent_id(url):
    """
    From a URL like https://patents.google.com/patent/US1234567A/en,
    extract 'US1234567A'.
    """
    if not url:
        return ""
    m = re.search(r"/patent/([^/]+)/", url)
    return m.group(1).strip() if m else ""

def normalize_id(pid):
    """
    For consistent comparisons, uppercase the patent ID and 
    remove whitespace and dashes.
    """
    return re.sub(r"[\s-]", "", pid.strip().upper())

def get_csv_patent_id(row):
    """
    Primary key function for a CSV row:
      1) if row['id'] is nonempty, use that,
      2) else attempt extract_patent_id(row['result link']).
    Always normalize.
    """
    pid = str(row.get("id", "") or "").strip()
    if pid:
        return normalize_id(pid)
    pid = extract_patent_id(row.get("result link", ""))
    return normalize_id(pid) if pid else ""

def load_csv(filepath):
    """ Read the CSV starting from row‑2 (header=1). """
    try:
        return pd.read_csv(filepath, header=1)
    except Exception:
        logging.exception(f"Could not read CSV {filepath}")
        return None

# ------------------------------------------------------------------------------
# SCRAPER WORKER INITIALIZATION
# ------------------------------------------------------------------------------

scraper = None

def init_worker():
    global scraper
    try:
        scraper = scraper_class(return_abstract=True, parser=DEFAULT_PARSER)
    except TypeError:
        logging.warning("parser arg not supported by scraper_class; using default.")
        scraper = scraper_class(return_abstract=True)

def process_row(row):
    """
    Worker function to request & parse one patent, with retries.
    Returns a dict (with at least 'id' and 'url') or {'id':..., 'error':...}.
    """
    pid = get_csv_patent_id(row)
    url = row.get("result link", "").strip()
    if not url or not pid:
        return {"id": pid, "error": "Missing URL or ID"}

    for attempt in range(1, MAX_RETRIES+1):
        try:
            err, soup, final_url = scraper.request_single_patent(url, url=True)
            if err != "Success":
                raise ValueError(f"HTTP error: {err}")
            data = scraper.get_scraped_data(soup, pid, final_url)
            # Normalize and annotate:
            # Prefer the CSV patent id for consistency.
            data["id"]          = normalize_id(row.get("id", pid))
            data["url"]         = final_url or url
            data["csv_title"]   = row.get("title", "")
            data["original_id"] = row.get("id", "")
            return data
        except Exception as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** (attempt-1))
                logging.info(f"Retry {attempt}/{MAX_RETRIES} for {pid} in {delay}s.")
                time.sleep(delay)
            else:
                logging.error(f"Giving up {pid}: {e}")
                return {"id": pid, "url": url, "error": str(e)}

# ------------------------------------------------------------------------------
# JSON OUTPUT MANAGEMENT
# ------------------------------------------------------------------------------

def list_existing_json(folder):
    """
    Return list of (index,fpath) for files named all_patents_###.json in sorted order.
    """
    pattern = os.path.join(folder, "all_patents_*.json")
    files = glob.glob(pattern)
    out = {}
    for f in files:
        name = os.path.basename(f)
        num = name.replace("all_patents_", "").replace(".json","")
        try:
            idx = int(num)
            out[idx] = f
        except ValueError:
            pass
    return sorted(out.items())

def load_processed_ids(folder):
    """
    Scan existing JSON outputs and extract patent IDs.
    We look (in order) for:
      1) the 'original_id' field, if present (from the CSV),
      2) the 'url' field (using extract_patent_id),
      3) fallback to the JSON 'id'.
    All keys are normalized.
    """
    processed = set()
    for idx, fpath in list_existing_json(folder):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            logging.exception(f"Failed to load {fpath}, skipping.")
            continue

        for entry in arr:
            # 1) use original_id if available
            orig = entry.get("original_id", "")
            if orig:
                processed.add(normalize_id(orig))
                continue
            # 2) try the URL extraction
            pid = extract_patent_id(entry.get("url","") or "")
            if pid:
                processed.add(normalize_id(pid))
                continue
            # 3) fallback to scraped JSON 'id'
            pid = entry.get("id","")
            if pid:
                processed.add(normalize_id(pid))
    return processed

def append_patents(new_list, folder, chunk=CHUNK_SIZE):
    """
    Take a list of patent dicts, append to the last JSON if it has room, then
    create new JSON chunk files for the remainder.
    """
    if not new_list:
        return

    existing = list_existing_json(folder)
    next_idx = existing[-1][0]+1 if existing else 0

    # try filling the last file if not already full
    if existing:
        last_idx, last_file = existing[-1]
        try:
            with open(last_file, "r", encoding="utf-8") as f:
                content = json.load(f)
        except Exception:
            content = []
        if len(content) < chunk:
            space = chunk - len(content)
            to_add = new_list[:space]
            content.extend(to_add)
            tmp = last_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=4, ensure_ascii=False)
            os.replace(tmp, last_file)
            logging.info(f"Appended {len(to_add)} to {last_file}")
            new_list = new_list[space:]
            next_idx = last_idx + 1

    # write full chunks
    for i in range(0, len(new_list), chunk):
        chunk_data = new_list[i:i+chunk]
        outf = os.path.join(folder, OUTPUT_TEMPLATE.format(next_idx))
        tmp  = outf + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, outf)
        logging.info(f"Wrote {len(chunk_data)} to {outf}")
        next_idx += 1

# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------
def main():
    # 1) load all CSV rows
    files = glob.glob(CSV_PATTERN, recursive=True)
    if not files:
        logging.error("No CSVs found, exiting.")
        return

    all_rows = []
    for c in files:
        logging.info(f"Loading {c}")
        df = load_csv(c)
        if df is None or df.empty:
            logging.warning(f"Skipping empty or bad CSV {c}")
            continue
        if "result link" not in df.columns:
            logging.warning(f"No 'result link' in {c}, skipping.")
            continue
        all_rows.extend(df.to_dict(orient="records"))

    logging.info(f"Total rows from CSV: {len(all_rows)}")

    # 2) dedupe by CSV patent‑ID
    unique = {}
    for r in all_rows:
        pid = get_csv_patent_id(r)
        if pid and pid not in unique:
            unique[pid] = r
    deduped = list(unique.values())
    logging.info(f"{len(deduped)} unique patents after deduplication")

    # 3) load already processed IDs
    processed = load_processed_ids(OUTPUT_FOLDER)
    logging.info(f"{len(processed)} patents already processed (from JSON)")

    # 4) filter new rows to process
    new_rows = []
    skipped   = 0
    for r in deduped:
        pid = get_csv_patent_id(r)
        if pid and pid not in processed:
            new_rows.append(r)
        else:
            skipped += 1
    logging.info(f"Skipping {skipped} already processed; {len(new_rows)} new to fetch")

    # 5) process in parallel
    if new_rows:
        buffer = []
        with mp.Pool(NUM_PROCESSES, initializer=init_worker) as pool:
            with tqdm(total=len(new_rows), desc="Scraping patents") as pbar:
                for result in pool.imap_unordered(process_row, new_rows):
                    buffer.append(result)
                    # flush in CHUNK_SIZE increments
                    if len(buffer) >= CHUNK_SIZE:
                        append_patents(buffer, OUTPUT_FOLDER, CHUNK_SIZE)
                        buffer = []
                    pbar.update(1)
            # final flush
            if buffer:
                append_patents(buffer, OUTPUT_FOLDER, CHUNK_SIZE)
    else:
        logging.info("Nothing new to do.")

if __name__ == "__main__":
    main()
