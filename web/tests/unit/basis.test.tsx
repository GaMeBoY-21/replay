import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { checkBasis } from "../../src/api/basis";
import { corpus, manifest } from "../../src/api/source";
import { App } from "../../src/App";

const row = (id: string) => corpus.rows.find((r) => r.run_id === id)!;
const verdicts = (id: string) => {
  const r = row(id);
  return checkBasis(r.basis_stated, r.currency_used, r.currency_write_step, r.read).citations.map((c) => `${c.cites}: ${c.verdict}`);
};

describe("what it said against what the log shows", () => {
  it("finds that a run citing the header read a header with no currency field", () => {
    expect(verdicts("qwen-02")).toEqual(["the invoice header: unsupported"]);
    const r = row("qwen-02");
    expect(checkBasis(r.basis_stated, "INR", 8, r.read).citations[0].why).toMatch(/no currency field/);
  });

  it("finds the one right run whose basis names the evidence it had", () => {
    expect(verdicts("qwen-07")).toEqual(["the bank details: supported", "an assumption: guess"]);
  });

  it("finds a cited history the run never looked up", () => {
    expect(verdicts("qwen-05")).toContain("a vendor record or history: unsupported");
  });

  it("names evidence the wrong run had read and did not cite", () => {
    const r = row(manifest.wrong.run_id);
    const check = checkBasis(r.basis_stated, r.currency_used, r.currency_write_step, r.read);
    expect(check.citations.map((c) => c.verdict)).toEqual(["guess"]);
    expect(check.uncited).toEqual(["the payment instructions, read at step 4: HDFC Bank, Baner, Pune, IFSC HDFC0004172"]);
  });

  it("uses the basis beside the currency write the conversion read, not a later one", () => {
    expect(row("qwen-04").basis_stated).toMatch(/^Invoice Header of INV-2291 with issued date/);
    expect(row("qwen-04").currency_write_step).toBe(row("qwen-04").assumption_step);
  });

  it("states the finding on the corpus page, counted from the checks", () => {
    history.replaceState(null, "", "/corpus");
    render(<App />);
    expect(screen.getByText(/The stated reasons are unreliable in both directions\./).closest("p")).toHaveTextContent(
      "Of 6 right runs, five cite a record that does not say what they claim, and only qwen-07 names the evidence it actually had. The wrong run, qwen-00, had read the bank details and called its choice a guess.",
    );
  });

  it("shows it on the wrong run's own page, read from its log", async () => {
    history.replaceState(null, "", `/runs/${manifest.wrong.run_id}`);
    render(<App />);
    const panel = (await screen.findByRole("heading", { name: "What it said, and what happened" })).closest("section")!;
    expect(within(panel).getByText("“Assumption based on standard business practices.”")).toBeInTheDocument();
    expect(await within(panel).findByText(/the payment instructions, read at step 4/)).toBeInTheDocument();
  });

  it("says the fork's basis came with the response it was given", async () => {
    history.replaceState(null, "", `/runs/${manifest.fork.run_id}`);
    render(<App />);
    const panel = (await screen.findByRole("heading", { name: "What it said, and what happened" })).closest("section")!;
    expect(panel).toHaveTextContent(`in a response taken from ${manifest.fork.substituted_from.run_id}`);
  });
});
