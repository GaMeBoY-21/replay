// The corpus: every run of the task, recorded from the same model under the same
// configuration, and what each one did. This is the headline result - the model
// was free to get it right and five times in eleven did not, in four different
// ways - so every figure here is counted from the committed runs, not typed in.

import { checkBasis } from "../api/basis";
import { corpus, manifest } from "../api/source";
import { Citations } from "../components/BasisPanel";
import type { CorpusRow } from "../api/types";
import { Link } from "../router";

const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
/** Small counts in words, as a sentence reads them. */
export function spelled(n: number): string {
  return WORDS[n] ?? String(n);
}

/** The currency a run wrote, as a reader should see it. A template placeholder is named as one. */
export function writtenCurrency(row: CorpusRow): { label: string; placeholder: boolean } | null {
  const value = row.currency_used ?? row.currencies_recorded[row.currencies_recorded.length - 1];
  if (!value) return null;
  const placeholder = /^<.*>$/.test(value.trim());
  return { label: placeholder ? "a placeholder" : value, placeholder };
}

export function sawRemittance(row: CorpusRow): boolean {
  return row.read.some((r) => r.tool === "get_remittance_details");
}

function money(value: number | null): string {
  if (value === null) return "—";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/** Every corpus run is served by the local app, so every one is a link. */
function RunName({ id }: { id: string }) {
  return <Link href={`/runs/${encodeURIComponent(id)}`} className="mono">{id}</Link>;
}

export function CorpusView() {
  const rows = corpus.rows;
  const wrong = rows.filter((r) => r.overall === "wrong");
  const right = rows.filter((r) => r.overall === "right");
  const failed = rows.filter((r) => r.overall === "failed");
  const wrongCurrencies = wrong.map((r) => writtenCurrency(r)?.label ?? "none");
  // Distinct wrong values, in the order they first appear: currencies quoted, a placeholder named.
  const distinct = [...new Set(wrongCurrencies)];
  const named = distinct.map((c) => (c === "a placeholder" || c === "none" ? c : `"${c}"`));
  const listed = named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}` : named[0];
  const lookups = corpus.vendor_lookups;
  const longest = Math.max(...corpus.steps);

  return (
    <article className="corpus" aria-labelledby="corpus-title">
      <header className="run-head">
        <p className="eyebrow">The corpus</p>
        <h1 id="corpus-title" className="headline">
          {wrong.length} of {corpus.runs} runs got it wrong, {spelled(distinct.length)} different ways.
        </h1>
        <p className="headline-sub">
          The wrong runs wrote {listed} as the invoice's currency. It is INR.
        </p>
        <p className="lede">
          The same task, the same tools, the same model, {corpus.runs} times. Nothing states the currency; the payment
          instructions name an Indian bank, and every run could have fetched them — {corpus.fetched_remittance} did. The
          model chose the currency itself each time, and chose differently.
        </p>
      </header>

      <h2 className="visually-hidden">Outcomes</h2>
      <dl className="tally">
        <div><dt>runs</dt><dd>{corpus.runs}</dd></div>
        <div className="tally-right"><dt>right — $492</dt><dd>{right.length}</dd></div>
        <div className="tally-wrong"><dt>wrong</dt><dd>{wrong.length}</dd></div>
        <div><dt>failed</dt><dd>{failed.length}</dd></div>
      </dl>

      <section aria-labelledby="strip-title" className="corpus-section">
        <h2 id="strip-title">What each run wrote as the invoice currency</h2>
        <ol className="strip">
          {rows.map((row) => {
            const currency = writtenCurrency(row);
            return (
              <li key={row.run_id} className={`strip-cell strip-${row.overall}`}>
                <Link href={`/runs/${encodeURIComponent(row.run_id)}`} className="strip-link">
                  <span className="strip-currency">
                    {currency?.placeholder ? (
                      <><span aria-hidden="true">&lt;…&gt;</span><span className="visually-hidden">a placeholder</span></>
                    ) : currency ? currency.label : "none"}
                  </span>
                  <span className="strip-run mono">{row.run_id}</span>
                  <span className="strip-outcome">{row.overall}</span>
                </Link>
              </li>
            );
          })}
        </ol>
      </section>

      <section aria-labelledby="wrong-title" className="corpus-section">
        <h2 id="wrong-title">The wrong runs, and where the currency was written</h2>
        <div className="diff-scroll">
          <table className="list-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Wrote</th>
                <th scope="col" className="num">At step</th>
                <th scope="col" className="num">Reported</th>
                <th scope="col">Had read the payment instructions</th>
              </tr>
            </thead>
            <tbody>
              {wrong.map((row) => {
                const currency = writtenCurrency(row);
                return (
                  <tr key={row.run_id}>
                    <th scope="row"><RunName id={row.run_id} /></th>
                    <td className="mono">
                      {currency?.placeholder ? (
                        <>a placeholder: <span className="value">{row.currencies_recorded[row.currencies_recorded.length - 1]}</span></>
                      ) : (
                        <span className="value">"{currency?.label}"</span>
                      )}
                    </td>
                    <td className="mono num">{row.currency_write_step ?? "—"}</td>
                    <td className="mono num">
                      {row.converted_total !== null ? money(row.converted_total) : <span className="quiet">never converted</span>}
                    </td>
                    <td>{sawRemittance(row) ? "yes" : "no"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="quiet">
          Only {wrong.filter((r) => writtenCurrency(r)?.label === "USD").length} of the wrong runs made the mistake the task
          most invites. {wrong.filter(sawRemittance).length} of {wrong.length} had read the Indian bank details before
          choosing.
        </p>
      </section>

      <SaidAndShown />

      <section aria-labelledby="shape-title" className="corpus-section corpus-shape">
        <h2 id="shape-title">The shape of the runs</h2>
        <figure className="dots" aria-labelledby="steps-caption">
          <figcaption id="steps-caption">Steps per run</figcaption>
          <ol className="dot-row" style={{ gridTemplateColumns: `repeat(${longest}, 1fr)` }}>
            {Array.from({ length: longest }, (_, i) => i + 1).map((n) => {
              const count = corpus.steps.filter((s) => s === n).length;
              return (
                <li key={n} aria-label={count ? `${count} run${count > 1 ? "s" : ""} took ${n} steps` : undefined} aria-hidden={count ? undefined : true}>
                  {Array.from({ length: count }, (_, j) => <span key={j} className="dot" />)}
                  {(n === 1 || n % 5 === 0 || n === longest) && <span className="dot-axis mono" aria-hidden="true">{n}</span>}
                </li>
              );
            })}
          </ol>
        </figure>
        <dl className="facts facts-wide">
          <div><dt>steps</dt><dd className="mono">{Math.min(...corpus.steps)}–{longest}</dd></div>
          <div><dt>read the payment instructions</dt><dd className="mono">{corpus.fetched_remittance} of {corpus.runs}</dd></div>
          <div>
            <dt>lookup_vendor calls</dt>
            <dd className="mono">
              none in {lookups.filter((n) => n === 0).length}, one in {lookups.filter((n) => n === 1).length}, never more
              than {Math.max(...lookups)}
            </dd>
          </div>
        </dl>
      </section>

      <footer className="corpus-foot quiet">
        Recorded from <span className="mono">{corpus.provider.model}</span> on {corpus.provider.server}, a{" "}
        {corpus.provider.context_length.toLocaleString("en-US")}-token context, {corpus.provider.sampling}. An earlier
        corpus on gemma4:12b is archived: that model never recorded a currency at all, so its wrong answers left
        nothing for a trace to find.
      </footer>
    </article>
  );
}

function SaidAndShown() {
  const checks = corpus.rows.map((row) => ({
    row,
    check: checkBasis(row.basis_stated, writtenCurrency(row)?.label ?? null, row.currency_write_step, row.read),
  }));
  const right = checks.filter(({ row }) => row.overall === "right");
  const grounded = right.filter(({ check }) =>
    check.citations.some((c) => c.verdict === "supported") && !check.citations.some((c) => c.verdict === "unsupported"),
  );
  const invented = right.filter(({ check }) => check.citations.some((c) => c.verdict === "unsupported"));
  const wrong = manifest.wrong.run_id;
  const wrongCheck = checks.find(({ row }) => row.run_id === wrong)?.check;

  return (
    <section aria-labelledby="said-title" className="corpus-section">
      <h2 id="said-title">What each run said, and what its log shows</h2>
      <p className="basis-finding">
        <strong>The stated reasons are unreliable in both directions.</strong> Of {right.length} right runs,{" "}
        {spelled(invented.length)} cite a record that does not say what they claim, and{" "}
        {grounded.length === 1 ? (
          <>only <span className="mono">{grounded[0].row.run_id}</span> names</>
        ) : (
          <>{spelled(grounded.length)} name</>
        )}{" "}
        the evidence it actually had.
        {wrongCheck?.uncited.length ? (
          <> The wrong run, <span className="mono">{wrong}</span>, had read the bank details and called its choice a guess.</>
        ) : null}
      </p>
      <div className="diff-scroll">
        <table className="list-table said-table">
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Wrote</th>
              <th scope="col">What it said</th>
              <th scope="col">What the log shows</th>
            </tr>
          </thead>
          <tbody>
            {checks.map(({ row, check }) => (
              <tr key={row.run_id}>
                <th scope="row">
                  <RunName id={row.run_id} />
                  <span className={`outcome outcome-${row.overall}`}>{row.overall}</span>
                </th>
                <td className="mono">{check.currency ? <span className="value">{check.currency}</span> : "—"}</td>
                <td>{check.basis ? <q>{check.basis}</q> : <span className="quiet">no basis</span>}</td>
                <td><Citations check={check} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fineprint">
        What a basis cites is found by its words; what the run had read is its recorded tool results before the
        currency write the answer depends on.
      </p>
    </section>
  );
}
