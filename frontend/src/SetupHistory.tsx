import type { EventHistoryState, SetupCheckEvent, SetupState } from "./types";

const time = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

// A failed or dropped write must never look like a saved one.
const WRITE_LABEL: Record<EventHistoryState, [string, string]> = {
  pending: ["This check: saving to history…", "neutral"],
  saved: ["This check: saved to Tiger Data", "ready"],
  failed: ["This check: NOT saved (history write failed)", "error"],
  dropped: ["This check: NOT saved (history queue full)", "error"],
  disabled: ["This check: not recorded (history off in local mode)", "neutral"],
};

function findings(e: SetupCheckEvent): string {
  if (e.status === "complete") return "Complete";
  const parts = [
    e.missing.length && `${e.missing.length} missing`,
    e.unexpected.length && `${e.unexpected.length} unexpected`,
    e.misplaced.length && `${e.misplaced.length} wrong zone`,
  ].filter(Boolean);
  return `Needs attention (${parts.join(", ")})`;
}

/** Readiness summary and recent checks from the backend's cached Tiger history. No charts, no raw JSON. */
export function SetupHistory({ setup, send }: { setup: SetupState; send: (payload: object) => void }) {
  const h = setup.history;
  const write = setup.resultHistory?.state;
  const s = h.summary;
  return (
    <div className="setup-history">
      <div className="setup-storage">
        <span className={`pill history-${h.state}`} title={h.message}>
          {h.state === "ready" ? "History: Tiger Data" : "History unavailable"}
        </span>
        {write && <span className={`pill history-${WRITE_LABEL[write][1]}`}>{WRITE_LABEL[write][0]}</span>}
        {h.state !== "disabled" && (
          <button className="ghost" onClick={() => send({ type: "history_refresh" })}>Refresh history</button>
        )}
      </div>
      {h.state !== "ready" && <p className={h.state === "error" ? "semantic-error" : "hint"}>{h.message}</p>}
      {h.writer.dropped > 0 && (
        <p className="semantic-error" role="alert">
          {h.writer.dropped} check{h.writer.dropped === 1 ? "" : "s"} not recorded: {h.writer.lastProblem}
        </p>
      )}
      {setup.selected && h.state !== "disabled" && (
        <div className="setup-grid">
          <div className="setup-list tone-neutral">
            <h3>Readiness</h3>
            {s && s.totalChecks > 0 ? (
              <ul>
                <li><b>{s.readinessPercent}%</b> ready · {s.totalChecks} checks</li>
                <li>{s.completeChecks} complete · {s.needsAttentionChecks} needed attention</li>
                {s.latestCheckedAt && <li>Last check {time(s.latestCheckedAt)}</li>}
                {s.buckets.length > 0 && (
                  <li>
                    Last {s.windowHours} h by hour:{" "}
                    {s.buckets.map((b) => `${new Date(b.bucketStart * 1000).getHours()}:00 ${b.complete}/${b.total}`).join(" · ")}
                  </li>
                )}
              </ul>
            ) : (
              <p className="hint">No recorded checks yet.</p>
            )}
          </div>
          <div className="setup-list tone-neutral">
            <h3>Recent checks</h3>
            {h.recent.length ? (
              <ul>
                {h.recent.slice(0, 5).map((e) => (
                  <li key={e.eventId}>{time(e.checkedAt)} — {findings(e)}</li>
                ))}
              </ul>
            ) : (
              <p className="hint">None yet.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
