import { useEffect, useRef } from "react";
import type { SceneState, Zone } from "./types";

const OBJECT_CSS: Record<string, string> = {
  red: "#ff5a5a",
  yellow: "#ffd23f",
  green: "#3ddc84",
  blue: "#4c9bff",
};

/** Draws zones and detected objects over the camera/simulator, in normalized coordinates. */
export function Overlay({ scene, zones, activeZones }: { scene?: SceneState; zones: Zone[]; activeZones?: string[] }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;
    ctx.clearRect(0, 0, W, H);

    for (const z of zones) {
      const active = activeZones?.includes(z.id);
      ctx.setLineDash([10, 8]);
      ctx.lineWidth = active ? 4 : 2;
      ctx.strokeStyle = active ? "rgba(255,255,255,0.95)" : "rgba(255,255,255,0.55)";
      ctx.strokeRect(z.x * W, z.y * H, z.w * W, z.h * H);
      ctx.setLineDash([]);
      ctx.font = "700 18px Inter, system-ui, sans-serif";
      const label = z.label;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(10,14,20,0.72)";
      ctx.fillRect(z.x * W + 6, z.y * H + 6, tw + 16, 28);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, z.x * W + 14, z.y * H + 26);
    }

    for (const o of scene?.objects ?? []) {
      if (!o.visible) continue;
      const [x, y, w, h] = o.bbox;
      const color = OBJECT_CSS[o.color] ?? "#fff";
      ctx.lineWidth = 3;
      ctx.strokeStyle = color;
      ctx.strokeRect(x * W, y * H, w * W, h * H);
      const text = `${o.id}${o.stackedOn ? ` on ${o.stackedOn}` : ""} · ${o.zone ?? "–"} · ${Math.round(o.confidence * 100)}%`;
      ctx.font = "600 14px Inter, system-ui, sans-serif";
      const tw = ctx.measureText(text).width;
      const ly = Math.max(0, y * H - 24);
      ctx.fillStyle = "rgba(10,14,20,0.8)";
      ctx.fillRect(x * W, ly, tw + 12, 22);
      ctx.fillStyle = color;
      ctx.fillText(text, x * W + 6, ly + 16);
    }
  });

  return <canvas ref={ref} className="overlay" />;
}
