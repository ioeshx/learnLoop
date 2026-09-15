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
  kind: "learning" | "review";
  started_at: string;
  completed_at: string | null;
  lesson_title: string;
  lesson_content: string;
  exercise: Exercise;
  latest_result: AttemptResult | null;
  adaptation: AdaptiveRecommendation;
};

export type PrerequisiteGap = {
  knowledge_node_id: string;
  title: string;
  score: number;
};

export type AdaptiveRecommendation = {
  mastery_score: number;
  base_difficulty: number;
  target_difficulty: number;
  prerequisite_gaps: PrerequisiteGap[];
  reasons: string[];
};

export type DueReview = {
  knowledge_node_id: string;
  knowledge_node_title: string;
  due_at: string;
  last_review_at: string | null;
  exercise: Exercise | null;
  priority_score: number;
  overdue_days: number;
  reason: string;
};

export type ReviewSchedule = {
  knowledge_node_id: string;
  due_at: string;
  last_review_at: string | null;
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
  | "failed"
  | "cancelled";

export type AgentEventKind =
  | "run_started"
  | "node_started"
  | "node_completed"
  | "tool_started"
  | "tool_completed"
  | "interrupt_created"
  | "run_completed"
  | "run_failed"
  | "model_completed"
  | "plan_created"
  | "plan_rejected"
  | "plan_replanned"
  | "action_decided"
  | "action_rejected"
  | "content_presented"
  | "observation_recorded"
  | "verification_completed"
  | "context_compiled"
  | "context_snapshot_created"
  | "memory_extracted"
  | "budget_updated"
  | "run_paused"
  | "run_cancelled"
  | "delegation_started"
  | "delegation_completed"
  | "delegation_failed"
  | "delegation_cancelled"
  | "delegation_reused"
  | "reflection_created"
  | "reflection_recalled"
  | "skill_candidate_created"
  | "skill_recalled"
  | "skill_usage_recorded"
  | "skill_quarantined"
  | "policy_selected"
  | "reward_recorded"
  | "reward_matured";

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
  graph: "daily_learning" | "goal_planning" | "researcher";
  resource_id: string;
  engine_version: "fixed_v1" | "dynamic_v2";
  parent_run_id: string | null;
  attempt_no: number;
  status: AgentRunStatus;
  terminal_reason: string | null;
  cancel_requested: boolean;
  version: number;
  created_at: string;
  updated_at: string;
};

export type DelegationRecord = {
  request: {
    id: string;
    parent_run_id: string;
    plan_step_id: string;
    role: "researcher" | "curriculum" | "tutor" | "evaluator";
    objective: string;
    goal_id: string;
    knowledge_node_id: string | null;
    allowed_tools: string[];
    budget: {
      allocated_tokens: number;
      max_queries: number;
      max_sources: number;
      deadline_seconds: number;
    };
    fingerprint: string;
    created_at: string;
  };
  child_run_id: string;
  status:
    | "running"
    | "completed"
    | "insufficient_evidence"
    | "failed"
    | "cancelled"
    | "deadline_exceeded";
  result: {
    summary: string;
    unresolved_questions: string[];
    usage: {
      allocated_tokens: number;
      used_tokens: number;
      queries: number;
      sources: number;
      duration_ms: number;
    };
    claims: Array<{ id: string; text: string; citation_status: string }>;
    evidence: Array<{
      id: string;
      resource_id: string;
      chunk_id: string;
      title: string;
      locator: string;
      excerpt: string;
      content_sha256: string;
    }>;
  } | null;
  updated_at: string;
};

export type ToolCallTrace = {
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  status: string;
  duration_ms: number | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
};

export type ModelCallTrace = {
  call_id: string;
  prompt_name: string;
  prompt_version: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  duration_ms: number;
  attempts: number;
  repaired: boolean;
  error: string | null;
  created_at: string;
};

