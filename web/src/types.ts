export type RunSummary = {
  id: string;
  key: string | null;
  title: string;
  repository: string;
  state: string;
  created_at: number | null;
  updated_at: number | null;
  request_budget: number;
  requests_used_or_reserved: number;
  repairs_used: number;
  candidate_sha256: string | null;
  revision: string;
};
export type Event = { seq: number; at: number; event: string };
export type Finding = {
  severity: string;
  category: string;
  file: string;
  line: number;
  detail: string;
};
export type Candidate = {
  sha256: string;
  state: string;
  changed_paths: string[];
  patch_sha256: string;
  verification: {
    passed: boolean;
    exit_code: number;
    reason: string | null;
  } | null;
  check_output: string | null;
  review: {
    blocked: boolean;
    risk: string;
    rationale: string;
    findings: Finding[];
  } | null;
};
export type Publication = {
  plan_sha256: string;
  state: string;
  error?: string;
  events: Event[];
  plan: {
    repository: string;
    branch: string;
    base_branch: string;
    base_sha: string;
    head_sha: string;
    candidate_sha256: string;
    title: string;
    body: string;
    expires_at: number;
    created_at: number;
  };
  pull_request?: {
    url: string;
    number: number;
    head_sha: string;
    draft: boolean;
  } | null;
  checks?: {
    head_sha: string;
    state: string;
    checks: { name: string; state: string; url: string | null }[];
  };
};
export type Run = RunSummary & {
  task: string;
  base_revision: string;
  deadline: number;
  active: { kind: string; key: string } | null;
  max_repairs: number;
  stages: {
    name: string;
    state: string;
    requests: number;
    started_at: number | null;
    finished_at: number | null;
  }[];
  events: Event[];
  candidate: Candidate | null;
  candidates: string[];
  publications: Publication[];
  can_cancel: boolean;
};
