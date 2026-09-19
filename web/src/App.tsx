import { useEffect, useState } from "react";
import { currentMode, type Mode } from "./api/source";
import { Empty } from "./components/States";
import { Link, useRoute, type Route } from "./router";
import { RunView } from "./views/RunView";

function Page({ route }: { route: Route }) {
  switch (route.name) {
    case "run":
      return <RunView id={route.id} step={route.step} />;
    case "runs":
    case "diff":
    case "corpus":
      return <Empty title="Not built yet">This view comes next.</Empty>;
    case "missing":
      return (
        <Empty title={`Nothing lives at ${route.path}`}>
          <Link href="/">See all runs</Link>
        </Empty>
      );
  }
}

export function App() {
  const route = useRoute();
  const [mode, setMode] = useState<Mode | null>(null);
  useEffect(() => {
    currentMode().then(setMode);
  }, []);

  useEffect(() => {
    const titles: Record<Route["name"], string> = {
      runs: "Runs", run: route.name === "run" ? route.id : "", diff: "Diff", corpus: "Corpus", missing: "Not found",
    };
    document.title = `${titles[route.name]} · Replay`;
  }, [route]);

  const here = (name: Route["name"]) => (route.name === name ? "page" : undefined);

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="masthead">
        <Link href="/" className="brand" aria-label="Replay, all runs">
          <span className="brand-mark" aria-hidden="true" />
          Replay
        </Link>
        <nav aria-label="Primary">
          <ul>
            <li><Link href="/" aria-current={here("runs")}>Runs</Link></li>
            <li><Link href="/corpus" aria-current={here("corpus")}>Corpus</Link></li>
          </ul>
        </nav>
        {mode === "fixtures" && (
          <p className="source-note" role="status">
            Recorded responses — no local server. Forking and resuming need it running.
          </p>
        )}
      </header>
      <main id="main" tabIndex={-1}>
        <Page route={route} />
      </main>
    </>
  );
}
