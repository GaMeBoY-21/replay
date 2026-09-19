// The run graph: a recording, not a flowchart.
//
// Steps run down a ruled timeline. The gutter holds right-aligned step numbers;
// calibration rules fall every five steps; each step sits on a channel by what it
// was - model call or tool call - and the spine joins them in order. Memory
// writes read on a second line beneath the step name. When a trace is drawn, the
// path through the graph lights in --ch-trace and its origin alone takes
// --signal: one orange mark in the interface, at the moment the demo is about.
//
// The picture is an SVG layer; every step is also a real button in an ordered
// list above it, so the graph is keyboard-operable and each row is labelled.

import { scaleBand } from "d3-scale";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { inline } from "../api/events";
import type { Step, Trace } from "../api/types";

export const ROW = 52;
/** Short screens - a 720p projector - get tighter rows, so the trace fits. */
const COMPACT_ROW = 42;
const COMPACT = "(max-height: 800px)";

function useRowHeight(): number {
  const query = typeof matchMedia === "function" ? matchMedia(COMPACT) : null;
  const [compact, setCompact] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const update = () => setCompact(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [query?.media]);
  return compact ? COMPACT_ROW : ROW;
}
const GUTTER = 56;
const LANE = { model: GUTTER + 40, tool: GUTTER + 72, breaker: GUTTER + 72 } as const;
export const LABEL_X = GUTTER + 100;

export interface SharedPrefix {
  /** The last step that is the parent's own log. */
  throughStep: number;
  parent: string;
}

export interface RunGraphProps {
  runId: string;
  steps: Step[];
  selected: number | null;
  onSelect: (step: number) => void;
  trace?: Trace | null;
  shared?: SharedPrefix | null;
  /** A note shown on a substituted step, e.g. where its response came from. */
  substitutedNote?: string | null;
  /** For a model step, the tools its response asked for - once the log is loaded. */
  asked?: Map<number, string> | null;
}

function writeLine(step: Step): string | null {
  const shown = step.writes.filter((w) => !w.key.endsWith(".basis"));
  if (!shown.length) return null;
  return shown.map((w) => `↦ ${w.key} = ${inline(w.value, 48)}`).join("   ");
}

/** The steps a trace passes through, earliest first: its chain, the read, the output. */
export function tracePath(trace: Trace): number[] {
  const steps = [...trace.chain.map((link) => link.step), trace.flagged.step, trace.output_step];
  return steps.filter((step, i) => steps.indexOf(step) === i).sort((a, b) => a - b);
}

function stepLabel(step: Step, role: string | null, note: string | null, asked: string | null): string {
  const parts = [`Step ${step.step}`, step.kind === "breaker" ? `${step.name} breaker` : `${step.kind} call`];
  if (step.kind === "tool") parts.push(step.name);
  if (asked) parts.push(asked);
  const written = step.writes.filter((w) => !w.key.endsWith(".basis"));
  if (written.length) parts.push(`writes ${written.map((w) => `${w.key} = ${inline(w.value, 40)}`).join(", ")}`);
  if (step.breaker) parts.push(step.breaker.detail);
  if (step.substituted) parts.push(note ?? "substituted");
  if (role) parts.push(role);
  return parts.join(", ");
}

