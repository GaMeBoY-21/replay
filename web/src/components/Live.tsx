// Whether this server can run the agent. Forking and resuming run it live; a
// deployment that only replays recordings has no model to run it with, and says
// so through GET /capabilities. Those controls are then shown as unavailable,
// with the reason, instead of failing when used.

import type { ReactNode } from "react";
import type { Capabilities } from "../api/types";
import { useResource } from "../api/useResource";

export const LOCAL_COMMAND = "uv run python -m replay.local --db replay.local.db --seed fixtures/canonical --static web/dist";

/** live: true or false once known; null while loading or if the server does not say. */
export function useLive(): { live: boolean | null; model: string | null } {
  const caps = useResource<Capabilities>("/api/capabilities");
  if (caps.state !== "ready") return { live: null, model: null };
  return { live: caps.data.live, model: caps.data.model };
}

export function Unavailable({ action }: { action: string }) {
  return (
    <div className="unavailable" role="note">
      <p>
        <strong>This deployment replays recordings.</strong> {action} live needs a model; run it locally to try it.
      </p>
      <pre className="json">{LOCAL_COMMAND}</pre>
    </div>
  );
}

/** Render the live control, or say plainly why it is not available here. */
export function LiveOnly({ action, children }: { action: string; children: ReactNode }) {
  const { live } = useLive();
  if (live === false) return <Unavailable action={action} />;
  return <>{children}</>;
}
