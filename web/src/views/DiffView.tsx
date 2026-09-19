// Two runs aligned on the effect axis.
//
// The shared prefix is drawn once, spanning both columns - one continuous band,
// because for a fork it is one log, stored once and read by both. That is the
// answer to "how do forks not explode storage", drawn rather than asserted. From
// the divergence on, each run has its own column.

import { useMemo } from "react";
import { callLine, effects, inline, type EffectDetail } from "../api/events";
import { manifest } from "../api/source";
import type { Diff, ReplayEvent, RunView, Step } from "../api/types";
import { useResource, type Resource } from "../api/useResource";
import { Answer } from "../components/Answer";
import { Failure, Loading, StatusChip } from "../components/States";
import { Link } from "../router";
import { provenanceOf, roleOf } from "./RunView";

function useRun(id: string) {
  const run = useResource<RunView>(`/api/runs/${encodeURIComponent(id)}`);
  const log = useResource<{ events: ReplayEvent[] }>(`/api/runs/${encodeURIComponent(id)}/events?limit=1000`);
  const byseq = useMemo(() => (log.state === "ready" ? effects(log.data.events) : null), [log]);
  return { run, byseq };
}

function stepAt(run: RunView, seq: number): Step | undefined {
  return run.steps.find((s) => s.seq === seq);
}

function Cell({ step, effect, note, gutter }: {
  step: Step | undefined;
  effect: EffectDetail | undefined;
  note?: string | null;
  /** The step number in the gutter; a cell names its own only when it differs. */
  gutter?: number;
}) {
  if (!step) return <span className="quiet">— no effect at this point</span>;
  const writes = step.writes.filter((w) => !w.key.endsWith(".basis"));
  return (
    <div className="diff-cell">
      <span className="row-name">
        {gutter !== undefined && step.step !== gutter && <span className="diff-step mono">step {step.step}</span>}
        <span className={`kind kind-${step.kind}`}>{step.kind === "breaker" ? `${step.name} breaker` : step.kind}</span>
        {step.kind === "tool" && <span className="tool-name">{step.name}</span>}
        {note && <span className="tag tag-substituted">{note}</span>}
      </span>
      {step.kind === "model" && effect && effect.calls.length > 0 && (
        <span className="diff-asked">asks for {effect.calls.map(callLine).join(", ")}</span>
      )}
      {step.kind === "model" && effect && effect.calls.length === 0 && effect.text && (
        <span className="diff-asked">answers</span>
      )}
      {writes.map((w) => (
        <span key={w.eid} className="row-writes">↦ {w.key} = {inline(w.value, 44)}</span>
      ))}
      {step.breaker && <span className="row-writes row-fault">{step.breaker.detail}</span>}
    </div>
  );
}

function Head({ resource, id }: { resource: Resource<RunView>; id: string }) {
  const role = roleOf(id);
  return (
    <th scope="col" className="diff-col-head">
      <Link href={`/runs/${id}`} className="mono">{id}</Link>
      {resource.state === "ready" && <StatusChip status={resource.data.summary.status} halted={resource.data.summary.halted?.name} />}
      {role && <span className="role">{role}</span>}
    </th>
  );
}

