"""Benchmark-only normalized detections and falsifiable adoption criteria."""
import math
import statistics


def label_key(label):
    if not isinstance(label, str) or not label.strip():
        raise ValueError("A non-empty label is required")
    return " ".join(label.lower().split())


def valid_box(box):
    if len(box) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box):
        raise ValueError("Expected four finite coordinates")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("Expected normalized xyxy box with positive area")
    return list(box)


def normalize(raw, width, height):
    if width <= 0 or height <= 0:
        raise ValueError("Invalid image size")
    detections = []
    for item in raw["detections"]:
        box = item["box"]
        if len(box) != 4:
            raise ValueError("Expected pixel xyxy box")
        normalized = valid_box([box[0] / width, box[1] / height, box[2] / width, box[3] / height])
        detections.append({"label": label_key(item["label"]), "bbox": normalized,
                           "confidence": None, "confidence_source": "not_exposed_by_cpp_api"})
    return detections


def iou(a, b):
    x1, y1, x2, y2 = a
    u1, v1, u2, v2 = b
    intersection = max(0, min(x2, u2) - max(x1, u1)) * max(0, min(y2, v2) - max(y1, v1))
    union = (x2 - x1) * (y2 - y1) + (u2 - u1) * (v2 - v1) - intersection
    return intersection / union if union else 0


def score(expected, predictions):
    """Maximum one-to-one same-label matching at IoU >= .5; duplicates are false positives."""
    edges = [[j for j, p in enumerate(predictions)
              if label_key(g["label"]) == label_key(p["label"]) and iou(g["bbox"], p["bbox"]) >= .5]
             for g in expected]
    matched = {}

    def assign(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or assign(matched[j], seen):
                matched[j] = i
                return True
        return False

    for i in range(len(expected)):
        assign(i, set())
    hits = len(matched)
    return {"correct": hits, "expected": len(expected), "false_positives": len(predictions) - hits}


def adoption_gate(manifest, requests):
    reasons = []
    scenes = manifest["scenes"]
    expected_labels = {label_key(o["label"]) for s in scenes for o in s["objects"]}
    critical = {label_key(x) for x in manifest.get("demo_critical_labels", [])}
    if manifest.get("purpose") != "adoption" or manifest.get("image_kind") != "real_tabletop":
        reasons.append("Not a real-tabletop adoption dataset")
    if len(expected_labels) < 10:
        reasons.append("Fewer than ten annotated object categories")
    if len({s["image"] for s in scenes}) < 3:
        reasons.append("Fewer than three distinct saved scenes")
    if not critical or not critical <= expected_labels:
        reasons.append("Demo-critical objects lack annotations")
    first = {}
    for row in requests:
        first.setdefault(row["scene_id"], row)
    totals = {"correct": 0, "expected": 0, "false_positives": 0}
    for scene in scenes:
        row = first.get(scene["id"], {})
        gt = [o for o in scene["objects"] if label_key(o["label"]) in critical]
        pred = [p for p in row.get("detections", []) if label_key(p["label"]) in critical]
        result = score(gt, pred)
        for key in totals:
            totals[key] += result[key]
        if row.get("status") != "ok":
            reasons.append(f"Scene {scene['id']} did not complete")
    accuracy = totals["correct"] / totals["expected"] if totals["expected"] else None
    if accuracy is None or accuracy < .8:
        reasons.append("Demo-critical localization below 80% or unmeasured")
    times = [r["round_trip_s"] for r in requests if r["status"] == "ok"]
    median = statistics.median(times) if times else None
    if median is None or median >= 3:
        reasons.append("Median stable-keyframe round trip is not under 3 seconds")
    reliable = len(requests) >= 10 and all(r["status"] == "ok" for r in requests)
    if not reliable:
        reasons.append("Ten consecutive requests without failure not demonstrated")
    return {"pass": not reasons, "reasons": reasons, "localization_accuracy": accuracy,
            "median_keyframe_s": median, "ten_consecutive_ok": reliable, **totals}
