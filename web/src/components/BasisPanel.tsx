// What it said, and what happened. The model's stated reason for its currency,
// beside what the log shows it had actually read when it chose.

import type { BasisCheck, Verdict } from "../api/basis";

const VERDICT: Record<Verdict, string> = {
  supported: "in the log",
  unsupported: "not in the log",
  guess: "a guess",
};

export function Citations({ check }: { check: BasisCheck }) {
  return (
    <ul className="citations">
      {check.citations.map((c, i) => (
        <li key={i} className={`citation citation-${c.verdict}`}>
          <span className="verdict">{VERDICT[c.verdict]}</span>
          <span>
            cites {c.cites} — <span className="quiet">{c.why}</span>
          </span>
        </li>
      ))}
      {check.uncited.map((u) => (
        <li key={u} className="citation citation-uncited">
          <span className="verdict">had, didn't cite</span>
          <span>{u}</span>
        </li>
      ))}
      {check.citations.length === 0 && check.uncited.length === 0 && <li className="quiet">Nothing to compare.</li>}
    </ul>
  );
}

export function BasisPanel({ check, source }: { check: BasisCheck; source?: string | null }) {
  if (!check.currency) {
    return (
      <section className="basis" aria-labelledby="basis-title">
        <h2 id="basis-title">What it said, and what happened</h2>
        <p className="quiet">This run never recorded a currency on the worksheet.</p>
      </section>
    );
  }
  return (
    <section className="basis" aria-labelledby="basis-title">
      <h2 id="basis-title">What it said, and what happened</h2>
      <div className="basis-grid">
        <div className="basis-said">
          <h3>What it said</h3>
          <p>
            Recorded <span className="mono value">invoice.currency = "{check.currency}"</span> at step {check.writeStep}
            {source && <>, in a response taken from {source}</>}, because:
          </p>
          <blockquote className="said-quote">{check.basis ? `“${check.basis}”` : "no basis given"}</blockquote>
        </div>
        <div className="basis-log">
          <h3>What the log shows</h3>
          <Citations check={check} />
        </div>
      </div>
      <p className="fineprint">
        What a basis cites is found by its words; what the run had read is its recorded tool results, before the
        write.
      </p>
    </section>
  );
}
