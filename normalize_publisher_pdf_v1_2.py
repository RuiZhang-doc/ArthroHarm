#!/usr/bin/env python3
"""Label-free publisher PDF to minimal JATS-like XML adapter.

The adapter preserves document text and table layout using Poppler's fixed-width
layout output. It does not inspect harms labels, predictions, or reference data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pdfplumber
from lxml import etree


ADAPTER_VERSION = "ArthroHarm-publisher-pdf-adapter-v1.2-RC2-coordinate-candidate"
TABLE_START = re.compile(r"^\s*(?:table|tab\.)\s+([0-9]+|[ivxlcdm]+)\b", re.I)
SECTION_HEADINGS = {
    "introduction": "Introduction",
    "background": "Introduction",
    "materials and methods": "Methods",
    "patients and methods": "Methods",
    "methods": "Methods",
    "methodology": "Methods",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusions",
    "conclusions": "Conclusions",
    "references": "References",
}


def clean(value: str) -> str:
    value = (value or "").replace("\u00ad", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    value = "".join(
        character
        for character in value
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )
    return " ".join(value.split())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pdftotext(binary: Path, pdf: Path, layout: bool) -> str:
    command = [str(binary)]
    if layout:
        command.append("-layout")
    command.extend([str(pdf), "-"])
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8", errors="replace")


def split_columns(line: str) -> list[str]:
    return [clean(part) for part in re.split(r"\s{2,}", line.strip()) if clean(part)]


def display_compact(value: str) -> str:
    """Collapse spaced display capitals such as 'M AT E R I A L S' and 'TA B L E'."""
    value = clean(value)
    letters = [character for character in value if character.isalpha()]
    if letters and sum(character.isupper() for character in letters) / len(letters) >= 0.80:
        return re.sub(r"\s+", "", value)
    return value


def table_label(line: str) -> str | None:
    direct = TABLE_START.match(line)
    if direct:
        return clean(line)
    collapsed = display_compact(line)
    match = re.match(r"^(?:table|tab\.)([0-9]+|[ivxlcdm]+)\b", collapsed, re.I)
    if match:
        return clean(line)
    return None


def looks_like_header(cells: list[str]) -> bool:
    low = " ".join(cells).lower()
    signals = ("variable", "group", "treatment", "control", "p value", "p-value", "outcome", "characteristic")
    return any(signal in low for signal in signals)


def looks_like_parallel_prose(cells: list[str]) -> bool:
    if len(cells) != 2:
        return False
    word_counts = [len(cell.split()) for cell in cells]
    numeric = sum(bool(re.search(r"\d", cell)) for cell in cells)
    return min(word_counts) >= 8 and numeric == 0


def clean_caption(caption: str) -> str:
    caption = clean(caption)
    # In two-column PDFs, the left-column sentence can precede a right-column
    # table caption on the same extracted line. Prefer the last title-like cue.
    cues = re.compile(
        r"\b(Baseline|Demographic|Operative|Postoperative|Preoperative|Comparison|"
        r"Clinical|Radiographic|Functional|Adverse|Complications?|Outcomes?|Characteristics?)\b",
        re.I,
    )
    matches = list(cues.finditer(caption))
    if matches and matches[-1].start() > 20:
        caption = caption[matches[-1].start():]
    return caption


def parse_layout_tables(layout_text: str) -> list[dict]:
    tables: list[dict] = []
    for page_number, page in enumerate(layout_text.split("\f"), 1):
        lines = page.splitlines()
        index = 0
        while index < len(lines):
            detected_label = table_label(lines[index])
            if not detected_label:
                index += 1
                continue
            label = detected_label
            index += 1
            block: list[str] = []
            blanks_after_data = 0
            data_seen = False
            while index < len(lines):
                line = lines[index]
                stripped = clean(line)
                if table_label(line) and data_seen:
                    break
                if stripped.lower() in SECTION_HEADINGS and data_seen:
                    break
                cells = split_columns(line)
                if len(cells) >= 2:
                    if data_seen and looks_like_parallel_prose(cells):
                        break
                    data_seen = True
                    blanks_after_data = 0
                elif not stripped and data_seen:
                    blanks_after_data += 1
                    if blanks_after_data >= 2:
                        break
                elif stripped:
                    blanks_after_data = 0
                block.append(line)
                index += 1

            caption_parts: list[str] = []
            rows: list[list[str]] = []
            started_rows = False
            for line in block:
                cells = split_columns(line)
                if len(cells) >= 2:
                    started_rows = True
                    rows.append(cells)
                elif not started_rows and clean(line):
                    caption_parts.append(clean(line))
                elif started_rows and clean(line):
                    # Preserve wrapped first-column content without inventing a new column.
                    if rows:
                        rows[-1][0] = clean(rows[-1][0] + " " + clean(line))
            if len(rows) >= 2:
                # If another page column was interleaved before the actual
                # table, align rows at the first explicit variable/outcome cue.
                start_column = 0
                for candidate_row in rows[:4]:
                    for cell_index, cell in enumerate(candidate_row):
                        if re.search(r"\b(variable|outcome|characteristic)\b", cell, re.I):
                            start_column = cell_index
                            break
                    if start_column:
                        break
                if start_column:
                    rows = [row[start_column:] for row in rows if len(row) > start_column]
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                header_signal = any(looks_like_header(row) for row in rows[:4])
                numeric_rows = sum(
                    sum(bool(re.search(r"\d", cell)) for cell in row) >= 2
                    for row in rows
                )
                if not header_signal and numeric_rows < 2:
                    continue
                header_rows = 1
                if len(rows) > 1 and (looks_like_header(rows[0]) or "n =" in " ".join(rows[1]).lower()):
                    header_rows = 2 if "n =" in " ".join(rows[1]).lower() else 1
                tables.append({
                    "label": label,
                    "caption": clean_caption(" ".join(caption_parts)),
                    "rows": rows,
                    "header_rows": min(header_rows, len(rows)),
                    "page": page_number,
                })
    return tables


def group_words_into_lines(words: list[dict], tolerance: float = 2.2) -> list[list[dict]]:
    """Group PDF words by baseline without using any clinical vocabulary."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > tolerance:
            lines.append([word])
        else:
            lines[-1].append(word)
    return [sorted(line, key=lambda item: item["x0"]) for line in lines]


