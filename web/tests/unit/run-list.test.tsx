import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { manifest } from "../../src/api/source";
import { App } from "../../src/App";
import { gist } from "../../src/views/RunList";

function open(path: string) {
  history.replaceState(null, "", path);
  return render(<App />);
}

describe("the run list", () => {
  it("lists every run the API returns, the demo's runs first and a fork beneath its parent", async () => {
    open("/");
    const table = await screen.findByRole("table");
    const names = within(table).getAllByRole("rowheader").map((th) => th.querySelector("a")!.textContent);
    expect(names.slice(0, 4)).toEqual([manifest.wrong.run_id, manifest.fork.run_id, manifest.right.run_id, manifest.halted!.run_id]);
    expect(names).toHaveLength(7);
    const forkRow = within(table).getByText(manifest.fork.run_id).closest("tr")!;
    expect(forkRow).toHaveClass("is-child");
    expect(within(forkRow).getByRole("link", { name: "diff" })).toHaveAttribute(
      "href",
      `/diff?a=${manifest.fork.parent}&b=${manifest.fork.run_id}`,
    );
  });

  it("names the breaker that halted a run, with its own message", async () => {
    open("/");
    const row = (await screen.findByText(manifest.halted!.run_id)).closest("tr")!;
    expect(row).toHaveTextContent("halted by a breaker");
    expect(row).toHaveTextContent("depth breaker · effect 15 exceeds ceiling of 15");
  });

  it("shows the part of each answer that states the total", async () => {
    open("/");
    await screen.findByRole("table");
    expect(screen.getByText("The total amount recorded in the system is $41,000 USD.")).toBeInTheDocument();
    expect(gist("- Vendor: Meridian\n- Total Amount (in INR): ₹41,000\n- Converted Total Amount (to USD): $492.00")).toBe(
      "Converted Total Amount (to USD): $492.00",
    );
  });
});
