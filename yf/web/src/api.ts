import {
  type CandidateGeometry,
  type CandidatePage,
  type CorpusSummary,
  type CreateJobInput,
  type JobEvent,
  type JobView,
  type OrderBook,
  type SupportStatus,
  type TaskDetail,
  type TaskPage,
  parseCandidateGeometry,
  parseCandidatePage,
  parseCorpusSummary,
  parseJobEvent,
  parseJobView,
  parseOrderBook,
  parseTaskDetail,
  parseTaskPage,
} from "./contracts";

export interface TaskFilters {
  status?: SupportStatus;
  constraintType?: string;
  taskId?: number;
  minParts?: number;
  maxParts?: number;
  cursor?: string;
}

export interface GenerateOrderBookInput {
  regime: "no_signal" | "exact_recurrence" | "high_mix";
  seed: number;
  event_count: number;
  starts_at: string;
  interval_minutes: number;
}

export interface WorkbenchClient {
  getCorpusSummary(): Promise<CorpusSummary>;
  listTasks(filters?: TaskFilters): Promise<TaskPage>;
  getTask(tasksIndex: number): Promise<TaskDetail>;
  createJob(input: CreateJobInput): Promise<JobView>;
  getJob(jobId: string): Promise<JobView>;
  cancelJob(jobId: string): Promise<JobView>;
  streamJobEvents(
    jobId: string,
    afterSequence: number,
    signal: AbortSignal,
    onEvent: (event: JobEvent) => void,
  ): Promise<void>;
  listCandidates(jobId: string, cursor?: string): Promise<CandidatePage>;
  getCandidateGeometry(jobId: string, candidateId: string): Promise<CandidateGeometry>;
  listTaskJobs(tasksIndex: number): Promise<{ schema_version: string; items: JobView[] }>;
  listOrderBooks(): Promise<{ items: OrderBook[]; next_cursor: string | null }>;
  getOrderBook(orderBookId: string): Promise<OrderBook>;
  generateOrderBook(input: GenerateOrderBookInput): Promise<OrderBook>;
}

export class ApiRequestError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function responseJson(response: Response): Promise<unknown> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiRequestError(response.status, "API returned invalid JSON");
  }
  if (!response.ok) {
    const message =
      typeof payload === "object" && payload !== null && "message" in payload
        ? String(payload.message)
        : `API request failed (${response.status})`;
    throw new ApiRequestError(response.status, message);
  }
  return payload;
}

function parseTaskJobPage(value: unknown): { schema_version: string; items: JobView[] } {
  if (typeof value !== "object" || value === null || !("items" in value)) {
    throw new TypeError("task job page is invalid");
  }
  const rawItems = (value as { items: unknown }).items;
  if (!Array.isArray(rawItems)) throw new TypeError("task job page items are invalid");
  return {
    schema_version: "yieldforge.api-task-jobs.v1",
    items: rawItems.map(parseJobView),
  };
}

function parseOrderBookPage(value: unknown): { items: OrderBook[]; next_cursor: string | null } {
  if (typeof value !== "object" || value === null || !("items" in value)) {
    throw new TypeError("order-book page is invalid");
  }
  const page = value as { items: unknown; next_cursor?: unknown };
  if ((value as { schema_version?: unknown }).schema_version !== "yieldforge.api-order-book-page.v1") {
    throw new TypeError("order-book page schema is invalid");
  }
  if (!Array.isArray(page.items)) throw new TypeError("order-book page items are invalid");
  if (page.next_cursor !== undefined && page.next_cursor !== null && typeof page.next_cursor !== "string") {
    throw new TypeError("order-book cursor is invalid");
  }
  return {
    items: page.items.map(parseOrderBook),
    next_cursor: typeof page.next_cursor === "string" ? page.next_cursor : null,
  };
}

interface SseRecord {
  id: string | null;
  data: string | null;
}

