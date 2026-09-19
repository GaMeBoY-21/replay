import { useEffect, useState } from "react";
import { get } from "./source";

export type Resource<T> =
  | { state: "loading" }
  | { state: "error"; status: number | null; message: string }
  | { state: "ready"; data: T };

/** GET a path; null skips the request. `version` re-fetches when it changes. */
export function useResource<T>(path: string | null, version = 0): Resource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  useEffect(() => {
    if (path === null) return;
    let live = true;
    setResource({ state: "loading" });
    get<T>(path).then(
      (data) => live && setResource({ state: "ready", data }),
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
