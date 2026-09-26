import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from contract import adoption_gate, iou, normalize, score, valid_box
from evaluate import Worker, load_manifest


class ContractTests(unittest.TestCase):
    def test_pixel_conversion_and_honest_confidence(self):
        result = normalize({"detections": [{"label": "  Blue   PHONE ", "box": [20, 10, 80, 50]}]}, 100, 100)
        self.assertEqual(result[0]["label"], "blue phone")
        self.assertEqual(result[0]["bbox"], [.2, .1, .8, .5])
        self.assertIsNone(result[0]["confidence"])

    def test_invalid_boxes_cannot_silently_pass(self):
        for box in ([0, 0, 0, 1], [.8, 0, .2, 1], [-1, 0, 1, 1], [0, 0, 2, 1], [0, 0, float("nan"), 1]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                valid_box(box)

    def test_iou(self):
        self.assertEqual(iou([0, 0, 1, 1], [0, 0, 1, 1]), 1)
        self.assertEqual(iou([0, 0, .2, .2], [.5, .5, 1, 1]), 0)

    def test_label_errors_and_duplicate_predictions(self):
        target = {"label": "blue phone", "bbox": [0, 0, 1, 1]}
        self.assertEqual(score([target], [target, target]), {"correct": 1, "expected": 1, "false_positives": 1})
        self.assertEqual(score([target, target], [target])["correct"], 1)
        self.assertEqual(score([target], [{**target, "label": "blue wristband"}])["correct"], 0)

    def dataset(self):
        objects = [{"label": f"object {i}", "bbox": [0, 0, .5, .5]} for i in range(10)]
        manifest = {"purpose": "adoption", "image_kind": "real_tabletop", "labels": [o["label"] for o in objects],
                    "demo_critical_labels": [o["label"] for o in objects],
                    "scenes": [{"id": str(i), "image": f"{i}.jpg", "objects": copy.deepcopy(objects)} for i in range(3)]}
        rows = [{"scene_id": str(i % 3), "status": "ok", "round_trip_s": 2.9,
                 "detections": copy.deepcopy(objects)} for i in range(10)]
        return manifest, rows

    def test_gate_passes_only_full_evidence(self):
        manifest, rows = self.dataset()
        self.assertTrue(adoption_gate(manifest, rows)["pass"])

    def test_exact_eighty_percent_passes_but_three_seconds_fails(self):
        manifest, rows = self.dataset()
        for row in rows:
            row["detections"] = row["detections"][:8]
        self.assertTrue(adoption_gate(manifest, rows)["pass"])
        for row in rows:
            row["round_trip_s"] = 3
        self.assertFalse(adoption_gate(manifest, rows)["pass"])

    def test_misses_fail_gate(self):
        manifest, rows = self.dataset()
        for row in rows:
            row["detections"] = row["detections"][:7]
        self.assertFalse(adoption_gate(manifest, rows)["pass"])

    def test_nine_requests_or_one_error_fail(self):
        manifest, rows = self.dataset()
        self.assertFalse(adoption_gate(manifest, rows[:9])["pass"])
        rows[-1]["status"] = "error"
        self.assertFalse(adoption_gate(manifest, rows)["pass"])

    def test_repeated_scene_does_not_inflate_accuracy(self):
        manifest, rows = self.dataset()
        rows[0]["detections"] = []
        gate = adoption_gate(manifest, rows)
        self.assertEqual(gate["expected"], 30)
        self.assertEqual(gate["correct"], 20)
        self.assertFalse(gate["pass"])

    def test_smoke_never_qualifies_for_adoption(self):
        manifest, rows = self.dataset()
        manifest["purpose"] = "runtime_smoke_only"
        self.assertFalse(adoption_gate(manifest, rows)["pass"])

    def test_missing_images_rejected_before_model_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            manifest, _ = self.dataset()
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Missing image"):
                load_manifest(path)

    def test_worker_crash_is_visible(self):
        with tempfile.TemporaryFile(mode="w+") as log:
            worker = Worker([sys.executable, "-c", "raise SystemExit(9)"], log, "Ubuntu")
            try:
                with self.assertRaisesRegex(RuntimeError, "Worker stopped"):
                    worker.receive(5)
            finally:
                worker.close()

    def test_timeout_and_cleanup(self):
        with tempfile.TemporaryFile(mode="w+") as log:
            worker = Worker([sys.executable, "-c", "import time; time.sleep(30)"], log, "Ubuntu")
            try:
                with self.assertRaises(TimeoutError):
                    worker.receive(.05)
            finally:
                worker.close()
            self.assertIsNotNone(worker.process.poll())


if __name__ == "__main__":
    unittest.main()