export type AgentTrace = {
  run: AgentRun;
  events: AgentEvent[];
  tool_calls: ToolCallTrace[];
  model_calls: ModelCallTrace[];
  total_tokens: number;
  total_model_duration_ms: number;
  total_tool_duration_ms: number;
  dynamic_state: DynamicAgentState | null;
  plan_versions: Array<{
    version: number;
    plan: DynamicAgentPlan;
    created_at: string;
  }>;
  context_snapshots: ContextSnapshot[];
  delegations: DelegationRecord[];
  child_runs: AgentRun[];
  reflections: RunReflection[];
  skill_usage: SkillUsage | null;
  reward: RewardRecord | null;
  policy_decisions: BanditDecision[];
};

export type PolicyVersion = {
  id: string;
  family: string;
  version: number;
  status: "candidate" | "active" | "disabled" | "quarantined";
  algorithm: "linucb";
  feature_schema_version: string;
  reward_version: string;
  alpha: number;
  epsilon: number;
  arms: Array<{
    id: string;
    instruction: string;
    required_tools: string[];
    prohibited_actions: string[];
  }>;
  source_experiment_id: string | null;
  created_at: string;
};

export type BanditDecision = {
  id: string;
  run_id: string;
  plan_step_id: string;
  decision_point_id: string;
  policy_id: string;
  policy_version: number;
  arm_id: string;
  context: {
    progress: number;
    consecutive_failures: number;
    retrieval_available: number;
    write_step: number;
    intercept: number;
  };
  propensity: number;
  score: number;
  exploratory: boolean;
  reward_id: string | null;
  created_at: string;
};

export type RewardRecord = {
  id: string;
  run_id: string;
  reward_version: string;
  status: "provisional" | "mature" | "ineligible";
  components: {
    task_completion: number;
    immediate_verification: number;
    delayed_retention: number | null;
    transfer: number | null;
    user_feedback: number | null;
    token_efficiency: number;
    tool_efficiency: number;
    latency_efficiency: number;
  };
  safety_violations: string[];
  hard_gate_passed: boolean;
  optimization_score: number | null;
  created_at: string;
  matured_at: string | null;
};

export type FailureCluster = {
  signature: string;
  problem_category: string;
  count: number;
  run_ids: string[];
  root_causes: string[];
  evidence_references: string[];
};

export type ExperimentReport = {
  manifest: {
    id: string;
    name: string;
    change_type: string;
    baseline_version: string;
    candidate_version: string;
    dataset_version: string;
    status: "draft" | "completed" | "promotable" | "rejected";
  };
  split: string;
  sample_count: number;
  effective_sample_size: number;
  baseline_reward: number;
  snips_reward: number;
  reward_lift: number;
  token_ratio: number;
  safety_violations: number;
  confidence_low: number;
  confidence_high: number;
  promotable: boolean;
  rejection_reasons: string[];
};

export type RunReflection = {
  id: string;
  run_id: string;
  outcome: "success" | "failure";
  problem_category: string;
  evidence: Array<{
    id: string;
    kind: "observation" | "verification" | "terminal";
    event_sequence: number;
    observation_id: string | null;
    summary: string;
    content_sha256: string;
  }>;
  root_causes: Array<{ statement: string; evidence_ids: string[] }>;
  improvements: Array<{ statement: string; evidence_ids: string[] }>;
  applicability: string[];
  strategy_key: string;
  created_at: string;
};

export type SkillStatus =
  | "candidate"
  | "active"
  | "rejected"
  | "quarantined"
  | "disabled";

export type SkillRecord = {
  id: string;
  family_key: string;
  name: string;
  description: string;
  version: number;
  status: SkillStatus;
  risk: "low" | "medium" | "high";
  applicability: {
    graph_kind: string;
    objective_keywords: string[];
    required_tools: string[];
  };
  prerequisites: string[];
  steps: Array<{
    order: number;
    instruction: string;
    allowed_tools: string[];
    verifier: string;
  }>;
  source_run_ids: string[];
  source_reflections: RunReflection[];
  success_count: number;
  failure_count: number;
  average_tool_calls: number;
  average_tokens: number;
  valid_until: string | null;
  review_note: string | null;
  created_at: string;
  updated_at: string;
};

export type SkillUsage = {
  run_id: string;
  skill_id: string;
  skill_version: number;
  status: string;
  succeeded: boolean | null;
  tool_calls: number;
  tokens: number;
  failure_type: string | null;
  created_at: string;
  completed_at: string | null;
};

