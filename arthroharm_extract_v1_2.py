#!/usr/bin/env python3
"""ArthroHarm v1.2 development extractor.

v1.2 is an error-driven successor to v1.1. The 60-article Stage I corpus is a
development input for this version and must never be described as v1.2
validation. Frozen v1.1 files are imported read-only and remain untouched.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("arthroharm_v11_frozen_base", HERE / "arthroharm_extract_v1_1.py")
V11 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(V11)


def load_rules(path: Path) -> dict:
    return V11.load_rules(path)


def category_hits(text: str, rules: dict) -> list[tuple[int, str, str, str]]:
    hits = V11.category_hits(text, rules)
    occupied = [(start, start + len(term)) for start, _, term, _ in hits]
    for phrase in rules.get("unmappable_event_patterns", []):
        match = V11.phrase_regex(phrase).search(text)
        if not match:
            continue
        span = (match.start(), match.end())
        if any(max(span[0], old[0]) < min(span[1], old[1]) for old in occupied):
            continue
        hits.append((match.start(), "Unmappable", match.group(0), "UNMAPPED_EVENT"))
        occupied.append(span)
    for phrase in rules.get("unclear_event_patterns", []):
        match = V11.phrase_regex(phrase).search(text)
        if not match:
            continue
        span = (match.start(), match.end())
        if any(max(span[0], old[0]) < min(span[1], old[1]) for old in occupied):
            continue
        hits.append((match.start(), "Unclear", match.group(0), "UNCLEAR_EVENT"))
        occupied.append(span)
    hits.sort(key=lambda row: (row[0], -len(row[2])))
    return hits


def parse_body_numeric(sentence: str, term_start: int, rules: dict) -> tuple[object, object, object, str]:
    parsed = V11.parse_body_numeric(sentence, term_start, rules)
    if parsed[3] != "NUM_NONE":
        return parsed
    # Propagate an explicit list-level zero across comma-separated adverse
    # events, e.g. "No ulcer, hematoma, infection ... was observed".
    if re.search(r"\b(?:no|none|neither)\b.{0,500}\b(?:observed|detected|reported|occurred|found|encountered|recorded|noted|suffered\s+from)\b", sentence, re.I):
        return 0, "", "", "NUM_LIST_ZERO"
    if re.search(r"\bthere\s+(?:was|were|are)\s+no\b", sentence, re.I):
        return 0, "", "", "NUM_LIST_ZERO"
    # Some JATS paragraphs use compact colon records without sentence-level
    # occurrence verbs, e.g. "stroke: 0/72".
    local_text = sentence[max(0, term_start - 20): term_start + 120]
    word_before = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+[A-Za-z-]+(?:\s+[A-Za-z-]+){0,3}\s*$", sentence[max(0, term_start - 80):term_start], re.I)
    if word_before:
        return rules["number_words"][word_before.group(1).lower()], "", "", "NUM_WORD_BEFORE_TERM"
    ratio = re.search(r"\b(\d+)\s*(?:/|of|out of)\s*(\d+)\b", local_text, re.I)
    if ratio:
        return int(ratio.group(1)), int(ratio.group(2)), "", "NUM_RATIO_LOCAL"
    return parsed


def body_context_allowed(section: str, sentence: str, rules: dict) -> bool:
    low_section = section.lower()
    if any(item in low_section for item in rules["body_excluded_section_patterns"]):
        return False
    if V11.has_phrase(sentence, rules["non_event_patterns"]):
        return False
    allowed_section = any(item in low_section for item in rules["body_allowed_section_patterns"])
    if not allowed_section:
        return False
    hits = category_hits(sentence, rules)
    if not hits:
        return False
    return V11.has_phrase(sentence, rules["strong_occurrence_patterns"]) or bool(re.search(r"\b\d+\s*(?:/|of|\(|patients?|cases?|subjects?)", sentence, re.I))


def is_repeated_section_row(row: list[str]) -> bool:
    values = [V11.clean(value) for value in row if V11.clean(value)]
    return len(values) >= 2 and len({value.lower() for value in values}) == 1


def has_any(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(pattern.lower() in low for pattern in patterns)


def is_statistical_header(header: str, rules: dict) -> bool:
    clean = V11.clean(header).lower().strip(" |:-")
    if not clean:
        return True
    if re.fullmatch(r"p(?:\s*[- ]?value)?(?:\s*\*+)?", clean, re.I):
        return True
    return has_any(clean, rules.get("table_statistical_column_patterns", []))


def is_group_size_summary_header(header: str) -> bool:
    clean = V11.clean(header)
    return bool(re.fullmatch(r"\d+(?:\s*[∶:]\s*\d+){1,8}", clean))


def parse_group_sizes(value: str) -> list[int]:
    clean = V11.clean(value)
    if not re.fullmatch(r"\d+(?:\s*[∶:]\s*\d+){1,8}", clean):
        return []
    return [int(part) for part in re.split(r"\s*[∶:]\s*", clean)]


def parse_complement_count(value: str, denominator: object) -> tuple[object, object, str] | None:
    if not isinstance(denominator, int):
        return None
    match = re.fullmatch(r"\s*(\d+)\s*[:∶]\s*(\d+)\s*", V11.clean(value))
    if not match:
        return None
    event, complement = int(match.group(1)), int(match.group(2))
    if event + complement != denominator:
        return None
    return event, "", "TABLE_COUNT_COMPLEMENT"


def parse_header_arm_denominator(header: str, rules: dict) -> tuple[str, object, str]:
    arm, denominator, time = V11.parse_header_arm_denominator(header, rules)
    if denominator == "" and re.search(r"\b(?:group|arm)\b", header, re.I):
        bare = re.search(r"\(\s*(\d+)\s*\)\s*$", header)
        if bare:
            denominator = int(bare.group(1))
            arm = V11.clean(re.sub(r"\(\s*\d+\s*\)\s*$", "", header).strip(" |:-"))
    return arm, denominator, time


def make_prediction(*args, **kwargs) -> dict:
    prediction = V11.make_prediction(*args, **kwargs)
    numerator = prediction["Numerator"]
    denominator = prediction["Denominator"]
    percentage = prediction["Reported Percentage"]
    arm = V11.clean(prediction["Arm"])
    if numerator != "" and denominator != "" and arm:
        prediction["Poolability"] = "Directly poolable"
    elif numerator != "" or percentage != "":
        prediction["Poolability"] = "Transformable"
    else:
        prediction["Poolability"] = "Not poolable"
    return prediction


def extract_file(path: Path, rules: dict) -> list[dict]:
    root = ET.parse(path).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    article, input_hash = V11.article_id(path), V11.sha256(path)
    predictions: list[dict] = []
    table_nodes = {node for wrap in root.iter() if V11.local(wrap.tag) == "table-wrap" for node in wrap.iter()}

    for node in root.iter():
        if V11.local(node.tag) != "p" or node in table_nodes:
            continue
        location = V11.section_title(node, parents)
        for sentence in V11.sentences(V11.node_text(node)):
            if not body_context_allowed(location, sentence, rules):
                continue
            for start, category, term, rule_id in category_hits(sentence, rules):
                numerator, denominator, percentage, numeric_rule = parse_body_numeric(sentence, start, rules)
                if numeric_rule == "NUM_NONE":
                    continue
                arm, time = V11.parse_arm(sentence), V11.parse_time(sentence, rules)
                if re.search(r"\b(?:both|either|all)\s+(?:groups?|arms?)\b", sentence, re.I):
                    arm = "Overall"
                predictions.append(make_prediction(article, "Body", location, sentence, category, term, rule_id, rules, input_hash, numerator, denominator, percentage, arm, time, numeric_rule))

    for wrap in (node for node in root.iter() if V11.local(node.tag) == "table-wrap"):
        label = next((V11.node_text(child) for child in wrap if V11.local(child.tag) == "label"), "Table")
        caption = next((V11.node_text(child) for child in wrap if V11.local(child.tag) == "caption"), "")
        grid, header_rows = V11.expanded_table(wrap)
        if not grid:
            continue
        caption_safety = has_any(caption, rules.get("table_safety_context_patterns", []))
        caption_excluded = has_any(caption, rules.get("table_excluded_context_patterns_v1_2", []))
        if caption_excluded and not caption_safety:
            continue
        caption_mixed = caption_safety and caption_excluded
        header_rows = max(1, header_rows)
        headers = [V11.clean(" | ".join(dict.fromkeys(row[col] for row in grid[:header_rows] if row[col]))) for col in range(len(grid[0]))]
        caption_hits = category_hits(caption, rules)
        active_subheading = ""
        table_time = V11.parse_time(caption, rules)

        # Transposed safety tables encode treatment groups in rows and event
        # names in columns. Process them explicitly instead of mistaking the
        # later p-value comparison table for event counts.
        transposed_event_columns = [(col, category_hits(headers[col], rules)) for col in range(1, len(headers)) if category_hits(headers[col], rules)]
        denominator_columns = [col for col, header in enumerate(headers) if re.fullmatch(r"(?:n|number|sample size|patients?)", V11.clean(header), re.I)]
        group_like_rows = sum(bool(re.fullmatch(r"(?:group\s*)?[A-Za-z0-9-]+", V11.clean(row[0]), re.I)) for row in grid[header_rows:] if row)
        if len(transposed_event_columns) >= 2 and denominator_columns and group_like_rows >= 2:
            den_col = denominator_columns[0]
            for row_index, row in enumerate(grid[header_rows:], start=header_rows + 1):
                arm_raw = V11.clean(row[0])
                if not arm_raw or re.fullmatch(r"p(?:\s*[- ]?value)?", arm_raw, re.I):
                    continue
                denominator = int(row[den_col]) if re.fullmatch(r"\d+", V11.clean(row[den_col])) else ""
                arm = f"Group {arm_raw}" if re.fullmatch(r"[A-Za-z0-9]", arm_raw) else arm_raw
                for col, hits in transposed_event_columns:
                    numerator, percentage, numeric_rule = V11.parse_table_value(row[col], rules)
                    if numeric_rule in {"TABLE_MISSING", "TABLE_NONNUMERIC", "TABLE_DESCRIPTIVE_NUMERIC"}:
                        continue
                    event_text = headers[col]
                    quote = f"{event_text} | {arm} | {arm}: {row[col]}"
                    location = V11.clean(f"{label} {caption} row {row_index} column {col + 1}")
                    for _, category, term, rule_id in hits:
                        predictions.append(make_prediction(article, "Table", location, quote, category, term, rule_id, rules, input_hash, numerator, denominator, percentage, arm, table_time, numeric_rule))
            continue

        for row_index, row in enumerate(grid[header_rows:], start=header_rows + 1):
            if is_repeated_section_row(row):
                active_subheading = row[0]
                continue
            event_text = V11.clean(row[0])
            if not event_text:
                continue
            active_safety = has_any(active_subheading, rules.get("table_safety_context_patterns", []))
            row_hits = category_hits(event_text, rules)
            caption_event = caption_hits if caption_hits and V11.parse_time(event_text, rules) else []
            if caption_event:
                row_hits = caption_event
            unknown_allowed = caption_safety and not caption_mixed or active_safety
            if not row_hits and unknown_allowed and not has_any(event_text, rules.get("table_non_event_row_patterns", [])):
                row_hits = [(0, "Unmappable", event_text, "TABLE_SAFETY_ROW")]
            if not row_hits:
                continue

            size_columns = [(col, parse_group_sizes(row[col])) for col in range(1, len(row)) if is_group_size_summary_header(headers[col])]
            group_sizes = next((sizes for _, sizes in size_columns if sizes), [])
            group_cols = [col for col in range(1, len(row)) if not is_group_size_summary_header(headers[col]) and not is_statistical_header(headers[col], rules)]
            for group_position, col in enumerate(group_cols):
                arm, denominator, time = parse_header_arm_denominator(headers[col], rules)
                if denominator == "" and group_position < len(group_sizes):
                    denominator = group_sizes[group_position]
                numerator, percentage, numeric_rule = V11.parse_table_value(row[col], rules)
                if numeric_rule in {"TABLE_MISSING", "TABLE_NONNUMERIC"}:
                    continue
                if numeric_rule == "TABLE_DESCRIPTIVE_NUMERIC":
                    complement = parse_complement_count(row[col], denominator)
                    if complement:
                        numerator, percentage, numeric_rule = complement
                    else:
                        continue
                if caption_event:
                    time = V11.parse_time(event_text, rules) or time
                time = time or V11.parse_time(event_text, rules) or table_time
                quote = f"{caption if caption_event else event_text} | {event_text if caption_event else headers[col]} | {headers[col]}: {row[col]}"
                location = V11.clean(f"{label} {caption} row {row_index} column {col + 1}")
                for _, category, term, rule_id in row_hits:
                    predictions.append(make_prediction(article, "Table", location, quote, category, term, rule_id, rules, input_hash, numerator, denominator, percentage, arm, time, numeric_rule))

    output: list[dict] = []
    seen: set[tuple] = set()
    for item in predictions:
        key = (item["Article ID"], item["Source Type"], item["Evidence Location"], V11.clean(item["Raw Event Term"]).lower(), V11.clean(item["Arm"]).lower(), item["Numerator"], item["Denominator"], V11.clean(item["Evidence Quote"]).lower())
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-mode", required=True, choices=("development", "external-blind"))
    args = parser.parse_args()
    joined = " ".join(str(value).lower() for value in (args.input_dir, args.rules, args.output_dir))
    if any(token in joined for token in ("final_reference", "reference_standard", "裁决", "医生a", "医生b")):
        raise SystemExit("Extraction guard: a path resembles reference/adjudication material.")
    rules = load_rules(args.rules)
    files = sorted(args.input_dir.glob("*.nxml"))
    if not files:
        raise SystemExit("No .nxml inputs found")
    predictions: list[dict] = []
    for path in files:
        predictions.extend(extract_file(path, rules))
    for index, item in enumerate(predictions, start=1):
        item["Prediction ID"] = f"AH12-{index:04d}"
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "ArthroHarm_Development_Predictions_v1.2" if args.run_mode == "development" else "ArthroHarm_External_Blind_Predictions_v1.2"
    json_path = args.output_dir / f"{prefix}.json"
    csv_path = args.output_dir / f"{prefix}.csv"
    json_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(predictions[0]) if predictions else [])
        writer.writeheader()
        writer.writerows(predictions)
    summary = {
        "rules_version": rules["rules_version"], "run_mode": args.run_mode,
        "development_only": args.run_mode == "development", "xml_files": len(files),
        "predictions": len(predictions), "json_sha256": V11.sha256(json_path),
        "csv_sha256": V11.sha256(csv_path),
    }
    (args.output_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
