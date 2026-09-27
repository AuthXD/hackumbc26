import { useEffect, useState, type RefObject } from "react";
import { QRCodeSVG } from "qrcode.react";

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

export function PhoneLinkButton({ streaming }: { streaming: boolean }) {
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
    <button className="ghost" onClick={() => setOpen(true)}>
      {streaming ? "Phone streaming" : "Connect phone"}
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

export function PhoneCamera({
  videoRef,
  connection,
  ready,
  error,
  active,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  connection: "connecting" | "open" | "closed";
  ready: boolean;
  error: string | null;
  active: boolean;
}) {
  return <main className="phone-camera-page">
    <div className="phone-camera-heading">
      <span className={`phone-state ${connection === "open" && ready && active ? "ready" : ""}`} />
      <div>
        <h1>TeachBack camera</h1>
        <p>{error ? "Camera unavailable" : connection !== "open" ? "Connecting to laptop…"
          : !ready ? "Waiting for camera permission…" : active ? "Streaming to laptop" : "Another camera is active"}</p>
      </div>
    </div>
    <video ref={videoRef} muted playsInline className="phone-preview" />
    {error && <section className="phone-camera-error">
      <strong>{error}</strong>
      {!window.isSecureContext && <p>Live camera access needs a trusted HTTPS link. Return to the laptop and use its secure QR.</p>}
    </section>}
    <p className="phone-hint">Use the rear camera. Keep the black mat inside the frame and hold still while scanning.</p>
  </main>;
}
