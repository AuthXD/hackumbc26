import { useEffect, useRef, useState, type RefObject } from "react";

const SEND_INTERVAL_MS = 200; // ~5 fps
const SEND_WIDTH = 640;
const JPEG_QUALITY = 0.7;
const IN_FLIGHT_TIMEOUT_MS = 2000;

export type Connection = "connecting" | "open" | "closed";

/**
 * Streams webcam frames to the backend over one WebSocket.
 * Backpressure: at most one frame is in flight; a new frame is captured only after the previous
 * result arrives, so the server never processes a stale backlog.
 */
export function useFrameStream<T>(
  videoRef: RefObject<HTMLVideoElement | null>,
  enabled: boolean,
  onMessage: (msg: T) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    let closedByUs = false;
    let retry: number | undefined;
    const connect = () => {
      setConnection("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => setConnection("open");
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") onMessageRef.current(JSON.parse(ev.data) as T);
      };
      ws.onclose = () => {
        setConnection("closed");
        if (!closedByUs) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      closedByUs = true;
      window.clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  // Frame pump. Tracks in-flight state via message arrival.
  useEffect(() => {
    if (!enabled) return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d")!;
    let inFlightSince = 0;
    let lastSent = 0;
    let stopped = false;

    const ws = () => wsRef.current;
    const markReceived = () => {
      inFlightSince = 0;
    };
    const attach = () => ws()?.addEventListener("message", markReceived);
    attach();
    let attachedTo = ws();

    const tick = () => {
      if (stopped) return;
      const sock = ws();
      if (sock !== attachedTo) {
        attachedTo?.removeEventListener("message", markReceived);
        attach();
        attachedTo = sock;
        inFlightSince = 0;
      }
      const video = videoRef.current;
      const now = performance.now();
      if (inFlightSince && now - inFlightSince > IN_FLIGHT_TIMEOUT_MS) inFlightSince = 0;
      if (
        sock?.readyState === WebSocket.OPEN &&
        !inFlightSince &&
        now - lastSent >= SEND_INTERVAL_MS &&
        video &&
        video.videoWidth > 0
      ) {
        const scale = SEND_WIDTH / video.videoWidth;
        canvas.width = SEND_WIDTH;
        canvas.height = Math.round(video.videoHeight * scale);
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        inFlightSince = now;
        lastSent = now;
        canvas.toBlob(
          (blob) => {
            if (!blob || sock.readyState !== WebSocket.OPEN) {
              inFlightSince = 0;
              return;
            }
            blob.arrayBuffer().then((buf) => sock.send(buf));
          },
          "image/jpeg",
          JPEG_QUALITY,
        );
      }
      window.setTimeout(tick, 20);
    };
    tick();
    return () => {
      stopped = true;
      attachedTo?.removeEventListener("message", markReceived);
    };
  }, [enabled, videoRef]);

  const send = (payload: object) => {
    const sock = wsRef.current;
    if (sock?.readyState === WebSocket.OPEN) sock.send(JSON.stringify(payload));
  };

  return { connection, send };
}
