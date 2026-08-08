#!/usr/bin/env python3
"""Finalize the label-free Stage II queue from frozen source/format metadata.

No predictions, harms annotations, or reference-standard files are accepted as
inputs. Selection uses only prespecified eligibility, format, version, source,
journal, publisher-family, and SHA-256 seeded ordering fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path


SEED = "ArthroHarm-v1.2-stage2-external-20260807"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seeded_key(*parts: str) -> str:
    return hashlib.sha256("|".join((SEED, *parts)).encode()).hexdigest()


def publisher_family(host: str) -> str:
    host = (host or "").strip()
    groups = {
        "BioMed Central": "Springer Nature",
        "Nature Portfolio": "Springer Nature",
        "Springer Nature": "Springer Nature",
        "Springer Science+Business Media": "Springer Nature",
        "Elsevier BV": "Elsevier",
        "Dove Medical Press": "Taylor & Francis/Informa",
        "Wolters Kluwer": "Wolters Kluwer",
        "Lippincott Williams & Wilkins": "Wolters Kluwer",
        "Hindawi Publishing Corporation": "Wiley",
        "Wiley": "Wiley",
        "SAGE Publishing": "SAGE",
        "Multidisciplinary Digital Publishing Institute": "MDPI",
        "British Editorial Society of Bone & Joint Surgery": "BESBJS",
        "Foundation for Rehabilitation Information": "Foundation for Rehabilitation Information",
    }
    return groups.get(host, host or "Independent/Unresolved")


def family_round_robin(rows: list[dict]) -> list[dict]:
    buckets: dict[str, deque] = {}
    for family, values in _group(rows, "publisher_family").items():
        buckets[family] = deque(sorted(values, key=lambda row: row["selection_key"]))
    family_order = sorted(buckets, key=lambda family: seeded_key("family", family))
    output: list[dict] = []
    while any(buckets.values()):
        for family in family_order:
            if buckets[family]:
                output.append(buckets[family].popleft())
    return output


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        output[row[key]].append(row)
    return output


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--pdf-audit", type=Path, required=True)
    parser.add_argument("--openalex-enriched", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    with args.pool.open(encoding="utf-8-sig", newline="") as stream:
        pool = list(csv.DictReader(stream))
    with args.pdf_audit.open(encoding="utf-8-sig", newline="") as stream:
        pdf = {row["doi"].lower(): row for row in csv.DictReader(stream)}
    with args.openalex_enriched.open(encoding="utf-8-sig", newline="") as stream:
        enriched = {row["doi"].lower(): row for row in csv.DictReader(stream)}

    audited = []
    for row in pool:
        doi = row["doi"].lower()
        host = enriched.get(doi, {}).get("oa_host", "")
        family = publisher_family(host)
        if row["source_route"] == "AUTOMATED_PUBLISHER_HTML":
            format_gate = "PASS_PUBLISHER_HTML_STRUCTURAL_GATE"
            normalized_file = row["normalized_file"]
            normalized_sha = row["normalized_sha256"]
        else:
            candidate = pdf.get(doi)
            format_gate = candidate["table_format_status"] if candidate else "MISSING_PDF_AUDIT"
            normalized_file = candidate["normalized_file"] if candidate else ""
            normalized_sha = candidate["normalized_sha256"] if candidate else ""
        eligible = (
            row["document_gate"].startswith("PASS")
            and row["old_20_plus_60_overlap_status"] == "NO_EXACT_DOI_TITLE_OR_REGISTRY_OVERLAP"
            and row["trial_family_status"].startswith("PASS")
            and format_gate.startswith("PASS")
            and row["publication_version"] in {"VOR", "PUBLISHER_AIP_COMPLETE"}
        )
        reason = "ELIGIBLE_ALL_PREFREEZE_GATES" if eligible else f"EXCLUDE_FORMAT_OR_SOURCE_GATE:{format_gate}"
        audited.append({
            **row,
            "oa_host": host,
            "publisher_family": family,
            "final_normalized_file": normalized_file,
            "final_normalized_sha256": normalized_sha,
            "format_gate_final": format_gate,
            "final_eligibility": reason,
            "selection_key": seeded_key(row["source_candidate_id"], row["source_sha256"]),
        })

    eligible_rows = [row for row in audited if row["final_eligibility"] == "ELIGIBLE_ALL_PREFREEZE_GATES"]
    excluded_rows = [row for row in audited if row not in eligible_rows]
    ordered = family_round_robin(eligible_rows)

    # Calibration: five different publisher families, selected before the main queue.
    calibration = []
    seen_families = set()
    for row in ordered:
        if row["publisher_family"] not in seen_families:
            calibration.append(row)
            seen_families.add(row["publisher_family"])
        if len(calibration) == 5:
            break
    calibration_ids = {row["source_candidate_id"] for row in calibration}
    remainder = [row for row in ordered if row["source_candidate_id"] not in calibration_ids]

    main = []
    family_counts = Counter()
    journal_counts = Counter()
    aip_count = 0
    deferred = []
    for row in remainder:
        is_aip = row["publication_version"] == "PUBLISHER_AIP_COMPLETE"
        if (
            family_counts[row["publisher_family"]] >= 20
            or journal_counts[row["journal"]] >= 5
            or (is_aip and aip_count >= 5)
        ):
            deferred.append(row)
            continue
        main.append(row)
        family_counts[row["publisher_family"]] += 1
        journal_counts[row["journal"]] += 1
        aip_count += int(is_aip)
        if len(main) == 50:
            break
    if len(main) != 50:
        raise SystemExit(f"Unable to select 50 main reports under caps; selected {len(main)}")
    main_ids = {row["source_candidate_id"] for row in main}
    reserves = [row for row in remainder if row["source_candidate_id"] not in main_ids]

    queue = []
    for role, rows in (("CALIBRATION", calibration), ("MAIN_EXTERNAL", main), ("RESERVE", reserves)):
        for order, row in enumerate(rows, 1):
            queue.append({"queue_role": role, "role_order": order, **row})

    write_csv(args.output_dir / "ArthroHarm_StageII_Final_Eligibility_Audit.csv", audited)
    write_csv(args.output_dir / "ArthroHarm_StageII_Queue_5_50_Reserves.csv", queue)
    write_csv(args.output_dir / "ArthroHarm_StageII_Excluded_Before_Freeze.csv", excluded_rows)
    summary = {
        "status": "QUEUE_SELECTED_PREFREEZE_DO_NOT_ANNOTATE_OR_SCORE",
        "seed": SEED,
        "input_candidates": len(pool),
        "eligible": len(eligible_rows),
        "excluded": len(excluded_rows),
        "roles": dict(Counter(row["queue_role"] for row in queue)),
        "main_publisher_families": dict(family_counts),
        "main_journals_max": max(journal_counts.values()),
        "main_aip": aip_count,
        "main_vor": 50 - aip_count,
        "predictions_or_reference_labels_read": False,
        "queue_sha256": sha256(args.output_dir / "ArthroHarm_StageII_Queue_5_50_Reserves.csv"),
        "eligibility_audit_sha256": sha256(args.output_dir / "ArthroHarm_StageII_Final_Eligibility_Audit.csv"),
        "excluded_sha256": sha256(args.output_dir / "ArthroHarm_StageII_Excluded_Before_Freeze.csv"),
    }
    (args.output_dir / "QUEUE_SELECTION_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
