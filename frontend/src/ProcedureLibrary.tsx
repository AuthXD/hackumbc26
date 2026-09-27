import { useEffect, useState } from "react";
import type { LibraryState, ProcedureCard, ServerUpdate, StorageStatus, SuggestionState } from "./types";

/** How long to wait for a save / load / refresh reply before letting the user try again. */
export const LIBRARY_REPLY_MS = 15000;

/** Mirrors backend setup_id_for / procedure_id_for: saving a name that maps to an existing id replaces it. */
export function procedureIdFor(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
}

export function storageLabel(storage: StorageStatus): string {
  // "Tiger Data" is only claimed when the Tiger repository actually loaded (state ready).
  if (storage.state === "error") return storage.provider === "tiger" ? "Tiger Data unavailable" : "Local storage unavailable";
  return storage.provider === "tiger" ? "Storage: Tiger Data" : "Storage: Local";
}

export const SUGGESTION_LABEL: Record<SuggestionState, string | null> = {
  none: null,
  generating: "Generating suggestion…",
  suggested: "Suggested by Gemini",
  unavailable: "Gemini unavailable — enter a name",
  rejected: "Suggestion rejected — enter a name",
};

/** What the name/summary fields start with: the loaded entry's own text, else the draft suggestion. */
export function prefill(library: LibraryState): { name: string; summary: string } {
  const loaded = library.procedures.find((p) => p.id === library.loadedId);
  if (loaded) return { name: loaded.name, summary: loaded.summary };
  const s = library.draft.suggestion;
  return { name: s?.name ?? "", summary: s?.summary ?? "" };
}

/** Explains an upsert before it happens: the same name replaces the stored copy. */
export function saveTarget(library: LibraryState, name: string): { id: string; existing: ProcedureCard | null } {
  const id = procedureIdFor(name);
  return { id, existing: library.procedures.find((p) => p.id === id) ?? null };
}

export function updatedText(updatedAt: number, nowMs: number): string {
  const seconds = Math.max(0, nowMs / 1000 - updatedAt);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(updatedAt * 1000).toLocaleDateString();
}

const DETECTOR_LABEL = { color: "Color blocks", semantic: "Semantic objects" } as const;

export function LibraryCard({ card, loaded, canLoad, nowMs, onLoad }: {
  card: ProcedureCard; loaded: boolean; canLoad: boolean; nowMs: number; onLoad: () => void;
}) {
  return (
    <article className={`library-card ${loaded ? "loaded" : ""}`}>
      <header className="library-card-head">
        <h3>{card.name}</h3>
        {card.aiGeneratedMetadata && <span className="slot-ai" title="Name and summary suggested by Gemini">AI name</span>}
        {loaded && <span className="library-loaded">Loaded</span>}
      </header>
      {card.summary && <p className="library-summary">{card.summary}</p>}
      <p className="library-meta">
        {DETECTOR_LABEL[card.detectorKind]} · {card.objectCount} objects · {card.stepCount} steps · updated{" "}
        {updatedText(card.updatedAt, nowMs)}
      </p>
      <button className="btn subtle" disabled={!canLoad} onClick={onLoad}>
        {loaded ? "Reload" : "Load"}
      </button>
    </article>
  );
}

