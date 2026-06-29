#!/usr/bin/env python3
"""
Extract oil, air, and cabin filter data from the Microgard/O'Reilly catalog PDF.
Outputs oil_rows.json and air_cabin_rows.json.

Known bugs fixed in this version:
- Year column: sparse (one row per year-block), carried forward
- Make: detected from header text at page top, not from "Section" keyword
- Footnotes: superscript digits separated by font size
- Air Microgard doubling: de-duplicated by checking for repeated substrings
- Model-carry: reset on make change, reject fuel-type/header lines as models
"""

import json
import re
import sys
import pdfplumber

PDF_PATH = "data/microgard.pdf"

# Page ranges (1-indexed)
OIL_START, OIL_END = 6, 419
AIR_START, AIR_END = 420, 922

# Column bins: (name, x_min, x_max)
OIL_COLUMNS = [
    ("vehicle",          60, 200),
    ("microgard",       230, 290),
    ("microgard_select", 285, 340),
    ("wix",             340, 385),
    ("wix_xp",          385, 435),
    ("mobil1",          435, 480),
    ("kn",              485, 530),
]

AIR_CABIN_COLUMNS = [
    ("vehicle",           60, 210),
    ("air_microgard",    240, 298),
    ("air_wix",          298, 348),
    ("air_kn",           348, 392),
    ("cabin_microgard",  392, 448),
    ("cabin_wix",        448, 498),
    ("cabin_kn",         498, 540),
]

JUNK_PATTERNS = re.compile(
    r'^[A-Z]$|^N/?[RA]$|^ALL$|^MAKES$|^OIL$|^FILTERS$|^AIR$|^CABIN$|'
    r'^ENGINE$|^FILTER$|^Section$|^O.Reilly|^Microgard|^Select$|^WIX$|'
    r'^Mobil$|^K&N$|^XP$|^\d{1,2}$|^MGL$|^MSL$|^MGA$'
)

HEADER_JUNK = re.compile(
    r'Section\s+O.Reilly.*|All\s+Makes\s+Oil\s+Filters?|'
    r'All\s+Makes\s+Air.*Filters?|Engine\s+Air\s+Filter|'
    r'Cabin\s+Air\s+Filter|Microgard|Select|WIX|XP|Mobil|K&N',
    re.IGNORECASE
)

FUEL_TYPES = {
    'DIESEL', 'GAS', 'FLEX', 'HYBRID', 'ELECTRIC', 'CNG', 'LPG',
    'TURBO', 'SUPERCHARGED', 'FFV', 'NATURAL GAS', 'ECOBOOST',
    'ECOBOOST TURBO', 'ELECTRIC/GAS', 'PLUG-IN HYBRID',
}

ENGINE_CODE_PATTERN = re.compile(
    r'^[A-Z]\d{2}[A-Z]\d+[A-Z]?$|'    # N52B30A, W10B16A, N26B20A
    r'^[A-Z]{2}\d{2}[A-Z]{2}$|'        # VQ35HR, QR25DE
    r'^[A-Z]{2,4}\d{1,3}$|'            # ED3, EDG3
    r'^\d[A-Z]{2,}\d*[A-Z]*$|'         # 4GRFSE, 2UZFE
    r'^[A-Z]\d{2,}[A-Z]*\d*$|'         # L15CA
    r'^[A-Z]{2}\d{2}[A-Z]\d+$|'        # FB25BC-like
    r'^[A-Z]\*$|'                       # P*
    r'^Charged$|^See$|^Ver$|^Voir$',    # header/continuation junk
    re.IGNORECASE
)

KNOWN_ENGINE_CODES = {
    'CCTA', 'EDG', 'ED3', 'ED6', 'EDE', 'ERB', 'ERE',
    'CBFA', 'CAEB', 'CPMA', 'CNCD', 'CHPA', 'CPLA',
    'CYMC', 'CJAA', 'CKRA', 'CPKA', 'CZCA', 'CZDA',
    'CZEA', 'CZTA', 'DKZA', 'DGUA', 'DFHA', 'DFKA',
}