function parseSseRecords(buffer: string): { records: SseRecord[]; remainder: string } {
  const chunks = buffer.split("\n\n");
  const remainder = chunks.pop() ?? "";
  const records = chunks
    .map((chunk) => {
      let id: string | null = null;
      const data: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("id:")) id = line.slice(3).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      return { id, data: data.length ? data.join("\n") : null };
    })
    .filter((record) => record.data !== null);
  return { records, remainder };
}

export class HttpWorkbenchClient implements WorkbenchClient {
  constructor(private readonly baseUrl = "") {}

  private async get(path: string): Promise<unknown> {
    return responseJson(await fetch(`${this.baseUrl}${path}`, { headers: { Accept: "application/json" } }));
  }

  async getCorpusSummary() {
    return parseCorpusSummary(await this.get("/api/corpus/summary"));
  }

  async listTasks(filters: TaskFilters = {}) {
    const query = new URLSearchParams({ limit: "50" });
    if (filters.status) query.set("status", filters.status);
    if (filters.constraintType) query.set("constraint_type", filters.constraintType);
    if (filters.taskId !== undefined) query.set("task_id", String(filters.taskId));
    if (filters.minParts !== undefined) query.set("min_parts", String(filters.minParts));
    if (filters.maxParts !== undefined) query.set("max_parts", String(filters.maxParts));
    if (filters.cursor) query.set("cursor", filters.cursor);
    return parseTaskPage(await this.get(`/api/tasks?${query}`));
  }

  async getTask(tasksIndex: number) {
    return parseTaskDetail(await this.get(`/api/tasks/${tasksIndex}`));
  }

  async createJob(input: CreateJobInput) {
    const response = await fetch(`${this.baseUrl}/api/solver-jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(input),
    });
    return parseJobView(await responseJson(response));
  }

  async getJob(jobId: string) {
    return parseJobView(await this.get(`/api/solver-jobs/${encodeURIComponent(jobId)}`));
  }

  async cancelJob(jobId: string) {
    const response = await fetch(`${this.baseUrl}/api/solver-jobs/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return parseJobView(await responseJson(response));
  }

  async streamJobEvents(
    jobId: string,
    afterSequence: number,
    signal: AbortSignal,
    onEvent: (event: JobEvent) => void,
  ): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/api/solver-jobs/${encodeURIComponent(jobId)}/events`,
      {
        headers: { Accept: "text/event-stream", "Last-Event-ID": String(afterSequence) },
        signal,
      },
    );
    if (!response.ok || !response.body) {
      throw new ApiRequestError(response.status, "solver event stream failed");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
      const parsed = parseSseRecords(buffer);
      buffer = parsed.remainder;
      for (const record of parsed.records) {
        const event = parseJobEvent(JSON.parse(record.data ?? "null"));
        if (record.id !== String(event.sequence)) throw new TypeError("SSE id/sequence mismatch");
        onEvent(event);
      }
      if (done) return;
    }
  }

  async listCandidates(jobId: string, cursor?: string) {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) query.set("cursor", cursor);
    return parseCandidatePage(
      await this.get(`/api/solver-jobs/${encodeURIComponent(jobId)}/candidates?${query}`),
    );
  }

  async getCandidateGeometry(jobId: string, candidateId: string) {
    return parseCandidateGeometry(
      await this.get(
        `/api/solver-jobs/${encodeURIComponent(jobId)}/candidates/${encodeURIComponent(candidateId)}/geometry`,
      ),
    );
  }

  async listTaskJobs(tasksIndex: number) {
    return parseTaskJobPage(await this.get(`/api/tasks/${tasksIndex}/solver-jobs`));
  }

  async listOrderBooks() {
    return parseOrderBookPage(await this.get("/api/order-books?limit=50"));
  }

  async getOrderBook(orderBookId: string) {
    return parseOrderBook(await this.get(`/api/order-books/${encodeURIComponent(orderBookId)}`));
  }

  async generateOrderBook(input: GenerateOrderBookInput) {
    const response = await fetch(`${this.baseUrl}/api/order-books`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(input),
    });
    return parseOrderBook(await responseJson(response));
  }
}
