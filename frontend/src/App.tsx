import { useCallback, useEffect, useRef, useState } from "react";
import { Overlay } from "./Overlay";
import { Simulator, type SimulatorHandle } from "./Simulator";
import { speaker } from "./speech";
import { StatusCard, statusView } from "./StatusCard";
import { Timeline } from "./Timeline";
import type { ServerUpdate, TrackerStatus } from "./types";
import { useCamera } from "./useCamera";
import { useFrameStream } from "./useFrameStream";

type Source = "camera" | "sim";

const TRACKER_LABEL: Record<TrackerStatus, string> = {
  stable: "Stable",
  settling: "Settling…",
  moving: "Hands moving",
  occluded: "Object hidden",
  empty: "No objects",
};

export default function App() {
  const [source, setSource] = useState<Source>(() =>
    new URLSearchParams(location.search).has("sim") ? "sim" : "camera",
  );
  const { videoRef, ready, error } = useCamera();
  const simRef = useRef<SimulatorHandle>(null);
  const [u, setU] = useState<ServerUpdate | null>(null);
  const [muted, setMuted] = useState(false);
  const [aspect, setAspect] = useState(4 / 3);
  const [fps, setFps] = useState(0);
  const frameTimes = useRef<number[]>([]);

  const onMessage = useCallback((m: ServerUpdate) => {
    if (m.type !== "update" || m.error) return;
    if (m.frameMs !== undefined) {
      const now = performance.now();
      const times = frameTimes.current.filter((x) => now - x < 2000);
      times.push(now);
      frameTimes.current = times;
      setFps(times.length / 2);
    }
    setU(m);
    speaker.useElevenLabs = !!m.integrations?.elevenlabs;
    if (m.active !== false) m.events?.forEach((e) => speaker.say(e));
  }, []);

  const { connection, send } = useFrameStream<ServerUpdate>(
    () => (source === "sim" ? simRef.current?.canvas ?? null : videoRef.current),
    source === "sim" || ready,
    onMessage,
  );

  const command = useCallback((action: string) => send({ type: "command", action }), [send]);

  useEffect(() => {
    speaker.muted = muted;
    if (muted) speaker.stop();
  }, [muted]);

  // Keep the overlay aligned with the real camera aspect ratio.
  useEffect(() => {
    const video = videoRef.current;
    if (source === "sim") return setAspect(4 / 3);
    if (!video) return;
    const onMeta = () => video.videoWidth && setAspect(video.videoWidth / video.videoHeight);
    onMeta();
    video.addEventListener("loadedmetadata", onMeta);
    return () => video.removeEventListener("loadedmetadata", onMeta);
  }, [source, ready, videoRef]);

  const mode = u?.mode ?? "idle";
  const hasProcedure = !!u?.procedure?.steps.length;
  const canFinish = mode === "teaching" && (u?.teach?.stepsRecorded ?? 0) > 0;

  // Presenter shortcuts: T teach, F finish, U undo, P practice, R reset, M mute.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "t") command("teach");
      else if (k === "f") command("finish");
      else if (k === "u") command("undo_step");
      else if (k === "p") command("practice");
      else if (k === "r") command("reset");
      else if (k === "m") setMuted((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [command]);

  const view = statusView(u);
  const tracker = u?.tracker;
  // During practice, highlight the zone(s) the expected step should end in.
  const expectedStep =
    u?.mode === "practicing" && u.practice && u.practice.status !== "setup"
      ? u.procedure?.steps[u.practice.expectedStepIndex]
      : undefined;
  const activeZones = expectedStep
    ? Object.values(expectedStep.delta.after).flatMap((p) => (p.zone ? [p.zone] : []))
    : undefined;
  const cameraProblem = source === "camera" && error;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo" aria-hidden>
            <svg viewBox="0 0 32 32">
              <path d="M9 17l5 5 9-11" />
            </svg>
          </div>
          <div>
            <h1>TeachBack</h1>
            <p className="tagline">Show a procedure once. It coaches the next person through it.</p>
          </div>
        </div>
        <div className="top-meta">
          <span className={`pill conn-${connection}`}>
            {connection === "open" ? "Connected" : connection === "connecting" ? "Connecting…" : "Offline"}
          </span>
          <div className="segmented" role="group" aria-label="Video source">
            <button className={source === "camera" ? "on" : ""} onClick={() => setSource("camera")}>
              Camera
            </button>
            <button className={source === "sim" ? "on" : ""} onClick={() => setSource("sim")}>
              Simulator
            </button>
          </div>
          <button className="ghost" onClick={() => setMuted((v) => !v)} aria-pressed={muted}>
            {muted ? "Voice off" : "Voice on"}
          </button>
        </div>
      </header>

      <nav className="controls" aria-label="Mode controls">
        <button className={`btn teach ${mode === "teaching" ? "active" : ""}`} onClick={() => command("teach")}>
          Teach
        </button>
        <button className="btn" disabled={!canFinish} onClick={() => command("finish")}>
          Finish Teaching
        </button>
        {mode === "teaching" && (
          <button className="btn subtle" disabled={!canFinish} onClick={() => command("undo_step")}>
            Undo last step
          </button>
        )}
        <button
          className={`btn practice ${mode === "practicing" ? "active" : ""}`}
          disabled={!hasProcedure || mode === "teaching"}
          onClick={() => command("practice")}
        >
          {mode === "practicing" ? "Restart Practice" : "Practice"}
        </button>
        <button className="btn subtle" onClick={() => command("reset")}>
          Reset
        </button>
        {u?.notice && <span className="notice">{u.notice}</span>}
      </nav>

      <main className="stage">
        <section className="camera-panel">
          <div className="camera-frame" style={{ aspectRatio: String(aspect) }}>
            <video ref={videoRef} muted playsInline className="camera-media" hidden={source !== "camera"} />
            {source === "sim" && <Simulator ref={simRef} zones={u?.zones ?? []} />}
            <Overlay scene={u?.scene ?? undefined} zones={u?.zones ?? []} activeZones={activeZones} />
            {tracker && (
              <span className={`tracker-chip t-${tracker.status}`}>
                <i />
                {TRACKER_LABEL[tracker.status]}
                {tracker.status === "occluded" && tracker.missing.length ? `: ${tracker.missing.join(", ")}` : ""}
              </span>
            )}
            {cameraProblem && (
              <div className="camera-error">
                <strong>Camera unavailable</strong>
                <span>{error}</span>
                <button className="btn" onClick={() => setSource("sim")}>
                  Use the simulator
                </button>
              </div>
            )}
          </div>
          {source === "sim" && (
            <p className="hint">Simulator: drag blocks between zones; drop one block on another to stack it.</p>
          )}
        </section>

        <StatusCard view={view} />
      </main>

      <Timeline u={u} />

      <details className="debug">
        <summary>
          Debug · {tracker ? `${TRACKER_LABEL[tracker.status]} · motion ${tracker.motion}% · stable ${tracker.stableForMs}ms` : "–"} ·{" "}
          {fps.toFixed(1)} fps · {u?.frameMs ?? "–"} ms/frame · Gemini {u?.integrations?.gemini ? "on" : "off"} · ElevenLabs{" "}
          {u?.integrations?.elevenlabs ? "on" : "off"}
        </summary>
        <table>
          <thead>
            <tr>
              <th>Object</th>
              <th>Zone</th>
              <th>Stacked on</th>
              <th>Center</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {u?.scene?.objects.map((o) => (
              <tr key={o.id}>
                <td>{o.id}</td>
                <td>{o.zone ?? "–"}</td>
                <td>{o.stackedOn ?? "–"}</td>
                <td>
                  {o.center[0].toFixed(2)}, {o.center[1].toFixed(2)}
                </td>
                <td>{Math.round(o.confidence * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        <pre>{JSON.stringify({ mode: u?.mode, tracker: u?.tracker, teach: u?.teach, practice: u?.practice }, null, 2)}</pre>
      </details>
    </div>
  );
}
