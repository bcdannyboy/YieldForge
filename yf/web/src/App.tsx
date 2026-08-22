import { useEffect, useMemo, useState } from "react";

import type { WorkbenchClient } from "./api";
import { CorpusExplorer } from "./corpus/CorpusExplorer";
import { NestLab } from "./nest/NestLab";
import { OrderBookLab } from "./order-books/OrderBookLab";

type View = "corpus" | "nest" | "order-books";

function currentLocation() {
  const params = new URLSearchParams(window.location.search);
  const rawView = params.get("view");
  const view: View = rawView === "nest" || rawView === "order-books" ? rawView : "corpus";
  const rawTask = params.get("task");
  const parsedTask = rawTask && /^\d+$/.test(rawTask) ? Number(rawTask) : null;
  return {
    view,
    task: parsedTask !== null && Number.isSafeInteger(parsedTask) ? parsedTask : null,
    book: params.get("book"),
  };
}

export function App({ client }: { client: WorkbenchClient }) {
  const [location, setLocation] = useState(currentLocation);
  const [apiStatus, setApiStatus] = useState<"checking" | "ready" | "unavailable">("checking");

  useEffect(() => {
    const update = () => setLocation(currentLocation());
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);

  useEffect(() => {
    let active = true;
    client
      .getCorpusSummary()
      .then(() => active && setApiStatus("ready"))
      .catch(() => active && setApiStatus("unavailable"));
    return () => {
      active = false;
    };
  }, [client]);

  const navigate = (url: string) => {
    window.history.pushState({}, "", url);
    setLocation(currentLocation());
  };
  const nav = useMemo(
    () => [
      ["corpus", "Corpus Explorer"],
      ["nest", "Nest Lab"],
      ["order-books", "Order Book Lab"],
    ] as const,
    [],
  );

  return (
    <>
      <a className="skip-link" href="#workspace">Skip to workspace</a>
      <header className="app-header">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">YF</span>
          <div><strong>YieldForge</strong><span>Research workbench</span></div>
        </div>
        <nav aria-label="Workbench views">
          {nav.map(([view, label]) => (
            <a
              key={view}
              href={`/?view=${view}`}
              aria-current={location.view === view ? "page" : undefined}
              onClick={(event) => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
                event.preventDefault();
                navigate(`/?view=${view}`);
              }}
            >{label}</a>
          ))}
        </nav>
        <div className={`api-state api-state--${apiStatus}`}>
          <span aria-hidden="true" /> API {apiStatus}
        </div>
      </header>
      <aside className="coordinate-warning" aria-label="Coordinate interpretation warning">
        <span aria-hidden="true">!</span> m^-4 · interpretation unresolved
      </aside>
      <main id="workspace">
        {location.view === "corpus" ? (
          <CorpusExplorer client={client} initialTask={location.task} navigate={navigate} />
        ) : null}
        {location.view === "nest" ? <NestLab client={client} tasksIndex={location.task} /> : null}
        {location.view === "order-books" ? (
          <OrderBookLab client={client} initialBook={location.book} />
        ) : null}
      </main>
    </>
  );
}
