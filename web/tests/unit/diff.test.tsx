import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { manifest } from "../../src/api/source";
import { App } from "../../src/App";

const { parent, run_id: fork, at_step } = manifest.fork;

function open(path: string) {
  history.replaceState(null, "", path);
  return render(<App />).container;
}

describe("the diff of the wrong run and its decision fork", () => {
  it("draws the shared prefix once, spanning both runs", async () => {
    const container = open(`/diff?a=${parent}&b=${fork}`);
    await screen.findByRole("table");
    const band = container.querySelector("tbody.diff-shared")!;
    const cells = [...band.querySelectorAll("tr:not(.diff-shared-label) td")];
    expect(cells).toHaveLength(at_step - 1);
    expect(cells.every((td) => td.getAttribute("colspan") === "2")).toBe(true);
    expect(band).toHaveTextContent(`shared by storage — ${at_step - 1} effects, one copy`);
    expect(screen.getByText(`Steps 1–${at_step - 1} are one log, stored once.`)).toBeInTheDocument();
  });

  it("shows each run's decision at the divergence, and where the fork's came from", async () => {
    const container = open(`/diff?a=${parent}&b=${fork}`);
    await screen.findByRole("table");
    const diverges = container.querySelector("tr.diff-diverges") as HTMLElement;
    const [wrong, forked] = within(diverges).getAllByRole("cell");
    expect(wrong).toHaveTextContent('asks for record_invoice_field currency = "USD"');
    expect(forked).toHaveTextContent('asks for record_invoice_field currency = "INR"');
    expect(forked).toHaveTextContent(`response from ${manifest.fork.substituted_from.run_id}`);
    expect(await screen.findByText(/Everything after it in .* ran live/)).toBeInTheDocument();
  });

  it("carries the consequence through to the totals", async () => {
    open(`/diff?a=${parent}&b=${fork}`);
    await screen.findByRole("table");
    expect(screen.getByText(/report\.total = \{"amount":41000,"currency":"USD"\}/)).toBeInTheDocument();
    expect(screen.getByText(/report\.total = \{"amount":492,"currency":"USD"\}/)).toBeInTheDocument();
  });

  it("says plainly when two runs share nothing", async () => {
    open(`/diff?a=${parent}&b=${manifest.right.run_id}`);
    expect(await screen.findByText("These runs share nothing.")).toBeInTheDocument();
    expect(document.querySelector("tbody.diff-shared")).toBeNull();
  });
});
