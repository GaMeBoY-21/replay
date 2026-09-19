// The trace: flag the output and walk back through what it read and what wrote
// that, to the write the answer rests on. The API returns a chain, not a step,
// and the chain is what is shown - "here is how the error reached the output" is
// the reveal, not a bare step number.

import { inline } from "../api/events";
import { manifest } from "../api/source";
import type { Trace } from "../api/types";
import type { Resource } from "../api/useResource";
import { Failure, Loading } from "./States";

export function TracePanel({
  runId,
  traced,
  trace,
  onDraw,
  onClear,
  onSelect,
}: {
  runId: string;
  traced: boolean;
  trace: Resource<Trace>;
  onDraw: () => void;
  onClear: () => void;
  onSelect: (step: number) => void;
}) {
  if (!traced) {
    return (
      <section className="trace trace-idle" aria-label="Trace">
        <button type="button" className="button button-primary" onClick={onDraw}>
          Trace the output
        </button>
        <p className="quiet">Walk back from the final answer through every read and write it depends on.</p>
      </section>
    );
  }
  if (trace.state === "loading") return <Loading what="the trace" />;
  if (trace.state === "error") {
    return (
      <Failure
        title={trace.status === 404 ? "There is nothing to trace" : "Could not trace the output"}
        message={trace.status === 404 ? "This run's output was not built from anything it recorded in memory." : trace.message}
      >
        <button type="button" className="text-button" onClick={onClear}>Hide the trace</button>
      </Failure>
    );
  }

  const t = trace.data;
  const head = t.head;
  const links = [
    ...t.chain.map((link) => ({ step: link.step, what: `${link.key} = ${inline(link.value, 60)}`, origin: link.eid === head.eid })),
    ...(t.chain.some((l) => l.step === t.flagged.step) ? [] : [{ step: t.flagged.step, what: `reads ${t.flagged.key}`, origin: false }]),
    { step: t.output_step, what: "the final answer", origin: false },
  ];
  const wrong = runId === manifest.wrong.run_id;

  return (
    <section className="trace trace-drawn" aria-labelledby="trace-title">
      <div className="trace-head">
        <h2 id="trace-title">How the output was built</h2>
        <button type="button" className="text-button" onClick={onClear}>Hide the trace</button>
      </div>
      <ol className="chain">
        {links.map((link) => (
          <li key={`${link.step}-${link.what}`} className={link.origin ? "chain-origin" : ""}>
            <button type="button" className="chain-link" onClick={() => onSelect(link.step)}>
              <span className="chain-step">step {link.step}</span>
              <span className="mono chain-what">{link.what}</span>
              {link.origin && <span className="tag tag-origin">origin — reads nothing earlier</span>}
            </button>
          </li>
        ))}
      </ol>
      {wrong ? (
        <p className="closer">
          The run broke at step {t.output_step}. It was wrong from step {head.step}. Nobody could see that until now.
        </p>
      ) : (
        <p className="quiet">
          The answer rests on step {head.step}: <span className="mono">{head.key} = {inline(head.value, 40)}</span>.
        </p>
      )}
    </section>
  );
}
