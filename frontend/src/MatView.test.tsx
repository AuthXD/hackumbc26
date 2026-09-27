import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  addPoint, CALIBRATION_REPLY_MS, calibrationReply, LANDMARK_STEPS, MAT_VIEW_STALE_MS, MatCalibrationBar, MatChip,
  MatClickLayer, MatLayout, matDisplay, MatUnavailable, RawQuad, StabilizedMat, undoPoint, type Pt,
} from "./MatView";
import { FramingGuide, PhoneLinkButton, PhoneMatCalibrationControls } from "./PhoneLink";
import { statusView, WORKER_LABEL } from "./StatusCard";
import type { MatStatus, ServerUpdate } from "./types";

const noop = () => {};

function mat(over: Partial<MatStatus> = {}): MatStatus {
  return {
    source: "phone", state: "tracking", message: "Mat tracking", calibrated: true, trustworthy: true,
    corners: [[0.2, 0.1], [0.8, 0.12], [0.78, 0.9], [0.22, 0.88]], viewSeq: 7, canonicalAspect: 0.41, band: 0.1,
    metrics: { inliers: 80, reprojError: 0.7, motion: 0.2 }, ...over,
  };
}

const live = { cameraFeed: true, live: true, viewAgeMs: 100 };

function bar(points: Pt[], error: string | null = null) {
  return renderToStaticMarkup(<MatCalibrationBar points={points} error={error} saving={false} calibrated={false}
    onUndo={noop} onRestart={noop} onSave={noop} onCancel={noop} onRemove={noop} />);
}

const clicks: Pt[] = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]];

describe("phone mat outline", () => {
  it("shows only the approximate guide before calibration", () => {
    const html = renderToStaticMarkup(<FramingGuide aspect={16 / 9} corners={null} />);
    expect(html).toContain('class="guide-mat"');
    expect(html).not.toContain('class="guide-tracked"');
  });

  it("replaces the approximate guide with the actual tracked corners", () => {
    const corners: [number, number][] = [[0.12, 0.18], [0.91, 0.22], [0.86, 0.81], [0.09, 0.77]];
    const html = renderToStaticMarkup(<FramingGuide aspect={16 / 9} corners={corners} />);
    expect(html).toContain('class="guide-tracked"');
    expect(html).not.toContain('class="guide-mat"');
    expect(html).toContain('points="12,18 91,22 86,81 9,77"');
  });
});

describe("mat calibration clicks", () => {
  it("asks for TL creature, TR frog, BR potion bottle, BL logo in that order", () => {
    expect(LANDMARK_STEPS).toEqual([
      "top-left purple creature", "top-right frog", "bottom-right purple potion bottle", "bottom-left SteelSeries logo",
    ]);
    let points: Pt[] = [];
    for (let i = 0; i < 4; i++) {
      expect(bar(points)).toContain(`Click ${i + 1} of 4: ${LANDMARK_STEPS[i]}`);
      points = addPoint(points, clicks[i]);
    }
    expect(points).toEqual(clicks);
    expect(bar(points)).toContain("All four landmarks selected");
    expect(addPoint(points, [0.5, 0.5])).toEqual(clicks); // a fifth click is ignored
  });

  it("enables Save only with four points", () => {
    expect(bar(clicks.slice(0, 3))).toMatch(/<button[^>]*disabled=""[^>]*>Save calibration/);
    expect(bar(clicks)).not.toMatch(/<button[^>]*disabled=""[^>]*>Save calibration/);
  });

  it("undoes the previous point and restarts from the creature", () => {
    const three = undoPoint(clicks);
    expect(three).toEqual(clicks.slice(0, 3));
    expect(bar(three)).toContain("Click 4 of 4: bottom-left SteelSeries logo");
    expect(bar(undoPoint(three))).toContain("Click 3 of 4: bottom-right purple potion bottle");
    expect(undoPoint([])).toEqual([]);
    expect(bar([])).toContain("Click 1 of 4: top-left purple creature");
    expect(bar([])).toMatch(/<button[^>]*disabled=""[^>]*>Undo/);
    expect(bar([])).toMatch(/<button[^>]*disabled=""[^>]*>Restart/);
  });
});

