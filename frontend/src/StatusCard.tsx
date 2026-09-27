import type { ServerUpdate } from "./types";

export type Tone = "neutral" | "success" | "warning" | "error";

type View = {
  tone: Tone;
  eyebrow: string;
  headline: string;
  expected: string;
  observed: string;
  fix?: string;
};

function sceneSummary(u: ServerUpdate): string {
  const objs = u.scene?.objects.filter((o) => o.visible) ?? [];
  if (!objs.length) return u.detector?.kind === "semantic" ? "No current semantic scan." : "No colored objects in view.";
  return objs.map((o) => `${o.id} ${o.stackedOn ? `on ${o.stackedOn}` : o.zone ? `in ${o.zone}` : "outside"}`).join(" · ");
}

export function statusView(u: ServerUpdate | null): View {
  if (!u || !u.mode) {
    return { tone: "neutral", eyebrow: "Connecting", headline: "Starting up…", expected: "", observed: "" };
  }
  const tracker = u.tracker;
  if (u.detector?.kind === "semantic" && u.detector.scanState !== "valid") {
    const d = u.detector;
    return {
      tone: d.scanState === "error" ? "error" : "warning",
      eyebrow: "Semantic Objects · Beta · Waiting",
      headline: d.workerState === "loading" ? "Loading object detector…"
        : d.scanState === "scanning" ? "Scanning objects…"
        : d.scanState === "ambiguous" ? "Separate the objects and rescan"
        : d.scanState === "error" ? "Object detector unavailable" : "Scan the settled table",
      expected: "Keep all requested objects separated and fully visible. Scan after each move.",
      observed: d.message,
      fix: d.scanState === "error" ? "Retry Scan Objects, or pause the procedure and explicitly switch to Color mode." : undefined,
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
    return {
      tone: "success",
      eyebrow: "Ready",
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
      </dl>
    </section>
  );
}
