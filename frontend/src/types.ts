// Mirrors backend/app/models.py (camelCase JSON).

export type Placement = { zone: string | null; stackedOn: string | null };

export type SceneObject = {
  id: string;
  center: [number, number];
  bbox: [number, number, number, number];
  zone: string | null;
  visible: boolean;
  stackedOn: string | null;
} & (
  | { kind: "color"; color: string; label: null; confidence: number }
  | { kind: "semantic"; color: null; label: string; confidence: null }
);

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

export type Procedure = { detectorKind: "color" | "semantic"; trackedIds: string[]; steps: LearnedStep[] };

export type DetectorState = {
  kind: "color" | "semantic";
  betaEnabled: boolean;
  labels: string[];
  workerState: "unloaded" | "loading" | "ready" | "error";
  workerMessage?: string;
  scanState: "idle" | "scanning" | "valid" | "ambiguous" | "error";
  message: string;
  canScan: boolean;
  switchLocked: boolean;
  procedureKind: "color" | "semantic" | null;
};

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

export type TrackerStatus = "stable" | "settling" | "moving" | "occluded" | "empty" | "untracked";

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

export type Workspace = "procedure" | "setup";

export type SetupObject = { label: string; zone: string | null };

export type SavedSetup = { id: string; name: string; objects: SetupObject[]; createdAt: number };

export type SetupCheckResult = {
  setupId: string;
  setupName: string;
  status: "complete" | "needs_attention";
  correct: SetupObject[];
  missing: SetupObject[];
  unexpected: SetupObject[];
  misplaced: { label: string; expectedZone: string | null; observedZone: string | null }[];
  checkedAt: number;
};

export type StorageStatus = { provider: "local" | "tiger"; state: "ready" | "error"; message: string };

export type SetupCheckEvent = {
  eventId: string;
  checkedAt: number;
  setupId: string;
  setupName: string;
  status: "complete" | "needs_attention";
  correct: SetupObject[];
  missing: SetupObject[];
  unexpected: SetupObject[];
  misplaced: { label: string; expectedZone: string | null; observedZone: string | null }[];
};

export type ReadinessSummary = {
  setupId: string;
  totalChecks: number;
  completeChecks: number;
  needsAttentionChecks: number;
  readinessPercent: number | null;
  latestCheckedAt: number | null;
  windowHours: number;
  bucketHours: number;
  buckets: { bucketStart: number; total: number; complete: number }[];
};

export type HistoryState = {
  provider: "tiger" | "local";
  state: "ready" | "disabled" | "error";
  message: string;
  recent: SetupCheckEvent[];
  summary: ReadinessSummary | null;
  writer: { queued: number; capacity: number; saved: number; failed: number; dropped: number; lastProblem: string };
  errors: string[];
};

export type EventHistoryState = "pending" | "saved" | "failed" | "dropped" | "disabled";

export type SetupState = {
  available: boolean;
  storage: StorageStatus;
  setups: { id: string; name: string; objectCount: number }[];
  selected: SavedSetup | null;
  canCapture: boolean;
  canCheck: boolean;
  checking: boolean;
  result: SetupCheckResult | null;
  resultStale: boolean;
  repositoryErrors: string[];
  history: HistoryState;
  resultHistory: { eventId: string; state: EventHistoryState | null } | null;
};

export type SuggestionState = "none" | "generating" | "suggested" | "unavailable" | "rejected";

export type ProcedureMetadata = { name: string; summary: string; tags: string[] };

export type ProcedureCard = {
  id: string;
  name: string;
  summary: string;
  tags: string[];
  detectorKind: "color" | "semantic";
  objectCount: number;
  stepCount: number;
  objects: string[];
  updatedAt: number;
  aiGeneratedMetadata: boolean;
};

export type LibraryState = {
  storage: StorageStatus;
  procedures: ProcedureCard[];
  loadedId: string | null; // library entry the active procedure came from or was saved as
  revision: number; // bumped when a save / load / refresh finishes
  errors: string[];
  draft: {
    available: boolean; // an active procedure exists and teaching is finished
    saved: boolean;
    suggestionState: SuggestionState;
    suggestion: ProcedureMetadata | null;
    key: string; // identifies this draft (Ask transcripts never carry over to another one)
  };
};

export type AskAnswer = {
  question: string;
  answer: string;
  relevantStepNumbers: number[];
  requiredObjects: string[];
  disclaimer: string | null;
  source: "gemini" | "stored";
  procedureKey: string;
  notice: string;
};

export type ServerUpdate = {
  type: "update";
  mode?: Mode;
  workspace?: Workspace;
  setup?: SetupState;
  library?: LibraryState;
  detector?: DetectorState;
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
  active?: boolean; // this page may send camera frames
  owner?: boolean; // this page's frames are the ones being evaluated
  speaker?: boolean; // this page speaks coaching aloud
  camera?: CameraStatus;
  mat?: MatStatus;
  error?: string;
};

export type CameraStatus = {
  owner: "webcam" | "sim" | "phone" | "none";
  phone: "disconnected" | "connected" | "streaming" | "error";
  phoneError: string;
  phoneFrames: number;
};

export type MatState = "off" | "uncalibrated" | "tracking" | "unsteady" | "lost" | "recalibrate";

export type MatStatus = {
  source: string;
  state: MatState;
  message: string;
  calibrated: boolean;
  trustworthy: boolean; // tracking is good enough to update Teach/Practice/Setup Check
  corners: [number, number][] | null; // tracked TL, TR, BR, BL in the source frame (normalized)
  viewSeq: number; // increments when a new stabilized mat image is available
  canonicalAspect: number | null; // width / height of the stabilized mat image
  band: number; // fraction of each edge excluded as the landmark band
  metrics: { inliers: number; reprojError: number | null; motion: number | null } | null;
};