MODEL_WHITELIST = {'H3T', 'H3X', 'GT', 'TT', 'XC40', 'XC60', 'XC90',
                   'CT4', 'CT5', 'CT6', 'XT4', 'XT5', 'XT6',
                   'CX3', 'CX5', 'CX9', 'MX5', 'MX30',
                   'Q3', 'Q5', 'Q7', 'Q8', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8',
                   'S3', 'S4', 'S5', 'S6', 'S7', 'S8',
                   'X1', 'X2', 'X3', 'X4', 'X5', 'X6', 'X7',
                   'Z4', 'M2', 'M3', 'M4', 'M5', 'M6', 'M8',
                   'C30', 'C70', 'S40', 'S60', 'S80', 'S90', 'V40', 'V60', 'V90'}

MAKE_PATTERN = re.compile(
    r'\b(ACURA|ALFA ROMEO|AUDI|BMW|BUICK|CADILLAC|CHEVROLET|CHRYSLER|'
    r'DODGE(?:\s*\(ALSO SEE RAM\))?|FIAT|FORD|FREIGHTLINER|GMC|HONDA|'
    r'HUMMER|HYUNDAI|INFINITI|ISUZU|JAGUAR|JEEP|KIA|LAND ROVER|LEXUS|'
    r'LINCOLN|LOTUS|MASERATI|MAZDA|MERCEDES[- ]?BENZ|MERCURY|MINI|'
    r'MITSUBISHI|MOBILITY VENTURES|NISSAN|OLDSMOBILE|PONTIAC|PORSCHE|'
    r'RAM|SATURN|SCION|SMART|SUBARU|SUZUKI|TOYOTA|VOLKSWAGEN|VOLVO)\b',
    re.IGNORECASE
)


def extract_make_from_header(page):
    """Extract make name from the page header area (top 70px)."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    header_words = [w for w in words if w['top'] < 70]
    if not header_words:
        header_words = [w for w in words if w['top'] < 90]
    header_text = ' '.join(w['text'] for w in sorted(header_words, key=lambda w: w['x0']))
    header_text = HEADER_JUNK.sub('', header_text).strip()
    header_text = re.sub(r'\s+', ' ', header_text).strip()

    m = MAKE_PATTERN.search(header_text)
    if m:
        make = m.group(1).upper().strip()
        # Extract year range if present
        year_match = re.search(r'(\d{4})\s*[-–]\s*(\d{4})', header_text)
        return make, year_match
    return None, None


def separate_footnote(chars_in_word):
    """Separate part number from superscript footnote using font size."""
    if not chars_in_word:
        return '', ''

    sizes = [c.get('size', 0) for c in chars_in_word]
    if not sizes:
        return ''.join(c['text'] for c in chars_in_word), ''

    main_size = max(set(sizes), key=sizes.count)
    part = []
    footnote = []
    for c in chars_in_word:
        if c.get('size', main_size) < main_size * 0.85:
            footnote.append(c['text'])
        else:
            part.append(c['text'])

    return ''.join(part), ''.join(footnote)


PART_BASE_PATTERNS = {
    'microgard':        re.compile(r'^(MGL\d{5,6})(\d{2,3})$'),
    'microgard_select': re.compile(r'^(MSL\d{5,6})(\d{2,3})$'),
    'wix':              re.compile(r'^(\d{5,6})(\d{2,3})$'),
    'wix_xp':           re.compile(r'^(\d{5,6}XP)(\d{2,3})$'),
    'mobil1':           re.compile(r'^(M1C?-\d{3,4}[A-Z]?)(\d{2,3})$'),
    'kn':               re.compile(r'^(HP-\d{4,5})(\d{2,3})$'),
    'air_microgard':    re.compile(r'^(MGA\d{5,6})(\d{2,3})$'),
    'air_wix':          re.compile(r'^(\d{2}-\d{4,5})(\d{2,3})$'),
    'air_kn':           re.compile(r'^(\d{2}-\d{4,5})(\d{2,3})$'),
    'cabin_microgard':  re.compile(r'^(MGA\d{5,6})(\d{2,3})$'),
    'cabin_wix':        re.compile(r'^(WP\d{5,6})(\d{2,3})$'),
    'cabin_kn':         re.compile(r'^(VF\d{4,5})(\d{2,3})$'),
}


def strip_trailing_footnote(part_num, col_name):
    """Fallback: split trailing footnote digits from part number by pattern."""
    pattern = PART_BASE_PATTERNS.get(col_name)
    if pattern:
        m = pattern.match(part_num)
        if m:
            return m.group(1), m.group(2)
    return part_num, ''


def dedup_microgard(val):
    """Fix MGA4284342843 → MGA42843 (doubled part number)."""
    if not val:
        return val
    for prefix in ('MGA', 'MGL', 'MSL'):
        if val.startswith(prefix):
            rest = val[len(prefix):]
            half = len(rest) // 2
            if half > 0 and rest[:half] == rest[half:]:
                return prefix + rest[:half]
    return val


def bin_word(x_mid, columns):
    """Assign a word to a column based on its x midpoint."""
    for name, xmin, xmax in columns:
        if xmin <= x_mid <= xmax:
            return name
    return None


def is_year(text):
    return bool(re.match(r'^(19|20)\d{2}$', text))


def is_model_junk(text):
    """Reject text that shouldn't be treated as a model name."""
    t = text.upper().strip()
    if t in MODEL_WHITELIST:
        return False
    if t in FUEL_TYPES:
        return True
    if t in KNOWN_ENGINE_CODES:
        return True
    if JUNK_PATTERNS.match(t):
        return True
    if re.match(r'^(L\d|V\d|H\d|W\d|I\d)', t) and 'L' in t:
        return True
    if ENGINE_CODE_PATTERN.match(t):
        return True
    if '/' in t and any(w in t for w in ('GAS', 'ELECTRIC', 'DIESEL', 'HYBRID')):
        return True
    return False


