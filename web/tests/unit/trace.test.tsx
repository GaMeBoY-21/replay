import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { manifest } from "../../src/api/source";
import { App } from "../../src/App";

function open(path: string) {
  history.replaceState(null, "", path);
  const view = render(<App />);
  return view.container;
}

describe("the trace", () => {
  it("is drawn from the output on request", async () => {
    open(`/runs/${manifest.wrong.run_id}`);
    await userEvent.click(await screen.findByRole("button", { name: "Trace the output" }));
    expect(location.search).toContain("trace=output");
    expect(await screen.findByRole("heading", { name: "How the output was built" })).toBeInTheDocument();
  });

  it("lists the whole chain earliest first, headed by the write that recorded the assumption", async () => {
    open(`/runs/${manifest.wrong.run_id}?trace=output`);
    const panel = (await screen.findByRole("heading", { name: "How the output was built" })).closest("section")!;
    const links = within(panel).getAllByRole("listitem").map((li) => li.textContent);
    expect(links).toEqual([
      'step 10invoice.currency = "USD"origin — reads nothing earlier',
      'step 11report.total = {"amount":41000,"currency":"USD"}',
      "step 13reads report.total",
      "step 14the final answer",
    ]);
  });

  it("lights the path on the graph and gives the origin alone the signal colour", async () => {
    const container = open(`/runs/${manifest.wrong.run_id}?trace=output`);
    await screen.findByRole("heading", { name: "How the output was built" });
    expect(container.querySelectorAll(".node-origin")).toHaveLength(1);
    expect(container.querySelector(".node-origin")).toHaveClass("node-tool");
    expect([...container.querySelectorAll(".node-traced")].length).toBe(4);
    expect(container.querySelectorAll(".trace-path")).toHaveLength(3);
    const rows = within(screen.getByRole("list", { name: `Steps of ${manifest.wrong.run_id}` })).getAllByRole("button");
    expect(rows[9]).toHaveAccessibleName(/origin of the trace$/);
    expect(rows[13]).toHaveAccessibleName(/the output the trace starts from$/);
  });

  it("selects the origin when the trace is drawn", async () => {
    open(`/runs/${manifest.wrong.run_id}?trace=output`);
    expect(await screen.findByRole("region", { name: /Step 10 record_invoice_field/ })).toBeInTheDocument();
  });

  it("closes on the finding, with steps read from the trace itself", async () => {
    open(`/runs/${manifest.wrong.run_id}?trace=output`);
    expect(
      await screen.findByText("The run broke at step 14. It was wrong from step 10. Nobody could see that until now."),
    ).toBeInTheDocument();
  });

  it("does not accuse a run that was right", async () => {
    open(`/runs/${manifest.right.run_id}?trace=output`);
    await screen.findByRole("heading", { name: "How the output was built" });
    expect(screen.queryByText(/Nobody could see that until now/)).not.toBeInTheDocument();
    expect(screen.getByText(/The answer rests on step/)).toHaveTextContent('invoice.currency = "INR"');
  });
});
