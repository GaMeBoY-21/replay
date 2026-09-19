import { defineConfig } from "@playwright/test";

// The browser tests run the built app (web/dist) against the real local server:
// one that can run the agent (a scripted model, so nothing calls Ollama) and one
// deployed with no model at all. Build first: `npm run build`.
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["line"]],
  use: { viewport: { width: 1280, height: 720 }, colorScheme: "dark" },
  webServer: [
    {
      command: "cd .. && uv run python web/tests/e2e/server.py --port 4180",
      url: "http://127.0.0.1:4180/api/runs",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "cd .. && uv run python web/tests/e2e/server.py --port 4181 --no-model",
      url: "http://127.0.0.1:4181/api/runs",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "cd .. && uv run python web/tests/e2e/server.py --port 4182 --slow 3",
      url: "http://127.0.0.1:4182/api/runs",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
