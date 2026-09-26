import type { ServerUpdate } from "./types";

type SlotState = "empty" | "recording" | "learned" | "upcoming" | "current" | "error" | "done";

const STATE_LABEL: Record<SlotState, string> = {
  empty: "Not learned",
  recording: "Watching…",
  learned: "Learned",
  upcoming: "Up next",
  current: "Now",
  error: "Fix this",
  done: "Done",
};

export function Timeline({ u }: { u: ServerUpdate | null }) {
  const steps = u?.procedure?.steps ?? [];
  const target = u?.teach?.target ?? Math.max(4, steps.length);
  const practice = u?.mode === "practicing" ? u.practice : null;

  const slotState = (i: number): SlotState => {
    if (practice) {
      if (practice.completed.includes(i)) return "done";
      if (practice.status === "setup") return "upcoming";
      if (i === practice.expectedStepIndex) return practice.status === "error" ? "error" : "current";
      return "upcoming";
    }
    if (i < steps.length) return "learned";
    if (u?.mode === "teaching" && u.teach?.phase === "recording" && i === steps.length) return "recording";
    return "empty";
  };

  return (
    <section className="timeline" aria-label="Learned steps">
      {Array.from({ length: target }, (_, i) => {
        const step = steps[i];
        const state = slotState(i);
        const title = step?.aiDescription?.title ?? step?.description.title;
        const instruction = step?.aiDescription?.instruction ?? step?.description.instruction;
        return (
          <article key={i} className={`slot slot-${state}`}>
            <header className="slot-head">
              <span className="slot-num">{state === "done" ? "✓" : i + 1}</span>
              <span className="slot-state">{STATE_LABEL[state]}</span>
              {step?.aiDescription && <span className="slot-ai" title="Worded by Gemini">AI</span>}
            </header>
            {step ? (
              <>
                <h3 className="slot-title">{title}</h3>
                <p className="slot-text">{instruction}</p>
                {step.aiDescription && <p className="slot-rule">{step.description.instruction}</p>}
                {(step.beforeImage || step.afterImage) && (
                  <div className="slot-frames">
                    {step.beforeImage && <img src={step.beforeImage} alt={`Before step ${i + 1}`} />}
                    <span aria-hidden>→</span>
                    {step.afterImage && <img src={step.afterImage} alt={`After step ${i + 1}`} />}
                  </div>
                )}
              </>
            ) : (
              <p className="slot-text muted">
                {state === "recording" ? "Perform this step, then hands off." : "—"}
              </p>
            )}
          </article>
        );
      })}
    </section>
  );
}
