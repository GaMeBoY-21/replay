import { useMemo } from "react";
import { effects } from "../api/events";
import { manifest } from "../api/source";
import type { ReplayEvent, RunView as RunViewData, Trace } from "../api/types";
import { useResource } from "../api/useResource";
import { Answer } from "../components/Answer";
import { RunGraph } from "../components/RunGraph";
import { TracePanel } from "../components/TracePanel";
import { Failure, Loading, StatusChip } from "../components/States";
import { StepDetail, type Provenance } from "../components/StepDetail";
import { Link, navigate } from "../router";

/** What the canonical manifest says this run is, if it is one of the canonical runs. */
export function roleOf(runId: string): string | null {
  if (runId === manifest.wrong.run_id) return "The wrong run";
  if (runId === manifest.right.run_id) return "The right run";
  if (runId === manifest.fork.run_id) return `The decision fork of ${manifest.fork.parent}`;
  if (runId === manifest.halted?.run_id) return "The halted run";
  if (manifest.halt.attempts.some((a) => a.run_id === runId)) return "A halt attempt that completed";
  return null;
}

/** Where a fork's substituted response came from: the manifest records it for the canonical fork. */
export function provenanceOf(run: RunViewData): Provenance | null {
  const parent = run.metadata.parent_run_id;
  if (!parent) return null;
  if (run.metadata.run_id === manifest.fork.run_id) {
    const from = manifest.fork.substituted_from;
    return { parent, fromRun: from.run_id, fromStep: from.step };
  }
  return { parent, fromRun: null, fromStep: null };
}