describe("calibration reply from the backend notice", () => {
  const wrongOrder = "Mat not calibrated: Wrong order. Select clockwise: creature, frog, potion, logo.";

  it("shows the backend's rejection reason", () => {
    expect(calibrationReply("Reset.", wrongOrder, 50)).toEqual({
      ok: false, error: "Wrong order. Select clockwise: creature, frog, potion, logo.",
    });
    expect(calibrationReply("", "No camera picture yet. Wait for the video, then calibrate.", 10)).toEqual({
      ok: false, error: "No camera picture yet. Wait for the video, then calibrate.",
    });
    const html = bar(clicks, "Wrong order. Select clockwise: creature, frog, potion, logo.");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Calibration rejected: Wrong order. Select clockwise: creature, frog, potion, logo.");
  });

  it("accepts success and ignores older notices until the reply arrives", () => {
    expect(calibrationReply("Reset.", "Mat calibrated. Hold still until it says Mat tracking.", 30)).toEqual({ ok: true });
    expect(calibrationReply("Reset.", "Reset.", 30)).toBeNull();
    expect(calibrationReply("Reset.", "Teaching: hold the starting layout still.", 30)).toBeNull();
    // The same text as before saving is trusted only after the wait.
    expect(calibrationReply(wrongOrder, wrongOrder, 30)).toBeNull();
    expect(calibrationReply(wrongOrder, wrongOrder, CALIBRATION_REPLY_MS)).toMatchObject({ ok: false });
    expect(calibrationReply("Reset.", "Reset.", CALIBRATION_REPLY_MS)).toMatchObject({ ok: false });
  });
});

describe("mat display", () => {
  it("shows the stabilized mat only while tracking", () => {
    const d = matDisplay(mat(), live);
    expect(d).toMatchObject({ view: "stabilized", label: "Mat tracking", blocksVerdict: false, rawZones: false });
    expect(renderToStaticMarkup(<MatChip display={d} />)).toContain("Mat tracking");

    const html = renderToStaticMarkup(<StabilizedMat mat={mat()} zones={[]} onError={noop} />);
    expect(html).toContain('src="/api/mat-view.jpg?v=7"');
    expect(html).toContain('class="mat-band"');
    expect(renderToStaticMarkup(<RawQuad corners={mat().corners} state="tracking" />)).toContain("quad-ok");
  });

  it("keeps unsteady frames stabilized but not trusted", () => {
    const d = matDisplay(mat({ state: "unsteady", trustworthy: false, message: "Hold the phone still." }), live);
    expect(d).toMatchObject({ view: "stabilized", label: "Hold still…", action: "Hold the phone still", blocksVerdict: false });
  });

  it("falls back to the raw feed with the backend's reason when tracking is lost", () => {
    const cases: [Partial<MatStatus>, string][] = [
      [{ state: "lost", message: "Mat tracking lost. Show all four corners." }, "Show all four corners"],
      [{ state: "lost", message: "The camera moved suddenly. Hold the phone still." }, "Hold the phone still"],
      [{ state: "recalibrate", message: "Camera view changed. Recalibrate mat." }, "Recalibrate mat"],
      [{ state: "recalibrate", message: "Saved mat calibration is unreadable (ValueError). Recalibrate mat." }, "Recalibrate mat"],
    ];
    for (const [over, action] of cases) {
      const d = matDisplay(mat({ ...over, corners: null, trustworthy: false }), live);
      expect(d).toMatchObject({ view: "raw", action, reason: over.message, blocksVerdict: true, rawZones: false });
      const html = renderToStaticMarkup(<MatUnavailable display={d} onRecalibrate={noop} />);
      expect(html).toContain(action);
      expect(html).toContain(over.message!);
    }
  });

  it("never shows an old stabilized picture as current", () => {
    const stale = matDisplay(mat(), { ...live, viewAgeMs: MAT_VIEW_STALE_MS + 1 });
    expect(stale).toMatchObject({ view: "raw", state: "stale", blocksVerdict: true });
    expect(matDisplay(mat(), { ...live, live: false })).toMatchObject({ view: "raw", blocksVerdict: true });
    expect(matDisplay(mat({ viewSeq: 0 }), live).view).toBe("raw");
  });

  it("bypasses the mat for the simulator and uncalibrated cameras", () => {
    const sim = matDisplay(mat(), { ...live, cameraFeed: false });
    expect(sim).toMatchObject({ view: "raw", label: "", blocksVerdict: false, rawZones: true });
    expect(renderToStaticMarkup(<MatChip display={sim} />)).toBe("");
    expect(matDisplay(mat({ source: "sim", state: "off", message: "" }), live).label).toBe("");
    const bare = matDisplay(mat({ state: "uncalibrated", calibrated: false, message: "Mat not calibrated." }), live);
    expect(bare).toMatchObject({ view: "raw", label: "Mat not calibrated", blocksVerdict: false, rawZones: true });
  });
});

