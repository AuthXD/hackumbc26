import type { MatDisplay } from "./MatView";
import type { DetectorState, ServerUpdate } from "./types";

export const WORKER_LABEL: Record<DetectorState["workerState"], string> = {
  unloaded: "Local model not loaded",
  loading: "Loading local model",
  ready: "Local model ready",
  error: "Local model error",
};

export type Tone = "neutral" | "success" | "warning" | "error";

export type View = {
  tone: Tone;
  eyebrow: string;
  headline: string;
  expected: string;
  observed: string;
  fix?: string;
  paused?: string; // the procedure feedback a mat problem is holding back
};

const MAT_FIX: Record<string, string> = {
  "Recalibrate mat": "Press Recalibrate mat and click the four corner stickers again.",
  "Show all four corners": "Keep hands and objects off the corner stickers and fit the whole mat in view.",
  "Hold the phone still": "Rest the camera or hold it steady until it says Mat tracking.",
};

/** Mat tracking comes first when it prevents a valid verdict; the procedure's own feedback stays visible. */
function withMat(view: View, mat: MatDisplay | undefined): View {
  if (!mat) return view;
  if (mat.blocksVerdict) {
    return {
      tone: mat.state === "stale" ? "warning" : "error",
      eyebrow: `${view.eyebrow} · ${mat.label}`,
      headline: mat.action,
      expected: view.expected,
      observed: mat.reason,
      fix: `${MAT_FIX[mat.action] ?? ""} Nothing is checked until the mat is tracked again.`.trim(),
      paused: view.headline,
    };
  }
  if (mat.state === "unsteady") {
    return { ...view, tone: view.tone === "success" ? "neutral" : view.tone, observed: mat.reason };
  }
  return view;
}

function sceneSummary(u: ServerUpdate): string {
  const objs = u.scene?.objects.filter((o) => o.visible) ?? [];
  if (!objs.length) return u.detector?.kind === "semantic" ? "No current semantic scan." : "No colored objects in view.";
  return objs.map((o) => `${o.id} ${o.stackedOn ? `on ${o.stackedOn}` : o.zone ? `in ${o.zone}` : "outside"}`).join(" · ");
}

const zoneText = (zone: string | null) => (zone ? `Zone ${zone}` : "outside the zones");

/** Setup Check verdicts come only from the backend's deterministic comparison. */
function setupView(u: ServerUpdate): View {
  const setup = u.setup!;
  const d = u.detector;
  const r = setup.result;
  const eyebrow = `Setup Check${setup.selected ? ` · ${setup.selected.name}` : ""}`;
  const expected = setup.selected
    ? setup.selected.objects.map((o) => `${o.label} in ${zoneText(o.zone)}`).join(" · ")
    : "Scan the organized table and capture it, or choose a saved setup.";
  if (setup.checking) {
    return { tone: "neutral", eyebrow, headline: "Checking setup…", expected, observed: d?.message ?? "" };
  }
  if (r && !setup.resultStale) {
    if (r.status === "complete") {
      return { tone: "success", eyebrow, headline: "Complete and correctly arranged", expected,
        observed: `All ${r.correct.length} expected objects are present and in place.` };
    }
    const found = [
      ...r.missing.map((o) => `${o.label} is missing`),
      ...r.unexpected.map((o) => `${o.label} should not be here (${zoneText(o.zone)})`),
      ...r.misplaced.map((m) => `${m.label} is in ${zoneText(m.observedZone)}`),
    ];
    const fix = [
      ...r.missing.map((o) => `Add ${o.label} to ${zoneText(o.zone)}.`),
      ...r.unexpected.map((o) => `Remove ${o.label}.`),
      ...r.misplaced.map((m) => `Move ${m.label} to ${zoneText(m.expectedZone)}.`),
    ];
    return { tone: "error", eyebrow, headline: "Setup needs attention", expected, observed: found.join(" · "),
      fix: fix.join(" ") };
  }
  if (d && (d.scanState === "error" || d.scanState === "ambiguous")) {
    return { tone: d.scanState === "error" ? "error" : "warning", eyebrow, headline: "No verdict — scan failed",
      expected, observed: d.message, fix: "Separate the objects, keep the table still, and try again." };
  }
  return {
    tone: "neutral",
    eyebrow,
    headline: r ? "Table changed — check again" : setup.selected ? "Ready to check" : "Capture an organized setup",
    expected,
    observed: d?.message ?? "",
  };
}

export function statusView(u: ServerUpdate | null, mat?: MatDisplay): View {
  const view = procedureView(u);
  return u?.mode ? withMat(view, mat) : view;
}

