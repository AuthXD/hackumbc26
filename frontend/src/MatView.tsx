import { Overlay } from "./Overlay";
import type { MatState, MatStatus, SceneState, Zone } from "./types";

export type Pt = [number, number];

export const LANDMARK_STEPS = [
  "top-left purple creature",
  "top-right frog",
  "bottom-right purple potion bottle",
  "bottom-left SteelSeries logo",
];

/** A stabilized picture older than this no longer counts as current. */
export const MAT_VIEW_STALE_MS = 2000;
/** How long to wait before trusting a calibration notice identical to the one shown before saving. */
export const CALIBRATION_REPLY_MS = 1500;

export const addPoint = (points: Pt[], p: Pt): Pt[] => (points.length >= 4 ? points : [...points, p]);
export const undoPoint = (points: Pt[]): Pt[] => points.slice(0, -1);

export type CalibrationReply = { ok: true } | { ok: false; error: string } | null;

const REJECTED = "Mat not calibrated: ";

/**
 * The backend answers mat_calibrate only through its (sticky) notice line, and frame updates sent
 * before the reply still carry the old notice. A changed notice is our reply; an unchanged one is
 * trusted only after CALIBRATION_REPLY_MS.
 */
export function calibrationReply(noticeAtSave: string, notice: string | undefined, waitedMs: number): CalibrationReply {
  const current = notice ?? "";
  const waited = waitedMs >= CALIBRATION_REPLY_MS;
  if (current === noticeAtSave && !waited) return null;
  if (current.startsWith("Mat calibrated")) return { ok: true };
  if (current.startsWith(REJECTED)) return { ok: false, error: current.slice(REJECTED.length) };
  if (current.startsWith("No camera picture yet") || current.startsWith("The simulator does not need")) {
    return { ok: false, error: current };
  }
  return waited ? { ok: false, error: "The server did not answer. Try Save calibration again." } : null;
}

export type MatDisplay = {
  view: "stabilized" | "raw";
  state: MatState | "stale";
  label: string; // chip text; "" hides the chip
  action: string; // direct instruction while tracking is unavailable
  reason: string; // the backend's own explanation
  blocksVerdict: boolean; // nothing may pass or fail until the mat is tracked again
  rawZones: boolean; // the snapshot's zones and objects are in raw-picture coordinates
};

const NO_MAT: MatDisplay = {
  view: "raw", state: "off", label: "", action: "", reason: "", blocksVerdict: false, rawZones: true,
};

export function matAction(state: MatState, message: string): string {
  if (state === "recalibrate" || /recalibrate/i.test(message)) return "Recalibrate mat";
  if (/hold (the phone )?still/i.test(message)) return "Hold the phone still";
  if (/waiting for the camera/i.test(message)) return "Waiting for the camera";
  return "Show all four corners";
}

/** What the camera panel may show for the mat right now. Only a fresh, tracked view is stabilized. */
export function matDisplay(mat: MatStatus | undefined, o: { cameraFeed: boolean; live: boolean; viewAgeMs: number }): MatDisplay {
  if (!o.cameraFeed || !mat || mat.state === "off") return NO_MAT;
  if (mat.state === "uncalibrated") return { ...NO_MAT, state: "uncalibrated", label: "Mat not calibrated", reason: mat.message };
  if (mat.state === "tracking" || mat.state === "unsteady") {
    if (!o.live || !mat.viewSeq || o.viewAgeMs > MAT_VIEW_STALE_MS) {
      return {
        view: "raw", state: "stale", label: "Mat view paused",
        action: o.live ? "Waiting for new camera frames" : "Reconnecting to the server",
        reason: o.live ? "No new stabilized mat picture for over 2 seconds." : "Not connected to the server.",
        blocksVerdict: true, rawZones: false,
      };
    }
    const steady = mat.state === "tracking";
    return {
      view: "stabilized", state: mat.state, label: steady ? "Mat tracking" : "Hold still…",
      action: steady ? "" : matAction(mat.state, mat.message), reason: mat.message, blocksVerdict: false, rawZones: false,
    };
  }
  return {
    view: "raw", state: mat.state, label: mat.state === "recalibrate" ? "Recalibrate mat" : "Mat tracking lost",
    action: matAction(mat.state, mat.message), reason: mat.message, blocksVerdict: true, rawZones: false,
  };
}

export function MatChip({ display }: { display: MatDisplay }) {
  if (!display.label) return null;
  return <span className={`tracker-chip mat-chip mat-${display.state}`} title={display.reason}>
    <i />{display.label}
  </span>;
}

