// A halted run, and resuming it.
//
// A breaker stopped the run before an effect ran. Resuming replays the run to the
// halt and continues live - but against the same ceiling it would trip again at
// once, so the API refuses a resume without a raised ceiling, and so does this
// form: there is no way to submit one without it.

import { useId, useState, type FormEvent } from "react";
import { ApiError, corpus, manifest, post } from "../api/source";
import type { RunMetadata, Summary } from "../api/types";
import { navigate } from "../router";
import { LiveOnly } from "./Live";

/** The ceiling each breaker is held to, by its name in the log. */
export const CEILING: Record<string, { key: string; noun: string }> = {
  depth: { key: "max_effects", noun: "effects" },
  loop: { key: "max_repeats", noun: "repeats of one call" },
};

type State =
  | { name: "idle" }
  | { name: "running"; ceiling: number }
  | { name: "failed"; message: string };

function ContinuePanel({ runId }: { runId: string }) {
  const [state, setState] = useState<State>({ name: "idle" });
  const go = async () => {
    setState({ name: "running", ceiling: 0 });
    try {
      const resumed = await post<{ run_id: string }>(`/api/runs/${encodeURIComponent(runId)}/resume`, {});
      navigate(`/runs/${encodeURIComponent(resumed.run_id)}`);
    } catch (error) {
      setState({ name: "failed", message: error instanceof ApiError ? error.message : String(error) });
    }
  };
  return (
    <section className="halt" aria-labelledby="halt-title">
      <h2 id="halt-title">Cancelled</h2>
      <p>
        Stopped by the operator before its next step. Everything it recorded up to then is kept, and the refusal is
        the last thing in its log. No ceiling was hit, so there is none to raise.
      </p>
      <LiveOnly action="Continuing">
        <div className="resume-row">
          <button type="button" className="button button-primary" onClick={go} disabled={state.name === "running"}>
            {state.name === "running" ? "Continuing…" : "Continue"}
          </button>
          <span className="fineprint">Replays this run to where it stopped, then carries on live.</span>
        </div>
        {state.name === "failed" && <p className="state-inline state-inline-error" role="alert">{state.message}</p>}
      </LiveOnly>
    </section>
  );
}

export function ResumePanel({ runId, summary, metadata }: { runId: string; summary: Summary; metadata: RunMetadata }) {
  const halted = summary.halted!;
  if (halted.name === "cancelled") return <ContinuePanel runId={runId} />;
  return <BreakerHalt runId={runId} summary={summary} metadata={metadata} />;
}

function BreakerHalt({ runId, summary, metadata }: { runId: string; summary: Summary; metadata: RunMetadata }) {
  const halted = summary.halted!;
  const ceiling = CEILING[halted.name];
  const recorded = Number((metadata.breaker_config as Record<string, unknown> | null)?.[ceiling?.key ?? ""] ?? NaN);
  const sorted = [...corpus.steps].sort((a, b) => a - b);
  const longest = sorted[sorted.length - 1];
  const median = sorted[Math.floor(sorted.length / 2)];
  const stopped = sorted.filter((n) => n > recorded).length;
  const [value, setValue] = useState(String(Math.max(80, Number.isFinite(recorded) ? recorded * 2 : 80)));
  const [state, setState] = useState<State>({ name: "idle" });
  const inputId = useId();
  const helpId = useId();

  const canonical = runId === manifest.halted?.run_id && manifest.halt.breaker === halted.name;
  const next = Number(value);
  const invalid = !Number.isInteger(next) || !(next > recorded);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ceiling || invalid) return;
    setState({ name: "running", ceiling: next });
    try {
      const resumed = await post<{ run_id: string }>(`/api/runs/${encodeURIComponent(runId)}/resume`, {
        breaker_overrides: { [ceiling.key]: next },
      });
      navigate(`/runs/${encodeURIComponent(resumed.run_id)}`);
    } catch (error) {
      setState({ name: "failed", message: error instanceof ApiError ? error.message : String(error) });
    }
  };

  return (
    <section className="halt" aria-labelledby="halt-title">
      <h2 id="halt-title">Halted by the {halted.name} breaker</h2>
      <p className="mono halt-detail">{halted.detail}</p>
      <p>
        The breaker stopped the effect before it ran. Nothing after that point executed, and the log ends at the
        refusal.
      </p>
      {canonical && halted.name === "depth" && (
        <p className="quiet">
          This ceiling — {recorded} effects — is deliberately tight, to show a halt
          {recorded === median ? <>: it is the median length of the {corpus.runs} recorded runs</> : null}, and it would
          have stopped {stopped} of them, ordinary runs included. A production depth ceiling is a runaway guard and
          would sit well above every observed run length; the longest recorded run used {longest}.
        </p>
      )}

      {!ceiling ? (
        <p className="quiet">This breaker has no ceiling the interface knows how to raise.</p>
      ) : (
        <LiveOnly action="Resuming">
        <form className="resume" onSubmit={submit} aria-describedby={helpId}>
          <label htmlFor={inputId}>
            Raise the {halted.name} ceiling to
          </label>
          <div className="resume-row">
            <input
              id={inputId}
              type="number"
              inputMode="numeric"
              min={Number.isFinite(recorded) ? recorded + 1 : 1}
              step={1}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-invalid={invalid}
              disabled={state.name === "running"}
            />
            <span className="quiet">{ceiling.noun}</span>
            <button type="submit" className="button button-primary" disabled={invalid || state.name === "running"}>
              {state.name === "running" ? "Resuming…" : "Resume"}
            </button>
          </div>
          <p id={helpId} className="fineprint">
            Resuming replays this run to the halt from its log, then continues live with the ceiling you set. Without
            a higher ceiling it would trip again at once, so one is required.
            {invalid && Number.isFinite(recorded) && <> It must be a whole number above {recorded}.</>}
          </p>
          <div aria-live="polite">
            {state.name === "running" && (
              <p className="state-inline" role="status">
                The agent is running live from the halt, against the model. This can take a minute or two.
              </p>
            )}
          </div>
          {state.name === "failed" && (
            <p className="state-inline state-inline-error" role="alert">{state.message}</p>
          )}
        </form>
        </LiveOnly>
      )}
    </section>
  );
}
