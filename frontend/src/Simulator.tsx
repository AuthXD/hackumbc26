import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { Zone } from "./types";

/**
 * A synthetic tabletop drawn to a <canvas>. Its pixels go through exactly the same pipeline as the
 * webcam (JPEG → WebSocket → OpenCV), so it is both an end-to-end test rig and a fallback for a
 * dead camera. Drag blocks between zones; drop one onto another to stack it. While dragging, a
 * skin-toned "hand" covers the block, producing the same motion/occlusion a real hand would.
 */

const W = 640;
const H = 480;
const SIZE = 56;
const COLORS: Record<string, string> = {
  red: "#d42828",
  yellow: "#f0d728",
  green: "#32aa46",
  blue: "#1e64c8",
};

type Block = { id: string; x: number; y: number; on: string | null };

export type SimulatorHandle = { canvas: HTMLCanvasElement | null };

function initialBlocks(zones: Zone[]): Block[] {
  const a = zones[0] ?? { x: 0.03, y: 0.12, w: 0.29, h: 0.8 };
  const cx = (a.x + a.w / 2) * W;
  return Object.keys(COLORS).map((id, i) => ({ id, x: cx, y: (a.y + a.h * (0.14 + 0.24 * i)) * H, on: null }));
}

export const Simulator = forwardRef<SimulatorHandle, { zones: Zone[] }>(function Simulator({ zones }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const blocks = useRef<Block[]>([]);
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const drawRef = useRef<() => void>(() => undefined);

  useImperativeHandle(ref, () => ({ get canvas() { return canvasRef.current; } }), []);

  useEffect(() => {
    if (blocks.current.length === 0 && zones.length) blocks.current = initialBlocks(zones);
  }, [zones]);

  useEffect(() => {
    const draw = () => {
      const ctx = canvasRef.current?.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "#a5a096";
        ctx.fillRect(0, 0, W, H);
        ctx.strokeStyle = "#55524c";
        ctx.lineWidth = 3;
        ctx.setLineDash([]);
        for (const z of zones) ctx.strokeRect(z.x * W, z.y * H, z.w * W, z.h * H);
        // Draw bottom-of-tower first so stacked blocks render on top.
        const depth = (b: Block): number => (b.on ? 1 + depth(blocks.current.find((o) => o.id === b.on)!) : 0);
        const ordered = [...blocks.current].sort((p, q) => depth(p) - depth(q));
        for (const b of ordered) {
          ctx.fillStyle = COLORS[b.id];
          ctx.fillRect(b.x - SIZE / 2, b.y - SIZE / 2, SIZE, SIZE);
        }
        const d = drag.current;
        if (d) {
          const b = blocks.current.find((o) => o.id === d.id)!;
          ctx.fillStyle = "#dcaa8c"; // skin tone "hand"
          ctx.beginPath();
          ctx.ellipse(b.x + 8, b.y + 18, 52, 40, 0, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    };
    drawRef.current = draw;
    draw();
    // A timer (not requestAnimationFrame) keeps the canvas current even when the tab isn't painting.
    const id = window.setInterval(draw, 50);
    return () => window.clearInterval(id);
  }, [zones]);

  const toCanvas = (e: React.PointerEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  };

  const onDown = (e: React.PointerEvent) => {
    const p = toCanvas(e);
    // Only blocks with nothing on top of them can be picked up.
    const free = blocks.current.filter((b) => !blocks.current.some((o) => o.on === b.id));
    const hit = [...free].reverse().find((b) => Math.abs(p.x - b.x) < SIZE / 2 && Math.abs(p.y - b.y) < SIZE / 2);
    if (!hit) return;
    hit.on = null;
    drag.current = { id: hit.id, dx: p.x - hit.x, dy: p.y - hit.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toCanvas(e);
    const b = blocks.current.find((o) => o.id === d.id)!;
    b.x = p.x - d.dx;
    b.y = p.y - d.dy;
    drawRef.current();
  };

  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const b = blocks.current.find((o) => o.id === d.id)!;
    // Dropped onto another free block → stack it (drawn as an angled-camera tower).
    const target = blocks.current.find(
      (o) =>
        o.id !== b.id &&
        !blocks.current.some((t) => t.on === o.id) &&
        Math.abs(o.x - b.x) < SIZE * 0.7 &&
        Math.abs(o.y - b.y) < SIZE * 0.7,
    );
    if (target) {
      b.on = target.id;
      b.x = target.x;
      b.y = target.y - SIZE * 0.78;
    }
    drawRef.current();
  };

  return (
    <canvas
      ref={canvasRef}
      width={W}
      height={H}
      className="camera-media sim"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
    />
  );
});
