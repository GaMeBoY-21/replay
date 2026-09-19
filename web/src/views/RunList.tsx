// Every run the log holds, from GET /runs - the projection, never the log itself.
// The demo's runs come first, in the order the story tells them; a fork sits
// under the run it was forked from.

import { corpus, manifest } from "../api/source";
import type { Summary } from "../api/types";
import { useEffect, useState } from "react";
import { usePoll, useResource } from "../api/useResource";
import { Empty, Failure, Loading, StatusChip } from "../components/States";
import { Link } from "../router";
import { roleOf } from "./RunView";

const ORDER = [manifest.wrong.run_id, manifest.fork.run_id, manifest.right.run_id, manifest.halted?.run_id];

function rank(run: Summary): number {
  const at = ORDER.indexOf(run.run_id);
  return at === -1 ? ORDER.length : at;
}

/** The part of an answer that states the USD total, or its opening. */
export function gist(answer: string | null): string {
  if (!answer) return "";
  const parts = answer
    .replace(/\*\*/g, "")
    .split(/\n+|(?<=[.!?])\s+/)
    .map((part) => part.replace(/^\s*[-*•]\s*/, "").replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const usd = parts.find((p) => /\$\s?[\d,]+|\b[\d,]+(\.\d+)?\s*USD\b/.test(p));
  const any = parts.find((p) => /\b\d[\d,]{2,}/.test(p));
  const chosen = usd ?? any ?? parts[0] ?? "";
  return chosen.length > 140 ? `${chosen.slice(0, 139)}…` : chosen;
}

function Row({ run, child }: { run: Summary; child: boolean }) {
  const role = roleOf(run.run_id);
  return (
    <tr className={child ? "is-child" : undefined}>
      <th scope="row">
        <Link href={`/runs/${run.run_id}`} className="mono run-link">{run.run_id}</Link>
        {role && <span className="role list-role">{role}</span>}
      </th>
      <td><StatusChip status={run.status} halted={run.halted?.name} /></td>
      <td className="mono num">{run.step_count}</td>
      <td className="list-answer">
        {run.halted ? (
          <span className="row-fault mono">{run.halted.name} breaker · {run.halted.detail}</span>
        ) : run.answer ? (
          gist(run.answer)
        ) : (
          <span className="quiet">no answer</span>
        )}
      </td>
      <td>
        {run.parent_run_id ? (
          <span className="lineage">
            fork of <Link href={`/runs/${run.parent_run_id}`}>{run.parent_run_id}</Link>
            {" · "}
            <Link href={`/diff?a=${run.parent_run_id}&b=${run.run_id}`}>diff</Link>
          </span>
        ) : (
          <span className="quiet">recorded</span>
        )}
      </td>
    </tr>
  );
}

export function RunList() {
  const [anyRunning, setAnyRunning] = useState(false);
  const runs = useResource<{ runs: Summary[] }>("/api/runs", usePoll(anyRunning, 2000));
  const running = runs.state === "ready" && runs.data.runs.some((r) => r.status === "running");
  useEffect(() => setAnyRunning(running), [running]);
  if (runs.state === "loading") return <Loading what="runs" />;
  if (runs.state === "error") return <Failure title="Could not list the runs" message={runs.message} />;

  const all = runs.data.runs;
  if (all.length === 0) {
    return (
      <Empty title="No runs recorded yet">
        <p>Seed the local server with the canonical runs:</p>
        <pre className="json">uv run python -m replay.local --db replay.local.db --seed fixtures/canonical</pre>
      </Empty>
    );
  }

  // Three groups, in this order: the demo's runs exactly as the story tells them,
  // then the rest of the corpus, then anything else - a run started or forked
  // here from a run in neither. A fork sits under the run it was forked from.
  const ids = new Set(all.map((r) => r.run_id));
  const demo = new Set(ORDER.filter(Boolean) as string[]);
  manifest.roots.forEach((id) => demo.add(id));
  const inCorpus = new Set(corpus.rows.map((r) => r.run_id));
  const roots = all
    .filter((r) => !r.parent_run_id || !ids.has(r.parent_run_id))
    .sort((a, b) => rank(a) - rank(b) || a.run_id.localeCompare(b.run_id));
  const children = (id: string) => all.filter((r) => r.parent_run_id === id).sort((a, b) => a.run_id.localeCompare(b.run_id));
  const group = (keep: (r: Summary) => boolean) => {
    const rows: { run: Summary; child: boolean }[] = [];
    const visit = (run: Summary, depth: number) => {
      rows.push({ run, child: depth > 0 });
      children(run.run_id).forEach((c) => visit(c, depth + 1));
    };
    roots.filter(keep).forEach((r) => visit(r, 0));
    return rows;
  };
  const groups = [
    { key: "demo", title: null, rows: group((r) => demo.has(r.run_id)) },
    { key: "corpus", title: "The rest of the corpus", rows: group((r) => !demo.has(r.run_id) && inCorpus.has(r.run_id)) },
    { key: "other", title: "Other runs", rows: group((r) => !demo.has(r.run_id) && !inCorpus.has(r.run_id)) },
  ].filter((g) => g.rows.length > 0);

  return (
    <section className="list" aria-labelledby="list-title">
      <header className="run-head">
        <h1 id="list-title">Runs</h1>
        <p className="quiet">
          {all.length} runs of one task — <em>reconcile invoice INV-2291 and report the total in USD</em> — recorded
          from {manifest.model}.
        </p>
      </header>
      {groups.map((g) => (
        <div key={g.key} className="list-group">
          {g.title && <h2 id={`list-${g.key}`} className="list-group-title">{g.title}</h2>}
          <div className="diff-scroll">
            <table className="list-table" aria-labelledby={g.title ? `list-${g.key}` : "list-title"}>
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">Steps</th>
                  <th scope="col">Answer</th>
                  <th scope="col">Lineage</th>
                </tr>
              </thead>
              <tbody>
                {g.rows.map(({ run, child }) => <Row key={run.run_id} run={run} child={child} />)}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  );
}
