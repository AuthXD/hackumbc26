import { useState } from "react";
import type { ServerUpdate, SetupObject } from "./types";

const zoneText = (zone: string | null) => (zone ? `Zone ${zone}` : "outside the zones");

function ObjectList({ title, tone, items }: { title: string; tone: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className={`setup-list tone-${tone}`}>
      <h3>
        {title} <span>{items.length}</span>
      </h3>
      <ul>
        {items.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </div>
  );
}

/** Capture a named workspace layout, pick a saved one, and show the deterministic check result. */
export function SetupPanel({ u, send }: { u: ServerUpdate | null; send: (payload: object) => void }) {
  const [name, setName] = useState("");
  const setup = u?.setup;
  if (!setup) return null;
  const result = setup.result;
  const described = (o: SetupObject) => `${o.label} — ${zoneText(o.zone)}`;

  return (
    <section className="setup-panel" aria-label="Setup Check">
      <div className="setup-row">
        <label className="object-input setup-name">
          Setup name
          <input value={name} maxLength={60} placeholder="e.g. Lab bench" onChange={(e) => setName(e.target.value)} />
        </label>
        <button className="btn" disabled={!setup.canCapture || !name.trim()}
          onClick={() => send({ type: "setup_capture", name })}>
          Capture Setup
        </button>
        <label className="object-input setup-select">
          Saved setup
          <select value={setup.selected?.id ?? ""} onChange={(e) => send({ type: "setup_select", id: e.target.value })}>
            <option value="" disabled>
              {setup.setups.length ? "Choose a setup…" : "No saved setups yet"}
            </option>
            {setup.setups.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.objectCount} objects)
              </option>
            ))}
          </select>
        </label>
        <button className="btn practice" disabled={!setup.canCheck} onClick={() => send({ type: "setup_check" })}>
          {setup.checking ? "Checking…" : "Check Setup"}
        </button>
      </div>
      <p className="hint">
        To capture: arrange the organized table, hold still, press <b>Scan Objects</b>, then <b>Capture Setup</b>. To
        check: hold still and press <b>Check Setup</b>. Unexpected objects are found among the object descriptions above.
      </p>
      {setup.repositoryErrors.length > 0 && (
        <p className="semantic-error" role="alert">
          Skipped unreadable saved setups: {setup.repositoryErrors.join("; ")}
        </p>
      )}

      <div className="setup-grid">
        {setup.selected && (
          <ObjectList title={`Expected in "${setup.selected.name}"`} tone="neutral"
            items={setup.selected.objects.map(described)} />
        )}
        {result && (
          <>
            <div className={`setup-verdict tone-${result.status === "complete" && !setup.resultStale ? "success" : setup.resultStale ? "neutral" : "error"}`}>
              {setup.resultStale ? "Table changed since this check — check again"
                : result.status === "complete" ? "Complete and correctly arranged" : "Needs attention"}
            </div>
            <ObjectList title="Missing" tone="error" items={result.missing.map(described)} />
            <ObjectList title="Unexpected" tone="error" items={result.unexpected.map(described)} />
            <ObjectList title="Wrong zone" tone="warning"
              items={result.misplaced.map((m) => `${m.label} — in ${zoneText(m.observedZone)}, belongs in ${zoneText(m.expectedZone)}`)} />
            <ObjectList title="Correct" tone="success" items={result.correct.map(described)} />
          </>
        )}
      </div>
    </section>
  );
}