def horizontal_edge_groups(page, tolerance: float = 1.5) -> list[dict]:
    """Collapse fragmented horizontal PDF rules into page-coordinate spans."""
    grouped: list[list[dict]] = []
    horizontal = [edge for edge in page.edges if edge.get("orientation") == "h"]
    for edge in sorted(horizontal, key=lambda item: (item["top"], item["x0"])):
        if not grouped or abs(grouped[-1][0]["top"] - edge["top"]) > tolerance:
            grouped.append([edge])
        else:
            grouped[-1].append(edge)
    output = []
    for edges in grouped:
        x0 = min(edge["x0"] for edge in edges)
        x1 = max(edge["x1"] for edge in edges)
        output.append({
            "top": sum(edge["top"] for edge in edges) / len(edges),
            "x0": x0,
            "x1": x1,
            "span": x1 - x0,
            "segments": len(edges),
        })
    return output


def cells_from_coordinate_line(words: list[dict], gap_points: float = 12.0) -> list[str]:
    """Reconstruct neutral cells from x gaps inside an already isolated table box."""
    if not words:
        return []
    cells: list[list[str]] = [[clean(words[0]["text"])]]
    previous_x1 = words[0]["x1"]
    for word in words[1:]:
        text = clean(word["text"])
        if word["x0"] - previous_x1 >= gap_points:
            cells.append([text])
        else:
            cells[-1].append(text)
        previous_x1 = max(previous_x1, word["x1"])
    return [clean(" ".join(cell)) for cell in cells if clean(" ".join(cell))]


def _table_mentions(line: list[dict]) -> list[dict]:
    mentions = []
    for index, word in enumerate(line):
        normalized = re.sub(r"[^A-Za-z.]", "", word["text"]).lower()
        if normalized not in {"table", "tab."}:
            continue
        following = line[index + 1]["text"] if index + 1 < len(line) else ""
        number = re.sub(r"[^0-9A-Za-z]", "", following)
        if re.fullmatch(r"[0-9]+|[ivxlcdm]+", number, re.I):
            mentions.append({
                "x0": word["x0"], "top": min(item["top"] for item in line),
                "bottom": max(item["bottom"] for item in line),
                "number": number, "line": line,
            })
    return mentions


def _matching_table_box(page, mention: dict, edges: list[dict]) -> tuple[float, float, float, float] | None:
    """Find a ruled table immediately following a Table label/reference."""
    minimum_span = min(150.0, page.width * 0.25)
    candidates = [
        edge for edge in edges
        if edge["span"] >= minimum_span
        and mention["top"] - 8 <= edge["top"] <= mention["bottom"] + 125
        and edge["x0"] - 20 <= mention["x0"] <= edge["x1"] + 20
    ]
    if not candidates:
        return None
    following_edges = [edge for edge in candidates if edge["top"] >= mention["bottom"] - 1]
    top_edge = min(
        following_edges or candidates,
        key=lambda edge: (abs(edge["top"] - mention["bottom"]), edge["top"]),
    )
    aligned = [
        edge for edge in edges
        if edge["top"] >= top_edge["top"] - 2
        and edge["top"] <= min(page.height - 5, top_edge["top"] + 620)
        and abs(edge["x0"] - top_edge["x0"]) <= 18
        and abs(edge["x1"] - top_edge["x1"]) <= 18
        and edge["span"] >= top_edge["span"] * 0.80
    ]
    if len(aligned) < 2:
        return None
    bottom_edge = max(aligned, key=lambda edge: edge["top"])
    if bottom_edge["top"] - top_edge["top"] < 18:
        return None
    return (top_edge["x0"], top_edge["top"], top_edge["x1"], bottom_edge["top"])