export type ContextSnapshot = {
  snapshot_id: string;
  run_id: string;
  purpose: "planner" | "decision" | "replan";
  plan_version: number;
  step_id: string;
  token_budget: number;
  reserved_output_tokens: number;
  input_token_limit: number;
  total_input_tokens: number;
  tokenizer_name: string;
  exact_token_count: boolean;
  partitions: Array<{
    name: string;
    priority: number;
    mandatory: boolean;
    token_count: number;
    item_count: number;
  }>;
  source_ids: string[];
  omitted_source_ids: string[];
  tool_names: string[];
  truncations: Array<{
    partition: string;
    reason: string;
    omitted_source_ids: string[];
  }>;
  conflicts: Array<{
    source: string;
    field: string;
    source_ids: string[];
  }>;
  compaction_version: number;
  input_hash: string;
  output_hash: string;
  observed_model_input_tokens: number | null;
  token_delta: number | null;
  created_at: string;
};

export type DynamicAgentPlan = {
  objective: string;
  version: number;
  change_reason: string | null;
  applied_skill_id: string | null;
  applied_skill_version: number | null;
  steps: Array<{
    id: string;
    objective: string;
    status: "pending" | "active" | "completed" | "blocked" | "skipped";
    success_criteria: string[];
    allowed_tools: string[];
    evidence_ids: string[];
    attempts: number;
  }>;
};

export type DynamicAgentState = {
  plan: DynamicAgentPlan;
  usage: {
    steps: number;
    model_calls: number;
    tool_calls: number;
    total_tokens: number;
    delegated_tokens: number;
    replans: number;
  };
  budget: {
    max_steps: number;
    max_model_calls: number;
    max_tool_calls: number;
    max_total_tokens: number;
    max_replans: number;
  };
};

