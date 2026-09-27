import { useEffect, useState, type RefObject } from "react";
import { QRCodeSVG } from "qrcode.react";
import type { CameraStatus, ServerUpdate } from "./types";

type PhoneLink = {
  url: string | null;
  lanUrl: string | null;
  secure: boolean;
  source: "configured" | "lan" | "unavailable";
};

function isPhoneLink(value: unknown): value is PhoneLink {
  if (!value || typeof value !== "object") return false;
  const url = Reflect.get(value, "url");
  const lanUrl = Reflect.get(value, "lanUrl");
  const secure = Reflect.get(value, "secure");
  const source = Reflect.get(value, "source");
  return (typeof url === "string" || url === null)
    && (typeof lanUrl === "string" || lanUrl === null)
    && typeof secure === "boolean"
    && (source === "configured" || source === "lan" || source === "unavailable");
}

function asPhoneUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    url.searchParams.set("phone", "1");
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

const PHONE_LABEL: Record<CameraStatus["phone"], string> = {
  disconnected: "Connect phone",
  connected: "Phone connected — no video yet",
  streaming: "Phone streaming",
  error: "Phone camera error",
};

export function PhoneLinkButton({ phone }: { phone: CameraStatus["phone"] }) {
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState<PhoneLink | null>(null);
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    if (!open || link) return;
    fetch("/api/phone-link")
      .then(async (response) => {
        const value: unknown = await response.json();
        return value;
      })
      .then((value) => {
        if (!isPhoneLink(value)) throw new Error("Invalid phone-link response");
        setLink(value);
        setDraft(value.url ?? "");
      })
      .catch(() => setProblem("Could not create a phone link."));
  }, [open, link]);

  const url = asPhoneUrl(draft);
  const secure = url?.startsWith("https://") ?? false;

  return <>
    <button className={`ghost phone-${phone}`} onClick={() => setOpen(true)}>
      {PHONE_LABEL[phone]}
    </button>
    {open && <div className="modal-backdrop" role="presentation" onMouseDown={() => setOpen(false)}>
      <section className="phone-link-modal" role="dialog" aria-modal="true" aria-labelledby="phone-link-title"
        onMouseDown={(event) => event.stopPropagation()}>
        <button className="modal-close" aria-label="Close" onClick={() => setOpen(false)}>×</button>
        <h2 id="phone-link-title">Use a phone camera</h2>
        <p>Open this link on the phone. Its rear camera becomes the active camera and this laptop keeps the controls.</p>
        {url && <div className="qr-card"><QRCodeSVG value={url} size={220} level="M" /></div>}
        <label className="phone-url">Phone URL
          <input value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} />
        </label>
        {problem && <p className="semantic-error">{problem}</p>}
        {!url && !problem && <p className="semantic-error">Enter a valid HTTP or HTTPS URL.</p>}
        {url && !secure && <p className="phone-warning">
          This LAN link can open TeachBack, but iPhone and Android browsers block live cameras on HTTP. Start a
          trusted HTTPS tunnel to <code>http://localhost:5173</code>, paste its HTTPS URL above, then scan the QR.
        </p>}
        {secure && <p className="phone-ready">Secure phone-camera link ready. Keep both pages open.</p>}
        <p className="hint">The newest connected page owns the camera. Closing the phone page returns control to this laptop.</p>
      </section>
    </div>}
  </>;
}

// Long side / short side of the landmark rectangle on the mat (measured on IMG_4738: ~1220 x 2990 px).
const MAT_SHORT_OVER_LONG = 0.41;
const LANDMARKS = ["Creature", "Frog", "Potion", "Logo"];

/** Approximate framing guide: where the mat and its four corner landmarks should sit in the picture. */
function FramingGuide({ aspect, corners }: { aspect: number; corners: [number, number][] | null }) {
  // Fit the mat's long axis along the picture's long axis, leaving a margin.
  const portrait = aspect < 1;
  const long = 0.84;
  const short = Math.min(0.84, long * MAT_SHORT_OVER_LONG * (portrait ? 1 / aspect : aspect));
  const [w, h] = portrait ? [short, long] : [long, short];
  const x0 = (1 - w) / 2;
  const y0 = (1 - h) / 2;
  const guide: [number, number][] = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]];
  return <svg className="phone-guide" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
    <polygon points={guide.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")} className="guide-mat" />
    {corners && <polygon points={corners.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")} className="guide-tracked" />}
    {guide.map(([x, y], i) => <g key={i}>
      <circle cx={x * 100} cy={y * 100} r="2.2" className="guide-corner" />
      <text x={x * 100 + (x < 0.5 ? 3 : -3)} y={y * 100 + (y < 0.5 ? 5 : -3)}
        textAnchor={x < 0.5 ? "start" : "end"} className="guide-label">{i + 1} {LANDMARKS[i]}</text>
    </g>)}
  </svg>;
}

export function PhoneCamera({
  videoRef,
  connection,
  ready,
  error,
  u,
  send,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  connection: "connecting" | "open" | "closed";
  ready: boolean;
  error: string | null;
  u: ServerUpdate | null;
  send: (payload: object) => void;
}) {
  const [aspect, setAspect] = useState(9 / 16);
  // Tell the laptop what the camera is doing, so it never claims "streaming" on our behalf.
  useEffect(() => {
    if (connection !== "open") return;
    send({ type: "camera_status", state: error ? "error" : ready ? "ready" : "starting", message: error ?? "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection, ready, error]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onMeta = () => video.videoWidth && setAspect(video.videoWidth / video.videoHeight);
    video.addEventListener("loadedmetadata", onMeta);
    return () => video.removeEventListener("loadedmetadata", onMeta);
  }, [videoRef]);

  const streaming = u?.owner === true && u.camera?.phone === "streaming";
  const status = error ? "Camera unavailable"
    : connection !== "open" ? "Connecting to laptop…"
    : !ready ? "Waiting for camera permission…"
    : streaming ? "Streaming to laptop"
    : u?.active === false ? "Another phone is the active camera"
    : "Starting stream…";
  const mat = u?.mat;
  const matLine = !streaming ? "" : !mat || mat.state === "off" ? ""
    : mat.state === "uncalibrated" ? "Mat not calibrated yet. Calibrate it on the laptop."
    : mat.message;

  return <main className="phone-camera-page">
    <div className="phone-camera-heading">
      <span className={`phone-state ${streaming ? "ready" : ""}`} />
      <div>
        <h1>TeachBack camera</h1>
        <p>{status}</p>
      </div>
    </div>
    <div className="phone-preview-wrap">
      <video ref={videoRef} muted playsInline className="phone-preview" />
      {ready && !error && <FramingGuide aspect={aspect} corners={streaming && mat?.state === "tracking" ? mat.corners : null} />}
    </div>
    {matLine && <p className={`phone-mat mat-${mat?.state}`}>{matLine}</p>}
    {error && <section className="phone-camera-error">
      <strong>{error}</strong>
      {!window.isSecureContext && <p>Live camera access needs a trusted HTTPS link. Return to the laptop and use its secure QR.</p>}
    </section>}
    <p className="phone-hint">Use the rear camera. Fit the mat inside the dashed guide with all four corner stickers
      visible (1 Creature, 2 Frog, 3 Potion, 4 Logo). Hold still while scanning.</p>
  </main>;
}
