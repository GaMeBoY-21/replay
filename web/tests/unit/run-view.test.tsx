import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { manifest } from "../../src/api/source";
import { App } from "../../src/App";

function open(path: string) {
  history.replaceState(null, "", path);
  return render(<App />);
}

async function steps(runId: string) {
  const list = await screen.findByRole("list", { name: `Steps of ${runId}` });
  return within(list).getAllByRole("button");
}

describe("the run view", () => {
  it("renders the wrong run as a graph of every recorded step", async () => {
    open(`/runs/${manifest.wrong.run_id}`);
    const rows = await steps(manifest.wrong.run_id);
    expect(rows).toHaveLength(14);
    expect(rows[9]).toHaveAccessibleName(/^Step 10, tool call, record_invoice_field.*writes invoice\.currency = "USD"/);
    expect(screen.getByText(/\$41,000 USD/)).toBeInTheDocument();
    expect(screen.getByText("The wrong run")).toBeInTheDocument();
  });

  it("names what each model call asked for, from its recorded response", async () => {
    open(`/runs/${manifest.wrong.run_id}`);
    const rows = await steps(manifest.wrong.run_id);
    await screen.findAllByText(/asks for/);
    expect(rows[8]).toHaveAccessibleName(/^Step 9, model call, asks for record_invoice_field, convert_invoice_total/);
  });

  it("opens a step's recorded arguments, result and memory", async () => {
    open(`/runs/${manifest.wrong.run_id}?step=10`);
    const detail = await screen.findByRole("region", { name: /Step 10 record_invoice_field/ });
    expect(await within(detail).findByText(/"Assumption based on standard business practices\."/, { selector: "pre" })).toBeInTheDocument();
    expect(within(detail).getByText("invoice.currency")).toBeInTheDocument();
  });

  it("moves between steps with the arrow keys", async () => {
    open(`/runs/${manifest.wrong.run_id}?step=1`);
    const rows = await steps(manifest.wrong.run_id);
    rows[0].focus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(location.search).toContain("step=3");
    expect(document.activeElement).toBe(rows[2]);
    await userEvent.keyboard("{End}");
    expect(location.search).toContain("step=14");
  });

  it("says plainly that the fork's decision came from another run and the rest ran live", async () => {
    open(`/runs/${manifest.fork.run_id}`);
    const rows = await steps(manifest.fork.run_id);
    const source = manifest.fork.substituted_from.run_id;
    expect(rows[manifest.fork.at_step - 1]).toHaveAccessibleName(new RegExp(`response from ${source}`));
    expect(screen.getByText(/Everything after it ran live/)).toBeInTheDocument();
    expect(screen.getByText(/shared by storage rather than copied/)).toHaveTextContent(
      `Steps 1–${manifest.fork.at_step - 1} are ${manifest.fork.parent}'s own log`,
    );
  });

  it("names a run that does not exist rather than showing an empty graph", async () => {
    open("/runs/no-such-run");
    expect(await screen.findByRole("alert")).toHaveTextContent("There is no run called no-such-run");
  });
});