export type LearningInsights = {
  goal_id: string;
  nodes: Array<{
    id: string;
    title: string;
    difficulty: number;
    mastery_score: number;
    status: string;
  }>;
  edges: Array<{
    source_node_id: string;
    target_node_id: string;
    relation: string;
  }>;
  mastery_trend: Array<{
    knowledge_node_id: string;
    knowledge_node_title: string;
    score: number;
    event_type: string;
    occurred_at: string;
  }>;
  weekly: {
    attempts: number;
    correct_attempts: number;
    completed_plan_items: number;
    average_mastery: number;
  };
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

export type BackgroundJob = {
  id: string;
  job_type: "resource.process" | "report.weekly" | "reviews.generate_due";
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  progress_message: string | null;
  attempts: number;
  max_attempts: number;
  available_at: string;
  cancel_requested: boolean;
  last_error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

export type ResourceImportResult = {
  resource: LearningResource;
  job: BackgroundJob;
};

export type AgentMemory = {
  id: string;
  user_id: string;
  kind: "working" | "episodic" | "semantic" | "procedural";
  content: string;
  attributes: Record<string, unknown>;
  memory_key: string;
  confidence: number;
  importance: number;
  status: "candidate" | "active" | "rejected" | "expired";
  trust: "untrusted" | "user_asserted" | "verified" | "system";
  sensitivity: "normal" | "personal" | "sensitive";
  requires_approval: boolean;
  goal_id: string | null;
  knowledge_node_id: string | null;
  valid_from: string;
  expires_at: string | null;
  supersedes_id: string | null;
  evidence: Array<{
    id: string;
    source_type: string;
    source_id: string;
    excerpt: string;
    trust: string;
    observed_at: string;
    run_id: string | null;
    session_id: string | null;
    attempt_id: string | null;
  }>;
  revisions: Array<{
    id: string;
    revision: number;
    previous_content: string | null;
    new_content: string;
    reason: string;
    actor: string;
    created_at: string;
  }>;
  created_at: string;
  updated_at: string;
};

export type EvidenceGrade = {
  relevance: number;
  source_quality: number;
  duplicate_score: number;
  coverage: number;
  verdict:
    | "accepted"
    | "low_relevance"
    | "low_quality"
    | "duplicate"
    | "prompt_injection";
  reason: string;
};

export type ResearchEvidence = {
  id: string;
  query_id: string;
  subquestion_id: string;
  resource_id: string;
  chunk_id: string;
  title: string;
  excerpt: string;
  page_number: number | null;
  section: string | null;
  source_uri: string | null;
  resource_version: string;
  resource_sha256: string;
  content_sha256: string;
  trust: "untrusted";
  grade: EvidenceGrade;
};

export type ResearchResult = {
  trace_id: string;
  mode: "no_retrieval" | "single_retrieval" | "multi_step_research";
  status: "completed" | "insufficient_evidence" | "failed";
  answer: string;
  claims: Array<{
    id: string;
    text: string;
    importance: "critical" | "supporting";
    citation_status: "supported" | "partially_supported" | "unsupported";
    included_in_answer: boolean;
  }>;
  citations: Array<{
    id: string;
    claim_id: string;
    evidence_id: string;
    resource_id: string;
    chunk_id: string;
    status: "supported" | "partially_supported" | "unsupported";
    explanation: string;
  }>;
  evidence: ResearchEvidence[];
  gaps: string[];
  usage: {
    rounds: number;
    queries: number;
    sources: number;
    read_chars: number;
    estimated_tokens: number;
    stopped_reason: string | null;
  };
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

export function fetchGoals(): Promise<Goal[]> {
  return apiRequest<Goal[]>("/goals");
}

export function fetchLearningInsights(goalId: string): Promise<LearningInsights> {
  return apiRequest<LearningInsights>(`/goals/${goalId}/insights`);
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

export function startReviewSession(
  knowledgeNodeId: string,
  idempotencyKey: string,
): Promise<StudySession> {
  return apiRequest<StudySession>("/reviews/sessions", {
    method: "POST",
    headers: postHeaders(idempotencyKey),
    body: JSON.stringify({ knowledge_node_id: knowledgeNodeId }),
  });
}

export function deferReview(
  knowledgeNodeId: string,
  days = 1,
): Promise<ReviewSchedule> {
  return apiRequest<ReviewSchedule>(`/reviews/${knowledgeNodeId}/defer`, {
    method: "POST",
    body: JSON.stringify({ days }),
  });
}

export function correctExerciseAttempt(
  sessionId: string,
  attemptId: string,
  selectedOptions: string[],
): Promise<AttemptResult> {
  return apiRequest<AttemptResult>(
    `/study-sessions/${sessionId}/attempts/${attemptId}`,
    {
      method: "PATCH",
      body: JSON.stringify({ selected_options: selectedOptions }),
    },
  );
}

export async function uploadResource(
  file: File,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<ResourceImportResult> {
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
  return (await response.json()) as ResourceImportResult;
}

export function importResourceUrl(
  url: string,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<ResourceImportResult> {
  return apiRequest<ResourceImportResult>("/resources/url", {
    method: "POST",
    body: JSON.stringify({
      url,
      goal_id: goalId,
      knowledge_node_id: knowledgeNodeId || null,
    }),
  });
}

export function fetchJob(jobId: string): Promise<BackgroundJob> {
  return apiRequest<BackgroundJob>(`/jobs/${jobId}`);
}

export function fetchJobs(
  jobType?: BackgroundJob["job_type"],
): Promise<BackgroundJob[]> {
  const parameters = new URLSearchParams({ limit: "200" });
  if (jobType) parameters.set("job_type", jobType);
  return apiRequest<BackgroundJob[]>(`/jobs?${parameters}`);
}

export function cancelJob(jobId: string): Promise<BackgroundJob> {
  return apiRequest<BackgroundJob>(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export function retryJob(jobId: string): Promise<BackgroundJob> {
  return apiRequest<BackgroundJob>(`/jobs/${jobId}/retry`, { method: "POST" });
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

export function fetchMemories(
  memoryStatus?: AgentMemory["status"],
): Promise<AgentMemory[]> {
  const query = memoryStatus
    ? `?memory_status=${encodeURIComponent(memoryStatus)}`
    : "";
  return apiRequest<AgentMemory[]>(`/memories${query}`);
}

export function approveMemory(memoryId: string): Promise<AgentMemory> {
  return apiRequest<AgentMemory>(`/memories/${memoryId}/approve`, {
    method: "POST",
  });
}

export function rejectMemory(memoryId: string): Promise<AgentMemory> {
  return apiRequest<AgentMemory>(`/memories/${memoryId}/reject`, {
    method: "POST",
  });
}

export function deactivateMemory(memoryId: string): Promise<AgentMemory> {
  return apiRequest<AgentMemory>(`/memories/${memoryId}/deactivate`, {
    method: "POST",
  });
}

export function correctMemory(
  memoryId: string,
  content: string,
  reason: string,
): Promise<AgentMemory> {
  return apiRequest<AgentMemory>(`/memories/${memoryId}`, {
    method: "PATCH",
    body: JSON.stringify({ content, reason }),
  });
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await checkedResponse(
    await fetch(`${API_BASE_URL}/memories/${memoryId}`, { method: "DELETE" }),
  );
}

export function startResearch(
  question: string,
  goalId: string,
  knowledgeNodeId?: string,
): Promise<ResearchResult> {
  return apiRequest<ResearchResult>("/research/runs", {
    method: "POST",
    body: JSON.stringify({
      question,
      goal_id: goalId,
      knowledge_node_id: knowledgeNodeId || null,
    }),
  });
}

export function fetchAgentRun(runId: string): Promise<AgentRun> {
  return apiRequest<AgentRun>(`/agent/runs/${runId}`);
}

export function fetchAgentRuns(): Promise<AgentRun[]> {
  return apiRequest<AgentRun[]>("/agent/runs");
}

export function fetchAgentTrace(runId: string): Promise<AgentTrace> {
  return apiRequest<AgentTrace>(`/agent/runs/${runId}/trace`);
}

export function fetchAgentSkills(): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/agent/skills");
}

export function fetchPolicyVersions(): Promise<PolicyVersion[]> {
  return apiRequest<PolicyVersion[]>("/agent/optimization/policies");
}

export function fetchOptimizationRewards(): Promise<RewardRecord[]> {
  return apiRequest<RewardRecord[]>("/agent/optimization/rewards");
}

export function fetchBanditDecisions(): Promise<BanditDecision[]> {
  return apiRequest<BanditDecision[]>("/agent/optimization/decisions");
}

export function fetchPolicyExperiments(): Promise<ExperimentReport[]> {
  return apiRequest<ExperimentReport[]>("/agent/optimization/experiments");
}

export function fetchFailureClusters(): Promise<FailureCluster[]> {
  return apiRequest<FailureCluster[]>("/agent/optimization/failure-clusters");
}

export function reviewAgentSkill(
  skillId: string,
  decision: "publish" | "reject",
  expectedVersion: number,
  note: string,
): Promise<SkillRecord> {
  return apiRequest<SkillRecord>(`/agent/skills/${skillId}/review`, {
    method: "POST",
    body: JSON.stringify({
      decision,
      expected_version: expectedVersion,
      note,
    }),
  });
}

export function updateAgentSkillStatus(
  skillId: string,
  skillStatus: SkillStatus,
  expectedVersion: number,
  note: string,
): Promise<SkillRecord> {
  return apiRequest<SkillRecord>(`/agent/skills/${skillId}/status`, {
    method: "POST",
    body: JSON.stringify({
      status: skillStatus,
      expected_version: expectedVersion,
      note,
    }),
  });
}

export async function downloadLearningData(): Promise<void> {
  const response = await checkedResponse(
    await fetch(`${API_BASE_URL}/data/export`, { cache: "no-store" }),
  );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "learnloop-learning-data.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function startDailyAgentRun(
  sessionId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  engineVersion: "fixed_v1" | "dynamic_v2" = "fixed_v1",
): Promise<AgentStreamResult> {
  return streamAgentEvents(
    `/agent/study-sessions/${sessionId}/runs?engine_version=${engineVersion}`,
    { method: "POST", signal },
    onEvent,
  );
}

export function cancelAgentRun(runId: string): Promise<AgentRun> {
  return apiRequest<AgentRun>(`/agent/runs/${runId}/cancel`, { method: "POST" });
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
