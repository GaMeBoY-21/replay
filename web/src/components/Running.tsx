// A run that is still going: say so, and how long it has been going.

import { useEffect, useState } from "react";

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function Running({ since, steps }: { since: number | null; steps: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="running-note" role="status">
      <strong>Running live</strong>
      <span className="mono">{since === null ? "starting" : `${clock((now - since) / 1000)} elapsed`}</span>
      <span className="quiet">{steps} steps recorded so far. Steps appear as they are recorded.</span>
    </div>
  );
}
