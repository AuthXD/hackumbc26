import { useState } from "react";
import { useCamera } from "./useCamera";
import { useFrameStream } from "./useFrameStream";

type Update = { type: "update"; frame: { w: number; h: number; ms: number } };

export default function App() {
  const { videoRef, ready, error } = useCamera();
  const [last, setLast] = useState<Update | null>(null);
  const { connection } = useFrameStream<Update>(videoRef, ready, setLast);

  return (
    <main style={{ padding: 24 }}>
      <h1>TeachBack</h1>
      <p>
        ws: {connection} · camera: {error ?? (ready ? "live" : "starting")} · last frame:{" "}
        {last ? `${last.frame.w}x${last.frame.h} decoded in ${last.frame.ms.toFixed(1)}ms` : "—"}
      </p>
      <video ref={videoRef} muted playsInline style={{ width: 640 }} />
    </main>
  );
}
