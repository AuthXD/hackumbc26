// Shared test fixtures: a library snapshot and an update with a two-step color procedure.
import type { LibraryState, ProcedureCard, ServerUpdate } from "./types";

export const card: ProcedureCard = {
  id: "kitchen-prep", name: "Kitchen Prep", summary: "4 ordered steps using red, blue.", tags: ["color"],
  detectorKind: "color", objectCount: 3, stepCount: 4, objects: ["blue", "green", "red"], updatedAt: 1000,
  aiGeneratedMetadata: false,
};

export function library(over: Partial<LibraryState> = {}, draft: Partial<LibraryState["draft"]> = {}): LibraryState {
  return {
    storage: { provider: "tiger", state: "ready", message: "Loaded 1 saved procedures from Tiger Data." },
    procedures: [card], loadedId: null, revision: 3, errors: [],
    draft: {
      available: true, saved: false, suggestionState: "unavailable", key: "draft-4",
      suggestion: { name: "Four-step color-block procedure", summary: "4 ordered steps using blue, red.", tags: ["color"] },
      ...draft,
    },
    ...over,
  };
}

const step = (i: number) => ({
  index: i, delta: { changedIds: ["red"], before: {}, after: {} }, beforeImage: `/api/keyframes/b${i}.jpg`,
  afterImage: `/api/keyframes/a${i}.jpg`, description: { title: `Step ${i + 1}`, instruction: "Move red to B." },
  aiDescription: null,
});

export function update(lib: LibraryState, over: Partial<ServerUpdate> = {}): ServerUpdate {
  return {
    type: "update", mode: "idle", library: lib,
    procedure: { detectorKind: "color", trackedIds: ["red"], steps: [step(0), step(1)] }, ...over,
  };
}