function procedureView(u: ServerUpdate | null): View {
  if (!u || !u.mode) {
    return { tone: "neutral", eyebrow: "Connecting", headline: "Starting up…", expected: "", observed: "" };
  }
  const tracker = u.tracker;
  if (u.workspace === "setup" && u.setup) return setupView(u);
  if (u.detector?.kind === "semantic" && (u.detector.scanState !== "valid" || u.detector.workerState !== "ready")) {
    const d = u.detector;
    const loading = d.workerState === "loading" || d.workerState === "unloaded";
    const broken = d.workerState === "error";
    return {
      tone: broken || d.scanState === "error" ? "error" : "warning",
      eyebrow: "Local LocateAnything · Waiting",
      headline: loading ? WORKER_LABEL[d.workerState]
        : broken ? WORKER_LABEL.error
        : d.scanState === "scanning" ? "Scanning objects…"
        : d.scanState === "ambiguous" ? "Separate the objects and rescan"
        : d.scanState === "error" ? "Object detector unavailable" : "Scan the settled table",
      expected: "Keep all requested objects separated and fully visible. Scan after each move.",
      observed: broken ? (d.workerMessage || d.message) : d.message,
      fix: broken ? "Retry loading the model, or select Color to keep using the color detector."
        : d.scanState === "error" ? "Retry Scan Objects, or pause the procedure and explicitly switch to Color mode." : undefined,
    };
  }
  if (u.procedure && u.detector && u.procedure.detectorKind !== u.detector.kind) {
    return { tone: "warning", eyebrow: "Saved procedure paused", headline: "Procedure preserved",
      expected: `Switch to ${u.procedure.detectorKind === "semantic" ? "Semantic Objects" : "Color"} mode to practice it.`,
      observed: "Starting a new teaching session will replace the saved procedure." };
  }
  const activity =
    tracker?.status === "moving"
      ? "Hands moving — waiting for the table to settle."
      : tracker?.status === "occluded"
        ? `Can't see: ${tracker.missing.join(", ")}. Waiting…`
        : null;

  if (u.mode === "teaching" && u.teach) {
    const t = u.teach;
    const lastStep = u.procedure?.steps.at(-1);
    if (t.phase === "capturing_initial") {
      return {
        tone: "warning",
        eyebrow: "Teach",
        headline: "Hold the starting layout still",
        expected: "Place the objects in their starting zones, then take your hands away.",
        observed: activity ?? sceneSummary(u),
      };
    }
    return {
      tone: lastStep ? "success" : "neutral",
      eyebrow: `Teach · ${t.stepsRecorded} of ${t.target} learned`,
      headline: `Show step ${t.stepsRecorded + 1}`,
      expected: "Do one step, then take your hands away so I can see the result.",
      observed: activity ?? (lastStep ? `Learned step ${lastStep.index + 1}: ${lastStep.description.instruction}` : "Starting layout captured."),
    };
  }

  if (u.mode === "practicing" && u.practice) {
    const p = u.practice;
    const tone: Tone =
      p.status === "error" ? "error" : p.status === "setup" ? "warning" : p.status === "waiting" ? "neutral" : "success";
    return {
      tone,
      eyebrow: `Practice · ${p.completed.length} of ${u.procedure?.steps.length ?? 0} done`,
      headline: p.headline,
      expected: p.status === "setup" ? `Starting layout: ${p.expectedDescription}` : p.expectedDescription,
      // Live "hands moving" feedback while waiting; keep verdicts (error/complete) on screen.
      observed:
        activity && (p.status === "waiting" || p.status === "setup")
          ? activity
          : p.observedDescription || (activity ?? sceneSummary(u)),
      fix: p.fixHint || undefined,
    };
  }

  if (u.procedure?.steps.length) {
    const library = u.library;
    const loaded = library?.procedures.find((p) => p.id === library.loadedId);
    if (library?.draft.available && !library.draft.saved) {
      // A freshly taught procedure: saving it is the next step, but Practice works right away too.
      return {
        tone: "success",
        eyebrow: `Ready · ${u.procedure.steps.length} steps learned`,
        headline: "Name and save this procedure",
        expected: "Type a name in Procedure Library below and press Save Procedure — or press Practice now.",
        observed: sceneSummary(u),
      };
    }
    return {
      tone: "success",
      eyebrow: loaded ? `Ready · "${loaded.name}"` : "Ready",
      headline: `Learned ${u.procedure.steps.length} steps — press Practice`,
      expected: "Hand the table to the next person and press Practice.",
      observed: sceneSummary(u),
    };
  }
  return {
    tone: "neutral",
    eyebrow: "Ready",
    headline: "Press Teach to begin",
    expected: u.detector?.kind === "semantic" ? "Put the requested objects in their starting zones. Scan after pressing Teach."
      : "Put the colored objects in their starting zones.",
    observed: sceneSummary(u),
  };
}

export function StatusCard({ view }: { view: View }) {
  return (
    <section className={`status tone-${view.tone}`} aria-live="polite">
      <div className="status-eyebrow">{view.eyebrow}</div>
      <h2 className="status-headline" key={view.headline}>
        {view.headline}
      </h2>
      <dl className="status-rows">
        {view.expected && (
          <div className="status-row">
            <dt>Expected</dt>
            <dd>{view.expected}</dd>
          </div>
        )}
        {view.observed && (
          <div className="status-row">
            <dt>Observed</dt>
            <dd>{view.observed}</dd>
          </div>
        )}
        {view.fix && (
          <div className="status-row fix">
            <dt>Fix</dt>
            <dd>{view.fix}</dd>
          </div>
        )}
        {view.paused && (
          <div className="status-row paused">
            <dt>Paused</dt>
            <dd>{view.paused}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