def extract_section(pdf, start_page, end_page, columns, section_name):
    """Extract rows from a section of the PDF."""
    rows = []
    current_make = None
    current_year = None
    current_model = None
    current_engine = None

    total_pages = end_page - start_page + 1

    for page_num in range(start_page, end_page + 1):
        page_idx = page_num - 1
        if page_idx >= len(pdf.pages):
            break

        page = pdf.pages[page_idx]

        if (page_num - start_page) % 50 == 0:
            print(f"  {section_name}: page {page_num} ({page_num - start_page}/{total_pages})",
                  file=sys.stderr)

        # Get make from header
        make, year_range = extract_make_from_header(page)
        if make:
            if make != current_make:
                current_model = None
                current_engine = None
                current_year = None
            current_make = make

        if not current_make:
            continue

        # Get all words with positions
        words = page.extract_words(x_tolerance=2, y_tolerance=2)

        # Also get chars for footnote separation
        chars = page.chars

        # Group words by y-position (row clustering)
        data_words = [w for w in words if w['top'] > 90]
        if not data_words:
            continue

        # Cluster by y (within 4px = same row)
        data_words.sort(key=lambda w: w['top'])
        row_groups = []
        current_row = [data_words[0]]
        for w in data_words[1:]:
            if abs(w['top'] - current_row[0]['top']) < 4:
                current_row.append(w)
            else:
                row_groups.append(current_row)
                current_row = [w]
        row_groups.append(current_row)

        for row_words in row_groups:
            # Bin each word into a column
            binned = {}
            for w in row_words:
                x_mid = (w['x0'] + w['x1']) / 2
                col = bin_word(x_mid, columns)
                if col:
                    if col not in binned:
                        binned[col] = []
                    binned[col].append(w)

            if not binned:
                continue

            # Process vehicle column
            vehicle_words = binned.get('vehicle', [])
            vehicle_text = ' '.join(w['text'] for w in sorted(vehicle_words, key=lambda w: w['x0']))

            # Check for year
            for w in vehicle_words:
                if is_year(w['text']):
                    current_year = int(w['text'])

            # Check for model/engine info
            non_year_words = [w for w in vehicle_words if not is_year(w['text'])]
            if non_year_words:
                text = ' '.join(w['text'] for w in sorted(non_year_words, key=lambda w: w['x0']))
                text = text.strip()

                if text and not JUNK_PATTERNS.match(text):
                    # Heuristic: if it contains displacement info (L, cc, CID), it's engine
                    if re.search(r'\d+\.\d+L|\d+cc|\d+CID|[VLIWHF]\d\s', text):
                        current_engine = text
                    elif not is_model_junk(text):
                        # Check if this looks like a model (not a continuation of engine)
                        if re.match(r'^[A-Z]', text) and len(text) > 1:
                            # If text has engine-like patterns mixed in, split
                            eng_match = re.search(r'((?:L|V|H|W|I|F)\d\s+\d+\.\d+L.*)', text)
                            if eng_match:
                                model_part = text[:eng_match.start()].strip()
                                engine_part = eng_match.group(1).strip()
                                if model_part:
                                    current_model = model_part
                                current_engine = engine_part
                            else:
                                current_model = text
                                current_engine = None

            # Process part columns
            part_cols = {k: v for k, v in binned.items() if k != 'vehicle'}
            if not part_cols:
                continue

            # Build part number values with footnote separation
            part_values = {}
            for col_name, col_words in part_cols.items():
                combined_text = ' '.join(w['text'] for w in sorted(col_words, key=lambda w: w['x0']))
                combined_text = combined_text.strip()

                if not combined_text or JUNK_PATTERNS.match(combined_text):
                    continue

                # Get chars for these words to detect footnotes
                col_chars = []
                for w in col_words:
                    word_chars = [c for c in chars
                                  if abs(c['top'] - w['top']) < 3
                                  and w['x0'] - 1 <= c['x0'] <= w['x1'] + 1]
                    col_chars.extend(sorted(word_chars, key=lambda c: c['x0']))

                part_num, footnote = separate_footnote(col_chars)
                part_num = re.sub(r'\s+', '', part_num)

                # Apply Microgard dedup
                if 'microgard' in col_name:
                    part_num = dedup_microgard(part_num)

                # Fallback footnote strip: if font-size detection didn't split,
                # try pattern-based separation for known part formats
                if not footnote and part_num:
                    part_num, footnote = strip_trailing_footnote(part_num, col_name)

                if part_num and len(part_num) >= 3:
                    part_values[col_name] = {
                        'part_number': part_num,
                        'footnote': footnote if footnote else None
                    }

            if part_values and current_make:
                row = {
                    'make': current_make,
                    'year': current_year,
                    'model': current_model,
                    'engine': current_engine,
                    'source_page': page_num,
                    'parts': part_values
                }
                rows.append(row)

    return rows


