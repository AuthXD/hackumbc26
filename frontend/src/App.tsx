import { useCallback, useEffect, useRef, useState } from "react";
import { Overlay } from "./Overlay";
import { PhoneCamera, PhoneLinkButton } from "./PhoneLink";
import { SetupPanel } from "./SetupPanel";
import { Simulator, type SimulatorHandle } from "./Simulator";
import { speaker } from "./speech";
import { StatusCard, statusView } from "./StatusCard";
import { Timeline } from "./Timeline";
import type { ServerUpdate, TrackerStatus } from "./types";
import { useCamera } from "./useCamera";
import { useFrameStream } from "./useFrameStream";

type Source = "camera" | "sim";

const CAL_ORDER = ["red", "yellow", "green", "blue"];
const DEFAULT_OBJECTS = "blue water bottle, brown wallet, green smartwatch, blue smartphone";
const PHONE_MODE = new URLSearchParams(location.search).get("phone") === "1";

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
  const { videoRef, ready, error } = useCamera(PHONE_MODE ? "environment" : undefined);
  const simRef = useRef<SimulatorHandle>(null);
  const [u, setU] = useState<ServerUpdate | null>(null);
  const [muted, setMuted] = useState(false);
  const [aspect, setAspect] = useState(4 / 3);
  const [fps, setFps] = useState(0);
  const [remoteFrame, setRemoteFrame] = useState<string | null>(null);
  const [calibrating, setCalibrating] = useState<string | null>(null); // color being calibrated
  const [objectDescriptions, setObjectDescriptions] = useState(DEFAULT_OBJECTS);
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
    // Exactly one laptop page speaks (the server picks it); the phone never does.
    if (!PHONE_MODE && m.speaker !== false) m.events?.forEach((e) => speaker.say(e));
  }, []);

  const onRemoteFrame = useCallback((jpeg: ArrayBuffer) => {
    const next = URL.createObjectURL(new Blob([jpeg], { type: "image/jpeg" }));
    setRemoteFrame((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return next;
    });
  }, []);

  useEffect(() => () => {
    if (remoteFrame) URL.revokeObjectURL(remoteFrame);
  }, [remoteFrame]);

  const { connection, send } = useFrameStream<ServerUpdate>(
    () => (source === "sim" ? simRef.current?.canvas ?? null : videoRef.current),
    (source === "sim" || ready) && (u?.active ?? true),
    onMessage,
    onRemoteFrame,
    PHONE_MODE ? { role: "phone" } : { role: "laptop", kind: source === "sim" ? "sim" : "webcam" },
  );

  // The server evaluates whichever page owns the camera; tell it when this laptop switches picture.
  useEffect(() => {
    if (!PHONE_MODE && connection === "open") send({ type: "source", kind: source === "sim" ? "sim" : "webcam" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, connection]);

  // A relayed phone picture is only shown while the phone really owns the camera; drop it otherwise.
  const phoneOwns = u?.camera?.owner === "phone";
  useEffect(() => {
    if (!phoneOwns) setRemoteFrame((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return null;
    });
  }, [phoneOwns]);

  const command = useCallback((action: string) => send({ type: "command", action }), [send]);
  const detector = u?.detector;
  const semantic = detector?.kind === "semantic";
  const configuredObjects = detector?.labels.join(", ");
  useEffect(() => {
    if (configuredObjects) setObjectDescriptions(configuredObjects);
  }, [configuredObjects]);
  useEffect(() => {
    if (semantic) setCalibrating(null);
  }, [semantic]);

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
  const setupMode = u?.workspace === "setup";
  const hasProcedure = !!u?.procedure?.steps.length;
  const canFinish = mode === "teaching" && (u?.teach?.stepsRecorded ?? 0) > 0;

  // Presenter shortcuts: T teach, F finish, U undo, P practice, R reset, M mute.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (setupMode) return; // procedure shortcuts do nothing in Setup Check
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
  }, [command, setupMode]);

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

  if (PHONE_MODE) {
    return <PhoneCamera videoRef={videoRef} connection={connection} ready={ready} error={error} u={u} send={send} />;
  }

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
          <PhoneLinkButton phone={u?.camera?.phone ?? "disconnected"} />
          <div className="segmented" role="group" aria-label="Video source">
            <button className={source === "camera" ? "on" : ""} onClick={() => setSource("camera")}>
              Camera
            </button>
            <button className={source === "sim" ? "on" : ""} disabled={semantic} onClick={() => setSource("sim")}
              title={semantic ? "Switch to Color mode to use the simulator" : undefined}>
              Simulator
            </button>
          </div>
          <button
            className="ghost"
            disabled={semantic}
            aria-pressed={!!calibrating}
            onClick={() => setCalibrating((c) => (c ? null : CAL_ORDER[0]))}
          >
            {calibrating ? "Done calibrating" : "Calibrate colors"}
          </button>
          <button className="ghost" onClick={() => setMuted((v) => !v)} aria-pressed={muted}>
            {muted ? "Voice off" : "Voice on"}
          </button>
        </div>
      </header>

      {(detector?.betaEnabled || u?.procedure?.detectorKind === "semantic") && (
        <section className="semantic-controls" aria-label="Object detector">
          <div className="semantic-toolbar">
            <div className="segmented" role="group" aria-label="Detector mode">
              <button className={!semantic ? "on" : ""} disabled={detector?.switchLocked || setupMode}
                onClick={() => send({ type: "detector", kind: "color" })}>Color</button>
              <button className={semantic ? "on" : ""}
                disabled={!detector?.betaEnabled || detector.switchLocked || source === "sim"}
                onClick={() => send({ type: "detector", kind: "semantic", labels: objectDescriptions })}>
                Semantic Objects <span className="beta-badge">Beta</span>
              </button>
            </div>
            {semantic && <span className="pill">Model: {detector?.workerState}</span>}
            {semantic && mode !== "idle" && <button className="ghost" onClick={() => command("pause")}>Pause procedure</button>}
          </div>
          {semantic && <>
            <label className="object-input">Object descriptions
              <input value={objectDescriptions} maxLength={485}
                disabled={mode !== "idle" || u?.procedure?.detectorKind === "semantic"}
                onChange={(e) => setObjectDescriptions(e.target.value)} />
            </label>
            <div className="semantic-toolbar">
              <button className="ghost" disabled={mode !== "idle" || u?.procedure?.detectorKind === "semantic"}
                onClick={() => send({ type: "detector", kind: "semantic", labels: objectDescriptions })}>Apply objects</button>
              <button className="btn" disabled={!detector?.canScan || source !== "camera"}
                onClick={() => command("scan")}>{detector?.scanState === "scanning" ? "Replace pending scan" : "Scan Objects"}</button>
              <span className="hint">2–6 unique descriptions. Choose large, distinct objects. Keep objects separated and fully visible.</span>
            </div>
            <p className="hint">Manual scans only. Hold still and scan the starting layout and each move. Stacking is unsupported.</p>
            {detector?.scanState === "error" && <p className="semantic-error" role="alert">
              {detector.message} Your procedure is preserved. {mode !== "idle" ? "Pause it, then select Color above." : "Select Color above to use the fallback."}
            </p>}
          </>}
        </section>
      )}

      <nav className="controls" aria-label="Mode controls">
        <div className="segmented workspace" role="group" aria-label="Product mode">
          <button className={!setupMode ? "on" : ""} onClick={() => send({ type: "workspace", workspace: "procedure" })}>
            Procedure
          </button>
          <button className={setupMode ? "on" : ""} disabled={!u?.setup?.available || mode !== "idle"}
            title={!u?.setup?.available ? "Setup Check needs the Semantic Objects beta (TEACHBACK_SEMANTIC_BETA=1)" : undefined}
            onClick={() => send({ type: "workspace", workspace: "setup" })}>
            Setup Check
          </button>
        </div>
        {!setupMode && <>
        <button className={`btn teach ${mode === "teaching" ? "active" : ""}`}
          disabled={semantic && (source !== "camera" || objectDescriptions !== configuredObjects)} onClick={() => command("teach")}>
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
          disabled={!hasProcedure || mode === "teaching" || (u?.procedure?.detectorKind !== detector?.kind)}
          onClick={() => command("practice")}
        >
          {mode === "practicing" ? "Restart Practice" : "Practice"}
        </button>
        <button className="btn subtle" onClick={() => command("reset")}>
          Reset
        </button>
        </>}
        {u?.notice && <span className="notice">{u.notice}</span>}
      </nav>

      <main className="stage">
        <section className="camera-panel">
          {calibrating && (
            <div className="calibrate-bar">
              <span>Click the</span>
              {CAL_ORDER.map((c) => (
                <button
                  key={c}
                  className={`chip ${c === calibrating ? "on" : ""}`}
                  style={{ ["--chip" as string]: u?.colors?.[c] ?? c }}
                  onClick={() => setCalibrating(c)}
                >
                  {c}
                </button>
              ))}
              <span>object in the view.</span>
              <button className="chip reset" onClick={() => send({ type: "reset_colors" })}>
                Reset colors
              </button>
            </div>
          )}
          <div
            className="camera-frame"
            style={{ aspectRatio: String(aspect), width: `min(100%, calc(var(--cam-h) * ${aspect}))` }}
          >
            <video ref={videoRef} muted playsInline className="camera-media"
              hidden={source !== "camera" || (phoneOwns && !!remoteFrame)} />
            {phoneOwns && remoteFrame && <img src={remoteFrame} alt="Live phone camera"
              className="camera-media" onLoad={(event) => {
                const image = event.currentTarget;
                if (image.naturalWidth) setAspect(image.naturalWidth / image.naturalHeight);
              }} />}
            {source === "sim" && <Simulator ref={simRef} zones={u?.zones ?? []} />}
            <Overlay scene={u?.scene ?? undefined} zones={u?.zones ?? []} activeZones={activeZones} />
            {calibrating && (
              <div
                className="calibrate-capture"
                onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  send({
                    type: "calibrate",
                    color: calibrating,
                    x: (e.clientX - r.left) / r.width,
                    y: (e.clientY - r.top) / r.height,
                  });
                  const next = CAL_ORDER[CAL_ORDER.indexOf(calibrating) + 1];
                  setCalibrating(next ?? null);
                }}
              />
            )}
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
                <button className="btn" disabled={semantic} onClick={() => setSource("sim")}>
                  {semantic ? "Select Color mode to use the simulator" : "Use the simulator"}
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

      {setupMode ? <SetupPanel u={u} send={send} /> : <Timeline u={u} />}

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
                <td>{o.kind === "color" ? `${Math.round(o.confidence * 100)}%` : "Not provided"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <pre>{JSON.stringify({ mode: u?.mode, tracker: u?.tracker, teach: u?.teach, practice: u?.practice }, null, 2)}</pre>
      </details>
    </div>
  );
}