export function RunGraph({ runId, steps, selected, onSelect, trace, shared, substitutedNote, asked }: RunGraphProps) {
  const list = useRef<HTMLOListElement>(null);
  const row = useRowHeight();
  const height = steps.length * row;
  const y = useMemo(
    () => scaleBand<number>().domain(steps.map((s) => s.step)).range([0, height]).paddingInner(0),
    [steps, height],
  );
  const cy = (step: number) => (y(step) ?? 0) + row / 2;
  const lane = (step: Step) => LANE[step.kind];
  const byStep = new Map(steps.map((s) => [s.step, s]));

  const path = trace ? tracePath(trace).filter((s) => byStep.has(s)) : [];
  const onPath = new Set(path);
  const origin = trace?.head.step ?? null;
  const output = trace?.output_step ?? null;
  const sharedEnd = shared ? (y(shared.throughStep) ?? 0) + row : 0;

  // When a trace is drawn, bring its origin into view. A jump, not a scroll
  // animation: the origin ring is the interface's one animation.
  useEffect(() => {
    if (origin === null) return;
    const index = steps.findIndex((s) => s.step === origin);
    list.current?.querySelectorAll("li")[index]?.scrollIntoView?.({ block: "center" });
  }, [origin, steps]);

  const role = (step: number): string | null => {
    if (step === origin) return "origin of the trace";
    if (step === output) return "the output the trace starts from";
    if (onPath.has(step)) return "on the trace path";
    return null;
  };

  const move = (event: KeyboardEvent<HTMLOListElement>) => {
    const keys: Record<string, number> = { ArrowDown: 1, ArrowUp: -1, Home: -Infinity, End: Infinity };
    if (!(event.key in keys)) return;
    event.preventDefault();
    const at = steps.findIndex((s) => s.step === selected);
    const delta = keys[event.key];
    const next = Math.max(0, Math.min(steps.length - 1, Number.isFinite(delta) ? (at < 0 ? 0 : at + delta) : delta < 0 ? 0 : steps.length - 1));
    onSelect(steps[next].step);
    list.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };

  return (
    <figure className="graph" aria-labelledby={`graph-title-${runId}`}>
      <figcaption id={`graph-title-${runId}`} className="visually-hidden">
        Run graph of {runId}: {steps.length} steps{trace ? `, with the trace from step ${output} back to step ${origin}` : ""}.
      </figcaption>
      <div className="graph-canvas" style={{ height }}>
        <svg className="graph-lines" width="100%" height={height} aria-hidden="true" focusable="false">
          <line className="gutter-rule" x1={GUTTER} x2={GUTTER} y1={0} y2={height} />
          {steps.filter((s) => s.step % 5 === 0).map((s) => (
            <line key={`cal-${s.step}`} className="calibration" x1={GUTTER} x2="100%" y1={cy(s.step)} y2={cy(s.step)} />
          ))}
          {shared && (
            <g className="shared-band">
              <rect x={LANE.model - 14} y={4} width={LANE.tool - LANE.model + 28} height={Math.max(0, sharedEnd - 8)} rx={6} />
            </g>
          )}
          <polyline
            className="spine"
            points={steps.map((s) => `${lane(s)},${cy(s.step)}`).join(" ")}
          />
          {path.slice(1).map((step, i) => {
            const from = byStep.get(path[i])!;
            const to = byStep.get(step)!;
            const [x1, y1, x2, y2] = [lane(from), cy(from.step), lane(to), cy(to.step)];
            const bend = Math.min(x1, x2) - 34;
            return <path key={`t-${step}`} className="trace-path" d={`M ${x1} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x2} ${y2}`} />;
          })}
          {steps.map((s) => {
            const x = lane(s);
            const c = cy(s.step);
            const classes = [
              "node",
              `node-${s.kind}`,
              s.substituted ? "node-substituted" : "",
              onPath.has(s.step) ? "node-traced" : "",
              s.step === origin ? "node-origin" : "",
              s.step === selected ? "node-selected" : "",
            ].join(" ");
            return (
              <g key={s.step} className={classes}>
                {s.step === origin && <circle className="origin-ring" cx={x} cy={c} r={9} />}
                {s.kind === "breaker" ? (
                  <rect x={x - 6} y={c - 6} width={12} height={12} transform={`rotate(45 ${x} ${c})`} />
                ) : (
                  <circle cx={x} cy={c} r={s.substituted ? 7 : 6} />
                )}
              </g>
            );
          })}
        </svg>

        <ol className="graph-rows" ref={list} onKeyDown={move} aria-label={`Steps of ${runId}`}>
          {steps.map((s) => {
            const written = writeLine(s);
            const r = role(s.step);
            const shared_ = shared && s.step <= shared.throughStep;
            return (
              <li key={s.step} className="graph-row" style={{ height: row }}>
                <button
                  type="button"
                  className={`row-button${s.step === selected ? " is-selected" : ""}${onPath.has(s.step) ? " is-traced" : ""}${s.step === origin ? " is-origin" : ""}`}
                  aria-current={s.step === selected ? "step" : undefined}
                  aria-label={stepLabel(s, r, s.substituted ? substitutedNote ?? null : null, asked?.get(s.step) ?? null)}
                  tabIndex={s.step === (selected ?? steps[0]?.step) ? 0 : -1}
                  onClick={() => onSelect(s.step)}
                >
                  <span className="gutter-num" aria-hidden="true">{s.step}</span>
                  <span className="row-body" aria-hidden="true" style={{ paddingLeft: LABEL_X - GUTTER }}>
                    <span className="row-name">
                      <span className={`kind kind-${s.kind}`}>{s.kind === "breaker" ? `${s.name} breaker` : s.kind}</span>
                      {s.kind === "tool" && <span className="tool-name">{s.name}</span>}
                      {s.kind === "model" && asked?.get(s.step) && <span className="asked">{asked.get(s.step)}</span>}
                      {s.substituted && <span className="tag tag-substituted">{substitutedNote ?? "substituted"}</span>}
                      {s.step === origin && <span className="tag tag-origin">origin</span>}
                      {s.step === output && s.step !== origin && <span className="tag tag-traced">output</span>}
                      {shared_ && s.step === shared.throughStep && (
                        <span className="tag tag-shared">steps 1–{shared.throughStep} shared with {shared.parent}</span>
                      )}
                    </span>
                    {s.breaker ? (
                      <span className="row-writes row-fault">{s.breaker.detail}</span>
                    ) : written ? (
                      <span className="row-writes">{written}</span>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </figure>
  );
}
