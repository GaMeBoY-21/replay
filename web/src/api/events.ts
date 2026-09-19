// Reading the recorded log: what each effect asked for and what came back.
//
// A model response is recorded as the provider's stream - one chunk per token -
// so its text and the tool calls it asked for are reassembled here. A tool result
// is recorded as the text the model was shown, which for these tools is JSON.

import type { ReplayEvent } from "./types";

type Chunk = Record<string, any>;

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
}

export interface EffectDetail {
  seq: number;
  kind: "model" | "tool";
  name: string | null;
  arguments: Record<string, unknown> | null;
  /** Model: the text it produced. */
  text: string;
  /** Model: the tools it asked for, in order. */
  calls: ToolCall[];
  /** Tool: the result, parsed if it was JSON. */
  result: unknown;
  substituted: boolean;
  requestedAt: string | null;
  completedAt: string | null;
}

export function modelText(chunks: Chunk[]): string {
  let text = "";
  for (const chunk of chunks) {
    const delta = chunk?.contentBlockDelta?.delta;
    if (delta && typeof delta.text === "string") text += delta.text;
  }
  return text.trim();
}

export function toolCalls(chunks: Chunk[]): ToolCall[] {
  const calls: { name: string; input: string }[] = [];
  for (const chunk of chunks) {
    const start = chunk?.contentBlockStart?.start?.toolUse;
    if (start) calls.push({ name: start.name, input: "" });
    const delta = chunk?.contentBlockDelta?.delta?.toolUse;
    if (delta && calls.length) calls[calls.length - 1].input += delta.input ?? "";
  }
  return calls.map((call) => {
    try {
      return { name: call.name, input: JSON.parse(call.input || "{}") };
    } catch {
      return { name: call.name, input: { raw: call.input } };
    }
  });
}

function toolResult(value: any): unknown {
  const text = value?.content?.[0]?.text;
  if (typeof text !== "string") return value;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Every effect in the log, by seq. */
export function effects(events: ReplayEvent[]): Map<number, EffectDetail> {
  const out = new Map<number, EffectDetail>();
  for (const event of events as any[]) {
    if (event.type === "EffectRequested") {
      const effect = event.effect;
      out.set(event.seq, {
        seq: event.seq,
        kind: effect.effect_kind === "model" ? "model" : "tool",
        name: effect.name ?? null,
        arguments: effect.arguments ?? null,
        text: "",
        calls: [],
        result: null,
        substituted: false,
        requestedAt: event.ts ?? null,
        completedAt: null,
      });
    } else if (event.type === "EffectCompleted" && out.has(event.seq)) {
      const detail = out.get(event.seq)!;
      const value = event.result?.value;
      detail.substituted = Boolean(event.substituted);
      detail.completedAt = event.ts ?? null;
      if (detail.kind === "model" && Array.isArray(value)) {
        detail.text = modelText(value);
        detail.calls = toolCalls(value);
      } else {
        detail.result = event.result?.error ? { error: event.result.error } : toolResult(value);
      }
    }
  }
  return out;
}

export function durationMs(detail: EffectDetail): number | null {
  if (!detail.requestedAt || !detail.completedAt) return null;
  return Date.parse(detail.completedAt) - Date.parse(detail.requestedAt);
}

/** A value as it reads on one line: strings quoted, objects compact. */
export function inline(value: unknown, max = 80): string {
  const text = typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value) ?? String(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** A requested tool call on one line: the recording tool as `field = value`, others by name. */
export function callLine(call: ToolCall): string {
  const input = call.input as Record<string, unknown>;
  if (call.name === "record_invoice_field" && typeof input.field === "string") {
    return `${call.name} ${input.field} = ${inline(input.value, 32)}`;
  }
  return call.name;
}
