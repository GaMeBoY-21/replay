import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { corpus } from "../../src/api/source";
import { App } from "../../src/App";

function open() {
  history.replaceState(null, "", "/corpus");
  return render(<App />);
}

describe("the corpus view", () => {
  it("leads with the finding, counted from the committed runs", () => {
    open();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("5 of 11 runs got it wrong, four different ways.");
    expect(screen.getByText(/The wrong runs wrote "USD", a placeholder, "CAD" and "EUR" as the invoice's currency\. It is INR\./)).toBeInTheDocument();
    const tally = screen.getByRole("heading", { name: "Outcomes" }).nextElementSibling!;
    expect(tally.textContent).toBe("runs11right — $4926wrong5failed0");
  });

  it("shows every run's written currency, one cell per run", () => {
    open();
    const strip = screen.getByRole("heading", { name: "What each run wrote as the invoice currency" }).nextElementSibling as HTMLElement;
    const cells = within(strip).getAllByRole("listitem");
    expect(cells).toHaveLength(corpus.runs);
    expect(cells.map((c) => c.textContent)).toEqual(
      corpus.rows.map((r) => {
        const v = r.currency_used ?? r.currencies_recorded.at(-1)!;
        return `${/^<.*>$/.test(v) ? "<…>a placeholder" : v}${r.run_id}${r.overall}`;
      }),
    );
  });

  it("lists each wrong run with where its currency write landed", () => {
    open();
    const section = screen.getByRole("heading", { name: "The wrong runs, and where the currency was written" }).closest("section")!;
    const table = within(section).getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    expect(rows).toEqual([
      ['"USD"', "10", "$41,000", "yes"],
      ["a placeholder: <result_of_get_invoice_header.currency>", "16", "never converted", "no"],
      ['"CAD"', "7", "$29,930", "no"],
      ['"CAD"', "9", "$29,930", "yes"],
      ['"EUR"', "13", "$44,280", "no"],
    ]);
  });

  it("links every run in the strip and the wrong-runs table to its run page", () => {
    open();
    const strip = screen.getByRole("heading", { name: "What each run wrote as the invoice currency" }).nextElementSibling as HTMLElement;
    const stripLinks = within(strip).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(stripLinks).toEqual(corpus.rows.map((r) => `/runs/${r.run_id}`));

    const section = screen.getByRole("heading", { name: "The wrong runs, and where the currency was written" }).closest("section")!;
    const wrongLinks = within(section).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(wrongLinks).toEqual(corpus.rows.filter((r) => r.overall === "wrong").map((r) => `/runs/${r.run_id}`));
  });

  it("opens a corpus run that is not one of the demo's runs", async () => {
    history.replaceState(null, "", "/runs/qwen-06");
    render(<App />);
    const list = await screen.findByRole("list", { name: "Steps of qwen-06" });
    expect(within(list).getAllByRole("button").some((b) => /writes invoice\.currency = "CAD"/.test(b.getAttribute("aria-label") ?? ""))).toBe(true);
  });
});
