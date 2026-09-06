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
