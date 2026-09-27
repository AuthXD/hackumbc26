import { useState, type DragEvent } from "react";

export type DemoStep = {
  object: string;
  from: string;
  to: string;
  image: string;
  imageSource: string;
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
  | { kind: "mistake"; completed: number; selected: number; destination: string }
  | { kind: "complete" };

export const DEMOS: readonly DemoScenario[] = [
  {
    id: "toolbox",
    eyebrow: "Demo 01 · Toolbox handoff",
    title: "Return every tool in order",
    purpose: "Drag each photographed tool into the registered storage layout.",
    steps: [
      { object: "Screwdriver", from: "Work surface", to: "Upper rack", image: "/demo-objects/screwdriver.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Screwdriver_(AM_2015.34.35-4).jpg" },
      { object: "Pliers", from: "Work surface", to: "Center slot", image: "/demo-objects/pliers.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Pliers_tool.jpg" },
      { object: "Tape measure", from: "Work surface", to: "Lower drawer", image: "/demo-objects/tape-measure.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Tape_measure_No_1.jpg" },
    ],
  },
  {
    id: "clinical",
    eyebrow: "Demo 02 · Clinical training",
    title: "Prepare a simulated training tray",
    purpose: "Place the training supplies into the registered tray positions in order.",
    disclaimer: "Training demonstration only. TeachBack does not provide medical judgment or certify sterility.",
    steps: [
      { object: "Hand sanitizer", from: "Supply area", to: "Prep position", image: "/demo-objects/sanitizer.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Purell_hand_sanitizer_gel_in_bottle_(8487014501).jpg" },
      { object: "Training gloves", from: "Supply area", to: "Left tray", image: "/demo-objects/gloves.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Gants_d%27examen_nitriles_non_poudr%C3%A9s_rob%C3%A9_m%C3%A9dical.jpg" },
      { object: "Gauze pack", from: "Supply area", to: "Right tray", image: "/demo-objects/gauze.jpg", imageSource: "https://commons.wikimedia.org/wiki/File:Gauze_(AM_614759-3).jpg" },
    ],
  },
];

export function selectDemoStep(state: DemoState, selected: number, stepCount: number): DemoState {
  if (state.kind === "complete") return state;
  const completed = state.completed;
  if (selected !== completed) return { kind: "mistake", completed, selected, destination: "" };
  return completed + 1 === stepCount ? { kind: "complete" } : { kind: "running", completed: completed + 1 };
}

export function placeDemoObject(state: DemoState, selected: number, destination: string, scenario: DemoScenario): DemoState {
  if (state.kind === "complete") return state;
  const completed = state.completed;
  if (selected !== completed || destination !== scenario.steps[selected]?.to) {
    return { kind: "mistake", completed, selected, destination };
  }
  return completed + 1 === scenario.steps.length ? { kind: "complete" } : { kind: "running", completed: completed + 1 };
}

function completedCount(state: DemoState, stepCount: number): number {
  return state.kind === "complete" ? stepCount : state.completed;
}

function DemoCard({ scenario, initiallyOpen }: { scenario: DemoScenario; initiallyOpen: boolean }) {
  const [state, setState] = useState<DemoState>({ kind: "running", completed: 0 });
  const [selectedObject, setSelectedObject] = useState<number | null>(null);
  const completed = completedCount(state, scenario.steps.length);
  const expected = scenario.steps[completed];
  const attempted = state.kind === "mistake" ? scenario.steps[state.selected] : undefined;

  const place = (selected: number, destination: string) => {
    setState((current) => placeDemoObject(current, selected, destination, scenario));
    setSelectedObject(null);
  };

  const handleDrop = (event: DragEvent<HTMLButtonElement>, destination: string) => {
    event.preventDefault();
    const selected = Number.parseInt(event.dataTransfer.getData("text/plain"), 10);
    if (Number.isInteger(selected) && selected >= 0 && selected < scenario.steps.length) place(selected, destination);
  };

  const reset = () => {
    setState({ kind: "running", completed: 0 });
    setSelectedObject(null);
  };

  return (
    <details className={`demo-card demo-${state.kind}`} open={initiallyOpen}>
      <summary className="demo-head">
        <span>
          <span className="demo-eyebrow">{scenario.eyebrow}</span>
          <strong>{scenario.title}</strong>
        </span>
        <span className="demo-progress">{completed}/{scenario.steps.length} placed</span>
      </summary>

      <div className="demo-card-body">
        <p className="demo-purpose">{scenario.purpose}</p>
        {scenario.disclaimer && <p className="demo-disclaimer">{scenario.disclaimer}</p>}

        <div className="demo-tabletop">
          <section className="demo-staging" aria-label={`${scenario.title} objects`}>
            <p className="demo-label">Objects in view</p>
            <div className="demo-photo-row">
              {scenario.steps.map((step, index) => {
                if (index < completed) return null;
                const selected = selectedObject === index;
                return (
                  <button
                    type="button"
                    className={`demo-photo-object${selected ? " selected" : ""}`}
                    draggable={state.kind !== "complete"}
                    onDragStart={(event) => event.dataTransfer.setData("text/plain", String(index))}
                    onClick={() => setSelectedObject(selected ? null : index)}
                    key={step.object}
                    aria-pressed={selected}
                  >
                    <img src={step.image} alt="" draggable={false} />
                    <span>{step.object}</span>
                  </button>
                );
              })}
              {state.kind === "complete" && <p className="demo-cleared">Work surface cleared</p>}
            </div>
            <p className="demo-drag-hint">Drag a photo into its registered zone—or select it, then select a zone.</p>
          </section>

          <section className="demo-zone-board" aria-label="Registered target setup">
            <p className="demo-label">Registered setup</p>
            <div className="demo-zones">
              {scenario.steps.map((step, index) => {
                const placed = index < completed;
                return (
                  <button
                    type="button"
                    className={`demo-drop-zone${placed ? " placed" : ""}`}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => handleDrop(event, step.to)}
                    onClick={() => selectedObject !== null && place(selectedObject, step.to)}
                    key={step.to}
                  >
                    <span className="demo-zone-name">Zone {String.fromCharCode(65 + index)} · {step.to}</span>
                    {placed ? <img src={step.image} alt={step.object} /> : <span className="demo-zone-number">{String(index + 1).padStart(2, "0")}</span>}
                  </button>
                );
              })}
            </div>
          </section>
        </div>

        <section className="demo-verdict" aria-live="polite">
          <div>
            <p className="demo-label">TeachBack · deterministic result</p>
            <h3>{state.kind === "complete" ? "Procedure complete" : state.kind === "mistake" ? "Move stopped" : `Step ${completed + 1} ready`}</h3>
            <p>
              {state.kind === "complete"
                ? "Every object matched the registered setup and demonstrated order."
                : state.kind === "mistake"
                  ? attempted && state.selected !== completed
                    ? `${attempted.object} moved out of order. Move ${expected?.object} next.`
                    : `${attempted?.object} belongs in ${attempted?.to}, not ${state.destination}.`
                  : `Move ${expected?.object} from ${expected?.from} to ${expected?.to}.`}
            </p>
          </div>
          <button type="button" className="ghost" onClick={reset}>Reset demo</button>
        </section>

        <p className="demo-photo-credit">
          Object photographs from Wikimedia Commons: {scenario.steps.map((step, index) => <span key={step.object}><a href={step.imageSource} target="_blank" rel="noreferrer">{step.object}</a>{index < scenario.steps.length - 1 ? ", " : ""}</span>)}.
        </p>
      </div>
    </details>
  );
}

export function DemoLab() {
  return (
    <main className="demo-lab">
      <header className="demo-lab-intro">
        <div>
          <p className="demo-eyebrow">Built-in presenter mode · no camera or model required</p>
          <h1>Drag through two complete procedures.</h1>
        </div>
        <p>Open either demo, move the photographed objects into the registered setup, and deliberately make one wrong move to show TeachBack stopping it.</p>
      </header>
      <div className="demo-grid">
        {DEMOS.map((scenario, index) => <DemoCard scenario={scenario} initiallyOpen={index === 0} key={scenario.id} />)}
      </div>
      <p className="demo-isolation">Demo Lab is isolated from the camera, saved procedures, Tiger Data, and mat calibration.</p>
    </main>
  );
}
