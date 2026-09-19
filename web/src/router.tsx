// Four routes, on the history API. The local server answers every non-API path
// with index.html, so a reload on /runs/qwen-00 lands back here.

import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

export type Route =
  | { name: "runs" }
  | { name: "run"; id: string; step: number | null }
  | { name: "diff"; a: string; b: string }
  | { name: "corpus" }
  | { name: "missing"; path: string };

export function parse(pathname: string, search: string): Route {
  const query = new URLSearchParams(search);
  if (pathname === "/" || pathname === "/runs") return { name: "runs" };
  const run = pathname.match(/^\/runs\/([^/]+)$/);
  if (run) {
    const step = Number(query.get("step"));
    return { name: "run", id: decodeURIComponent(run[1]), step: Number.isInteger(step) && step > 0 ? step : null };
  }
  if (pathname === "/diff" && query.get("a") && query.get("b")) {
    return { name: "diff", a: query.get("a")!, b: query.get("b")! };
  }
  if (pathname === "/corpus") return { name: "corpus" };
  return { name: "missing", path: pathname };
}

/** Keep ?source= across navigation, so a forced source survives clicks. */
function withSource(href: string): string {
  const source = new URLSearchParams(location.search).get("source");
  if (!source || href.includes("source=")) return href;
  return href + (href.includes("?") ? "&" : "?") + `source=${source}`;
}

export function navigate(href: string, replace = false): void {
  const target = withSource(href);
  if (replace) history.replaceState(null, "", target);
  else history.pushState(null, "", target);
  dispatchEvent(new PopStateEvent("popstate"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.pathname, location.search));
  useEffect(() => {
    const update = () => setRoute(parse(location.pathname, location.search));
    addEventListener("popstate", update);
    return () => removeEventListener("popstate", update);
  }, []);
  return route;
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(href);
  };
  return <a href={withSource(href)} onClick={handle} {...rest} />;
}
