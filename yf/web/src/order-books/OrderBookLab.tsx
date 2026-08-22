import { useEffect, useState } from "react";

import type { WorkbenchClient } from "../api";
import type { OrderBook } from "../contracts";
import { mapOrderBookProvenance, ProvenanceMark } from "../components/Provenance";

export function OrderBookLab({
  client,
  initialBook,
}: {
  client: WorkbenchClient;
  initialBook: string | null;
}) {
  const [books, setBooks] = useState<OrderBook[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [solvable, setSolvable] = useState<Record<number, boolean>>({});
  const [regime, setRegime] = useState<"no_signal" | "exact_recurrence" | "high_mix">("exact_recurrence");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    client
      .listOrderBooks()
      .then(async (page) => {
        if (!active) return;
        setBooks(page.items);
        if (initialBook) return client.getOrderBook(initialBook);
        return page.items[0] ?? null;
      })
      .then((value) => active && value && setBook(value))
      .catch((reason: unknown) => active && setError(String(reason)));
    return () => {
      active = false;
    };
  }, [client, initialBook]);

  useEffect(() => {
    if (!book) return;
    const taskIds = [...new Set(book.events.map((event) => event.source_task.tasks_index))];
    void Promise.all(
      taskIds.map(async (taskId) => [taskId, (await client.getTask(taskId)).summary.solve_capability.can_solve] as const),
    ).then((entries) => setSolvable(Object.fromEntries(entries)));
  }, [book, client]);

  const limitedRegime = regime === "no_signal" || regime === "high_mix";

  return (
    <section aria-labelledby="orders-heading">
      <div className="section-heading">
        <div><p className="eyebrow">Deterministic hybrid scenarios</p><h1 id="orders-heading">Order Book Lab</h1></div>
        <ProvenanceMark kind="generated" />
      </div>
      <div className="notice notice--assumed">
        <strong>Analysis-only full manifest</strong>
        <p>{book?.analysis_warning ?? "Future events and generator-only regime labels are excluded from baseline-facing views."}</p>
      </div>
      {error ? <p className="notice notice--error">{error}</p> : null}

      <div className="workbench-grid workbench-grid--orders">
        <aside className="panel control-panel">
          <h2>Available immutable books</h2>
          <div className="book-list">
            {books.map((item) => (
              <button key={item.order_book_id} onClick={() => setBook(item)} data-selected={book?.order_book_id === item.order_book_id}>
                <span className="mono">{item.order_book_id}</span>
                <span>{item.request.regime} · {item.request.event_count} events</span>
              </button>
            ))}
          </div>
          <h2>Generate</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const localStart = String(data.get("startsAt"));
              void client
                .generateOrderBook({
                  regime,
                  seed: Number(data.get("seed")),
                  event_count: Number(data.get("eventCount")),
                  starts_at: new Date(`${localStart}:00Z`).toISOString(),
                  interval_minutes: Number(data.get("interval")),
                })
                .then((generated) => {
                  setBooks((current) => [generated, ...current.filter((item) => item.order_book_id !== generated.order_book_id)]);
                  setBook(generated);
                })
                .catch((reason: unknown) => setError(String(reason)));
            }}
          >
            <label>Regime<select value={regime} onChange={(event) => setRegime(event.target.value as typeof regime)}>
              <option value="exact_recurrence">Exact recurrence</option>
              <option value="no_signal">No signal</option>
              <option value="high_mix">High mix</option>
            </select></label>
            <label>Events<input name="eventCount" type="number" min="2" max={limitedRegime ? 2 : 100} defaultValue="2" /></label>
            {limitedRegime ? <p className="field-note">The two-task slice supports only a truthful 2-event {regime} book.</p> : null}
            <label>Seed<input name="seed" type="number" defaultValue="7" /></label>
            <label>Starts at (UTC)<input name="startsAt" type="datetime-local" defaultValue="2026-01-01T00:00" /></label>
            <label>Interval minutes<input name="interval" type="number" min="1" defaultValue="60" /></label>
            <button type="submit">Generate deterministic book</button>
          </form>
        </aside>

        <article className="panel inspector">
          {book ? (
            <>
              <div className="inspector-title"><div><p className="eyebrow">Immutable identity</p><h2>{book.order_book_id}</h2></div><span className="status status--success">Validated</span></div>
              <p className="hash-line">{book.content_sha256}</p>
              <dl className="fact-grid">
                <div><dt>Regime</dt><dd>{book.request.regime}</dd></div>
                <div><dt>Events</dt><dd>{book.request.event_count}</dd></div>
                <div><dt>Shape recurrence</dt><dd>{book.diagnostics.shape_recurrence.toFixed(3)}</dd></div>
                <div><dt>Task concentration</dt><dd>{book.diagnostics.max_task_concentration.toFixed(3)}</dd></div>
              </dl>

              <table aria-label="Field provenance">
                <caption>Field-family evidence boundary</caption>
                <thead><tr><th>Family</th><th>Kind</th><th>Explanation</th></tr></thead>
                <tbody>
                  {book.field_provenance.map((item) => (
                    <tr key={item.family}><th scope="row">{item.family}</th><td><ProvenanceMark kind={mapOrderBookProvenance(item.kind)} /></td><td>{item.explanation}</td></tr>
                  ))}
                </tbody>
              </table>

              <table aria-label="Chronological order events">
                <caption>Generated chronology linked to source-observed tasks</caption>
                <thead><tr><th>Seq</th><th>Arrival</th><th>Task</th><th>Material</th><th>Value</th><th>Links</th></tr></thead>
                <tbody>
                  {book.events.map((event) => (
                    <tr key={event.event_id}>
                      <td>{event.sequence}</td><td>{new Date(event.occurred_at).toLocaleString()}</td><td>{event.source_task.tasks_index}</td><td>{event.material.material_code}</td><td>{event.economics.value_index}</td>
                      <td className="table-links">
                        <a href={`/?view=corpus&task=${event.source_task.tasks_index}`} aria-label={`Open corpus task ${event.source_task.tasks_index}`}>Corpus</a>
                        {solvable[event.source_task.tasks_index] ? <a href={`/?view=nest&task=${event.source_task.tasks_index}`} aria-label={`Open Nest Lab for task ${event.source_task.tasks_index}`}>Nest</a> : <span>Blocked</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : <p>Loading immutable order-book manifest…</p>}
        </article>
      </div>
    </section>
  );
}