describe("status card with mat problems", () => {
  const practice: ServerUpdate = {
    type: "update", mode: "practicing", workspace: "procedure",
    procedure: { detectorKind: "color", trackedIds: ["red"], steps: [] },
    detector: { kind: "color", betaEnabled: false, labels: [], workerState: "unloaded", scanState: "idle", message: "",
      canScan: false, switchLocked: false, procedureKind: "color" },
    practice: { expectedStepIndex: 1, status: "step_complete", errorType: null, headline: "Step 1 correct",
      expectedDescription: "Move red to Zone B.", observedDescription: "red in Zone A", fixHint: "", completed: [0] },
  };

  it("puts a blocking mat problem first and keeps the procedure feedback", () => {
    const lost = matDisplay(mat({ state: "lost", message: "Mat tracking lost. Show all four corners.", corners: null }), live);
    const view = statusView(practice, lost);
    expect(view.tone).toBe("error");
    expect(view.headline).toBe("Show all four corners");
    expect(view.observed).toBe("Mat tracking lost. Show all four corners.");
    expect(view.expected).toBe("Move red to Zone B.");
    expect(view.paused).toBe("Step 1 correct");
    expect(view.eyebrow).toContain("Practice");
  });

  it("does not let an old setup verdict look current", () => {
    const setup = { ...practice, mode: "idle" as const, workspace: "setup" as const, practice: null, setup: {
      available: true, storage: { provider: "local" as const, state: "ready" as const, message: "" }, setups: [],
      selected: { id: "s", name: "Desk", objects: [{ label: "cup", zone: "A" }], createdAt: 0 }, canCapture: false,
      canCheck: false, checking: false, resultStale: false, repositoryErrors: [], resultHistory: null,
      result: { setupId: "s", setupName: "Desk", status: "complete" as const, correct: [{ label: "cup", zone: "A" }],
        missing: [], unexpected: [], misplaced: [], checkedAt: 0 },
      history: { provider: "local" as const, state: "disabled" as const, message: "", recent: [], summary: null, errors: [],
        writer: { queued: 0, capacity: 0, saved: 0, failed: 0, dropped: 0, lastProblem: "" } },
    } };
    expect(statusView(setup, matDisplay(mat(), live)).headline).toBe("Complete and correctly arranged");
    const lost = statusView(setup, matDisplay(mat({ state: "recalibrate", message: "Camera view changed. Recalibrate mat." }), live));
    expect(lost.headline).toBe("Recalibrate mat");
    expect(lost.tone).toBe("error");
  });

  it("leaves tracking, simulator and uncalibrated feedback alone", () => {
    const base = statusView(practice);
    expect(statusView(practice, matDisplay(mat(), live))).toEqual(base);
    expect(statusView(practice, matDisplay(mat(), { ...live, cameraFeed: false }))).toEqual(base);
    expect(statusView(practice, matDisplay(mat({ state: "uncalibrated", message: "Mat not calibrated." }), live))).toEqual(base);
    const unsteady = statusView(practice, matDisplay(mat({ state: "unsteady", message: "Settling… hold still." }), live));
    expect(unsteady).toMatchObject({ headline: base.headline, tone: "neutral", observed: "Settling… hold still." });
  });
});