export function DiffView({ a, b }: { a: string; b: string }) {
  const diff = useResource<Diff>(`/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const left = useRun(a);
  const right = useRun(b);

  const title = <h1 id="diff-title"><span className="mono">{a}</span> <span className="quiet">against</span> <span className="mono">{b}</span></h1>;
  if (diff.state === "loading" || left.run.state === "loading" || right.run.state === "loading") {
    return <><div className="run-head">{title}</div><Loading what="the two runs" /></>;
  }
  for (const r of [diff, left.run, right.run]) {
    if (r.state === "error") {
      return <><div className="run-head">{title}</div><Failure title="Could not compare these runs" message={r.message} /></>;
    }
  }
  if (diff.state !== "ready" || left.run.state !== "ready" || right.run.state !== "ready") return null;

  const d = diff.data;
  const A = left.run.data;
  const B = right.run.data;
  const shared = d.rows.filter((r) => r.shared || (d.shared_by === "content" && r.same && r.seq < d.shared_prefix));
  const after = d.rows.filter((r) => !shared.includes(r));
  const lastShared = shared.length ? stepAt(A, shared[shared.length - 1].seq)?.step ?? null : null;
  const provenance = provenanceOf(B);
  const note = (run: RunView, seq: number) => {
    const s = stepAt(run, seq);
    if (!s?.substituted) return null;
    const p = provenanceOf(run);
    return p?.fromRun ? `response from ${p.fromRun}` : "substituted";
  };

  return (
    <article className="diff" aria-labelledby="diff-title">
      <header className="run-head">
        {title}
        <p className="diff-summary">
          {d.shared_by === "storage" ? (
            <>
              <strong>Steps 1–{lastShared} are one log, stored once.</strong> {b} does not copy them: it reads{" "}
              {a}'s recorded effects up to the fork point and owns its log from there.
            </>
          ) : d.shared_prefix > 0 ? (
            <>
              <strong>These runs share no storage.</strong> Their first {d.shared_prefix} effects are the same in
              content, recorded separately.
            </>
          ) : (
            <>
              <strong>These runs share nothing.</strong> They were recorded separately and differ from their first
              effect: the model's first response was not the same.
            </>
          )}
          {d.divergence_seq !== null && <> They diverge at step {stepAt(A, d.divergence_seq)?.step}.</>}
        </p>
        {provenance?.fromRun && provenance.parent === a && (
          <p className="quiet">
            At that step {b} was given the response {provenance.fromRun} recorded at its step {provenance.fromStep},
            unchanged. Everything after it in {b} ran live.
          </p>
        )}
      </header>

      <div className="diff-scroll">
        <table className="diff-table">
          <caption className="visually-hidden">
            {a} and {b}, aligned effect by effect. The shared prefix spans both columns.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="diff-gutter">Step</th>
              <Head resource={left.run} id={a} />
              <Head resource={right.run} id={b} />
            </tr>
          </thead>
          {shared.length > 0 && (
            <tbody className="diff-shared">
              {shared.map((row, i) => (
                <tr key={row.seq}>
                  <th scope="row" className="diff-gutter mono">{stepAt(A, row.seq)?.step}</th>
                  <td colSpan={2}>
                    <Cell step={stepAt(A, row.seq)} effect={left.byseq?.get(row.seq)} />
                    {i === 0 && <span className="visually-hidden">Shared by both runs.</span>}
                  </td>
                </tr>
              ))}
              <tr className="diff-shared-label">
                <td />
                <td colSpan={2}>
                  {d.shared_by === "storage" ? `shared by storage — ${shared.length} effects, one copy` : `same content — ${shared.length} effects, stored twice`}
                </td>
              </tr>
            </tbody>
          )}
          <tbody className="diff-own">
            {after.map((row) => (
              <tr key={row.seq} className={row.seq === d.divergence_seq ? "diff-diverges" : row.same ? "" : "diff-different"}>
                <th scope="row" className="diff-gutter mono">
                  {stepAt(A, row.seq)?.step ?? stepAt(B, row.seq)?.step}
                  {row.seq === d.divergence_seq && <span className="diff-marker">diverges</span>}
                </th>
                <td><Cell step={stepAt(A, row.seq)} effect={left.byseq?.get(row.seq)} note={note(A, row.seq)} gutter={stepAt(A, row.seq)?.step ?? stepAt(B, row.seq)?.step} /></td>
                <td><Cell step={stepAt(B, row.seq)} effect={right.byseq?.get(row.seq)} note={note(B, row.seq)} gutter={stepAt(A, row.seq)?.step ?? stepAt(B, row.seq)?.step} /></td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" className="diff-gutter">Answer</th>
              <td>{A.summary.answer ? <Answer text={A.summary.answer} /> : <span className="quiet">no answer</span>}</td>
              <td>{B.summary.answer ? <Answer text={B.summary.answer} /> : <span className="quiet">no answer</span>}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {a === manifest.wrong.run_id && b !== manifest.right.run_id && (
        <p className="quiet">
          Compare {a} with <Link href={`/diff?a=${a}&b=${manifest.right.run_id}`}>{manifest.right.run_id}</Link>, a
          separate run of the same task that decided the other way. Nothing is shared: two recordings, stored in full.
        </p>
      )}
    </article>
  );
}
