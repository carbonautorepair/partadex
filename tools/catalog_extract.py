#!/usr/bin/env python3
"""
PDF catalog extraction pipeline for O'Reilly/Microgard All Makes catalog.
Supports multiple section configs; currently implements AIR_CABIN section.

Usage:
    python3 tools/catalog_extract.py --section aircabin --pages 420-916 \
        --db data/aircabin_staging.db --pdf data/microgard.pdf

Design:
  - Uses pdftohtml -xml via subprocess (stdlib only; avoids broken pypdf/pdfminer)
  - Per-page column-band derivation from brand header elements on each page
  - Font-size classification: size12=data text, Arial-Black=model header,
    size15+white=year banner, size6=footnote superscript, size18=make header
  - Row banding: engine-spec lines start rows; rows extend until next engine/model/year
  - Footnote superscripts attached by x-adjacency (never concatenated into part numbers)
  - N/R, N/A, N/S cells → NULL (nothing stored)
  - Sidebar single-letter elements excluded by x-position guard
  - HEPA variants detected by HP suffix in cabin Microgard column
"""

import argparse
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "1.1.0"

# ─────────────────────────── Section configs ──────────────────────────────────

SECTION_CONFIGS = {
    "aircabin": {
        # columns in left-to-right x order matching the 6 brand headers on each page
        "columns": [
            {"brand": "MICROGARD",      "filter_category": "engine_air", "name": "mg_air"},
            {"brand": "WIX",            "filter_category": "engine_air", "name": "wix_air"},
            {"brand": "K&N",            "filter_category": "engine_air", "name": "kn_air"},
            {"brand": "MICROGARD",      "filter_category": "cabin_air",  "name": "mg_cabin"},
            {"brand": "WIX",            "filter_category": "cabin_air",  "name": "wix_cabin"},
            {"brand": "K&N",            "filter_category": "cabin_air",  "name": "kn_cabin"},
        ],
        "footnote_pages": [3, 4, 5],
        # Regex matching brand-header label text (applied after stripping HTML)
        "header_label_re": re.compile(r"^(microgard|wix|k&n|k&amp;n)$", re.IGNORECASE),
    },
    # Oil filter section: add here with different column set
}

