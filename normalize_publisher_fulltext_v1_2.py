#!/usr/bin/env python3
"""Normalize publisher VOR HTML plus linked table pages to minimal JATS-like XML.

The adapter is label-free: it preserves article text and table structure but
does not identify adverse events or use reference-standard information.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from lxml import etree, html


ADAPTER_VERSION = "ArthroHarm-publisher-adapter-v1.2-RC1"
EXCLUDED_SECTION_TITLES = {
    "inline recommendations", "references", "author information", "additional information",
    "rights and permissions", "about this article", "related content", "acknowledgements",
    "funding", "ethics declarations",
}


def clean(value) -> str:
    return " ".join(str(value or "").split())


def local_name(node) -> str:
    return etree.QName(node).localname.lower()


def nearest_section_title(node) -> str:
    current = node
    while current is not None:
        if local_name(current) == "section" and current.get("data-title"):
            return clean(current.get("data-title"))
        current = current.getparent()
    return "Results"


def clone_table(source, caption: str = ""):
    wrap = etree.Element("table-wrap")
    if caption:
        caption_node = etree.SubElement(wrap, "caption"); etree.SubElement(caption_node, "p").text = caption
    table = etree.SubElement(wrap, "table")
    for row in source.xpath(".//*[local-name()='tr']"):
        tr = etree.SubElement(table, "tr")
        for cell in row.xpath("./*[local-name()='th' or local-name()='td']"):
            tag = "th" if local_name(cell) == "th" else "td"
            out = etree.SubElement(tr, tag)
            for attribute in ("rowspan", "colspan"):
                if cell.get(attribute): out.set(attribute, cell.get(attribute))
            out.text = clean(" ".join(cell.itertext()))
    return wrap


def normalized_article(main_path: Path, table_paths: list[Path]):
    source = html.parse(str(main_path)).getroot()
    article = etree.Element("article")
    front = etree.SubElement(article, "front"); meta = etree.SubElement(front, "article-meta")
    title = clean(source.xpath("string((//h1[@data-test='article-title'] | //h1[contains(@class,'article-title')])[1])"))
    group = etree.SubElement(meta, "title-group"); etree.SubElement(group, "article-title").text = title
    body = etree.SubElement(article, "body")
    section_nodes = source.xpath("//section[@data-title]")
    sections = {}
    for source_section in section_nodes:
        section_title = clean(source_section.get("data-title"))
        if not section_title or section_title.lower() in EXCLUDED_SECTION_TITLES:
            continue
        sec = etree.SubElement(body, "sec"); etree.SubElement(sec, "title").text = section_title
        sections.setdefault(section_title, sec)
        for paragraph in source_section.xpath(".//p"):
            if nearest_section_title(paragraph) != section_title:
                continue
            value = clean(" ".join(paragraph.itertext()))
            if value: etree.SubElement(sec, "p").text = value
    if not sections:
        sec = etree.SubElement(body, "sec"); etree.SubElement(sec, "title").text = "Article body"
        for paragraph in source.xpath("//main//p | //article//p"):
            value = clean(" ".join(paragraph.itertext()))
            if value: etree.SubElement(sec, "p").text = value
        sections["Article body"] = sec

    # Record the section in which each full-size-table link appeared.
    link_sections = []
    for anchor in source.xpath('//a[contains(@href,"/tables/")]'):
        link_sections.append(nearest_section_title(anchor))
    table_count = 0
    for index, table_path in enumerate(sorted(table_paths), 1):
        page = html.parse(str(table_path)).getroot()
        tables = page.xpath("//table")
        if not tables:
            continue
        caption = clean(page.xpath("string((//figcaption | //caption | //h1)[1])"))
        section_title = link_sections[index - 1] if index - 1 < len(link_sections) else "Results"
        sec = sections.get(section_title)
        if sec is None:
            sec = sections.get("Results")
        if sec is None:
            sec = etree.SubElement(body, "sec"); etree.SubElement(sec, "title").text = section_title
            sections[section_title] = sec
        for table in tables:
            sec.append(clone_table(table, caption)); table_count += 1
    return article, {
        "title": title, "section_count": len(sections), "paragraph_count": len(article.xpath(".//p")),
        "table_count": table_count, "has_results_section": any(key.lower() == "results" for key in sections),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", required=True, type=Path)
    parser.add_argument("--table-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists(): raise SystemExit(f"Refusing to overwrite output directory: {args.output_dir}")
    xml_dir = args.output_dir / "normalized_nxml"; xml_dir.mkdir(parents=True)
    main_audit = list(csv.DictReader((args.download_dir / "DOWNLOAD_AUDIT.csv").open(encoding="utf-8-sig")))
    table_audit = list(csv.DictReader((args.table_dir / "TABLE_DOWNLOAD_AUDIT.csv").open(encoding="utf-8-sig")))
    tables_by_doi = defaultdict(list)
    for row in table_audit:
        if row["status"] == "success": tables_by_doi[row["doi"]].append(args.table_dir / row["local_file"])
    outputs = []
    usable = [row for row in main_audit if row["download_status"] == "success" and row["local_file"].endswith(".html")]
    for index, row in enumerate(sorted(usable, key=lambda item: item["doi"]), 1):
        main_path = args.download_dir / row["local_file"]
        root, diagnostics = normalized_article(main_path, tables_by_doi[row["doi"]])
        output = xml_dir / f"AH-S2C-{index:03d}_{re.sub(r'[^A-Za-z0-9]+', '_', row['doi'])}.nxml"
        output.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))
        outputs.append({
            "candidate_id": f"AH-S2C-{index:03d}", "doi": row["doi"], "source_main_file": row["local_file"],
            "source_main_sha256": row["sha256"], "source_table_pages": len(tables_by_doi[row["doi"]]),
            "normalized_file": str(output.relative_to(args.output_dir)), "normalized_sha256": sha256(output),
            **diagnostics,
            "structural_status": "PASS" if diagnostics["title"] and diagnostics["has_results_section"] and diagnostics["paragraph_count"] >= 5 else "REVIEW",
        })
    headers = list(outputs[0])
    with (args.output_dir / "NORMALIZATION_AUDIT.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers); writer.writeheader(); writer.writerows(outputs)
    summary = {
        "adapter_version": ADAPTER_VERSION, "normalized_articles": len(outputs),
        "structural_pass": sum(row["structural_status"] == "PASS" for row in outputs),
        "structural_review": sum(row["structural_status"] == "REVIEW" for row in outputs),
        "linked_tables_embedded": sum(row["table_count"] for row in outputs),
        "label_free": True,
    }
    (args.output_dir / "NORMALIZATION_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
