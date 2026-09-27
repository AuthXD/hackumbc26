import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  LibraryCard, prefill, procedureIdFor, ProcedureLibrary, saveTarget, storageLabel, SUGGESTION_LABEL, updatedText,
} from "./ProcedureLibrary";
import { statusView } from "./StatusCard";
import { Timeline } from "./Timeline";
import { card, library, update } from "./testFixtures";
import type { ServerUpdate } from "./types";

const noop = () => {};

const render = (u: ServerUpdate) => renderToStaticMarkup(<ProcedureLibrary u={u} send={noop} />);

describe("procedure library helpers", () => {
  it("derives the same ids as the backend, so re-saving a name is a visible replace", () => {
    expect(procedureIdFor("  Kitchen   Prep! ")).toBe("kitchen-prep");
    expect(procedureIdFor("!!!")).toBe("");
    expect(saveTarget(library(), "kitchen prep").existing?.name).toBe("Kitchen Prep");
    expect(saveTarget(library(), "Other").existing).toBeNull();
  });

  it("only claims Tiger Data when the Tiger repository is ready", () => {
    expect(storageLabel({ provider: "tiger", state: "ready", message: "" })).toBe("Storage: Tiger Data");
    expect(storageLabel({ provider: "tiger", state: "error", message: "" })).toBe("Tiger Data unavailable");
    expect(storageLabel({ provider: "local", state: "ready", message: "" })).toBe("Storage: Local");
  });

  it("names every suggestion state honestly", () => {
    expect(SUGGESTION_LABEL).toEqual({
      none: null, generating: "Generating suggestion…", suggested: "Suggested by Gemini",
      unavailable: "Gemini unavailable — enter a name", rejected: "Suggestion rejected — enter a name",
    });
  });

  it("prefills from the loaded entry, else from the draft suggestion", () => {
    expect(prefill(library())).toEqual({ name: "Four-step color-block procedure", summary: "4 ordered steps using blue, red." });
    expect(prefill(library({ loadedId: "kitchen-prep" }))).toEqual({ name: "Kitchen Prep", summary: card.summary });
    expect(prefill(library({}, { suggestion: null }))).toEqual({ name: "", summary: "" });
  });

  it("formats the modified time", () => {
    expect(updatedText(1000, 1_030_000)).toBe("just now");
    expect(updatedText(1000, 1_000_000 + 5 * 60_000)).toBe("5 min ago");
    expect(updatedText(1000, 1_000_000 + 3 * 3_600_000)).toBe("3 h ago");
  });
});

describe("procedure library panel", () => {
  it("shows a card with name, summary, detector, counts, time, and the loaded marker", () => {
    const html = renderToStaticMarkup(<LibraryCard card={card} loaded canLoad nowMs={1_000_000} onLoad={noop} />);
    expect(html).toContain("Kitchen Prep");
    expect(html).toContain("4 ordered steps using red, blue.");
    expect(html).toContain("Color blocks · 3 objects · 4 steps · updated just now");
    expect(html).toContain(">Loaded<");
    expect(html).toContain(">Reload<");
    const other = renderToStaticMarkup(<LibraryCard card={card} loaded={false} canLoad={false} nowMs={0} onLoad={noop} />);
    expect(other).not.toContain(">Loaded<");
    expect(other).toMatch(/<button[^>]*disabled[^>]*>Load</);
  });

  it("offers Save Procedure for a fresh draft with the storage pill and suggestion state", () => {
    const html = render(update(library({ procedures: [] })));
    expect(html).toContain("Procedure Library");
    expect(html).toContain("Storage: Tiger Data");
    expect(html).toContain("Procedure name");
    expect(html).toContain("Save Procedure");
    expect(html).toContain("Gemini unavailable — enter a name");
    expect(html).toContain("Not saved yet. Practice works without saving.");
    expect(html).toContain("No saved procedures yet.");
  });

  it("disables saving and explains a storage outage without claiming a local fallback", () => {
    const html = render(update(library({
      storage: { provider: "tiger", state: "error", message: "Tiger Data unavailable: could not load procedures (OperationalError)." },
    })));
    expect(html).toContain("Tiger Data unavailable");
    expect(html).toContain("nothing is saved locally instead");
    expect(html).toContain("Retry connection");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Save Procedure</);
  });

  it("hides the draft form while teaching and locks Load outside idle", () => {
    const html = render(update(library({}, { available: false }), { mode: "teaching" }));
    expect(html).not.toContain("Procedure name");
    expect(html).toContain("Finish teaching to name and save this procedure.");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Load</);
    expect(html).toContain("Stop teaching or practice to load another procedure.");
  });

  it("reports skipped unreadable rows", () => {
    expect(render(update(library({ errors: ["row 'broken': invalid"] })))).toContain(
      "Skipped unreadable saved procedures: row &#x27;broken&#x27;: invalid");
  });

  it("renders nothing without library state", () => {
    expect(renderToStaticMarkup(<ProcedureLibrary u={null} send={noop} />)).toBe("");
  });
});

describe("after teaching", () => {
  it("makes naming and saving the primary next action while Practice stays available", () => {
    const view = statusView(update(library()), undefined);
    expect(view.headline).toBe("Name and save this procedure");
    expect(view.expected).toContain("or press Practice now");
  });

  it("returns to the practice prompt once the procedure is saved or loaded", () => {
    const view = statusView(update(library({ loadedId: "kitchen-prep" }, { saved: true })), undefined);
    expect(view.headline).toBe("Learned 2 steps — press Practice");
    expect(view.eyebrow).toBe('Ready · "Kitchen Prep"');
  });

  it("still renders thumbnails when they exist (missing ones hide themselves on error)", () => {
    const html = renderToStaticMarkup(<Timeline u={update(library())} />);
    expect(html).toContain('src="/api/keyframes/b0.jpg"');
    expect(html).toContain("Move red to B.");
  });
});
