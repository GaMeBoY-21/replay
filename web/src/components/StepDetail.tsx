// One step, as recorded: what the effect asked for, what came back, what it read
// and wrote, and how long it took.

import { durationMs, type EffectDetail } from "../api/events";
import type { Step } from "../api/types";
import { Link } from "../router";

export interface Provenance {
  /** The run the substituted response was recorded in, if known. */
  fromRun: string | null;
  fromStep: number | null;
  parent: string;
}

function Json({ value }: { value: unknown }) {
  return <pre className="json">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

export function StepDetail({
  step,
  effect,
  writerStep,
  provenance,
  runId,
}: {
  step: Step;
  effect: EffectDetail | null;
  /** The step that wrote a value, by the write's eid - for reads. */
  writerStep: (eid: number) => number | null;
  provenance: Provenance | null;
  runId: string;
}) {
  const ms = effect ? durationMs(effect) : null;
  return (
    <section className="detail" aria-labelledby="detail-title">
      <header className="detail-head">
        <h2 id="detail-title">
          <span className="detail-step">Step {step.step}</span>{" "}
          {step.kind === "breaker" ? `${step.name} breaker` : step.kind === "tool" ? step.name : "model call"}
        </h2>
        <dl className="facts">
          <div><dt>kind</dt><dd><span className={`kind kind-${step.kind}`}>{step.kind}</span></dd></div>
          {step.seq !== null && <div><dt>effect</dt><dd className="mono">seq {step.seq}</dd></div>}
          {ms !== null && <div><dt>took</dt><dd className="mono">{ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`}</dd></div>}
        </dl>
      </header>

      {step.substituted && (
        <div className="callout callout-substituted">
          <p className="callout-title">Substituted response</p>
          {provenance?.fromRun ? (
            <p>
              This response was not generated in {runId}. It is the response the model gave in{" "}
              <Link href={`/runs/${provenance.fromRun}?step=${provenance.fromStep ?? ""}`}>{provenance.fromRun}</Link>
              {provenance.fromStep !== null && <> at step {provenance.fromStep}</>}, served here exactly as recorded.
              Everything after it ran live.
            </p>
          ) : (
            <p>
              This response replaced the one recorded in {provenance?.parent ?? "the parent run"}. The run's metadata
              does not record where it came from. Everything after it ran live.
            </p>
          )}
        </div>
      )}

      {step.breaker && (
        <div className="callout callout-fault" role="note">
          <p className="callout-title">{step.breaker.name} breaker</p>
          <p className="mono">{step.breaker.detail}</p>
          <p>The effect was stopped before it ran. Nothing after this point was executed.</p>
        </div>
      )}

      {effect?.kind === "tool" && (
        <>
          <h3>Arguments</h3>
          <Json value={effect.arguments ?? {}} />
          <h3>Result</h3>
          <Json value={effect.result} />
        </>
      )}

      {effect?.kind === "model" && (
        <>
          {effect.calls.length > 0 && (
            <>
              <h3>Asked for</h3>
              <ol className="calls">
                {effect.calls.map((call, i) => (
                  <li key={i}>
                    <span className="tool-name">{call.name}</span>
                    <Json value={call.input} />
                  </li>
                ))}
              </ol>
            </>
          )}
          {effect.text && (
            <>
              <h3>Said</h3>
              <p className="said">{effect.text}</p>
            </>
          )}
          {!effect.text && effect.calls.length === 0 && <p className="quiet">The response carried no text and asked for nothing.</p>}
        </>
      )}

      {!effect && step.kind !== "breaker" && <p className="quiet">The events for this step are not loaded.</p>}

      <h3>Memory</h3>
      {step.reads.length === 0 && step.writes.length === 0 ? (
        <p className="quiet">Read and wrote nothing.</p>
      ) : (
        <ul className="memory">
          {step.reads.map((r) => {
            const from = r.source === null ? null : writerStep(r.source);
            return (
              <li key={`r${r.eid}`} className="memory-read">
                <span className="memory-op">read</span> <span className="mono">{r.key}</span>{" "}
                <span className="quiet">{from === null ? "— not set" : `← written at step ${from}`}</span>
              </li>
            );
          })}
          {step.writes.map((w) => (
            <li key={`w${w.eid}`} className="memory-write">
              <span className="memory-op">wrote</span> <span className="mono">{w.key}</span> ={" "}
              <span className="mono value">{JSON.stringify(w.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
