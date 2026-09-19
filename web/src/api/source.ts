// Where the data comes from: the API, or the recorded API responses.
//
// There is no API URL anywhere. The app and the API share an origin with the API
// under /api, so the same build works on localhost and deployed. If nothing
// answers at /api - the frontend opened on its own - the app reads the responses
// web/scripts/record_fixtures.py recorded from the real handlers, and says so.
// `?source=fixtures` or `?source=api` forces one.

import corpusData from "../fixtures/corpus.json";
import manifestData from "../fixtures/manifest.json";
import type { Corpus, Manifest } from "./types";

export type Mode = "api" | "fixtures";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Recorded = Record<string, { status: number; body: unknown }>;

let recorded: Promise<Recorded> | null = null;
let mode: Promise<Mode> | null = null;

function forced(): Mode | null {
  const value = new URLSearchParams(globalThis.location?.search ?? "").get("source");
  return value === "api" || value === "fixtures" ? value : null;
}

async function detect(): Promise<Mode> {
  try {
    const response = await fetch("/api/runs", { headers: { accept: "application/json" } });
    const json = (response.headers.get("content-type") ?? "").includes("json");
    return response.ok && json ? "api" : "fixtures";
  } catch {
    return "fixtures";
  }
}

export function currentMode(): Promise<Mode> {
  mode ??= Promise.resolve(forced() ?? detect());
  return mode;
}

/** For tests: pin the source instead of probing for an API. */
export function setMode(value: Mode): void {
  mode = Promise.resolve(value);
}

function fixtures(): Promise<Recorded> {
  recorded ??= import("../fixtures/api.json").then((m) => m.default as Recorded);
  return recorded;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body?.error === "string" ? body.error : response.statusText;
  } catch {
    return response.statusText;
  }
}

export async function get<T>(path: string): Promise<T> {
  if ((await currentMode()) === "fixtures") {
    const hit = (await fixtures())[path];
    if (!hit) throw new ApiError(404, `not in the recordings: ${path}`);
    if (hit.status >= 400) throw new ApiError(hit.status, String((hit.body as { error?: string })?.error));
    return hit.body as T;
  }
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  return (await response.json()) as T;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  if ((await currentMode()) === "fixtures") {
    throw new ApiError(
      503,
      "This is a recording. Forking and resuming run the agent, which needs the local server: " +
        "uv run python -m replay.local --db replay.local.db --seed fixtures/canonical",
    );
  }
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  return (await response.json()) as T;
}

/** Committed records, bundled: which run plays which role, and the corpus. */
export const manifest = manifestData as Manifest;
export const corpus = corpusData as Corpus;
