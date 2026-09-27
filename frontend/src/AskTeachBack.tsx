import { useState } from "react";
import type { AskAnswer, ServerUpdate } from "./types";

export const MAX_QUESTION = 300;
export const MAX_TURNS = 6;

export type AskTarget = { value: string; label: string; key: string; saved: boolean; procedureId: string | null };
export type Turn = { question: string; answer: AskAnswer | null; error: string | null };
export type Transcripts = Record<string, Turn[]>;

/** Procedures Ask can be grounded in: the active one (saved or not) and every saved one. */
export function askTargets(u: ServerUpdate | null): { targets: AskTarget[]; fallback: string } {
  const library = u?.library;
  const targets: AskTarget[] = [];
  const cards = library?.procedures ?? [];
  const loaded = cards.find((p) => p.id === library?.loadedId);
  const activeReady = !!u?.procedure?.steps.length && u?.mode !== "teaching";
  if (activeReady && !loaded) {
    targets.push({ value: "active", label: "Active procedure (unsaved)", key: library?.draft.key ?? "draft",
      saved: false, procedureId: null });
  }
  for (const card of cards) {
    // The transcript key changes when the entry is re-saved, so old answers never describe new steps.
    targets.push({ value: card.id, label: card.id === loaded?.id ? `${card.name} (loaded)` : card.name,
      key: `saved:${card.id}:${card.updatedAt}`, saved: true, procedureId: card.id });
  }
  const fallback = loaded ? loaded.id : activeReady ? "active" : "";
  return { targets, fallback };
}

export function appendTurn(all: Transcripts, key: string, turn: Turn): Transcripts {
  return { ...all, [key]: [...(all[key] ?? []), turn].slice(-MAX_TURNS) };
}

export function settleTurn(all: Transcripts, key: string, question: string, done: Omit<Turn, "question">): Transcripts {
  const turns = all[key] ?? [];
  let i = turns.length - 1;
  while (i >= 0 && !(turns[i].question === question && !turns[i].answer && !turns[i].error)) i--;
  if (i < 0) return all;
  return { ...all, [key]: turns.map((t, j) => (j === i ? { question, ...done } : t)) };
}

export function AskTurn({ turn }: { turn: Turn }) {
  const a = turn.answer;
  return (
    <li className="ask-turn">
      <p className="ask-question">{turn.question}</p>
      {!a && !turn.error && <p className="ask-thinking">Thinking…</p>}
      {turn.error && <p className="semantic-error" role="alert">{turn.error}</p>}
      {a && (
        <div className={`ask-answer source-${a.source}`}>
          {a.notice && <p className="ask-notice">{a.notice}</p>}
          <p>{a.answer}</p>
          {a.relevantStepNumbers.length > 0 && (
            <div className="ask-chips" aria-label="Relevant steps">
              {a.relevantStepNumbers.map((n) => <span key={n} className="chip on">Step {n}</span>)}
            </div>
          )}
          {a.requiredObjects.length > 0 && <p className="ask-objects">Objects: {a.requiredObjects.join(", ")}</p>}
          {a.disclaimer && <p className="ask-disclaimer">{a.disclaimer}</p>}
          <p className="ask-source">{a.source === "gemini" ? "Answered by Gemini · " : "Stored instructions · "}Grounded in this
            {a.procedureKey.startsWith("draft-") ? " procedure's learned steps." : " saved procedure."}</p>
        </div>
      )}
    </li>
  );
}

export function AskPanel({ targets, selected, turns, busy, question, onSelect, onQuestion, onAsk }: {
  targets: AskTarget[]; selected: AskTarget | null; turns: Turn[]; busy: boolean; question: string;
  onSelect: (value: string) => void; onQuestion: (q: string) => void; onAsk: () => void;
}) {
  const canAsk = !!selected && !busy && !!question.trim() && question.length <= MAX_QUESTION;
  return (
    <section className="ask-panel" aria-label="Ask TeachBack">
      <div className="library-head">
        <h2>Ask TeachBack</h2>
        <label className="object-input setup-select">
          Procedure
          <select value={selected?.value ?? ""} onChange={(e) => onSelect(e.target.value)} disabled={!targets.length}>
            <option value="" disabled>{targets.length ? "Choose a procedure…" : "No procedure selected"}</option>
            {targets.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
      </div>
      {selected ? (
        <form className="setup-row" onSubmit={(e) => { e.preventDefault(); if (canAsk) onAsk(); }}>
          <label className="object-input setup-name">
            Ask about this procedure
            <input value={question} maxLength={MAX_QUESTION} placeholder="e.g. What do I need? What is step 2?"
              onChange={(e) => onQuestion(e.target.value)} />
          </label>
          <button className="btn" type="submit" disabled={!canAsk}>{busy ? "Thinking…" : "Ask"}</button>
        </form>
      ) : (
        <p className="hint">No procedure selected. Teach one, or save and load one from the library.</p>
      )}
      {turns.length > 0 && <ol className="ask-transcript">{turns.map((t, i) => <AskTurn key={i} turn={t} />)}</ol>}
      <p className="hint">Answers use only the selected procedure's name, starting setup, objects and learned steps.
        Practice still decides whether each step was done correctly.</p>
    </section>
  );
}

export function AskTeachBack({ u }: { u: ServerUpdate | null }) {
  const [choice, setChoice] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [transcripts, setTranscripts] = useState<Transcripts>({});
  const [inFlight, setInFlight] = useState<string | null>(null); // transcript key awaiting an answer

  const { targets, fallback } = askTargets(u);
  const value = choice && targets.some((t) => t.value === choice) ? choice : fallback;
  const selected = targets.find((t) => t.value === value) ?? null;
  const turns = selected ? transcripts[selected.key] ?? [] : [];

  const ask = async () => {
    if (!selected) return;
    const q = question.trim();
    const key = selected.key; // answers land on the procedure they were asked about, never on a newer one
    setQuestion("");
    setInFlight(key);
    setTranscripts((all) => appendTurn(all, key, { question: q, answer: null, error: null }));
    let done: Omit<Turn, "question">;
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, procedureId: selected.procedureId }),
      });
      if (res.ok) done = { answer: (await res.json()) as AskAnswer, error: null };
      else if (res.status === 404) done = { answer: null, error: "No procedure selected. It may have been reset or removed." };
      else done = { answer: null, error: "That question could not be asked. Keep it under 300 characters." };
    } catch {
      done = { answer: null, error: "Could not reach TeachBack. Check the connection and try again." };
    }
    setTranscripts((all) => settleTurn(all, key, q, done));
    setInFlight((k) => (k === key ? null : k));
  };

  return (
    <AskPanel targets={targets} selected={selected} turns={turns} busy={!!selected && inFlight === selected.key}
      question={question} onSelect={setChoice} onQuestion={setQuestion} onAsk={ask} />
  );
}
