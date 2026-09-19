// Fork from a model step: serve, in place of the response this run recorded, the
// response another run recorded - unchanged - and let everything after it run
// live. Nothing is typed in: the substituted response is another run's own.
//
// The UI speaks steps. It sends the step to fork at; the API maps it to a seq and
// echoes both. The response it sends is read from the other run's log.

import { useMemo, useState, type FormEvent } from "react";
import { callLine, effects } from "../api/events";
import { ApiError, manifest, post } from "../api/source";
import type { ReplayEvent, RunView, Step, Summary } from "../api/types";
import { useResource } from "../api/useResource";
import { navigate } from "../router";
import { LiveOnly } from "./Live";

function defaultSource(runId: string, runs: Summary[]): string {
  if (runId === manifest.wrong.run_id) return manifest.right.run_id;
  return runs.find((r) => r.run_id !== runId && r.status === "completed")?.run_id ?? "";
}

export function ForkPanel({ runId, step }: { runId: string; step: Step }) {
  const runs = useResource<{ runs: Summary[] }>("/api/runs");
  const candidates = runs.state === "ready" ? runs.data.runs.filter((r) => r.run_id !== runId && r.status === "completed") : [];
  const [chosen, setChosen] = useState<string | null>(null);
  const source = chosen ?? (runs.state === "ready" ? defaultSource(runId, runs.data.runs) : "");
  const [sourceStep, setSourceStep] = useState(String(step.step));
  const [state, setState] = useState<{ name: "idle" } | { name: "running" } | { name: "failed"; message: string }>({ name: "idle" });

  const view = useResource<RunView>(source ? `/api/runs/${encodeURIComponent(source)}` : null);
  const log = useResource<{ events: ReplayEvent[] }>(source ? `/api/runs/${encodeURIComponent(source)}/events?limit=1000` : null);

  const picked = useMemo(() => {
    if (view.state !== "ready" || log.state !== "ready") return null;
    const n = Number(sourceStep);
    const target = view.data.steps.find((s) => s.step === n);
    if (!target) return { error: `${source} has no step ${sourceStep}.` };
    if (target.kind !== "model" || target.seq === null) return { error: `Step ${n} of ${source} is not a model call.` };
    const detail = effects(log.data.events).get(target.seq);
    const recorded = (log.data.events as any[]).find((e) => e.type === "EffectCompleted" && e.seq === target.seq);
    if (!detail || !recorded) return { error: `${source} recorded no response at step ${n}.` };
    const does = detail.calls.length ? `asks for ${detail.calls.map(callLine).join(", ")}` : detail.text ? `answers: “${detail.text.slice(0, 120)}${detail.text.length > 120 ? "…" : ""}”` : "says nothing";
    return { value: recorded.result.value as unknown, does };
  }, [view, log, source, sourceStep]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!picked || !("value" in picked)) return;
    setState({ name: "running" });
    try {
      const forked = await post<{ run_id: string }>(`/api/runs/${encodeURIComponent(runId)}/fork`, {
        at_step: step.step,
        mutation: picked.value,
      });
      navigate(`/runs/${encodeURIComponent(forked.run_id)}`);
    } catch (error) {
      setState({ name: "failed", message: error instanceof ApiError ? error.message : String(error) });
    }
  };

  const ready = picked !== null && "value" in picked && state.name !== "running";

  return (
    <section className="fork" aria-labelledby="fork-title">
      <h3 id="fork-title">Fork from here</h3>
      <p className="fineprint">
        Give this step the response another run recorded, unchanged. Steps before it stay this run's log, shared by
        storage; everything after it runs live.
      </p>
      <LiveOnly action="Forking">
        <form className="fork-form" onSubmit={submit}>
          <div className="fork-fields">
            <label>
              <span>Response from</span>
              <select value={source} onChange={(e) => setChosen(e.target.value)} disabled={state.name === "running"}>
                {candidates.map((r) => (
                  <option key={r.run_id} value={r.run_id}>{r.run_id}</option>
                ))}
              </select>
            </label>
            <label>
              <span>at its step</span>
              <input type="number" min={1} step={1} value={sourceStep} onChange={(e) => setSourceStep(e.target.value)} disabled={state.name === "running"} />
            </label>
          </div>
          <p className="fork-preview" aria-live="polite">
            {picked === null ? "Reading that response…" : "error" in picked ? picked.error : <>That response {picked.does}</>}
          </p>
          <button type="submit" className="button button-primary" disabled={!ready}>
            {state.name === "running" ? "Forking…" : `Fork at step ${step.step}`}
          </button>
          {state.name === "running" && (
            <p className="state-inline" role="status">The fork is running live from step {step.step}. This can take a minute or two.</p>
          )}
          {state.name === "failed" && <p className="state-inline state-inline-error" role="alert">{state.message}</p>}
        </form>
      </LiveOnly>
    </section>
  );
}
