import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { setMode } from "../../src/api/source";

// Unit tests read the recorded API responses; nothing probes for a server.
beforeEach(() => {
  setMode("fixtures");
  history.replaceState(null, "", "/");
});
afterEach(cleanup);
