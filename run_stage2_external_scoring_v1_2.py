#!/usr/bin/env python3
"""Audited one-time Stage II scoring wrapper for frozen ArthroHarm v1.2.

This wrapper does not modify the sealed predictions, the prediction seal, the
locked reference standard, or the frozen scorer. It verifies every lock, makes
a deterministic article-ID interface crosswalk, filters the prespecified
50-article main set, and calls the frozen scorer's evaluate() function.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_SEAL_STATUS = "SEALED_BEFORE_STAGEII_CLINICIAN_ANNOTATION_AND_SCORING"
EXPECTED_REFERENCE_STATUS = "FINAL_LOCKED_BEFORE_PREDICTION_ACCESS"
SCORING_STATUS = "LOCKED_ONE_TIME_STAGEII_EXTERNAL_SCORING"
ARTICLE_ID_PATTERN = re.compile(r"^(AH-S2[A-Z]-\d{3})(?:_|$)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_frozen_scorer(path: Path):
    spec = importlib.util.spec_from_file_location("arthroharm_frozen_evaluation_v1_2", path)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to import frozen scorer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_article_id(sealed_id: str) -> str:
    match = ARTICLE_ID_PATTERN.match(str(sealed_id).strip())
    if not match:
        raise SystemExit(f"Unrecognized sealed Article ID: {sealed_id}")
    return match.group(1)


def reference_subset(reference: dict, article_ids: set[str]) -> dict:
    return {
        **{key: value for key, value in reference.items() if key not in {"articles", "harms"}},
        "articles": [row for row in reference["articles"] if row["article_id"] in article_ids],
        "harms": [row for row in reference["harms"] if row["article_id"] in article_ids],
    }


def flatten_matches(matches: list[dict]) -> list[dict]:
    rows = []
    for row in matches:
        rows.append({
            "event_id": row["gold"]["event_id"],
            "prediction_id": row["pred"]["Prediction ID"],
            "article_id": row["gold"]["article_id"],
            "route": row["route"],
            "rank_score": row["rank_score"],
            "quote_jaccard": row["quote_jaccard"],
            "term_similarity": row["term_similarity"],
            "category_exact": row["category_exact"],
            "gold_event_term": row["gold"]["event_term_raw"],
            "predicted_event_term": row["pred"]["Raw Event Term"],
            "gold_category": row["gold"]["category_canonical"],
            "predicted_category": row["pred"]["Predicted Category"],
            "gold_evidence_quote": row["gold"]["evidence_quote_raw"],
            "predicted_evidence_quote": row["pred"]["Evidence Quote"],
        })
    return rows


def article_signal_metrics(reference: dict, predictions: list[dict], scorer) -> tuple[dict, list[dict]]:
    eligible = [row for row in reference["articles"] if str(row["eligible"]).lower() == "yes"]
    predicted_ids = {row["Article ID"] for row in predictions}
    rows = []
    tp = fp = fn = tn = 0
    for article in eligible:
        gold_positive = str(article["harms_status"]).strip().lower() in {"yes", "unclear"}
        predicted_positive = article["article_id"] in predicted_ids
        if gold_positive and predicted_positive:
            classification = "TP"; tp += 1
        elif not gold_positive and predicted_positive:
            classification = "FP"; fp += 1
        elif gold_positive and not predicted_positive:
            classification = "FN"; fn += 1
        else:
            classification = "TN"; tn += 1
        rows.append({
            "article_id": article["article_id"],
            "publication_version": article["publication_version"],
            "harms_status": article["harms_status"],
            "gold_positive": gold_positive,
            "predicted_positive": predicted_positive,
            "classification": classification,
        })
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    accuracy = (tp + tn) / len(eligible) if eligible else None
    return ({
        "definition": "Among reference-eligible articles, positive means harms_status Yes or Unclear; predicted positive means at least one sealed extraction.",
        "articles": len(eligible), "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "sensitivity": recall,
        "sensitivity_ci95_wilson": scorer.wilson(tp, tp + fn),
        "specificity": specificity,
        "specificity_ci95_wilson": scorer.wilson(tn, tn + fp),
        "precision": precision,
        "precision_ci95_wilson": scorer.wilson(tp, tp + fp),
        "f1": f1,
        "accuracy": accuracy,
        "accuracy_ci95_wilson": scorer.wilson(tp + tn, len(eligible)),
    }, rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--prediction-seal", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--reference-lock", required=True, type=Path)
    parser.add_argument("--scorer", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {args.output_dir}")

    seal = json.loads(args.prediction_seal.read_text(encoding="utf-8"))
    reference_lock = json.loads(args.reference_lock.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    if seal.get("status") != EXPECTED_SEAL_STATUS:
        raise SystemExit(f"Unexpected prediction seal status: {seal.get('status')}")
    if reference_lock.get("status") != EXPECTED_REFERENCE_STATUS or reference.get("status") != EXPECTED_REFERENCE_STATUS:
        raise SystemExit("Reference standard is not locked before prediction access")

    input_hashes = {
        "sealed_predictions": sha256(args.predictions),
        "prediction_seal": sha256(args.prediction_seal),
        "locked_manifest": sha256(args.manifest),
        "reference_json": sha256(args.reference),
        "reference_lock": sha256(args.reference_lock),
        "frozen_scorer": sha256(args.scorer),
        "wrapper": sha256(Path(__file__)),
    }
    if input_hashes["sealed_predictions"] != seal.get("prediction_sha256"):
        raise SystemExit("Prediction JSON hash does not match seal")
    if input_hashes["locked_manifest"] != seal.get("files", {}).get(args.manifest.name):
        raise SystemExit("Manifest hash does not match seal")
    if input_hashes["reference_json"] != reference_lock.get("files", {}).get(args.reference.name):
        raise SystemExit("Reference JSON hash does not match reference lock")
    if input_hashes["frozen_scorer"] != reference_lock.get("inputs", {}).get("locked_scorer"):
        raise SystemExit("Frozen scorer hash does not match reference lock")

    # This is the first point at which the sealed prediction payload is parsed.
    sealed_predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    if len(sealed_predictions) != seal.get("prediction_count"):
        raise SystemExit("Prediction count does not match seal")

    main_ids = {row["article_id"] for row in reference["articles"]}
    crosswalk = {}
    normalized_predictions = []
    for row in sealed_predictions:
        sealed_id = row["Article ID"]
        short_id = normalized_article_id(sealed_id)
        crosswalk[sealed_id] = short_id
        if short_id in main_ids:
            derived = dict(row)
            derived["Sealed Article ID"] = sealed_id
            derived["Article ID"] = short_id
            normalized_predictions.append(derived)

    if len(main_ids) != 50:
        raise SystemExit(f"Expected 50 main articles, found {len(main_ids)}")
    unresolved_main = sorted(main_ids - set(crosswalk.values()))
    # No extracted events is a valid system output; absent IDs are documented,
    # not treated as a crosswalk error.
    id_crosswalk_rows = [
        {"sealed_article_id": sealed_id, "normalized_article_id": short_id,
         "in_main_50": short_id in main_ids}
        for sealed_id, short_id in sorted(crosswalk.items())
    ]

    args.output_dir.mkdir(parents=True)
    main_predictions_path = args.output_dir / "MAIN50_PREDICTION_SUBSET_ID_NORMALIZED.json"
    write_json(main_predictions_path, normalized_predictions)
    write_csv(args.output_dir / "ARTICLE_ID_CROSSWALK.csv", id_crosswalk_rows)

    scorer = load_frozen_scorer(args.scorer)
    main_result, matches, false_positives, false_negatives = scorer.evaluate(reference, normalized_predictions)
    excluded_ids = {row["article_id"] for row in reference["articles"] if str(row["eligible"]).lower() != "yes"}
    excluded_article_predictions = [row for row in normalized_predictions if row["Article ID"] in excluded_ids]

    vor_ids = {row["article_id"] for row in reference["articles"] if row["publication_version"] == "VOR"}
    aip_ids = {row["article_id"] for row in reference["articles"] if row["publication_version"] == "PUBLISHER_AIP_COMPLETE"}
    vor_reference = reference_subset(reference, vor_ids)
    aip_reference = reference_subset(reference, aip_ids)
    vor_predictions = [row for row in normalized_predictions if row["Article ID"] in vor_ids]
    aip_predictions = [row for row in normalized_predictions if row["Article ID"] in aip_ids]
    vor_result, vor_matches, vor_fp, vor_fn = scorer.evaluate(vor_reference, vor_predictions)
    aip_result, aip_matches, aip_fp, aip_fn = scorer.evaluate(aip_reference, aip_predictions)
    article_result, article_rows = article_signal_metrics(reference, normalized_predictions, scorer)

    execution_time = datetime.now(timezone.utc).isoformat()
    interface_note = {
        "issue": "Frozen scorer CLI expects an obsolete seal-status literal and exact short Article IDs, while the stronger actual seal uses the finalized status and sealed predictions contain filename-derived Article IDs.",
        "resolution": "Verified the actual stronger seal, imported the unchanged frozen evaluate() function, and normalized Article ID only via the fixed leading AH-S2X-NNN token in an immutable derived main-set copy.",
        "prediction_content_changed": False,
        "scorer_or_match_policy_changed": False,
        "sealed_source_overwritten": False,
        "main_articles_without_predictions": unresolved_main,
    }
    main_result["analysis_label"] = "Prespecified Stage II main external validation"
    main_result["execution_utc"] = execution_time
    main_result["input_hashes"] = input_hashes
    main_result["derived_main_prediction_subset_sha256"] = sha256(main_predictions_path)
    main_result["interface_audit"] = interface_note
    vor_result["analysis_label"] = "Prespecified VOR-only sensitivity analysis"
    aip_result["analysis_label"] = "Prespecified publisher AIP complete-case descriptive subset"

    metrics_path = args.output_dir / "ArthroHarm_v1.2_StageII_External_Validation_Main_Metrics.json"
    write_json(metrics_path, main_result)
    write_json(args.output_dir / "ArthroHarm_v1.2_StageII_VOR_Sensitivity_Metrics.json", vor_result)
    write_json(args.output_dir / "ArthroHarm_v1.2_StageII_AIP_Descriptive_Metrics.json", aip_result)
    write_json(args.output_dir / "ArthroHarm_v1.2_StageII_Article_Signal_Metrics.json", article_result)
    write_csv(args.output_dir / "main_matches.csv", flatten_matches(matches))
    write_csv(args.output_dir / "main_false_positives.csv", false_positives)
    write_csv(args.output_dir / "main_false_negatives.csv", false_negatives)
    write_csv(args.output_dir / "main_predictions_from_excluded_articles.csv", excluded_article_predictions)
    write_csv(args.output_dir / "article_signal_classification.csv", article_rows)
    write_csv(args.output_dir / "vor_matches.csv", flatten_matches(vor_matches))
    write_csv(args.output_dir / "vor_false_positives.csv", vor_fp)
    write_csv(args.output_dir / "vor_false_negatives.csv", vor_fn)
    write_csv(args.output_dir / "aip_matches.csv", flatten_matches(aip_matches))
    write_csv(args.output_dir / "aip_false_positives.csv", aip_fp)
    write_csv(args.output_dir / "aip_false_negatives.csv", aip_fn)

    audit = {
        "status": "PASS",
        "execution_utc": execution_time,
        "one_time_main_scoring": True,
        "input_hashes": input_hashes,
        "derived_files": {
            main_predictions_path.name: sha256(main_predictions_path),
            metrics_path.name: sha256(metrics_path),
        },
        "interface_audit": interface_note,
        "counts": {
            "sealed_predictions_all_68_articles": len(sealed_predictions),
            "main_50_predictions": len(normalized_predictions),
            "main_articles": len(reference["articles"]),
            "eligible_articles": sum(str(row["eligible"]).lower() == "yes" for row in reference["articles"]),
            "reference_events": len(reference["harms"]),
            "vor_articles": len(vor_ids),
            "aip_articles": len(aip_ids),
        },
    }
    audit_path = args.output_dir / "SCORING_EXECUTION_AUDIT.json"
    write_json(audit_path, audit)
    lock = {
        "status": SCORING_STATUS,
        "locked_at": execution_time,
        "scorer_version": scorer.SCORER_VERSION,
        "main_metrics_sha256": sha256(metrics_path),
        "execution_audit_sha256": sha256(audit_path),
        "derived_main_prediction_subset_sha256": sha256(main_predictions_path),
        "sealed_prediction_source_sha256": input_hashes["sealed_predictions"],
        "reference_standard_sha256": input_hashes["reference_json"],
    }
    write_json(args.output_dir / "SCORING_LOCK.json", lock)
    print(json.dumps({
        "main_event_level": main_result["event_level"],
        "vor_event_level": vor_result["event_level"],
        "aip_event_level": aip_result["event_level"],
        "article_signal": article_result,
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