/** Name, save and load taught procedures. Correctness still comes only from deterministic Practice. */
export function ProcedureLibrary({ u, send }: { u: ServerUpdate | null; send: (payload: object) => void }) {
  const library = u?.library;
  const initial = () => (library?.draft.available ? prefill(library) : { name: "", summary: "" });
  const [name, setName] = useState(() => initial().name);
  const [summary, setSummary] = useState(() => initial().summary);
  const [edited, setEdited] = useState(false);
  const [pending, setPending] = useState<{ revision: number; at: number; action: string } | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const available = !!library?.draft.available;
  const start = library ? prefill(library) : { name: "", summary: "" };
  const prefillKey = `${library?.loadedId ?? ""}|${start.name}|${start.summary}`;

  // A new draft or a newly loaded entry refills the fields, unless the user is typing their own name.
  useEffect(() => {
    if (!available) {
      setEdited(false);
      setName("");
      setSummary("");
    } else if (!edited) {
      setName(start.name);
      setSummary(start.summary);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available, prefillKey]);

  // A reply arrived (the backend bumps the revision when a save / load / refresh finishes).
  const revision = library?.revision ?? 0;
  useEffect(() => {
    if (!pending) return;
    if (revision > pending.revision) {
      if (pending.action === "save" || pending.action === "load") setEdited(false);
      setPending(null);
      return;
    }
    const id = window.setTimeout(() => setPending(null), Math.max(0, LIBRARY_REPLY_MS - (Date.now() - pending.at)));
    return () => window.clearTimeout(id);
  }, [revision, pending]);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, []);

  if (!library) return null;
  const storage = library.storage;
  const idle = (u?.mode ?? "idle") === "idle";
  const target = saveTarget(library, name);
  const busy = !!pending;
  const canSave = available && storage.state === "ready" && !!target.id && !busy;
  const suggestion = SUGGESTION_LABEL[library.draft.suggestionState];
  const request = (action: string, payload: object) => {
    setPending({ revision, at: Date.now(), action });
    send(payload);
  };

  return (
    <section className="library-panel" aria-label="Procedure Library">
      <div className="library-head">
        <h2>Procedure Library</h2>
        <span className={`pill storage-${storage.state}`} title={storage.message}>{storageLabel(storage)}</span>
        <button className="ghost" disabled={busy} onClick={() => request("refresh", { type: "procedure_refresh" })}>
          {pending?.action === "refresh" ? "Refreshing…" : storage.state === "error" ? "Retry connection" : "Refresh"}
        </button>
      </div>
      {storage.state === "error" && (
        <p className="semantic-error" role="alert">
          {storage.message} Saving is disabled; nothing is saved locally instead. Practice still works.
        </p>
      )}

      {available ? (
        <div className="library-draft">
          <div className="setup-row">
            <label className="object-input setup-name">
              Procedure name
              <input value={name} maxLength={60} placeholder="e.g. Kitchen prep"
                onChange={(e) => { setEdited(true); setName(e.target.value); }} />
            </label>
            <label className="object-input setup-name">
              Summary (optional)
              <input value={summary} maxLength={200}
                onChange={(e) => { setEdited(true); setSummary(e.target.value); }} />
            </label>
            <button className="btn practice" disabled={!canSave}
              onClick={() => request("save", { type: "procedure_save", name, summary })}>
              {pending?.action === "save" ? "Saving…" : target.existing ? "Replace Saved Procedure" : "Save Procedure"}
            </button>
          </div>
          <p className="hint">
            {suggestion && <span className={`suggestion suggestion-${library.draft.suggestionState}`}>{suggestion}</span>}{" "}
            {target.existing
              ? `"${target.existing.name}" is already saved; saving replaces it with this procedure.`
              : library.draft.saved ? "Saved. Edit the name to save another copy." : "Not saved yet. Practice works without saving."}
          </p>
        </div>
      ) : (
        <p className="hint">{u?.mode === "teaching" ? "Finish teaching to name and save this procedure."
          : "Teach a procedure to save it here, or load a saved one."}</p>
      )}

      {library.errors.length > 0 && (
        <p className="semantic-error" role="alert">Skipped unreadable saved procedures: {library.errors.join("; ")}</p>
      )}

      {library.procedures.length ? (
        <div className="library-grid">
          {library.procedures.map((card) => (
            <LibraryCard key={card.id} card={card} loaded={card.id === library.loadedId} nowMs={nowMs}
              canLoad={idle && !busy} onLoad={() => request("load", { type: "procedure_load", id: card.id })} />
          ))}
        </div>
      ) : (
        storage.state === "ready" && <p className="hint">No saved procedures yet.</p>
      )}
      {!idle && library.procedures.length > 0 && <p className="hint">Stop teaching or practice to load another procedure.</p>}
    </section>
  );
}
