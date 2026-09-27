import { useState } from "react";

export type DemoStep = {
  object: string;
  from: string;
  to: string;
};

export type DemoScenario = {
  id: string;
  eyebrow: string;
  title: string;
  purpose: string;
  disclaimer?: string;
  steps: readonly DemoStep[];
};

export type DemoState =
  | { kind: "running"; completed: number }
  | { kind: "mistake"; completed: number; selected: number }
  | { kind: "complete" };

export const DEMOS: readonly DemoScenario[] = [
  {
    id: "toolbox",
    eyebrow: "Demo 01 · Toolbox handoff",
    title: "Return every tool in order",
    purpose: "Show how one demonstrated cleanup becomes repeatable coaching for the next technician.",
    steps: [
      { object: "Screwdriver", from: "Work surface", to: "Upper rack" },
      { object: "Pliers", from: "Work surface", to: "Center slot" },
      { object: "Tape measure", from: "Work surface", to: "Lower drawer" },
    ],
  },
  {
    id: "clinical",
    eyebrow: "Demo 02 · Clinical training",
    title: "Prepare a simulated training tray",
    purpose: "Show ordered practice, immediate correction, and recovery without changing the learned procedure.",
    disclaimer: "Training demonstration only. TeachBack does not provide medical judgment or certify sterility.",
    steps: [
      { object: "Hand sanitizer", from: "Supply area", to: "Prep position" },
      { object: "Training gloves", from: "Supply area", to: "Left tray" },
      { object: "Gauze pack", from: "Supply area", to: "Right tray" },
    ],
  },
];

export function selectDemoStep(state: DemoState, selected: number, stepCount: number): DemoState {
  if (state.kind === "complete") return state;
  const completed = state.completed;
  if (selected !== completed) return { kind: "mistake", completed, selected };
  return completed + 1 === stepCount ? { kind: "complete" } : { kind: "running", completed: completed + 1 };
}

function completedCount(state: DemoState, stepCount: number): number {
  return state.kind === "complete" ? stepCount : state.completed;
}

function DemoCard({ scenario }: { scenario: DemoScenario }) {
  const [state, setState] = useState<DemoState>({ kind: "running", completed: 0 });
  const completed = completedCount(state, scenario.steps.length);
  const expected = scenario.steps[completed];
  const selected = state.kind === "mistake" ? scenario.steps[state.selected] : undefined;

  return (
    <article className={`demo-card demo-${state.kind}`}>
      <header className="demo-head">
        <p className="demo-eyebrow">{scenario.eyebrow}</p>
        <h2>{scenario.title}</h2>
        <p>{scenario.purpose}</p>
        {scenario.disclaimer && <p className="demo-disclaimer">{scenario.disclaimer}</p>}
      </header>

      <div className="demo-workspace">
        <section className="demo-actions" aria-label={`${scenario.title} actions`}>
          <p className="demo-label">Objects in view · choose the next move</p>
          {scenario.steps.map((step, index) => {
            const done = index < completed;
            return (
              <button
                type="button"
                className={done ? "demo-object done" : index === completed ? "demo-object expected" : "demo-object"}
                disabled={done || state.kind === "complete"}
                onClick={() => setState((current) => selectDemoStep(current, index, scenario.steps.length))}
                key={step.object}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{step.object}</strong>
                <small>{done ? `Placed · ${step.to}` : `${step.from} → ${step.to}`}</small>
              </button>
            );
          })}
        </section>

        <section className="demo-verdict" aria-live="polite">
          <p className="demo-label">TeachBack · deterministic result</p>
          <h3>
            {state.kind === "complete"
              ? "Procedure complete"
              : state.kind === "mistake"
                ? "Wrong step caught"
                : `Step ${completed + 1} ready`}
          </h3>
          <p>
            {state.kind === "complete"
              ? "Every action matched the demonstrated order."
              : state.kind === "mistake"
                ? `${selected?.object} was selected, but the learned sequence expects ${expected?.object}. Correct it to continue.`
                : `Move ${expected?.object} from ${expected?.from} to ${expected?.to}.`}
          </p>
          <ol className="demo-sequence">
            {scenario.steps.map((step, index) => (
              <li className={index < completed ? "done" : index === completed ? "current" : ""} key={step.object}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <span>{step.object}</span>
                <span>{step.to}</span>
              </li>
            ))}
          </ol>
          <button type="button" className="ghost" onClick={() => setState({ kind: "running", completed: 0 })}>
            Reset demo
          </button>
        </section>
      </div>
    </article>
  );
}

export function DemoLab() {
  return (
    <main className="demo-lab">
      <header className="demo-lab-intro">
        <div>
          <p className="demo-eyebrow">Built-in presenter mode · no camera or model required</p>
          <h1>Demo TeachBack in under a minute.</h1>
        </div>
        <p>
          Click the correct objects to complete either procedure—or click a later object first to show how TeachBack
          stops an out-of-order action and coaches the correction.
        </p>
      </header>
      <div className="demo-grid">
        {DEMOS.map((scenario) => <DemoCard scenario={scenario} key={scenario.id} />)}
      </div>
      <p className="demo-isolation">Demo Lab is isolated from the camera, saved procedures, Tiger Data, and mat calibration.</p>
    </main>
  );
}