def parse_coordinate_tables(pdf_path: Path) -> tuple[list[dict], dict]:
    """Extract ruled tables from coordinate crops, excluding adjacent page columns."""
    tables: list[dict] = []
    rejected_mentions = 0
    seen_boxes: set[tuple] = set()
    with pdfplumber.open(pdf_path) as document:
        for page_number, page in enumerate(document.pages, 1):
            page_words = page.extract_words(x_tolerance=1.5, y_tolerance=2.0, keep_blank_chars=False)
            lines = group_words_into_lines(page_words)
            edges = horizontal_edge_groups(page)
            mentions = [mention for line in lines for mention in _table_mentions(line)]
            for mention in mentions:
                box = _matching_table_box(page, mention, edges)
                if box is None:
                    rejected_mentions += 1
                    continue
                box_key = (page_number, *(round(value, 1) for value in box))
                if box_key in seen_boxes:
                    continue
                seen_boxes.add(box_key)
                crop = page.within_bbox((box[0], box[1] + 0.5, box[2], box[3] - 0.5))
                row_words = crop.extract_words(x_tolerance=1.5, y_tolerance=2.0, keep_blank_chars=False)
                rows = [cells_from_coordinate_line(line) for line in group_words_into_lines(row_words)]
                rows = [row for row in rows if row]
                multi_cell_rows = sum(len(row) >= 2 for row in rows)
                numeric_rows = sum(
                    sum(bool(re.search(r"\d", cell)) for cell in row) >= 1
                    for row in rows
                )
                if len(rows) < 2 or multi_cell_rows < 2 or numeric_rows < 1:
                    rejected_mentions += 1
                    continue
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                label = f"Table {mention['number']}"
                label_line = " ".join(item["text"] for item in mention["line"])
                label_position = re.search(
                    rf"\btable\s+{re.escape(mention['number'])}\b", label_line, re.I
                )
                caption = label_line[label_position.end():] if label_position else ""
                tables.append({
                    "label": label, "caption": clean_caption(caption), "rows": rows,
                    "header_rows": 1, "page": page_number,
                    "bbox": [round(value, 2) for value in box],
                    "extraction_method": "COORDINATE_RULED_TABLE",
                })
    return tables, {
        "coordinate_table_count": len(tables),
        "rejected_table_mentions": rejected_mentions,
        "coordinate_boxes_unique": len(seen_boxes),
    }


def heading_name(line: str) -> str | None:
    normalized = clean(line)
    normalized = re.sub(r"^\s*[|]\s*", "", normalized)
    normalized = re.sub(r"^\s*(?:\d+|[ivxlcdm]+)\s*[|.):-]\s*", "", normalized, flags=re.I)
    normalized = display_compact(normalized).lower().rstrip(":.")
    compacted = re.sub(r"[^a-z]", "", normalized)
    if compacted in {"materialsandmethods", "patientsandmethods", "methods", "methodology"}:
        return "Methods"
    if normalized.startswith("results") and len(normalized) <= 40:
        return "Results"
    if normalized.startswith("discussion") and len(normalized) <= 40:
        return "Discussion"
    if normalized.startswith("conclusion") and len(normalized) <= 40:
        return "Conclusions"
    return SECTION_HEADINGS.get(normalized)


def plain_sections(plain_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"Article body": []}
    current = "Article body"
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        value = clean(" ".join(paragraph))
        if value:
            sections.setdefault(current, []).append(value)
        paragraph = []

    for raw in plain_text.splitlines():
        line = clean(raw)
        heading = heading_name(line)
        if heading:
            flush()
            current = heading
            sections.setdefault(current, [])
            if heading == "References":
                break
            continue
        if not line:
            flush()
            continue
        # Remove common isolated page numbers and running headers.
        if re.fullmatch(r"\d{1,4}", line):
            continue
        paragraph.append(line)
    flush()
    return {key: value for key, value in sections.items() if value and key != "References"}


