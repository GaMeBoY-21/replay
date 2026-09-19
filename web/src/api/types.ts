// The shapes the API returns. Event and metadata types are generated from the
// Pydantic models (src/types/events.d.ts, rebuilt on every build); the views here
// are projections, built by replay.projector and replay.kernel, with no schema of
// their own - web/scripts/record_fixtures.py records them from the real handlers.

import type { ReplayEvent, RunMetadata } from "../types/events";

export type { ReplayEvent, RunMetadata };

export type RunStatus = "running" | "completed" | "failed" | "tripped";

export interface Summary {
  run_id: string;
  status: RunStatus;
  answer: string | null;
  step_count: number;
  effect_count: number;
  parent_run_id: string | null;
  forked_at_seq: number | null;
  /** The breaker row, if a breaker stopped the run: its name ("depth", "loop") and message. */
  halted: { step: number; seq: number | null; name: string; detail: string } | null;
}

export interface Write {
  eid: number;
  key: string;
  value: unknown;
  reads: number[];
  tombstone: boolean;
}

export interface Read {
  eid: number;
  key: string;
  /** The eid of the write this read saw, or null if the key was unset. */
  source: number | null;
}

export interface Step {
  step: number;
  seq: number | null;
  kind: "model" | "tool" | "breaker";
  name: string;
  reads: Read[];
  writes: Write[];
  substituted: boolean;
  breaker: { name: string; detail: string } | null;
}

export interface RunView {
  metadata: RunMetadata;
  summary: Summary;
  steps: Step[];
  step_to_seq: Record<string, number>;
}

export interface TraceLink {
  eid: number;
  step: number;
  key: string;
  value: unknown;
}

export interface Trace {
  run_id: string;
  flagged: { eid: number; step: number; key: string };
  output_step: number;
  chain: TraceLink[];
  head: TraceLink;
}

export interface DiffSide {
  effect: string;
  shape: string;
  result: string;
  substituted: boolean;
}

export interface DiffRow {
  seq: number;
  a: DiffSide | null;
  b: DiffSide | null;
  same: boolean;
  shared: boolean;
}

export interface Diff {
  a: string;
  b: string;
  shared_by: "storage" | "content";
  shared_prefix: number;
  divergence_seq: number | null;
  rows: DiffRow[];
}

export interface Manifest {
  model: string;
  roots: string[];
  wrong: { run_id: string; why: string };
  right: { run_id: string; why: string };
  fork: {
    run_id: string;
    parent: string;
    at_seq: number;
    outcome: string;
    /** The step the fork point falls on; recorded from the log, never computed here. */
    at_step: number;
    substituted_from: { run_id: string; seq: number; step: number };
  };
  halt: { breaker: "loop" | "depth"; ceiling: number; attempts: { run_id: string; status: string }[] };
  halted?: { run_id: string };
}

export interface EvidenceRead {
  step: number;
  tool: string;
  result: Record<string, unknown> | null;
}

export interface CorpusRow {
  run_id: string;
  overall: "right" | "wrong" | "failed";
  steps: number;
  currencies_recorded: string[];
  currency_used: string | null;
  assumption_step: number | null;
  assumption_basis: string | null;
  basis_stated: string | null;
  converted_total: number | null;
  submitted: boolean;
  vendor_lookups: number;
  answer: string | null;
  read: EvidenceRead[];
  currency_write_step: number | null;
}

export interface Corpus {
  provider: { model: string; context_length: number; server: string; sampling: string };
  runs: number;
  overall: Record<string, number>;
  steps: number[];
  vendor_lookups: number[];
  fetched_remittance: number;
  rows: CorpusRow[];
}

export interface Capabilities {
  live: boolean;
  model: string | null;
  reason: string | null;
}
