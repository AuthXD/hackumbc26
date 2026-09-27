import { describe, expect, it } from "vitest";
import { DEMOS, selectDemoStep, type DemoState } from "./DemoLab";

describe("Demo Lab", () => {
  it("contains separate toolbox and clinical procedures", () => {
    expect(DEMOS.map((demo) => demo.id)).toEqual(["toolbox", "clinical"]);
    expect(DEMOS.every((demo) => demo.steps.length === 3)).toBe(true);
  });

  it("stops an out-of-order action without advancing", () => {
    const start: DemoState = { kind: "running", completed: 0 };
    expect(selectDemoStep(start, 2, 3)).toEqual({ kind: "mistake", completed: 0, selected: 2 });
  });

  it("recovers after a mistake and completes only in order", () => {
    let state: DemoState = { kind: "mistake", completed: 0, selected: 2 };
    state = selectDemoStep(state, 0, 3);
    expect(state).toEqual({ kind: "running", completed: 1 });
    state = selectDemoStep(state, 1, 3);
    state = selectDemoStep(state, 2, 3);
    expect(state).toEqual({ kind: "complete" });
  });
});
