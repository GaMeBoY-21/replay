// A run that is still going: say so, how long it has been going, and let the
// operator stop it. A cancel stops the run before its next step, the way a
// breaker does; a model call already in flight finishes and is recorded first.

import { useEffect, useState } from "react";
import { ApiError, post } from "../api/source";

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function Running({ runId, since, steps }: { runId: string; since: number | null; steps: number }) {
  const [now, setNow] = useState(() => Date.now());
  const [cancel, setCancel] = useState<{ name: "idle" } | { name: "sent" } | { name: "failed"; message: string }>({ name: "idle" });
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const stop = async () => {
    setCancel({ name: "sent" });
    try {
      await post(`/api/runs/${encodeURIComponent(runId)}/cancel`, {});
    } catch (error) {
      setCancel({ name: "failed", message: error instanceof ApiError ? error.message : String(error) });
    }
  };

  return (
    <div className="running-note">
      <p role="status">
        <strong>{cancel.name === "sent" ? "Cancelling" : "Running live"}</strong>{" "}
        <span className="mono">{since === null ? "starting" : `${clock((now - since) / 1000)} elapsed`}</span>{" "}
        <span className="quiet">
          {cancel.name === "sent"
            ? "The run stops before its next step; a model call in flight finishes and is recorded first."
            : `${steps} steps recorded so far. Steps appear as they are recorded.`}
        </span>
      </p>
      <button type="button" className="button" onClick={stop} disabled={cancel.name === "sent"}>
        {cancel.name === "sent" ? "Cancelling…" : "Cancel"}
      </button>
      {cancel.name === "failed" && <p className="state-inline state-inline-error" role="alert">{cancel.message}</p>}
    </div>
  );
}
