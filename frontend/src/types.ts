// Mirrors backend/app/models.py (camelCase JSON).

export type Placement = { zone: string | null; stackedOn: string | null };

export type SceneObject = {
  id: string;
  color: string;
  center: [number, number];
  bbox: [number, number, number, number];
  zone: string | null;
  visible: boolean;
  stackedOn: string | null;
  confidence: number;
};

export type SceneState = { objects: SceneObject[]; capturedAt: number; stableSince: number | null };

export type Zone = { id: string; label: string; x: number; y: number; w: number; h: number };

export type StepText = { title: string; instruction: string };

export type LearnedStep = {
  index: number;
  delta: { changedIds: string[]; before: Record<string, Placement>; after: Record<string, Placement> };
  beforeImage: string | null;
  afterImage: string | null;
  description: StepText;
  aiDescription: StepText | null;
};

export type Procedure = { trackedIds: string[]; steps: LearnedStep[] };

export type PracticeStatus = "setup" | "waiting" | "step_complete" | "error" | "complete";

export type PracticeState = {
  expectedStepIndex: number;
  status: PracticeStatus;
  errorType: string | null;
  headline: string;
  expectedDescription: string;
  observedDescription: string;
  fixHint: string;
  completed: number[];
};

export type TrackerStatus = "stable" | "settling" | "moving" | "occluded" | "empty";

export type Tracker = {
  status: TrackerStatus;
  motion: number;
  stableForMs: number;
  missing: string[];
};

export type Mode = "idle" | "teaching" | "practicing";

export type TeachState = {
  phase: "capturing_initial" | "recording" | "done";
  stepsRecorded: number;
  target: number;
  message: string;
  trackedIds: string[];
};

export type SpeakEvent = { kind: "speak"; text: string; priority: "info" | "success" | "error" };

export type ServerUpdate = {
  type: "update";
  mode?: Mode;
  scene?: SceneState | null;
  zones?: Zone[];
  tracker?: Tracker | null;
  teach?: TeachState | null;
  procedure?: Procedure | null;
  practice?: PracticeState | null;
  events?: SpeakEvent[];
  frameMs?: number;
  colors?: Record<string, string>;
  integrations?: { gemini: boolean; elevenlabs: boolean };
  notice?: string;
  active?: boolean;
  error?: string;
};
