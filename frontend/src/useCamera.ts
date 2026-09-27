import { useEffect, useRef, useState } from "react";

/** Starts the webcam once and exposes the <video> ref plus any permission/device error. */
export function useCamera(facingMode?: "user" | "environment") {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let cancelled = false;
    navigator.mediaDevices
      .getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          ...(facingMode ? { facingMode: { ideal: facingMode } } : {}),
        },
        audio: false,
      })
      .then(async (s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = s;
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = s;
        await video.play().catch(() => undefined);
        setReady(true);
      })
      .catch((e: Error) => setError(e.message || "Camera unavailable"));
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [facingMode]);

  return { videoRef, ready, error };
}
