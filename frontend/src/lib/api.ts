export type HealthResponse = {
  status: "ok";
  service: string;
  version: string;
  environment: string;
};

export type Goal = {
  id: string;
  user_id: string;
  title: string;
  description: string;
  desired_outcome: string;
  weekly_minutes: number;
  target_date: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type PlanItem = {
  id: string;
  knowledge_node_id: string;
  title: string;
  description: string;
  position: number;
  estimated_minutes: number;
  status: string;
};

export type StudyPlan = {
  id: string;
  goal_id: string;
  version: number;
  status: string;
  created_at: string;
  items: PlanItem[];
};

export type Exercise = {
  id: string;
  exercise_type: string;
  prompt: string;
  options: string[];
  max_score: number;
};

export type AttemptResult = {
  attempt_id: string;
  selected_options: string[];
  score: number;
  is_correct: boolean;
  expected_answer: string[];
  mastery_score: number;
  due_at: string;
  attempted_at: string;
};

export type StudySession = {
  id: string;
  goal_id: string;
  plan_id: string;
  plan_item_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  lesson_title: string;
  lesson_content: string;
  exercise: Exercise;
  latest_result: AttemptResult | null;
};

export type DueReview = {
  knowledge_node_id: string;
  knowledge_node_title: string;
  due_at: string;
  last_review_at: string | null;
  exercise: Exercise | null;
};

export type CreateGoalInput = {
  title: string;
  description: string;
  desired_outcome: string;
  weekly_minutes: number;
  target_date: string | null;
};

export type AgentRunStatus =
  | "created"
  | "running"
  | "awaiting_input"
  | "completed"
  | "failed";

export type AgentEventKind =
  | "run_started"
  | "node_started"
  | "node_completed"
  | "tool_started"
  | "tool_completed"
  | "interrupt_created"
  | "run_completed"
  | "run_failed";

export type AgentEvent = {
  run_id: string;
  sequence: number;
  event: AgentEventKind;
  node: string | null;
  timestamp: string;
  data: Record<string, unknown>;
};

export type AgentRun = {
  run_id: string;
  thread_id: string;
  graph: "daily_learning" | "goal_planning";
  resource_id: string;
  status: AgentRunStatus;
  created_at: string;
  updated_at: string;
};

export type AgentStreamResult = {
  runId: string | null;
  lastEvent: AgentEvent | null;
};

export type LearningResource = {
  id: string;
  goal_id: string;
  knowledge_node_id: string | null;
  title: string;
  source_type: "file" | "url";
  source_uri: string | null;
  original_filename: string | null;
  media_type: string;
  sha256: string;
  size_bytes: number;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type ResourceCitation = {
  resource_id: string;
  chunk_id: string;
  title: string;
  excerpt: string;
  score: number;
  page_number: number | null;
  section: string | null;
  locator: string | null;
  source_uri: string | null;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";

type ApiErrorBody = {
  error?: {
    message?: string;
  };
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(
      body.error?.message ?? `请求失败，后端返回 ${response.status}`,
    );
  }

  return (await response.json()) as T;
}

function postHeaders(idempotencyKey?: string): HeadersInit {
  return idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {};
}

async function checkedResponse(response: Response): Promise<Response> {
  if (response.ok) return response;
  const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
  throw new Error(
    body.error?.message ?? `请求失败，后端返回 ${response.status}`,
  );
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health", { signal });
}

export function createGoal(
  input: CreateGoalInput,
  idempotencyKey: string,
): Promise<Goal> {
  return apiRequest<Goal>("/goals", {
    method: "POST",
    headers: postHeaders(idempotencyKey),
    body: JSON.stringify(input),
  });
}

export function fetchGoal(goalId: string): Promise<Goal> {
  return apiRequest<Goal>(`/goals/${goalId}`);
}

export function createStudyPlan(goalId: string): Promise<StudyPlan> {
  return apiRequest<StudyPlan>(`/goals/${goalId}/plans`, { method: "POST" });
}

export function fetchStudyPlan(planId: string): Promise<StudyPlan> {
  return apiRequest<StudyPlan>(`/plans/${planId}`);
}

export function startStudySession(
  goalId: string,
  planItemId: string,
  idempotencyKey: string,
): Promise<StudySession> {
  return apiRequest<StudySession>("/study-sessions", {
    method: "POST",
    headers: postHeaders(idempotencyKey),
    body: JSON.stringify({ goal_id: goalId, plan_item_id: planItemId }),
  });
}

export function fetchStudySession(sessionId: string): Promise<StudySession> {
  return apiRequest<StudySession>(`/study-sessions/${sessionId}`);
}

export function submitExerciseAttempt(
  sessionId: string,
  exerciseId: string,
  selectedOptions: string[],
  idempotencyKey: string,
): Promise<AttemptResult> {
  return apiRequest<AttemptResult>(`/study-sessions/${sessionId}/attempts`, {
    method: "POST",
    headers: postHeaders(idempotencyKey),
    body: JSON.stringify({
      exercise_id: exerciseId,
      selected_options: selectedOptions,
    }),
  });
}

export function completeStudySession(sessionId: string): Promise<StudySession> {
  return apiRequest<StudySession>(`/study-sessions/${sessionId}/complete`, {
    method: "POST",
  });
}

export function fetchDueReviews(dueBefore?: string): Promise<DueReview[]> {
  const query = dueBefore
    ? `?due_before=${encodeURIComponent(dueBefore)}`
    : "";
  return apiRequest<DueReview[]>(`/reviews/due${query}`);
}

export async function uploadResource(
  file: File,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<LearningResource> {
  const form = new FormData();
  form.set("file", file);
  form.set("goal_id", goalId);
  if (knowledgeNodeId) form.set("knowledge_node_id", knowledgeNodeId);
  const response = await checkedResponse(
    await fetch(`${API_BASE_URL}/resources/files`, {
      method: "POST",
      body: form,
    }),
  );
  return (await response.json()) as LearningResource;
}

export function importResourceUrl(
  url: string,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<LearningResource> {
  return apiRequest<LearningResource>("/resources/url", {
    method: "POST",
    body: JSON.stringify({
      url,
      goal_id: goalId,
      knowledge_node_id: knowledgeNodeId || null,
    }),
  });
}

export function fetchResources(goalId: string): Promise<LearningResource[]> {
  return apiRequest<LearningResource[]>(
    `/resources?goal_id=${encodeURIComponent(goalId)}`,
  );
}

export function searchResources(
  query: string,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<ResourceCitation[]> {
  const parameters = new URLSearchParams({ query, goal_id: goalId });
  if (knowledgeNodeId) parameters.set("knowledge_node_id", knowledgeNodeId);
  return apiRequest<ResourceCitation[]>(`/resources/search?${parameters}`);
}

export async function deleteResource(resourceId: string): Promise<void> {
  await checkedResponse(
    await fetch(`${API_BASE_URL}/resources/${resourceId}`, { method: "DELETE" }),
  );
}

export function fetchAgentRun(runId: string): Promise<AgentRun> {
  return apiRequest<AgentRun>(`/agent/runs/${runId}`);
}

export function startDailyAgentRun(
  sessionId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<AgentStreamResult> {
  return streamAgentEvents(
    `/agent/study-sessions/${sessionId}/runs`,
    { method: "POST", signal },
    onEvent,
  );
}

export function resumeAgentRun(
  runId: string,
  value: unknown,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<AgentStreamResult> {
  return streamAgentEvents(
    `/agent/runs/${runId}/resume`,
    { method: "POST", body: JSON.stringify({ value }), signal },
    onEvent,
  );
}

export function replayAgentEvents(
  runId: string,
  afterSequence: number,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<AgentStreamResult> {
  return streamAgentEvents(
    `/agent/runs/${runId}/events?after=${afterSequence}`,
    {
      headers: { "Last-Event-ID": String(afterSequence) },
      signal,
    },
    onEvent,
  );
}

async function streamAgentEvents(
  path: string,
  init: RequestInit,
  onEvent: (event: AgentEvent) => void,
): Promise<AgentStreamResult> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(
      body.error?.message ?? `Agent 请求失败，后端返回 ${response.status}`,
    );
  }
  if (!response.body) {
    throw new Error("浏览器不支持读取 Agent 事件流");
  }

  const runId = response.headers.get("X-Agent-Run-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastEvent: AgentEvent | null = null;

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseSseBlock(block);
      if (event) {
        lastEvent = event;
        onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  return { runId: runId ?? lastEvent?.run_id ?? null, lastEvent };
}

function parseSseBlock(block: string): AgentEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  return JSON.parse(data) as AgentEvent;
}
