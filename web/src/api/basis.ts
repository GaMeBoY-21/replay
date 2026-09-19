// What a run said about its currency, set against what its log shows it had read.
//
// The model records a basis beside the currency it chooses. That basis is prose,
// and it is unreliable in both directions: runs that got it right cite records
// that say nothing about the currency, and the run that got it wrong called its
// guess a guess after reading the evidence. This checks each thing a basis cites
// against the tool results the run had received before it wrote the currency.
//
// The citation is found by keywords, and the UI says so. The evidence side is
// not a heuristic: it is the recorded tool results, in order.

import { effects } from "./events";
import type { EvidenceRead, ReplayEvent, RunView } from "./types";

export type Verdict = "supported" | "unsupported" | "guess";

export interface Citation {
  cites: string;
  verdict: Verdict;
  why: string;
}

export interface BasisCheck {
  currency: string | null;
  basis: string | null;
  writeStep: number | null;
  citations: Citation[];
  /** Evidence the run had read that bears on the currency, but did not cite. */
  uncited: string[];
}

const EVIDENCE = new Set(["get_invoice_header", "get_line_items", "get_remittance_details", "lookup_vendor"]);

function first(read: EvidenceRead[], tool: string): EvidenceRead | undefined {
  return read.find((r) => r.tool === tool);
}

function bankDetails(read: EvidenceRead): string {
  const r = read.result ?? {};
  return [r.bank, r.branch, r.ifsc && `IFSC ${r.ifsc}`].filter(Boolean).join(", ");
}

export function checkBasis(basis: string | null, currency: string | null, writeStep: number | null, read: EvidenceRead[]): BasisCheck {
  const text = basis ?? "";
  const citations: Citation[] = [];
  const header = first(read, "get_invoice_header");
  const remittance = first(read, "get_remittance_details");
  const vendor = first(read, "lookup_vendor");

  const citesHeader = /header/i.test(text);
  const citesBank = /bank|remittance|payment instruction|location|ifsc|india|pune/i.test(text);
  const citesVendor = /previous|past|history|dealings|vendor master|vendor record|for vendor/i.test(text);
  const guesses = /assum|infer|default|standard|practice|typical|usual|likely/i.test(text);

  if (citesHeader) {
    if (!header) citations.push({ cites: "the invoice header", verdict: "unsupported", why: "the run had not read the header" });
    else if (header.result && "currency" in header.result)
      citations.push({ cites: "the invoice header", verdict: "supported", why: `the header read at step ${header.step} has a currency field` });
    else
      citations.push({
        cites: "the invoice header",
        verdict: "unsupported",
        why: `the header it read at step ${header.step} has no currency field: ${Object.keys(header.result ?? {}).join(", ")}`,
      });
  }
  if (citesBank) {
    citations.push(
      remittance
        ? { cites: "the bank details", verdict: "supported", why: `read at step ${remittance.step}: ${bankDetails(remittance)}` }
        : { cites: "the bank details", verdict: "unsupported", why: "the run had not read the payment instructions" },
    );
  }
  if (citesVendor) {
    citations.push(
      vendor && vendor.result && !("error" in vendor.result)
        ? { cites: "a vendor record", verdict: "supported", why: `read at step ${vendor.step}` }
        : {
            cites: "a vendor record or history",
            verdict: "unsupported",
            why: vendor ? `the vendor lookup at step ${vendor.step} found no record` : "the run never looked the vendor up",
          },
    );
  }
  if (guesses) citations.push({ cites: "an assumption", verdict: "guess", why: "the basis presents the choice as a guess or a default" });
  if (basis && citations.length === 0) {
    citations.push({ cites: "nothing the log can check", verdict: "unsupported", why: "the basis names no record the run read" });
  }

  const uncited: string[] = [];
  if (remittance && !citesBank) uncited.push(`the payment instructions, read at step ${remittance.step}: ${bankDetails(remittance)}`);
  return { currency, basis, writeStep, citations, uncited };
}

/** The same inputs, read from a run's own log - for any run, forks included. */
export function evidenceFromLog(run: RunView, events: ReplayEvent[]): { basis: string | null; currency: string | null; writeStep: number | null; read: EvidenceRead[] } {
  const steps = run.steps;
  // The currency write the answer depends on: the one the last conversion read,
  // or the last one made if the run never converted.
  const totalStep = [...steps].reverse().find((s) => s.writes.some((w) => w.key === "report.total"));
  const readSource = totalStep?.reads.find((r) => r.key === "invoice.currency")?.source ?? null;
  const currencyStep =
    (readSource !== null ? steps.find((s) => s.writes.some((w) => w.eid === readSource)) : undefined) ??
    [...steps].reverse().find((s) => s.writes.some((w) => w.key === "invoice.currency"));
  const basisWrite = currencyStep?.writes.find((w) => w.key === "invoice.currency.basis");
  const currencyWrite = currencyStep?.writes.find((w) => w.key === "invoice.currency");
  const byseq = effects(events);
  const read: EvidenceRead[] = [];
  for (const s of steps) {
    if (currencyStep && s.step >= currencyStep.step) break;
    const e = s.seq !== null ? byseq.get(s.seq) : undefined;
    if (e && e.kind === "tool" && e.name && EVIDENCE.has(e.name)) {
      read.push({ step: s.step, tool: e.name, result: (e.result && typeof e.result === "object" ? e.result : null) as Record<string, unknown> | null });
    }
  }
  return {
    basis: typeof basisWrite?.value === "string" ? basisWrite.value : null,
    currency: typeof currencyWrite?.value === "string" ? currencyWrite.value : null,
    writeStep: currencyStep?.step ?? null,
    read,
  };
}
