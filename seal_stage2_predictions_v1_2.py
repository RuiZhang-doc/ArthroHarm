#!/usr/bin/env python3
"""Seal v1.2 Stage II predictions after the first external-blind execution."""

from __future__ import annotations

import argparse
import hashlib
import json
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
    parser.add_argument("--capsule-dir", type=Path, required=True)
    args = parser.parse_args()
    seal_path = args.capsule_dir / "PREDICTION_SEAL.json"
    if seal_path.exists():
        raise SystemExit(f"Refusing to overwrite existing seal: {seal_path}")
    files = {
        "LOCKED_MANIFEST.json": args.capsule_dir / "LOCKED_MANIFEST.json",
        "ArthroHarm_External_Blind_Predictions_v1.2.json": args.capsule_dir / "predictions" / "ArthroHarm_External_Blind_Predictions_v1.2.json",
        "ArthroHarm_External_Blind_Predictions_v1.2.csv": args.capsule_dir / "predictions" / "ArthroHarm_External_Blind_Predictions_v1.2.csv",
        "run_summary.json": args.capsule_dir / "predictions" / "run_summary.json",
    }
    for path in files.values():
        if not path.is_file():
            raise SystemExit(f"Missing file required for seal: {path}")
    predictions = json.loads(files["ArthroHarm_External_Blind_Predictions_v1.2.json"].read_text(encoding="utf-8"))
    seal = {
        "seal_version": "2.0",
        "sealed_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SEALED_BEFORE_STAGEII_CLINICIAN_ANNOTATION_AND_SCORING",
        "prediction_count": len(predictions),
        "prediction_sha256": sha256(files["ArthroHarm_External_Blind_Predictions_v1.2.json"]),
        "files": {name: sha256(path) for name, path in files.items()},
    }
    seal_path.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"seal": str(seal_path), "sha256": sha256(seal_path), "prediction_count": len(predictions)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