describe("semantic model readiness labels", () => {
  it("uses plain loading and ready text, never raw worker tokens", () => {
    expect(WORKER_LABEL).toEqual({
      unloaded: "Model not loaded", loading: "Loading model", ready: "Model ready", error: "Model error",
    });
    const base: ServerUpdate = {
      type: "update", mode: "idle",
      detector: { kind: "semantic", betaEnabled: true, labels: ["a", "b"], workerState: "loading", scanState: "idle",
        message: "", canScan: false, switchLocked: false, procedureKind: null },
    };
    expect(statusView(base).headline).toBe("Loading model");
    expect(statusView({ ...base, detector: { ...base.detector!, workerState: "ready" } }).headline).toBe("Scan the settled table");
    const broken = statusView({ ...base, detector: { ...base.detector!, workerState: "error",
      workerMessage: "The object detector is unavailable. Try again, or use Color mode." } });
    expect(broken.headline).toBe("Model error");
    expect(broken.fix).toMatch(/Retry loading/);
    expect(broken.observed).toContain("unavailable");
  });
});

describe("landscape mat layout", () => {
  it("keeps the raw preview and the tracking status off the canonical picture", () => {
    const html = renderToStaticMarkup(<MatLayout stabilized
      picture={<img alt="Stabilized top-down mat" />}
      raw={<div className="raw-layer" />}
      status={<MatChip display={matDisplay(mat(), live)} />} />);
    expect(html).toContain('data-layout="landscape"');
    expect(html).not.toContain("inset");
    expect(html).toContain("Raw phone view");
    const picture = html.slice(html.indexOf('data-testid="mat-picture"'), html.indexOf('data-testid="mat-status-bar"'));
    expect(picture).toContain("Stabilized top-down mat");
    expect(picture).not.toContain("Mat tracking");
    expect(picture).not.toContain("raw-preview");
    expect(html.indexOf('data-testid="mat-status-bar"')).toBeLessThan(html.indexOf('data-testid="raw-preview"'));
  });

  it("puts Hold still under the picture, and calibration clicks stay on the raw view", () => {
    const hold = renderToStaticMarkup(<MatLayout stabilized
      picture={<img alt="Stabilized top-down mat" />}
      raw={<div className="raw-layer" />}
      status={<MatChip display={matDisplay(mat({ state: "unsteady", message: "Hold the phone still." }), live)} />} />);
    const picture = hold.slice(hold.indexOf('data-testid="mat-picture"'), hold.indexOf('data-testid="mat-status-bar"'));
    expect(picture).not.toContain("Hold still");
    expect(hold).toContain("Hold still");

    const raw = renderToStaticMarkup(<MatLayout stabilized={false} picture={null}
      raw={<div className="raw-layer"><MatClickLayer points={[]} onAdd={() => undefined} /></div>}
      status={null} />);
    expect(raw).toContain('data-layout="raw"');
    expect(raw).toContain('data-testid="mat-click"');
    expect(raw).not.toContain("raw-preview");
  });

});

describe("phone state labels", () => {
  it("still names each phone state", () => {
    expect(renderToStaticMarkup(<PhoneLinkButton phone="disconnected" />)).toContain("Connect phone");
    expect(renderToStaticMarkup(<PhoneLinkButton phone="connected" />)).toContain("Phone connected — no video yet");
    expect(renderToStaticMarkup(<PhoneLinkButton phone="streaming" />)).toContain("Phone streaming");
    expect(renderToStaticMarkup(<PhoneLinkButton phone="error" />)).toContain("Phone camera error");
  });

  it("puts the complete four-corner calibration workflow on the phone", () => {
    const controls = (points: Pt[], error: string | null = null, saving = false) => renderToStaticMarkup(
      <PhoneMatCalibrationControls points={points} error={error} saving={saving}
        onUndo={noop} onRestart={noop} onSave={noop} onCancel={noop} />,
    );
    expect(controls([])).toContain("Tap 1 of 4: top-left purple creature");
    expect(controls(clicks.slice(0, 3))).toContain("Tap 4 of 4: bottom-left SteelSeries logo");
    expect(controls(clicks)).toContain("All four corners selected");
    expect(controls(clicks)).not.toMatch(/<button[^>]*disabled=""[^>]*>Save calibration/);
    expect(controls(clicks, "Wrong order")).toContain("Calibration rejected: Wrong order");
    expect(controls(clicks, null, true)).toContain("Saving…");
  });
});