def main():
    print("Opening PDF...", file=sys.stderr)
    pdf = pdfplumber.open(PDF_PATH)
    print(f"Total pages: {len(pdf.pages)}", file=sys.stderr)

    # Extract oil section
    print(f"\n=== OIL SECTION (pp {OIL_START}-{OIL_END}) ===", file=sys.stderr)
    oil_rows = extract_section(pdf, OIL_START, OIL_END, OIL_COLUMNS, "Oil")
    print(f"Oil rows extracted: {len(oil_rows)}", file=sys.stderr)

    with open('data/oil_rows.json', 'w') as f:
        json.dump(oil_rows, f)

    # Extract air/cabin section
    print(f"\n=== AIR/CABIN SECTION (pp {AIR_START}-{AIR_END}) ===", file=sys.stderr)
    air_rows = extract_section(pdf, AIR_START, AIR_END, AIR_CABIN_COLUMNS, "Air/Cabin")
    print(f"Air/cabin rows extracted: {len(air_rows)}", file=sys.stderr)

    with open('data/air_cabin_rows.json', 'w') as f:
        json.dump(air_rows, f)

    # Summary
    oil_makes = set(r['make'] for r in oil_rows if r['make'])
    air_makes = set(r['make'] for r in air_rows if r['make'])
    oil_with_year = sum(1 for r in oil_rows if r['year'])
    air_with_year = sum(1 for r in air_rows if r['year'])

    print(f"\n=== SUMMARY ===", file=sys.stderr)
    print(f"Oil:       {len(oil_rows):,} rows, {len(oil_makes)} makes, {oil_with_year:,} with year", file=sys.stderr)
    print(f"Air/Cabin: {len(air_rows):,} rows, {len(air_makes)} makes, {air_with_year:,} with year", file=sys.stderr)

    # Quick sanity: GX 470
    gx_rows = [r for r in oil_rows if r.get('model') and 'GX' in r['model'] and '470' in r['model']]
    if gx_rows:
        print(f"\nGX 470 sample: {json.dumps(gx_rows[0], indent=2)}", file=sys.stderr)

    pdf.close()
    print("\nDone.", file=sys.stderr)


if __name__ == '__main__':
    main()
