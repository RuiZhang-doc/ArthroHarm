#!/usr/bin/env python3
"""Create a non-overwritable v1.2 Stage II external-validation capsule."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--pdf-normalized-dir", type=Path, required=True)
    parser.add_argument("--html-normalized-dir", type=Path, required=True)
    parser.add_argument("--code-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--capsule-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.capsule_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing capsule: {args.capsule_dir}")

    with args.queue.open(encoding="utf-8-sig", newline="") as stream:
        queue = list(csv.DictReader(stream))
    roles = {role: sum(row["queue_role"] == role for row in queue) for role in ("CALIBRATION", "MAIN_EXTERNAL", "RESERVE")}
    if roles != {"CALIBRATION": 5, "MAIN_EXTERNAL": 50, "RESERVE": 13}:
        raise SystemExit(f"Unexpected queue roles: {roles}")

    inputs_dir = args.capsule_dir / "inputs"
    code_dir = args.capsule_dir / "locked_code"
    governance_dir = args.capsule_dir / "governance"
    inputs_dir.mkdir(parents=True)
    code_dir.mkdir()
    governance_dir.mkdir()

    input_records = []
    for row in queue:
        source_root = args.pdf_normalized_dir if row["source_route"] == "MANUAL_PUBLISHER_PDF" else args.html_normalized_dir
        source = source_root / row["final_normalized_file"]
        if not source.is_file():
            raise SystemExit(f"Missing normalized input: {source}")
        if sha256(source) != row["final_normalized_sha256"]:
            raise SystemExit(f"Normalized input hash mismatch: {source}")
        target = inputs_dir / source.name
        if target.exists():
            raise SystemExit(f"Duplicate normalized input filename: {target.name}")
        shutil.copyfile(source, target)
        input_records.append({
            "source_candidate_id": row["source_candidate_id"],
            "doi": row["doi"],
            "queue_role": row["queue_role"],
            "role_order": int(row["role_order"]),
            "publication_version": row["publication_version"],
            "publisher_family": row["publisher_family"],
            "journal": row["journal"],
            "input_file": target.name,
            "input_sha256": sha256(target),
            "publisher_source_sha256": row["source_sha256"],
        })

    components = [
        "arthroharm_extract_v1_2.py",
        "arthroharm_extract_v1_1.py",
        "arthroharm_rules_v1.2.json",
        "arthroharm_rules_v1.1.json",
        "arthroharm_rules_v1.0.json",
        "test_arthroharm_v1_2.py",
        "test_arthroharm_v1_1.py",
        "arthroharm_evaluation_v1_2.py",
        "test_arthroharm_evaluation_v1_2.py",
        "normalize_publisher_fulltext_v1_2.py",
        "test_normalize_publisher_fulltext_v1_2.py",
        "normalize_publisher_pdf_v1_2.py",
        "test_normalize_publisher_pdf_v1_2.py",
        "finalize_stage2_external_queue.py",
        "freeze_stage2_external_v1_2.py",
        "seal_stage2_predictions_v1_2.py",
    ]
    component_records = {}
    for name in components:
        source = args.code_dir / name
        if not source.is_file():
            raise SystemExit(f"Missing locked component: {source}")
        target = code_dir / name
        shutil.copyfile(source, target)
        component_records[name] = sha256(target)

    queue_target = governance_dir / "ArthroHarm_StageII_Queue_5_50_Reserves.csv"
    protocol_target = governance_dir / "LOCKED_STAGEII_PROTOCOL_RC2.md"
    shutil.copyfile(args.queue, queue_target)
    shutil.copyfile(args.protocol, protocol_target)
    manifest = {
        "manifest_version": "2.0",
        "status": "FROZEN_BEFORE_STAGEII_ANNOTATION_AND_PREDICTION_EXECUTION",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tool_version": "ArthroHarm-v1.2-RC2-external",
        "blind_boundary": "No Stage II clinician annotations, adjudication, reference standard, or predictions were read to build this capsule.",
        "queue_roles": roles,
        "input_count": len(input_records),
        "inputs": input_records,
        "locked_components": component_records,
        "governance": {
            "queue_file": str(queue_target.relative_to(args.capsule_dir)),
            "queue_sha256": sha256(queue_target),
            "protocol_file": str(protocol_target.relative_to(args.capsule_dir)),
            "protocol_sha256": sha256(protocol_target),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "planned_command": "python3 locked_code/arthroharm_extract_v1_2.py --input-dir inputs --rules locked_code/arthroharm_rules_v1.2.json --output-dir predictions --run-mode external-blind",
    }
    manifest_path = args.capsule_dir / "LOCKED_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "capsule": str(args.capsule_dir), "inputs": len(input_records),
        "manifest_sha256": sha256(manifest_path), "queue_sha256": sha256(queue_target),
        "protocol_sha256": sha256(protocol_target),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
