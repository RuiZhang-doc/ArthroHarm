#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("evaluation", HERE / "arthroharm_evaluation_v1_2.py")
E = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(E)


def gold(event_id="G1", quote="Deep vein thrombosis occurred in two patients.", term="deep vein thrombosis", category="Venous thromboembolism", arm="Control", time="2 weeks"):
    return {
        "event_id": event_id, "article_id": "A1", "evidence_quote_raw": quote,
        "event_term_raw": term, "category_canonical": category,
        "numerator_value": 2, "denominator_value": 20,
        "arm_raw": arm, "arm_canonical": E.canonical_arm(arm),
        "time_raw": time, "time_canonical": E.canonical_time(time),
        "poolability": "Directly poolable",
    }


def pred(prediction_id="P1", quote="Deep vein thrombosis occurred in two patients.", term="deep vein thrombosis", category="Venous thromboembolism", arm="Control", time="2 weeks"):
    return {
        "Prediction ID": prediction_id, "Article ID": "A1", "Evidence Quote": quote,
        "Raw Event Term": term, "Predicted Category": category,
        "Numerator": 2, "Denominator": 20, "Arm": arm,
        "Time Window": time, "Poolability": "Directly poolable",
    }


class EvaluationTests(unittest.TestCase):
    def test_exact_quote_and_term_match(self):
        self.assertTrue(E.pair_features(gold(), pred())["eligible"])

    def test_category_alone_cannot_create_match(self):
        p = pred(quote="The mean age was 68 years.", term="age")
        self.assertFalse(E.pair_features(gold(), p)["eligible"])

    def test_numeric_values_do_not_define_event_identity(self):
        p = pred(); p["Numerator"] = 99; p["Denominator"] = 100
        self.assertTrue(E.pair_features(gold(), p)["eligible"])

    def test_raw_and_canonical_arm_are_separate(self):
        g, p = gold(arm="Control group"), pred(arm="Control")
        metrics = E.field_metrics([{"gold": g, "pred": p}])
        self.assertEqual(metrics["arm_raw_exact"]["correct"], 0)
        self.assertEqual(metrics["arm_canonical_exact"]["correct"], 1)

    def test_raw_and_canonical_time_are_separate(self):
        g, p = gold(time="at 2 weeks"), pred(time="within 2 wk")
        metrics = E.field_metrics([{"gold": g, "pred": p}])
        self.assertEqual(metrics["time_raw_exact"]["correct"], 0)
        self.assertEqual(metrics["time_canonical_exact"]["correct"], 1)

    def test_persistent_ids_make_tie_break_deterministic(self):
        gs = [gold("G2"), gold("G1")]
        ps = [pred("P2"), pred("P1")]
        forward = [(x["gold"]["event_id"], x["pred"]["Prediction ID"]) for x in E.one_to_one_match(gs, ps)]
        reverse = [(x["gold"]["event_id"], x["pred"]["Prediction ID"]) for x in E.one_to_one_match(list(reversed(gs)), list(reversed(ps)))]
        self.assertEqual(forward, reverse)
        self.assertEqual(forward, [("G1", "P1"), ("G2", "P2")])

    def test_same_quote_two_terms_pair_by_term(self):
        quote = "Deep vein thrombosis occurred in two patients and pulmonary embolism in one."
        gs = [gold("G1", quote, "deep vein thrombosis"), gold("G2", quote, "pulmonary embolism")]
        ps = [pred("P2", quote, "pulmonary embolism"), pred("P1", quote, "deep vein thrombosis")]
        pairs = {(x["gold"]["event_id"], x["pred"]["Prediction ID"]) for x in E.one_to_one_match(gs, ps)}
        self.assertEqual(pairs, {("G1", "P1"), ("G2", "P2")})

    def test_cluster_bootstrap_is_deterministic(self):
        counts = {"A1": (3, 1, 2), "A2": (1, 2, 1), "A3": (0, 1, 3)}
        first = E.cluster_bootstrap(counts, replicates=100)
        second = E.cluster_bootstrap(counts, replicates=100)
        self.assertEqual(first, second)
        self.assertEqual(first["replicates"], 100)
        self.assertLessEqual(first["f1"][0], first["f1"][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
