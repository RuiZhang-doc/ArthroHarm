#!/usr/bin/env python3
"""ArthroHarm v1.1 RC1 extractor.

This candidate was developed with the old 20-article pilot but never reads
annotations or a reference standard. v1.0 files remain untouched.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_text(node: ET.Element) -> str:
    return clean(" ".join(node.itertext()))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def article_id(path: Path) -> str:
    match = re.search(r"(AH-(?:P\d{2}|V\d{3}))", path.name)
    return match.group(1) if match else path.stem


def load_rules(path: Path) -> dict:
    overlay = json.loads(path.read_text(encoding="utf-8"))
    base = json.loads((path.parent / overlay["base_rules_file"]).read_text(encoding="utf-8"))
    base.update(overlay)
    by_category = {item["category"]: item for item in base["categories"]}
    for category, patterns in overlay.get("additional_category_patterns", {}).items():
        by_category[category]["patterns"] = list(dict.fromkeys(by_category[category]["patterns"] + patterns))
    return base


def phrase_regex(phrase: str) -> re.Pattern[str]:
    # Lexical phrases are literal and bounded on both sides.  This prevents
    # died/studied and PE/name fragments while preserving punctuation variants.
    escaped = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.I)


def has_phrase(text: str, phrases: list[str]) -> bool:
    return any(phrase_regex(item).search(text) for item in phrases)


def category_hits(text: str, rules: dict) -> list[tuple[int, str, str, str]]:
    hits: list[tuple[int, str, str, str]] = []
    for item in rules["categories"]:
        for phrase in item["patterns"]:
            match = phrase_regex(phrase).search(text)
            if match:
                hits.append((match.start(), item["category"], match.group(0), item["rule_id"]))
    if not hits:
        for cue in rules["generic_reporting_cues"]:
            match = phrase_regex(cue).search(text)
            if match:
                hits.append((match.start(), "Unmappable", match.group(0), "GENERIC"))
                break
    hits.sort(key=lambda row: (row[0], -len(row[2])))
    kept: list[tuple[int, str, str, str]] = []
    spans: list[tuple[int, int]] = []
    for hit in hits:
        span = (hit[0], hit[0] + len(hit[2]))
        if any(max(span[0], old[0]) < min(span[1], old[1]) for old in spans):
            continue
        spans.append(span)
        kept.append(hit)
    return kept


def section_title(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    titles: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if local(current.tag) == "sec":
            title = next((node_text(child) for child in current if local(child.tag) == "title"), "")
            if title:
                titles.append(title)
    return " > ".join(reversed(titles)) or "Body"


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", clean(text)) if len(part.strip()) >= 8]


def body_context_allowed(section: str, sentence: str, rules: dict) -> bool:
    low_section = section.lower()
    if any(item in low_section for item in rules["body_excluded_section_patterns"]):
        return False
    if has_phrase(sentence, rules["non_event_patterns"]):
        return False
    allowed_section = any(item in low_section for item in rules["body_allowed_section_patterns"])
    return allowed_section and has_phrase(sentence, rules["strong_occurrence_patterns"])


def parse_time(text: str, rules: dict) -> str:
    matches: list[tuple[int, str]] = []
    for pattern in rules["time_patterns_v1_1"]:
        for match in re.finditer(pattern, text, re.I):
            matches.append((match.start(), clean(match.group(0))))
    return min(matches, default=(0, ""), key=lambda row: row[0])[1]


def parse_arm(text: str) -> str:
    patterns = [
        r"\b(?:the\s+)?([A-Za-z][A-Za-z0-9+/-]{0,20}(?:\s+[A-Za-z][A-Za-z0-9+/-]{0,20}){0,3}\s+group)\b",
        r"\b(group\s+[A-Za-z0-9]+)\b",
        r"\b(intervention|control|placebo)\s+(?:arm|group)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return clean(match.group(1))
    return ""


def parse_body_numeric(sentence: str, term_start: int, rules: dict) -> tuple[object, object, object, str]:
    # Prefer the clause that contains the event term; do not bind an arbitrary
    # number from another clause/sentence.
    boundaries = [m.start() for m in re.finditer(r"[;,]", sentence)]
    left = max([x + 1 for x in boundaries if x < term_start], default=0)
    right = min([x for x in boundaries if x > term_start], default=len(sentence))
    clause = sentence[left:right]
    ratio = re.search(r"\b(\d+)\s*(?:/|of|out of)\s*(\d+)\b", clause, re.I)
    if ratio:
        return int(ratio.group(1)), int(ratio.group(2)), "", "NUM_RATIO_CLAUSE"
    count_pct = re.search(r"\b(\d+)\s*(?:patients?|participants?|subjects?|cases?|knees?)?\s*\((\d+(?:\.\d+)?)\s*%\)", clause, re.I)
    if count_pct:
        return int(count_pct.group(1)), "", float(count_pct.group(2)) / 100, "NUM_COUNT_PCT_CLAUSE"
    count = re.search(r"\b(\d+)\s+(?:patients?|participants?|subjects?|cases?|knees?)\b", clause, re.I)
    if count:
        return int(count.group(1)), "", "", "NUM_COUNT_CLAUSE"
    word_count = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:patients?|participants?|subjects?|cases?|knees?)\b", clause, re.I)
    if word_count:
        return rules["number_words"][word_count.group(1).lower()], "", "", "NUM_WORD_CLAUSE"
    if re.search(r"\b(?:no|none|zero)\b", clause, re.I):
        return 0, "", "", "NUM_ZERO_CLAUSE"
    pct = re.search(r"\b(\d+(?:\.\d+)?)\s*%", clause)
    return "", "", float(pct.group(1)) / 100 if pct else "", "NUM_PCT_ONLY" if pct else "NUM_NONE"


def parse_header_arm_denominator(header: str, rules: dict) -> tuple[str, object, str]:
    denominator: object = ""
    match = re.search(r"\b[nN]\s*=\s*(\d+)\b", header)
    if match:
        denominator = int(match.group(1))
    arm = re.sub(r"\(?\s*[nN]\s*=\s*\d+\s*\)?", "", header)
    arm = clean(re.sub(r"\b(?:p\s*[- ]?value|no\.?|number|%)\b", "", arm, flags=re.I).strip(" |:-"))
    time = parse_time(header, rules)
    if time and arm.lower() == time.lower():
        arm = ""
    if any(noise == arm.lower() for noise in rules["arm_noise_patterns"]):
        arm = ""
    return arm, denominator, time


def parse_table_value(value: str, rules: dict) -> tuple[object, object, str]:
    value = clean(value)
    if value.lower() in rules["missing_cell_tokens"]:
        return "", "", "TABLE_MISSING"
    if value.lower() in rules["zero_cell_tokens"]:
        return 0, "", "TABLE_ZERO"
    ratio = re.search(r"\b(\d+)\s*/\s*(\d+)\b", value)
    if ratio:
        return int(ratio.group(1)), int(ratio.group(2)), "TABLE_RATIO"
    count_pct = re.search(r"\b(\d+)\s*\((\d+(?:\.\d+)?)\s*%?\)", value)
    if count_pct:
        return int(count_pct.group(1)), float(count_pct.group(2)) / 100, "TABLE_COUNT_PCT"
    number = re.fullmatch(r"\s*(\d+)\s*", value)
    if number:
        return int(number.group(1)), "", "TABLE_COUNT"
    pct = re.search(r"\b(\d+(?:\.\d+)?)\s*%", value)
    if pct:
        return "", float(pct.group(1)) / 100, "TABLE_PCT"
    if re.search(r"\d", value):
        return "", "", "TABLE_DESCRIPTIVE_NUMERIC"
    return "", "", "TABLE_NONNUMERIC"


def expanded_table(wrap: ET.Element) -> tuple[list[list[str]], int]:
    rows = [row for row in wrap.iter() if local(row.tag) == "tr"]
    grid: list[list[str]] = []
    spans: dict[int, tuple[int, str]] = {}
    header_rows = 0
    for row in rows:
        out: list[str] = []
        col = 0
        cells = [cell for cell in row if local(cell.tag) in {"td", "th"}]
        if cells and all(local(cell.tag) == "th" for cell in cells):
            header_rows += 1
        for cell in cells:
            while col in spans:
                remaining, value = spans[col]
                out.append(value)
                if remaining <= 1:
                    del spans[col]
                else:
                    spans[col] = (remaining - 1, value)
                col += 1
            value = node_text(cell)
            colspan = int(cell.attrib.get("colspan", "1"))
            rowspan = int(cell.attrib.get("rowspan", "1"))
            for _ in range(colspan):
                out.append(value)
                if rowspan > 1:
                    spans[col] = (rowspan - 1, value)
                col += 1
        while col in spans:
            remaining, value = spans[col]
            out.append(value)
            if remaining <= 1:
                del spans[col]
            else:
                spans[col] = (remaining - 1, value)
            col += 1
        grid.append(out)
    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid], header_rows


def make_prediction(article: str, source: str, location: str, quote: str, category: str, term: str,
                    rule_id: str, rules: dict, input_hash: str, numerator: object = "",
                    denominator: object = "", percentage: object = "", arm: str = "",
                    time: str = "", numeric_rule: str = "NUM_NONE") -> dict:
    return {
        "Prediction ID": "", "Article ID": article, "Source Type": source,
        "Evidence Location": location, "Evidence Quote": quote[: rules["quote_character_limit"]],
        "Raw Event Term": term, "Predicted Category": category, "Arm": arm,
        "Numerator": numerator, "Denominator": denominator, "Reported Percentage": percentage,
        "Count Unit": "patients" if numerator != "" or denominator != "" or percentage != "" else "Unclear",
        "Time Window": time, "Serious": "Yes" if re.search(r"\b(?:serious adverse events?|SAEs?)\b", quote, re.I) else "Unclear",
        "Attribution": "Yes" if re.search(r"\b(?:related to|attributable to|caused by|due to)\b", quote, re.I) else "Unclear",
        "Poolability": "Directly poolable" if numerator != "" and denominator != "" and arm and time else "Transformable" if percentage != "" and denominator != "" else "Not poolable",
        "Confidence": "High" if category != "Unmappable" and numeric_rule != "NUM_NONE" else "Moderate",
        "Rule ID": f"{rule_id}+{numeric_rule}", "Input SHA256": input_hash,
    }


def extract_file(path: Path, rules: dict) -> list[dict]:
    root = ET.parse(path).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    article, input_hash = article_id(path), sha256(path)
    predictions: list[dict] = []
    table_nodes = {node for wrap in root.iter() if local(wrap.tag) == "table-wrap" for node in wrap.iter()}
    for node in root.iter():
        if local(node.tag) != "p" or node in table_nodes:
            continue
        location = section_title(node, parents)
        for sentence in sentences(node_text(node)):
            if not body_context_allowed(location, sentence, rules):
                continue
            for start, category, term, rule_id in category_hits(sentence, rules):
                numerator, denominator, percentage, numeric_rule = parse_body_numeric(sentence, start, rules)
                arm, time = parse_arm(sentence), parse_time(sentence, rules)
                predictions.append(make_prediction(article, "Body", location, sentence, category, term, rule_id, rules, input_hash, numerator, denominator, percentage, arm, time, numeric_rule))

    for wrap in (node for node in root.iter() if local(node.tag) == "table-wrap"):
        label = next((node_text(child) for child in wrap if local(child.tag) == "label"), "Table")
        caption = next((node_text(child) for child in wrap if local(child.tag) == "caption"), "")
        grid, header_rows = expanded_table(wrap)
        if not grid:
            continue
        header_rows = max(1, header_rows)
        headers = [clean(" | ".join(dict.fromkeys(row[col] for row in grid[:header_rows] if row[col]))) for col in range(len(grid[0]))]
        caption_hits = category_hits(caption, rules)
        safety_table = has_phrase(caption, rules["generic_reporting_cues"]) or bool(caption_hits)
        for row_index, row in enumerate(grid[header_rows:], start=header_rows + 1):
            event_text = row[0]
            hits = category_hits(event_text, rules)
            if not hits and safety_table and event_text:
                hits = [(0, "Unmappable", event_text, "TABLE_SAFETY_ROW")]
            # Some tables put the adverse outcome in the caption and time in
            # the first column (for example, repeated area-of-numbness rows).
            caption_event = caption_hits if caption_hits and parse_time(event_text, rules) else []
            if caption_event:
                hits = caption_event
            if not hits:
                continue
            for col in range(1, len(row)):
                numerator, percentage, numeric_rule = parse_table_value(row[col], rules)
                if numeric_rule in {"TABLE_MISSING", "TABLE_NONNUMERIC"}:
                    continue
                arm, denominator, time = parse_header_arm_denominator(headers[col], rules)
                if caption_event:
                    time = parse_time(event_text, rules) or time
                quote = f"{caption if caption_event else event_text} | {event_text if caption_event else headers[col]} | {headers[col]}: {row[col]}"
                location = clean(f"{label} {caption} row {row_index} column {col + 1}")
                for _, category, term, rule_id in hits:
                    predictions.append(make_prediction(article, "Table", location, quote, category, term, rule_id, rules, input_hash, numerator, denominator, percentage, arm, time, numeric_rule))
    output: list[dict] = []
    seen: set[tuple] = set()
    for item in predictions:
        key = (item["Article ID"], item["Source Type"], item["Evidence Location"], clean(item["Raw Event Term"]).lower(), clean(item["Arm"]).lower(), item["Numerator"], item["Denominator"], clean(item["Evidence Quote"]).lower())
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-mode", required=True, choices=("development", "blind"))
    args = parser.parse_args()
    joined = " ".join(str(value).lower() for value in (args.input_dir, args.rules, args.output_dir))
    if any(token in joined for token in ("final_reference", "reference_standard", "裁决", "医生a", "医生b")):
        raise SystemExit("Extraction guard: a path resembles reference/adjudication material.")
    rules = load_rules(args.rules)
    predictions: list[dict] = []
    files = sorted(args.input_dir.glob("*.nxml"))
    if not files:
        raise SystemExit("No .nxml inputs found")
    for path in files:
        predictions.extend(extract_file(path, rules))
    for index, item in enumerate(predictions, start=1):
        item["Prediction ID"] = f"AH11-{index:04d}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "ArthroHarm_Development_Predictions_v1.1" if args.run_mode == "development" else "ArthroHarm_Blind_Predictions_v1.1"
    json_path = args.output_dir / f"{prefix}.json"
    csv_path = args.output_dir / f"{prefix}.csv"
    json_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(predictions[0]) if predictions else [])
        writer.writeheader(); writer.writerows(predictions)
    summary = {"rules_version": rules["rules_version"], "run_mode": args.run_mode, "development_only": args.run_mode == "development", "xml_files": len(files), "predictions": len(predictions), "json_sha256": sha256(json_path), "csv_sha256": sha256(csv_path)}
    (args.output_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