/** Clicks the four landmarks (in order) on the raw camera picture. */
export function MatClickLayer({ points, onAdd }: { points: Pt[]; onAdd: (p: Pt) => void }) {
  return <div className="mat-click" data-testid="mat-click" onClick={(e) => {
    if (points.length >= 4) return;
    const r = e.currentTarget.getBoundingClientRect();
    onAdd([Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]);
  }}>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
      {points.length > 1 && <polyline points={[...points, ...(points.length === 4 ? [points[0]] : [])]
        .map(([x, y]) => `${x * 100},${y * 100}`).join(" ")} className="mat-click-line" />}
    </svg>
    {points.map(([x, y], i) => <span key={i} className="mat-click-dot" style={{ left: `${x * 100}%`, top: `${y * 100}%` }}>
      {i + 1}
    </span>)}
  </div>;
}

export function MatCalibrationBar({ points, error, saving, calibrated, onUndo, onRestart, onSave, onCancel, onRemove }: {
  points: Pt[];
  error: string | null;
  saving: boolean;
  calibrated: boolean;
  onUndo: () => void;
  onRestart: () => void;
  onSave: () => void;
  onCancel: () => void;
  onRemove: () => void;
}) {
  const next = points.length < 4 ? LANDMARK_STEPS[points.length] : null;
  return <div className="calibrate-bar mat-bar" role="group" aria-label="Calibrate mat">
    <strong className="mat-step">{next ? `Click ${points.length + 1} of 4: ${next}` : "All four landmarks selected. Save calibration."}</strong>
    <span className="hint">Order: 1 purple creature (top-left) → 2 frog (top-right) → 3 purple potion bottle
      (bottom-right) → 4 SteelSeries logo (bottom-left). Click the centre of each sticker and hold the camera still.</span>
    {error && <span className="semantic-error" role="alert">Calibration rejected: {error}</span>}
    <span className="mat-bar-actions">
      <button className="chip" disabled={!points.length || saving} onClick={onUndo}>Undo</button>
      <button className="chip" disabled={!points.length || saving} onClick={onRestart}>Restart</button>
      <button className="chip on" disabled={points.length !== 4 || saving} onClick={onSave}>
        {saving ? "Saving…" : "Save calibration"}
      </button>
      {calibrated && <button className="chip" disabled={saving} onClick={onRemove}>Remove saved calibration</button>}
      <button className="chip reset" onClick={onCancel}>Cancel</button>
    </span>
  </div>;
}

/** Shown over the raw feed whenever mat tracking is unavailable: what to do, and the backend's reason. */
export function MatUnavailable({ display, onRecalibrate }: { display: MatDisplay; onRecalibrate: () => void }) {
  return <div className="mat-unavailable" role="alert">
    <strong>{display.action}</strong>
    {display.reason.replace(/\.$/, "") !== display.action && <span>{display.reason}</span>}
    {display.action === "Recalibrate mat" && <button className="btn" onClick={onRecalibrate}>Recalibrate mat</button>}
  </div>;
}

/** The tracked outline over the raw picture (inset while the stabilized mat is shown). */
export function RawQuad({ corners, state }: { corners: [number, number][] | null; state: MatStatus["state"] }) {
  if (!corners) return null;
  return <svg className="raw-quad" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
    <polygon points={corners.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}
      className={state === "tracking" ? "quad-ok" : "quad-warn"} />
  </svg>;
}

/** Shades the outer landmark band of the stabilized mat: nothing there counts. */
export function MatBand({ band }: { band: number }) {
  const b = band * 100;
  return <svg className="mat-band" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
    <path d={`M0,0H100V100H0Z M${b},${b}V${100 - b}H${100 - b}V${b}Z`} fillRule="evenodd" />
    <rect x={b} y={b} width={100 - 2 * b} height={100 - 2 * b} className="mat-band-edge" />
  </svg>;
}

/** The stabilized top-down mat with the excluded band and the canonical zones and objects. */
export function StabilizedMat({ mat, scene, zones, activeZones, onError }: {
  mat: MatStatus;
  scene?: SceneState;
  zones: Zone[];
  activeZones?: string[];
  onError: () => void;
}) {
  return <>
    <img src={`/api/mat-view.jpg?v=${mat.viewSeq}`} alt="Stabilized top-down mat" className="camera-media mat-view"
      onError={onError} />
    <MatBand band={mat.band} />
    <Overlay scene={scene} zones={zones} activeZones={activeZones} />
  </>;
}
