import { useRef, useState } from "react";
import { Overlay } from "./Overlay";
import { Simulator, type SimulatorHandle } from "./Simulator";
import type { ServerUpdate } from "./types";
import { useCamera } from "./useCamera";
import { useFrameStream } from "./useFrameStream";

type Source = "camera" | "sim";

export default function App() {
  const [source, setSource] = useState<Source>(() =>
    new URLSearchParams(location.search).has("sim") ? "sim" : "camera",
  );
  const { videoRef, ready, error } = useCamera();
  const simRef = useRef<SimulatorHandle>(null);
  const [update, setUpdate] = useState<ServerUpdate | null>(null);
  const { connection } = useFrameStream<ServerUpdate>(
    () => (source === "sim" ? simRef.current?.canvas ?? null : videoRef.current),
    source === "sim" || ready,
    (m) => setUpdate(m),
  );
  const zones = update?.zones ?? [];

  return (
    <main style={{ padding: 24 }}>
      <h1>TeachBack</h1>
      <p>
        ws: {connection} · camera: {error ?? (ready ? "live" : "starting")} · frame {update?.frameMs ?? "–"}ms ·
        motion {update?.tracker?.motion ?? "–"}%{" "}
        <button onClick={() => setSource(source === "sim" ? "camera" : "sim")}>source: {source}</button>
      </p>
      <div className="camera-frame">
        <video ref={videoRef} muted playsInline className="camera-media" hidden={source !== "camera"} />
        {source === "sim" && <Simulator ref={simRef} zones={zones} />}
        <Overlay scene={update?.scene} zones={zones} />
      </div>
      <pre>{JSON.stringify(update?.scene?.objects.map((o) => [o.id, o.zone, o.stackedOn, o.confidence]))}</pre>
    </main>
  );
}

