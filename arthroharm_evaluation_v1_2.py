#!/usr/bin/env python3
"""Locked-candidate evaluation core for ArthroHarm v1.2.

The module intentionally separates event identity from field correctness.
An event match is anchored by the verbatim evidence quote plus the raw event
term or canonical category. Arm, time, numerator, denominator, and poolability
are evaluated only after one-to-one event matching.

This file contains no Stage II labels and may be tested only with synthetic or
development data before the external-validation capsule is frozen.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from pathlib import Path


SCORER_VERSION = "ArthroHarm-evaluation-v1.2-RC1"
BOOTSTRAP_SEED = "ArthroHarm-v1.2-external-bootstrap-20260807"
BOOTSTRAP_REPLICATES = 10000
MATCH_POLICY = {
    "name": "conservative_quote_term_anchor_v1",
    "route_1": "quote_jaccard >= 0.50 AND (term_similarity >= 0.50 OR category_exact)",
    "route_2": "term_similarity >= 0.85 AND quote_jaccard >= 0.15",
    "event_fields_excluded_from_identity": [
        "numerator", "denominator", "arm", "time", "poolability"
    ],
}

STOPWORDS = set(
    "a an the and or of to in on for with without from by at as is was were "
    "are be been being this that these those patient patients participant "
    "participants group groups postoperative postoperatively after before "
    "during study trial reported reports reporting".split()
)


def as_text(value) -> str:
    return "" if value is None else str(value).strip()


def norm(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", as_text(value).lower())).strip()


def lexical_tokens(value) -> set[str]:
    tokens = []
    for token in norm(value).split():
        if len(token) <= 1 or token in STOPWORDS:
            continue
        # Minimal deterministic normalization for common English inflections.
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return set(tokens)


def jaccard(a, b) -> float:
    aa, bb = lexical_tokens(a), lexical_tokens(b)
    return len(aa & bb) / len(aa | bb) if aa and bb else 0.0


def term_similarity(a, b) -> float:
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.85
    return jaccard(aa, bb)


def numeric_equal(a, b):
    if as_text(a) == "" or as_text(b) == "":
        return None
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return norm(a) == norm(b)


def canonical_arm(value) -> str:
    raw = as_text(value).lower()
    aliases = {
        "both groups combined": "overall",
        "all groups combined": "overall",
        "either group": "overall",
        "combined": "overall",
        "total": "overall",
        "overall": "overall",
        "control group": "control",
        "control arm": "control",
    }
    for old, new in aliases.items():
        raw = raw.replace(old, new)
    raw = re.sub(r"\(\s*(?:n\s*=\s*)?\d+\s*\)", "", raw)
    raw = re.sub(r"\b(?:group|arm)\b", "", raw)
    raw = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", raw)).strip()
    return raw


def canonical_time(value) -> str:
    raw = as_text(value).lower().replace("–", "-").replace("—", "-")
    unit_map = {
        "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
        "d": "days", "day": "days", "days": "days",
        "w": "weeks", "wk": "weeks", "wks": "weeks", "week": "weeks", "weeks": "weeks",
        "mo": "months", "mos": "months", "month": "months", "months": "months",
        "y": "years", "yr": "years", "yrs": "years", "year": "years", "years": "years",
    }
    found = []
    pattern = r"(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)\s*(hours?|hrs?|h|days?|d|weeks?|wks?|wk|w|months?|mos?|years?|yrs?|yr|y)\b"
    for number, unit in re.findall(pattern, raw):
        compact_number = re.sub(r"\s+", "", number)
        found.append(f"{compact_number} {unit_map[unit]}")
    if "in-hospital" in raw or "hospital stay" in raw:
        found.append("in-hospital")
    if "perioperative" in raw or "peri-operative" in raw:
        found.append("perioperative")
    return " | ".join(sorted(set(found)))


def pair_features(gold: dict, pred: dict) -> dict:
    quote = jaccard(gold["evidence_quote_raw"], pred["Evidence Quote"])
    term = term_similarity(gold["event_term_raw"], pred["Raw Event Term"])
    category_exact = norm(gold["category_canonical"]) == norm(pred["Predicted Category"])
    route_1 = quote >= 0.50 and (term >= 0.50 or category_exact)
    route_2 = term >= 0.85 and quote >= 0.15
    # Score ranks eligible edges only; it does not set eligibility.
    rank_score = 0.60 * quote + 0.30 * term + 0.10 * float(category_exact)
    return {
        "eligible": route_1 or route_2,
        "quote_jaccard": quote,
        "term_similarity": term,
        "category_exact": category_exact,
        "route": "route_1" if route_1 else "route_2" if route_2 else "none",
        "rank_score": rank_score,
    }


def one_to_one_match(gold_rows: list[dict], predictions: list[dict]) -> list[dict]:
    """Return deterministic article-local greedy matches on eligible edges.

    Tie-breaking uses persistent identifiers rather than input row order. The
    matching policy and this algorithm must be frozen before Stage II labels.
    """
    gold_by_article, pred_by_article = defaultdict(list), defaultdict(list)
    for row in gold_rows:
        gold_by_article[as_text(row["article_id"])].append(row)
    for row in predictions:
        pred_by_article[as_text(row["Article ID"])].append(row)
    matches = []
    for article_id in sorted(set(gold_by_article) | set(pred_by_article)):
        edges = []
        for gold in gold_by_article[article_id]:
            for pred in pred_by_article[article_id]:
                features = pair_features(gold, pred)
                if features["eligible"]:
                    edges.append({"gold": gold, "pred": pred, **features})
        edges.sort(key=lambda edge: (
            -edge["rank_score"], -edge["quote_jaccard"], -edge["term_similarity"],
            as_text(edge["gold"]["event_id"]), as_text(edge["pred"]["Prediction ID"]),
        ))
        used_gold, used_pred = set(), set()
        for edge in edges:
            gid, pid = as_text(edge["gold"]["event_id"]), as_text(edge["pred"]["Prediction ID"])
            if gid in used_gold or pid in used_pred:
                continue
            used_gold.add(gid)
            used_pred.add(pid)
            matches.append(edge)
    return matches


def wilson(success: int, total: int, z: float = 1.959963984540054):
    if not total:
        return [None, None]
    p = success / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0, center - half), min(1, center + half)]


def accuracy(success: int, total: int) -> dict:
    return {
        "correct": success,
        "eligible": total,
        "accuracy": success / total if total else None,
        "ci95": wilson(success, total),
    }


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position); upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def cluster_bootstrap(article_counts: dict[str, tuple[int, int, int]], replicates: int = BOOTSTRAP_REPLICATES) -> dict:
    """Article-cluster percentile CIs for event precision, recall, and F1."""
    article_ids = sorted(article_counts)
    seed_value = int(hashlib.sha256(BOOTSTRAP_SEED.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed_value)
    distributions = {"precision": [], "recall": [], "f1": []}
    for _ in range(replicates):
        sampled = [article_ids[generator.randrange(len(article_ids))] for _ in article_ids]
        tp = sum(article_counts[item][0] for item in sampled)
        fp = sum(article_counts[item][1] for item in sampled)
        fn = sum(article_counts[item][2] for item in sampled)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        distributions["precision"].append(precision)
        distributions["recall"].append(recall)
        distributions["f1"].append(f1)
    return {
        "method": "article-cluster percentile bootstrap",
        "replicates": replicates,
        "seed_label": BOOTSTRAP_SEED,
        **{key: [percentile(values, 0.025), percentile(values, 0.975)] for key, values in distributions.items()},
    }


def field_metrics(matches: list[dict]) -> dict:
    pairs = [(row["gold"], row["pred"]) for row in matches]

    def calculate(gold_key, pred_key, comparator, require_gold=True):
        eligible = [(g, p) for g, p in pairs if not require_gold or as_text(g[gold_key])]
        return accuracy(sum(bool(comparator(g[gold_key], p[pred_key])) for g, p in eligible), len(eligible))

    return {
        "category_canonical_exact": calculate("category_canonical", "Predicted Category", lambda a, b: norm(a) == norm(b), False),
        "numerator_value_exact": calculate("numerator_value", "Numerator", lambda a, b: numeric_equal(a, b) is True),
        "denominator_value_exact": calculate("denominator_value", "Denominator", lambda a, b: numeric_equal(a, b) is True),
        "arm_raw_exact": calculate("arm_raw", "Arm", lambda a, b: norm(a) == norm(b)),
        "arm_canonical_exact": calculate("arm_canonical", "Arm", lambda a, b: norm(a) == canonical_arm(b)),
        "time_raw_exact": calculate("time_raw", "Time Window", lambda a, b: norm(a) == norm(b)),
        "time_canonical_exact": calculate("time_canonical", "Time Window", lambda a, b: norm(a) == norm(canonical_time(b))),
        "poolability_exact": calculate("poolability", "Poolability", lambda a, b: norm(a) == norm(b), False),
    }


def evaluate(reference: dict, predictions: list[dict]) -> tuple[dict, list[dict], list[dict], list[dict]]:
    articles, gold = reference["articles"], reference["harms"]
    included_ids = {as_text(row["article_id"]) for row in articles if as_text(row["eligible"]).lower() == "yes"}
    evaluated = [row for row in predictions if as_text(row["Article ID"]) in included_ids]
    excluded_article_predictions = [row for row in predictions if as_text(row["Article ID"]) not in included_ids]
    matches = one_to_one_match(gold, evaluated)
    matched_gold = {as_text(row["gold"]["event_id"]) for row in matches}
    matched_pred = {as_text(row["pred"]["Prediction ID"]) for row in matches}
    false_negatives = [row for row in gold if as_text(row["event_id"]) not in matched_gold]
    false_positives = [row for row in evaluated if as_text(row["Prediction ID"]) not in matched_pred]
    tp, fp, fn = len(matches), len(false_positives), len(false_negatives)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    match_by_article = defaultdict(int)
    for row in matches: match_by_article[as_text(row["gold"]["article_id"])] += 1
    fp_by_article = defaultdict(int)
    for row in false_positives: fp_by_article[as_text(row["Article ID"])] += 1
    fn_by_article = defaultdict(int)
    for row in false_negatives: fn_by_article[as_text(row["article_id"])] += 1
    article_counts = {item: (match_by_article[item], fp_by_article[item], fn_by_article[item]) for item in sorted(included_ids)}
    bootstrap = cluster_bootstrap(article_counts)
    result = {
        "scorer_version": SCORER_VERSION,
        "match_policy": MATCH_POLICY,
        "external_validation": True,
        "evaluation_universe": {
            "articles_total": len(articles),
            "articles_included": len(included_ids),
            "reference_events": len(gold),
            "predictions_total": len(predictions),
            "predictions_evaluated": len(evaluated),
            "predictions_from_excluded_articles": len(excluded_article_predictions),
        },
        "event_level": {
            "TP": tp, "FP": fp, "FN": fn,
            "precision": precision, "precision_ci95": wilson(tp, tp + fp),
            "recall": recall, "recall_ci95": wilson(tp, tp + fn), "f1": f1,
            "article_cluster_bootstrap_ci95": bootstrap,
        },
        "field_metrics_conditional_on_event_match": field_metrics(matches),
    }
    return result, matches, false_positives, false_negatives


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--prediction-seal", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to overwrite output directory: {args.output_dir}")
    seal = json.loads(args.prediction_seal.read_text(encoding="utf-8"))
    if seal.get("status") != "SEALED_BEFORE_REFERENCE_ANNOTATION":
        raise SystemExit("Prediction seal status is not SEALED_BEFORE_REFERENCE_ANNOTATION")
    sealed_hash = seal.get("prediction_sha256") or seal.get("files", {}).get(args.predictions.name)
    if sealed_hash != sha256(args.predictions):
        raise SystemExit("Prediction hash does not match the prediction seal")
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    if reference.get("status") != "FINAL_LOCKED_BEFORE_PREDICTION_ACCESS":
        raise SystemExit("Reference standard is not locked before prediction access")
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    result, matches, false_positives, false_negatives = evaluate(reference, predictions)
    args.output_dir.mkdir(parents=True)
    result["input_hashes"] = {
        "predictions": sha256(args.predictions),
        "prediction_seal": sha256(args.prediction_seal),
        "reference": sha256(args.reference),
        "scorer": sha256(Path(__file__)),
    }
    (args.output_dir / "ArthroHarm_v1.2_External_Validation_Metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    match_rows = []
    for row in matches:
        match_rows.append({
            "event_id": row["gold"]["event_id"],
            "prediction_id": row["pred"]["Prediction ID"],
            "article_id": row["gold"]["article_id"],
            "route": row["route"],
            "rank_score": row["rank_score"],
            "quote_jaccard": row["quote_jaccard"],
            "term_similarity": row["term_similarity"],
            "category_exact": row["category_exact"],
        })
    write_csv(args.output_dir / "matches.csv", match_rows)
    write_csv(args.output_dir / "false_positives.csv", false_positives)
    write_csv(args.output_dir / "false_negatives.csv", false_negatives)
    (args.output_dir / "SCORING_LOCK.json").write_text(json.dumps({
        "status": "LOCKED_ONE_TIME_EXTERNAL_SCORING",
        "scorer_version": SCORER_VERSION,
        "metrics_sha256": sha256(args.output_dir / "ArthroHarm_v1.2_External_Validation_Metrics.json"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["event_level"], indent=2))


if __name__ == "__main__":
    main()