export function RunView({ id, step, traced }: { id: string; step: number | null; traced: boolean }) {
  const run = useResource<RunViewData>(`/api/runs/${encodeURIComponent(id)}`);
  const trace = useResource<Trace>(traced ? `/api/runs/${encodeURIComponent(id)}/trace/output` : null);
  const log = useResource<{ events: ReplayEvent[] }>(`/api/runs/${encodeURIComponent(id)}/events?limit=1000`);
  const byseq = useMemo(() => (log.state === "ready" ? effects(log.data.events) : null), [log]);
  const asked = useMemo(() => {
    if (!byseq || run.state !== "ready") return null;
    return new Map(
      run.data.steps.flatMap((s) => {
        const detail = s.kind === "model" && s.seq !== null ? byseq.get(s.seq) : undefined;
        if (!detail) return [];
        const names = detail.calls.map((c) => c.name);
        return [[s.step, names.length ? `asks for ${names.join(", ")}` : detail.text ? "answers" : "says nothing"] as const];
      }),
    );
  }, [byseq, run]);

  if (run.state === "loading") return <Loading what={`run ${id}`} />;
  if (run.state === "error") {
    return (
      <Failure title={run.status === 404 ? `There is no run called ${id}` : `Could not load ${id}`} message={run.message}>
        <p><Link href="/">Back to all runs</Link></p>
      </Failure>
    );
  }

  const data = run.data;
  const { summary, steps, metadata } = data;
  const drawnHead = traced && trace.state === "ready" ? trace.data.head.step : null;
  const selected = step ?? drawnHead ?? steps[0]?.step ?? null;
  const current = steps.find((s) => s.step === selected) ?? steps[0];
  const writer = new Map(steps.flatMap((s) => s.writes.map((w) => [w.eid, s.step] as const)));
  const provenance = provenanceOf(data);
  const forkStep = metadata.forked_at_seq !== null && metadata.forked_at_seq !== undefined
    ? steps.find((s) => s.seq === metadata.forked_at_seq)?.step ?? null
    : null;
  const forkedAt = metadata.forked_at_seq;
  const sharedThrough = forkedAt === null || forkedAt === undefined
    ? null
    : Math.max(0, ...steps.filter((s) => s.seq !== null && s.seq < forkedAt).map((s) => s.step));
  const substitutedNote = provenance?.fromRun ? `response from ${provenance.fromRun}` : null;
  const here = (params: { step?: number | null; trace?: boolean }) => {
    const query = new URLSearchParams();
    const n = params.step === undefined ? step : params.step;
    if (n) query.set("step", String(n));
    if (params.trace ?? traced) query.set("trace", "output");
    const q = query.toString();
    return `/runs/${encodeURIComponent(id)}${q ? `?${q}` : ""}`;
  };
  const select = (n: number) => navigate(here({ step: n }), true);
  const drawn = traced && trace.state === "ready" ? trace.data : null;
  const role = roleOf(id);

  return (
    <article className="run" aria-labelledby="run-title">
      <header className="run-head">
        <div className="run-title-row">
          <h1 id="run-title" className="mono">{id}</h1>
          <StatusChip status={summary.status} />
          {role && <span className="role">{role}</span>}
        </div>
        <p className="run-meta">
          {summary.step_count} steps · {summary.effect_count} effects
          {metadata.parent_run_id && (
            <> · forked from <Link href={`/runs/${metadata.parent_run_id}`}>{metadata.parent_run_id}</Link></>
          )}
        </p>

        {provenance && forkStep !== null && (
          <div className="fork-note">
            <p>
              Steps 1–{sharedThrough} are {provenance.parent}'s own log, shared by storage rather than copied.
              {" "}Step {forkStep} is a <strong>model response</strong>
              {provenance.fromRun ? (
                <> taken unchanged from <Link href={`/runs/${provenance.fromRun}?step=${provenance.fromStep}`}>{provenance.fromRun}</Link>, where the model decided differently</>
              ) : (
                <> substituted for the one {provenance.parent} recorded</>
              )}
              . <strong>Everything after it ran live</strong>: the tools it asked for executed for real, against this run's memory.
            </p>
            <p><Link href={`/diff?a=${provenance.parent}&b=${id}`}>Compare with {provenance.parent}</Link></p>
          </div>
        )}

        {summary.halted && (
          <div className="halt-note" role="note">
            <p>
              <strong>Halted by the {summary.halted.name} breaker:</strong>{" "}
              <span className="mono">{summary.halted.detail}</span>
            </p>
          </div>
        )}

        {summary.answer ? (
          <Answer text={summary.answer} />
        ) : (
          <p className="quiet">This run gave no final answer.</p>
        )}
      </header>

      <div className="run-body">
        <section className="run-graph" aria-label="Run graph">
          <div className="legend" aria-hidden="true">
            <span className="kind kind-model">model</span>
            <span className="kind kind-tool">tool</span>
            <span className="legend-memory">↦ memory write</span>
            {steps.some((s) => s.kind === "breaker") && <span className="kind kind-breaker">breaker</span>}
          </div>
          <RunGraph
            runId={id}
            steps={steps}
            selected={current?.step ?? null}
            onSelect={select}
            shared={provenance && sharedThrough ? { throughStep: sharedThrough, parent: provenance.parent } : null}
            substitutedNote={substitutedNote}
            asked={asked}
            trace={drawn}
          />
        </section>
        <aside className="run-detail">
          <TracePanel
            runId={id}
            traced={traced}
            trace={trace}
            onDraw={() => {
              const head = trace.state === "ready" ? trace.data.head.step : null;
              navigate(here({ trace: true, step: head }), true);
            }}
            onClear={() => navigate(here({ trace: false }), true)}
            onSelect={select}
          />
          {current ? (
            <StepDetail
              key={current.step}
              runId={id}
              step={current}
              effect={current.seq !== null ? byseq?.get(current.seq) ?? null : null}
              writerStep={(eid) => writer.get(eid) ?? null}
              provenance={current.substituted ? provenance : null}
            />
          ) : (
            <p className="quiet">This run has no steps.</p>
          )}
          {log.state === "loading" && <Loading what="the recorded effects" />}
          {log.state === "error" && <Failure title="Could not load the recorded effects" message={log.message} />}
        </aside>
      </div>
    </article>
  );
}
