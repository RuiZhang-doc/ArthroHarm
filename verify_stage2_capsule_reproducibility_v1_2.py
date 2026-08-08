#!/usr/bin/env python3
"""Verify a sealed Stage II capsule without inspecting prediction content."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsule-dir", type=Path, required=True)
    parser.add_argument("--rerun-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.rerun_dir.exists() or args.report.exists():
        raise SystemExit("Refusing to overwrite an existing rerun directory or report")
    manifest_path = args.capsule_dir / "LOCKED_MANIFEST.json"
    seal_path = args.capsule_dir / "PREDICTION_SEAL.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if manifest["input_count"] != len(list((args.capsule_dir / "inputs").glob("*.nxml"))):
        raise SystemExit("Input count differs from manifest")
    for record in manifest["inputs"]:
        path = args.capsule_dir / "inputs" / record["input_file"]
        if sha256(path) != record["input_sha256"]:
            raise SystemExit(f"Input hash mismatch: {path}")
    subprocess.run([
        sys.executable,
        str(args.capsule_dir / "locked_code" / "arthroharm_extract_v1_2.py"),
        "--input-dir", str(args.capsule_dir / "inputs"),
        "--rules", str(args.capsule_dir / "locked_code" / "arthroharm_rules_v1.2.json"),
        "--output-dir", str(args.rerun_dir),
        "--run-mode", "external-blind",
    ], check=True)
    primary = args.capsule_dir / "predictions"
    names = [
        "ArthroHarm_External_Blind_Predictions_v1.2.json",
        "ArthroHarm_External_Blind_Predictions_v1.2.csv",
        "run_summary.json",
    ]
    comparisons = {
        name: {
            "primary_sha256": sha256(primary / name),
            "rerun_sha256": sha256(args.rerun_dir / name),
            "identical": sha256(primary / name) == sha256(args.rerun_dir / name),
        }
        for name in names
    }
    prediction_name = names[0]
    if seal["prediction_sha256"] != comparisons[prediction_name]["primary_sha256"]:
        raise SystemExit("Primary prediction hash differs from seal")
    report = {
        "status": "PASS" if all(item["identical"] for item in comparisons.values()) else "FAIL",
        "manifest_sha256": sha256(manifest_path),
        "seal_sha256": sha256(seal_path),
        "input_count": manifest["input_count"],
        "prediction_count": seal["prediction_count"],
        "comparisons": comparisons,
        "prediction_content_inspected": False,
        "reference_or_annotation_files_read": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