# ─────────────────────────── DB schema ────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extraction_runs (
    id                INTEGER PRIMARY KEY,
    source_pdf_path   TEXT NOT NULL,
    extracted_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    extractor_version TEXT NOT NULL,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS application_rows (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL,
    make            TEXT NOT NULL,
    year            INTEGER NOT NULL,
    model           TEXT NOT NULL,
    engine          TEXT,
    engine_code     TEXT,
    row_footnote    TEXT,
    source_pdf_path TEXT NOT NULL,
    source_page     INTEGER NOT NULL,
    source_y        REAL NOT NULL,
    raw_row_text    TEXT,
    confidence      TEXT NOT NULL,
    status          TEXT NOT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS application_parts (
    id                     INTEGER PRIMARY KEY,
    application_row_id     INTEGER NOT NULL,
    filter_category        TEXT NOT NULL,
    brand                  TEXT NOT NULL,
    source_column          TEXT NOT NULL,
    raw_part_number        TEXT NOT NULL,
    normalized_part_number TEXT,
    footnote               TEXT,
    confidence             TEXT NOT NULL,
    status                 TEXT NOT NULL,
    notes                  TEXT
);

CREATE TABLE IF NOT EXISTS footnotes (
    code TEXT PRIMARY KEY,
    text TEXT
);
"""

# ─────────────────────────── XML helpers ──────────────────────────────────────

def fetch_page_xml(pdf_path: str, page_num: int) -> ET.Element:
    """Run pdftohtml -xml for a single page and return the <page> element."""
    result = subprocess.run(
        ["pdftohtml", "-xml", "-f", str(page_num), "-l", str(page_num), "-stdout", pdf_path],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftohtml failed on page {page_num}: {result.stderr[:200]}")
    root = ET.fromstring(result.stdout)
    pages = root.findall("page")
    if not pages:
        raise ValueError(f"No <page> element for page {page_num}")
    return pages[0]


def get_text(elem: ET.Element) -> str:
    """Get inner text stripping child element markup."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def parse_fontspecs(page_elem: ET.Element):
    """Return (size_map, color_map, family_map) keyed by font id string."""
    sz, col, fam = {}, {}, {}
    for spec in page_elem.findall("fontspec"):
        fid = spec.get("id")
        sz[fid]  = int(spec.get("size", "0"))
        col[fid] = spec.get("color", "#000000").lower()
        fam[fid] = spec.get("family", "").lower()
    return sz, col, fam


# ─────────────────────────── Column band derivation ───────────────────────────

# Sidebar exclusion margins
LEFT_MARGIN  = 85
RIGHT_MARGIN = 855


def derive_column_bands(page_elem: ET.Element, sz_map: dict, section_config: dict):
    """
    Locate the 6 brand-header label elements (appear at top ~80-135 on every page)
    and derive per-column x-bands.
    Returns list of 6 (x_left, x_right) tuples, or None if < 6 headers found.
    """
    lre = section_config["header_label_re"]
    candidates = []
    for elem in page_elem.findall("text"):
        top  = int(elem.get("top",   "0"))
        left = int(elem.get("left",  "0"))
        w    = int(elem.get("width", "0"))
        fid  = elem.get("font", "")
        txt  = get_text(elem)
        # Brand headers live in the top ~145px header area
        if top < 60 or top > 145:
            continue
        if left < LEFT_MARGIN or left > RIGHT_MARGIN:
            continue
        if lre.match(txt):
            candidates.append((left, left + w, txt))

    candidates.sort(key=lambda c: c[0])

    # Collapse very close duplicates (within 10px)
    merged = []
    for c in candidates:
        if merged and c[0] - merged[-1][1] < 10:
            if (c[1] - c[0]) > (merged[-1][1] - merged[-1][0]):
                merged[-1] = c
        else:
            merged.append(c)

    if len(merged) < 6:
        return None

    # If somehow more than 6, take 6 with widest spread
    if len(merged) > 6:
        # heuristic: keep 6 spanning widest x range
        merged = merged[:6]

    # Build bands using midpoints between consecutive header centers.
    # Data elements appear ~15-25px LEFT of their column header label, so
    # set band boundaries at midpoints between adjacent header left-edges.
    centers = [cl for cl, cr, _ in merged]
    bands = []
    for i, (cl, cr, _) in enumerate(merged):
        # Band starts at midpoint to previous center (or far left)
        if i == 0:
            band_l = 330  # leave room for MGA parts at x≈354
        else:
            band_l = (centers[i - 1] + centers[i]) // 2
        # Band ends at midpoint to next center (or far right)
        if i + 1 < len(merged):
            band_r = (centers[i] + centers[i + 1]) // 2 - 1
        else:
            band_r = RIGHT_MARGIN
        bands.append((band_l, band_r))

    return bands


def assign_column(left: int, bands) -> int:
    """Return 0-5 column index for a data element at x=left."""
    if bands is None:
        return -1
    for ci, (bl, br) in enumerate(bands):
        if bl <= left <= br:
            return ci
    # Fall back to nearest midpoint
    best, best_d = -1, 99999
    for ci, (bl, br) in enumerate(bands):
        mid = (bl + br) / 2
        d = abs(left - mid)
        if d < best_d:
            best_d, best = d, ci
    return best


# ─────────────────────────── Part utilities ───────────────────────────────────

NR_VALS = {"N/R", "N/A", "N/S", "NR", "NA", "NS"}
_NR_PREFIX_RE = re.compile(r'^N/?[ARS]', re.IGNORECASE)

def is_nr(text: str) -> bool:
    t = text.strip().upper()
    return t in NR_VALS or _NR_PREFIX_RE.match(t) is not None


def is_valid_part(text: str) -> bool:
    """True if text plausibly contains a part number."""
    t = text.strip()
    if len(t) < 3:
        return False
    if not any(c.isdigit() for c in t):
        return False
    return True


def normalize_part(raw: str) -> str:
    """Normalize a part number: strip whitespace, uppercase, remove trailing annotations."""
    s = raw.strip().upper()
    # Strip trailing annotation like " - Left", " - Right", " - Old Body Style", etc.
    s = re.sub(r'\s*-\s*[A-Z].*$', '', s).strip()
    return s


# ─────────────────────────── Footnote extraction ──────────────────────────────

def extract_footnotes(pdf_path: str, pages: list) -> dict:
    """
    Parse footnote pages (PDF pages 3-5).  Table layout:
      col0 = numeric code (x ~86-135)
      col1 = English text (x ~122-345)
    Returns {code_str: english_text}.
    """
    footnotes = {}

    for pg in pages:
        try:
            page_elem = fetch_page_xml(pdf_path, pg)
        except Exception:
            continue
        sz_map, _, _ = parse_fontspecs(page_elem)

        rows_by_top = {}
        for elem in page_elem.findall("text"):
            txt  = get_text(elem)
            if not txt:
                continue
            top  = int(elem.get("top",  "0"))
            left = int(elem.get("left", "0"))
            fid  = elem.get("font", "")
            sz   = sz_map.get(fid, 0)
            # Only size-12 text; ignore headers (size 15+), skip French/Spanish columns
            if sz not in (12, 16) and sz < 10:
                continue
            # Footnote page layout: codes at x<135, English at 120<x<350
            if left > 350:
                continue
            if top not in rows_by_top:
                rows_by_top[top] = []
            rows_by_top[top].append((left, txt))

        # Process each line
        last_code = None
        for top in sorted(rows_by_top):
            items = sorted(rows_by_top[top], key=lambda x: x[0])
            if not items:
                continue
            first_left, first_txt = items[0]

            if first_txt.strip().lstrip('-').isdigit() and first_left < 135:
                last_code = first_txt.strip()
                eng_parts = [t for l, t in items if l >= 118]
                if eng_parts and last_code:
                    footnotes[last_code] = " ".join(eng_parts)
            elif last_code and first_left >= 118:
                # Continuation of previous footnote
                footnotes[last_code] = footnotes.get(last_code, "") + " " + " ".join(t for _, t in items)

    return {k: v.strip() for k, v in footnotes.items() if v.strip()}


# ─────────────────────────── Engine-line detection ────────────────────────────

# Continuation descriptors: second line of an engine description (not a new row)
_CONTINUATION_DESCRIPTORS_RE = re.compile(
    r'^(Electric/?Gas|Diesel|Gas|Hybrid|CNG|LPG|Bi-?Fuel|PHEV|BEV|'
    r'FCV|HEV|\d+-?Cyl|Turbo|[A-Z0-9]+/[A-Z0-9]+)$',
    re.IGNORECASE
)

# Engine lines start with a displacement prefix or fuel type
ENGINE_START_RE = re.compile(
    r'^(V\s*\d|L\d|I\d|H\d|W\d|'         # VN, LN, IN, HN, WN displacement prefixes
    r'\d\s*Cyl|\d+-?Cyl|'                  # N Cyl variants
    r'\d+\.\d+L|'                           # N.NL displacement (e.g. "3.5L V6")
    r'\d+\s+\d+L|'                          # N NL patterns
    r'Electric|Diesel|Petrol|Gas)',
    re.IGNORECASE
)

# Strings to ignore as spurious data (page headers, footnote refs)
IGNORE_TEXT_RE = re.compile(
    r"^(O'Reilly|All Makes|Air.Cabin|Microgard|WIX|K&N|Wix|"
    r"Year|Model|Engine|Eng\.|VIN|NIV|Code|Año|Année|Modelo|Moteur|"
    r"See pages|footnotes|notas|notes)$",
    re.IGNORECASE
)


# ─────────────────────────── Page parser ──────────────────────────────────────

# Y threshold: page content starts below header area
# Note: first make header on page can be at top=138; use 135 to catch it
CONTENT_TOP_MIN = 135
CONTENT_TOP_MAX = 1105

# X thresholds for left side (vehicle info) vs right side (part columns)
# Left-side content: engine, eng code, VIN; right side: parts
LEFT_MAX_X  = 335   # x < this = left-side region (engine text + eng code + vin)
VIN_X_RANGE = (295, 340)
ENG_CODE_X_RANGE = (200, 295)
ENGINE_TEXT_X_MAX = 200


def parse_page(page_elem, page_num, pdf_path, section_config,
               current_make, current_year, column_bands):
    """
    Parse one page of the catalog section.
    Returns (list_of_row_dicts, current_make, current_year, column_bands).
    Each row dict has keys: make, year, model, engine, engine_code, row_footnote,
    source_page, source_y, raw_row_text, confidence, status, parts.
    parts = {col_idx: [(raw_str, footnote_str, is_hepa), ...]}
    """
    sz_map, col_map, fam_map = parse_fontspecs(page_elem)

    # Re-derive column bands from this page's headers (they shift slightly page to page)
    new_bands = derive_column_bands(page_elem, sz_map, section_config)
    if new_bands:
        column_bands = new_bands

    # Collect and classify all text elements
    class E:
        __slots__ = ('top','left','width','text','role','size')
        def __init__(self, top, left, width, text, role, size):
            self.top=top; self.left=left; self.width=width
            self.text=text; self.role=role; self.size=size

    all_e = []
    for elem in page_elem.findall("text"):
        txt = get_text(elem)
        if not txt:
            continue
        top  = int(elem.get("top",   "0"))
        left = int(elem.get("left",  "0"))
        w    = int(elem.get("width", "0"))
        fid  = elem.get("font", "")
        sz   = sz_map.get(fid, 0)
        color = col_map.get(fid, "#000000")
        family = fam_map.get(fid, "")

        # Discard sidebar single letters
        if (left < LEFT_MARGIN or left > RIGHT_MARGIN) and len(txt) <= 2 and txt.isalpha():
            continue

        # Discard page header/footer by position
        if top < CONTENT_TOP_MIN or top > CONTENT_TOP_MAX:
            continue

        # Classify
        if sz == 6:
            role = "super"
        elif sz >= 15 and color == "#ffffff":
            role = "year"
        elif sz >= 14 and "arial-black" in family:
            role = "model"
        elif sz >= 17 and "arial-black" not in family and color == "#000000":
            role = "make"
        elif sz == 18 and color == "#000000":
            role = "make"
        else:
            role = "data"

        all_e.append(E(top, left, w, txt, role, sz))

    all_e.sort(key=lambda e: (e.top, e.left))

    # Group into visual lines (elements within LINE_TOL vertical pixels = same line)
    LINE_TOL = 4
    lines = []
    for e in all_e:
        if lines and abs(e.top - lines[-1][0].top) <= LINE_TOL:
            lines[-1].append(e)
        else:
            lines.append([e])

    # Index superscripts for footnote lookup: super_top -> [(left, text)]
    super_idx = {}
    for e in all_e:
        if e.role == "super":
            if e.top not in super_idx:
                super_idx[e.top] = []
            super_idx[e.top].append((e.left, e.text))

    def find_super(data_left, data_top, data_width):
        """Find superscript immediately right of a data element."""
        right_edge = data_left + data_width
        for stop, items in super_idx.items():
            if abs(stop - data_top) <= 20:
                for sl, st in items:
                    if right_edge <= sl <= right_edge + 28:
                        return st
        return None

    # ── State machine ──
    rows_out = []

    current_model = None
    current_engine = None
    current_engine_code = None
    current_row_footnote = None
    pending_parts = {i: [] for i in range(7)}   # 0-5 = columns, 6 = HEPA sentinel
    pending_top = None
    pending_raw_tokens = []
    last_engine_top = None   # track y of last engine-start line for continuation detection

    def flush():
        nonlocal pending_parts, pending_top, pending_raw_tokens
        if current_engine is None or current_make is None or current_year is None:
            pending_parts = {i: [] for i in range(7)}
            pending_top = None
            pending_raw_tokens = []
            return
        row = dict(
            make=current_make, year=current_year,
            model=current_model or "",
            engine=current_engine,
            engine_code=current_engine_code,
            row_footnote=current_row_footnote,
            source_page=page_num,
            source_y=float(pending_top) if pending_top is not None else 0.0,
            raw_row_text=" | ".join(pending_raw_tokens),
            confidence="high", status="extracted",
            parts={i: list(pending_parts[i]) for i in range(7)},
        )
        rows_out.append(row)
        pending_parts = {i: [] for i in range(7)}
        pending_top = None
        pending_raw_tokens = []

    for line in lines:
        line_top = line[0].top

        # ── Make header (size18 non-black-italic, not sidebar) ──
        makes = [e for e in line if e.role == "make"]
        if makes:
            txt = makes[0].text
            txt = re.sub(r"\s*\(Cont'?d/Suite\)\s*", "", txt, flags=re.IGNORECASE).strip()
            if txt and not IGNORE_TEXT_RE.match(txt):
                flush()
                current_make = txt.upper()
                current_model = None
                current_engine = None
                current_engine_code = None
                current_row_footnote = None
            continue

        # ── Year banner ──
        years = [e for e in line if e.role == "year"]
        if years:
            for yb in years:
                m = re.search(r'\b(20\d\d|19\d\d)\b', yb.text)
                if m:
                    flush()
                    current_year = int(m.group(1))
                    current_engine = None
                    current_engine_code = None
                    current_row_footnote = None
            continue

        # ── Model header (Arial-Black) ──
        models = [e for e in line if e.role == "model"]
        if models:
            txt = " ".join(e.text for e in models)
            txt = re.sub(r"\s*\(Cont'?d/Suite\)\s*", "", txt, flags=re.IGNORECASE).strip()
            if txt:
                flush()
                current_model = txt
                current_engine = None
                current_engine_code = None
                current_row_footnote = None
            continue

        # ── Data line ──
        data = [e for e in line if e.role == "data"]
        if not data:
            continue

        # Separate left-side (vehicle info) from right-side (parts)
        col0_x = column_bands[0][0] if column_bands else 340
        left_side  = [e for e in data if e.left < col0_x - 5]
        right_side = [e for e in data if e.left >= col0_x - 5]

        # ── Engine line detection ──
        eng_candidate = None
        for e in left_side:
            if e.left < ENGINE_TEXT_X_MAX and ENGINE_START_RE.match(e.text):
                eng_candidate = e.text
                break

        # A line that matches ENGINE_START_RE but is within 20px of the previous engine
        # line top → it's a continuation descriptor (e.g. "Electric/Gas" second line).
        # These parts should be merged into the current row, not start a new one.
        # "Electric/Gas" and similar short descriptors are the key pattern here.
        is_continuation = (
            eng_candidate is not None
            and last_engine_top is not None
            and abs(line_top - last_engine_top) <= 20
            and current_engine is not None
            and _CONTINUATION_DESCRIPTORS_RE.match(eng_candidate)
        )

        # Engine-code-only variant rows: line has only an engine code (x ~200-295)
        # with right-side parts but no engine text (x < ENGINE_TEXT_X_MAX).
        # These represent a different engine variant (same displacement, different code).
        # Pattern: left_side has only code-like element at 200-295, right_side has parts.
        ENG_CODE_ONLY_RE = re.compile(r'^[A-Z0-9]{3,12}$')
        engine_code_only_elements = [
            e for e in left_side
            if ENG_CODE_X_RANGE[0] <= e.left <= ENG_CODE_X_RANGE[1]
            and ENG_CODE_ONLY_RE.match(e.text)
        ]
        no_engine_text_on_line = not any(
            e.left < ENGINE_TEXT_X_MAX for e in left_side
        )
        is_eng_code_only_variant = (
            no_engine_text_on_line
            and len(engine_code_only_elements) >= 1
            and bool(right_side)
            and current_engine is not None
            and not is_continuation
        )

        if eng_candidate is not None and not is_continuation:
            flush()
            # Collect full engine text (may span multiple left-side elements)
            engine_parts = [eng_candidate]
            for e in left_side:
                if e.left >= ENGINE_TEXT_X_MAX and not (
                    ENG_CODE_X_RANGE[0] <= e.left <= ENG_CODE_X_RANGE[1]
                ) and not (
                    VIN_X_RANGE[0] <= e.left <= VIN_X_RANGE[1]
                ):
                    engine_parts.append(e.text)
            current_engine = " ".join(engine_parts)
            last_engine_top = line_top

            # Engine code (x ~200-295)
            eng_codes = [e for e in left_side
                         if ENG_CODE_X_RANGE[0] <= e.left <= ENG_CODE_X_RANGE[1]]
            current_engine_code = " ".join(e.text for e in eng_codes) or None

            # VIN / row footnote (x ~295-340)
            vin_elems = [e for e in left_side
                         if VIN_X_RANGE[0] <= e.left <= VIN_X_RANGE[1]]
            vin_text = ",".join(e.text for e in vin_elems)
            # Pure numeric = footnote reference on row, not VIN
            if re.match(r'^\d{1,2}(,\d{1,2})*$', vin_text):
                current_row_footnote = vin_text
            else:
                current_row_footnote = None

            pending_top = line_top

        elif is_eng_code_only_variant:
            # New variant row: flush previous, reuse engine text, new engine code
            flush()
            # Keep current_engine (same displacement), update engine code
            current_engine_code = " ".join(e.text for e in engine_code_only_elements)
            pending_top = line_top

        elif current_engine is not None and left_side:
            # Continuation of engine text (e.g. "Electric/Gas" on next line)
            for e in left_side:
                if e.left < ENGINE_TEXT_X_MAX:
                    current_engine += " " + e.text
                    break

        # ── Part numbers on right side ──
        for e in right_side:
            txt = e.text.strip()
            if not txt or is_nr(txt):
                continue
            if IGNORE_TEXT_RE.match(txt):
                continue
            if not is_valid_part(txt):
                continue

            ci = assign_column(e.left, column_bands)
            if ci < 0:
                continue

            fn = find_super(e.left, e.top, e.width)

            is_hepa = ci == 3 and txt.upper().endswith("HP")
            store_ci = 6 if is_hepa else ci   # 6 = HEPA sentinel

            if current_engine is not None:
                if pending_top is None:
                    pending_top = line_top
                pending_parts[store_ci].append((txt, fn, is_hepa))
                pending_raw_tokens.append(txt)

    flush()
    return rows_out, current_make, current_year, column_bands


# ─────────────────────────── Main extraction loop ─────────────────────────────

def run_extraction(pdf_path: str, start_page: int, end_page: int,
                   db_path: str, section_name: str):
    section_config = SECTION_CONFIGS[section_name]
    columns = section_config["columns"]

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Extract footnotes
    print(f"Extracting footnotes from pages {section_config['footnote_pages']}...", flush=True)
    footnotes = extract_footnotes(pdf_path, section_config["footnote_pages"])
    print(f"  Found {len(footnotes)} footnote codes", flush=True)
    conn.executemany(
        "INSERT OR REPLACE INTO footnotes(code, text) VALUES (?, ?)",
        list(footnotes.items())
    )
    conn.commit()

    # Create run record
    run_id = conn.execute(
        "INSERT INTO extraction_runs(source_pdf_path, extractor_version, notes) VALUES (?,?,?)",
        (pdf_path, VERSION, f"section={section_name} pages={start_page}-{end_page}")
    ).lastrowid
    conn.commit()

    current_make = None
    current_year = None
    column_bands = None

    total_rows = 0
    total_parts = 0

    print(f"Processing pages {start_page}–{end_page}...", flush=True)
    for page_num in range(start_page, end_page + 1):
        if page_num % 50 == 0:
            print(f"  Page {page_num} (rows so far: {total_rows})", flush=True)
        try:
            page_elem = fetch_page_xml(pdf_path, page_num)
        except Exception as exc:
            print(f"  WARN page {page_num}: {exc}", file=sys.stderr)
            continue

        rows, current_make, current_year, column_bands = parse_page(
            page_elem, page_num, pdf_path, section_config,
            current_make, current_year, column_bands
        )

        for row in rows:
            row_id = conn.execute(
                """INSERT INTO application_rows
                   (run_id,make,year,model,engine,engine_code,row_footnote,
                    source_pdf_path,source_page,source_y,raw_row_text,confidence,status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, row["make"], row["year"], row["model"],
                 row["engine"], row["engine_code"], row["row_footnote"],
                 pdf_path, row["source_page"], row["source_y"],
                 row["raw_row_text"], row["confidence"], row["status"])
            ).lastrowid
            total_rows += 1

            # Insert parts per column
            for ci in range(7):
                if not row["parts"].get(ci):
                    continue

                if ci == 6:
                    # HEPA parts
                    for raw, fn, _ in row["parts"][ci]:
                        norm = normalize_part(raw)
                        conn.execute(
                            """INSERT INTO application_parts
                               (application_row_id,filter_category,brand,source_column,
                                raw_part_number,normalized_part_number,footnote,
                                confidence,status)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (row_id, "cabin_air", "MICROGARD HEPA", "mg_cabin_hepa",
                             raw, norm, fn, "high", "extracted")
                        )
                        total_parts += 1
                else:
                    col_cfg = columns[ci]
                    for raw, fn, _ in row["parts"][ci]:
                        norm = normalize_part(raw)
                        conn.execute(
                            """INSERT INTO application_parts
                               (application_row_id,filter_category,brand,source_column,
                                raw_part_number,normalized_part_number,footnote,
                                confidence,status)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (row_id, col_cfg["filter_category"], col_cfg["brand"],
                             col_cfg["name"], raw, norm, fn, "high", "extracted")
                        )
                        total_parts += 1

        if page_num % 100 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    print(f"Done. Rows: {total_rows}, Parts: {total_parts}", flush=True)
    return total_rows, total_parts


# ─────────────────────────── CLI ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Extract filter catalog data from PDF")
    p.add_argument("--section", required=True, choices=list(SECTION_CONFIGS.keys()))
    p.add_argument("--pages",   required=True, help="e.g. 420-916")
    p.add_argument("--db",      required=True, help="Output SQLite path")
    p.add_argument("--pdf",     default="data/microgard.pdf", help="Source PDF")
    args = p.parse_args()

    start_pg, end_pg = map(int, args.pages.split("-"))
    print(f"Catalog extractor v{VERSION}")
    print(f"  Section={args.section}  Pages={start_pg}-{end_pg}  PDF={args.pdf}  DB={args.db}")
    run_extraction(args.pdf, start_pg, end_pg, args.db, args.section)


if __name__ == "__main__":
    main()
