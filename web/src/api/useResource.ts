import { useEffect, useRef, useState } from "react";
import { get } from "./source";

export type Resource<T> =
  | { state: "loading" }
  | { state: "error"; status: number | null; message: string }
  | { state: "ready"; data: T };

/** GET a path; null skips the request. `version` re-fetches when it changes.
 *  A re-fetch of the same path keeps showing what it had until the new answer
 *  arrives, so polling a running run never flashes back to "Loading". */
export function useResource<T>(path: string | null, version = 0): Resource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (path === null) return;
    let live = true;
    if (shown.current !== path) setResource({ state: "loading" });
    get<T>(path).then(
      (data) => {
        if (!live) return;
        shown.current = path;
        setResource({ state: "ready", data });
      },
      (error) =>
        live &&
        setResource({ state: "error", status: error?.status ?? null, message: String(error?.message ?? error) }),
    );
    return () => {
      live = false;
    };
  }, [path, version]);
  return resource;
}

/** A counter that ticks every `ms` while `on` - drive re-fetches with it. */
export function usePoll(on: boolean, ms: number): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(timer);
  }, [on, ms]);
  return tick;
}