def build_article(
    title: str, doi: str, plain_text: str, layout_text: str,
    supplied_tables: list[dict] | None = None, table_diagnostics: dict | None = None,
) -> tuple[etree._Element, dict]:
    article = etree.Element("article")
    front = etree.SubElement(article, "front")
    meta = etree.SubElement(front, "article-meta")
    title_group = etree.SubElement(meta, "title-group")
    etree.SubElement(title_group, "article-title").text = clean(title)
    etree.SubElement(meta, "article-id", {"pub-id-type": "doi"}).text = doi
    body = etree.SubElement(article, "body")

    sections = plain_sections(plain_text)
    section_nodes: dict[str, etree._Element] = {}
    for name, paragraphs in sections.items():
        sec = etree.SubElement(body, "sec")
        etree.SubElement(sec, "title").text = name
        section_nodes[name] = sec
        for paragraph in paragraphs:
            etree.SubElement(sec, "p").text = paragraph

    tables = supplied_tables if supplied_tables is not None else parse_layout_tables(layout_text)
    table_section = section_nodes.get("Results")
    if table_section is None:
        table_section = etree.SubElement(body, "sec")
        etree.SubElement(table_section, "title").text = "Tables"
    for table_info in tables:
        wrap = etree.SubElement(table_section, "table-wrap", {"source-page": str(table_info["page"])})
        etree.SubElement(wrap, "label").text = table_info["label"]
        caption = etree.SubElement(wrap, "caption")
        etree.SubElement(caption, "p").text = table_info["caption"]
        table = etree.SubElement(wrap, "table")
        for row_index, row in enumerate(table_info["rows"]):
            tr = etree.SubElement(table, "tr")
            tag = "th" if row_index < table_info["header_rows"] else "td"
            for value in row:
                etree.SubElement(tr, tag).text = value

    diagnostics = {
        "section_count": len(sections),
        "paragraph_count": sum(len(value) for value in sections.values()),
        "table_count": len(tables),
        "table_row_count": sum(len(item["rows"]) for item in tables),
        "has_methods": "Methods" in sections,
        "has_results": "Results" in sections,
        "has_discussion": "Discussion" in sections,
        **(table_diagnostics or {}),
    }
    return article, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool-csv", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--pdftotext", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {args.output_dir}")
    xml_dir = args.output_dir / "normalized_nxml"
    xml_dir.mkdir(parents=True)

    with args.pool_csv.open(encoding="utf-8-sig", newline="") as stream:
        pool = [row for row in csv.DictReader(stream) if row["source_route"] == "MANUAL_PUBLISHER_PDF"]
    outputs = []
    for row in pool:
        source = args.pdf_dir / row["source_main_file"]
        plain = run_pdftotext(args.pdftotext, source, layout=False)
        layout = run_pdftotext(args.pdftotext, source, layout=True)
        coordinate_tables, table_diagnostics = parse_coordinate_tables(source)
        root, diagnostics = build_article(
            row["title"], row["doi"], plain, layout,
            supplied_tables=coordinate_tables, table_diagnostics=table_diagnostics,
        )
        output = xml_dir / f"{row['source_candidate_id']}_{re.sub(r'[^A-Za-z0-9]+', '_', row['doi'])}.nxml"
        output.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))
        structural_status = "PASS" if diagnostics["paragraph_count"] >= 5 and diagnostics["has_results"] else "REVIEW"
        table_format_status = "PASS_COORDINATE_TABLES" if diagnostics["table_count"] >= 1 else "REVIEW_NO_RELIABLE_TABLE"
        outputs.append({
            "source_candidate_id": row["source_candidate_id"], "doi": row["doi"],
            "source_pdf": row["source_main_file"], "source_sha256": row["source_sha256"],
            "normalized_file": str(output.relative_to(args.output_dir)), "normalized_sha256": sha256(output),
            **diagnostics, "structural_status": structural_status,
            "table_format_status": table_format_status,
        })

    audit = args.output_dir / "PDF_NORMALIZATION_AUDIT.csv"
    with audit.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(outputs[0])); writer.writeheader(); writer.writerows(outputs)
    summary = {
        "adapter_version": ADAPTER_VERSION,
        "normalized_articles": len(outputs),
        "structural_status": dict(Counter(row["structural_status"] for row in outputs)),
        "total_tables": sum(row["table_count"] for row in outputs),
        "total_table_rows": sum(row["table_row_count"] for row in outputs),
        "table_format_status": dict(Counter(row["table_format_status"] for row in outputs)),
        "label_free": True,
        "predictions_or_reference_labels_read": False,
        "audit_sha256": sha256(audit),
    }
    (args.output_dir / "PDF_NORMALIZATION_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
