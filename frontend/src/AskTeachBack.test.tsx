import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { appendTurn, AskPanel, askTargets, AskTeachBack, MAX_TURNS, settleTurn, type Turn } from "./AskTeachBack";
import { ProcedureLibrary } from "./ProcedureLibrary";
import { card, library, update } from "./testFixtures";
import type { AskAnswer } from "./types";

const noop = () => {};

const answer = (over: Partial<AskAnswer> = {}): AskAnswer => ({
  question: "What do I do?", answer: "Move the red block to Zone B.", relevantStepNumbers: [1, 2],
  requiredObjects: ["red"], disclaimer: null, source: "gemini", procedureKey: "kitchen-prep@1000", notice: "", ...over,
});

function panel(turns: Turn[], busy = false, u = update(library({ loadedId: "kitchen-prep" }, { saved: true }))) {
  const { targets, fallback } = askTargets(u);
  return renderToStaticMarkup(
    <AskPanel targets={targets} selected={targets.find((t) => t.value === fallback) ?? null} turns={turns}
      busy={busy} question="" onSelect={noop} onQuestion={noop} onAsk={noop} />,
  );
}

describe("ask targets", () => {
  it("defaults to the loaded saved procedure and lists every saved one", () => {
    const { targets, fallback } = askTargets(update(library({ loadedId: "kitchen-prep" }, { saved: true })));
    expect(fallback).toBe("kitchen-prep");
    expect(targets).toEqual([{ value: "kitchen-prep", label: "Kitchen Prep (loaded)",
      key: "saved:kitchen-prep:1000", saved: true, procedureId: "kitchen-prep" }]);
  });

  it("offers the unsaved active procedure after teaching", () => {
    const { targets, fallback } = askTargets(update(library()));
    expect(fallback).toBe("active");
    expect(targets[0]).toEqual({ value: "active", label: "Active procedure (unsaved)", key: "draft-4", saved: false,
      procedureId: null });
  });

  it("has nothing selected with no active procedure, and nothing active while teaching", () => {
    expect(askTargets(update(library({ procedures: [] }), { procedure: null }))).toEqual({ targets: [], fallback: "" });
    expect(askTargets(update(library(), { mode: "teaching" })).fallback).toBe("");
    expect(askTargets(null)).toEqual({ targets: [], fallback: "" });
  });

  it("gives a re-saved or different procedure a different transcript key", () => {
    const before = askTargets(update(library({ loadedId: "kitchen-prep" }))).targets[0].key;
    const resaved = askTargets(update(library({ loadedId: "kitchen-prep", procedures: [{ ...card, updatedAt: 2000 }] })));
    expect(resaved.targets[0].key).not.toBe(before);
    const other = askTargets(update(library({}, { key: "draft-5" }))).targets[0].key;
    expect(other).not.toBe(askTargets(update(library())).targets[0].key);
  });
});

describe("transcripts", () => {
  it("keeps a short per-procedure transcript and settles the pending turn", () => {
    let all = {};
    for (let i = 0; i < MAX_TURNS + 2; i++) all = appendTurn(all, "a", { question: `q${i}`, answer: null, error: null });
    expect((all as Record<string, Turn[]>).a).toHaveLength(MAX_TURNS);
    all = appendTurn(all, "b", { question: "only b", answer: null, error: null });
    all = settleTurn(all, "b", "only b", { answer: answer(), error: null });
    const t = all as Record<string, Turn[]>;
    expect(t.b[0].answer?.answer).toBe("Move the red block to Zone B.");
    expect(t.a.every((x) => x.answer === null)).toBe(true); // an answer lands only on the procedure it was asked about
  });
});

describe("ask panel", () => {
  it("prompts for a question grounded in the selected procedure", () => {
    const html = panel([]);
    expect(html).toContain("Ask TeachBack");
    expect(html).toContain("Ask about this procedure");
    expect(html).toContain("Kitchen Prep (loaded)");
  });

  it("shows No procedure selected without a target", () => {
    const html = panel([], false, update(library({ procedures: [] }), { procedure: null }));
    expect(html).toContain("No procedure selected");
    expect(html).not.toContain("Ask about this procedure");
  });

  it("shows Thinking… while an answer is pending", () => {
    const html = panel([{ question: "What first?", answer: null, error: null }], true);
    expect(html).toContain("What first?");
    expect(html).toContain("Thinking…");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Thinking…</);
  });

  it("shows a grounded Gemini answer with step chips", () => {
    const html = panel([{ question: "What do I do?", answer: answer(), error: null }]);
    expect(html).toContain("Move the red block to Zone B.");
    expect(html).toContain(">Step 1<");
    expect(html).toContain(">Step 2<");
    expect(html).toContain("Objects: red");
    expect(html).toContain("Grounded in this saved procedure.");
    expect(html).toContain("Answered by Gemini");
  });

  it("labels the stored-instruction fallback honestly", () => {
    const html = panel([{ question: "q", error: null, answer: answer({
      source: "stored", notice: "Gemini unavailable, showing stored instructions.",
      disclaimer: "TeachBack only checks where objects are placed.",
    }) }]);
    expect(html).toContain("Gemini unavailable, showing stored instructions.");
    expect(html).toContain("Stored instructions");
    expect(html).not.toContain("Answered by Gemini");
    expect(html).toContain("TeachBack only checks where objects are placed.");
  });

  it("shows request errors in place of an answer", () => {
    const html = panel([{ question: "q", answer: null, error: "Could not reach TeachBack." }]);
    expect(html).toContain("Could not reach TeachBack.");
    expect(html).not.toContain("Thinking…");
  });

  it("starts empty for a newly loaded procedure (no stale answer)", () => {
    const html = renderToStaticMarkup(<AskTeachBack u={update(library({ loadedId: "kitchen-prep" }))} />);
    expect(html).toContain("Ask about this procedure");
    expect(html).not.toContain("ask-turn");
  });
});

describe("name suggestion states", () => {
  const render = (state: "generating" | "suggested" | "rejected" | "unavailable") =>
    renderToStaticMarkup(<ProcedureLibrary u={update(library({ procedures: [] }, { suggestionState: state }))} send={noop} />);

  it("shows each suggestion state and only credits Gemini for an accepted one", () => {
    expect(render("generating")).toContain("Generating suggestion…");
    expect(render("suggested")).toContain("Suggested by Gemini");
    expect(render("rejected")).toContain("Suggestion rejected — enter a name");
    expect(render("unavailable")).toContain("Gemini unavailable — enter a name");
    for (const s of ["generating", "rejected", "unavailable"] as const) expect(render(s)).not.toContain("Suggested by Gemini");
  });

  it("keeps Save available while a suggestion is generating", () => {
    expect(render("generating")).toMatch(/<button class="btn practice">Save Procedure</);
  });

  it("marks saved entries whose name came from Gemini", () => {
    const html = renderToStaticMarkup(<ProcedureLibrary send={noop}
      u={update(library({ procedures: [{ ...card, aiGeneratedMetadata: true }] }))} />);
    expect(html).toContain("AI name");
  });
});
