import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Box,
  Check,
  CheckCheck,
  ChevronRight,
  Circle,
  CircleAlert,
  Clock3,
  Code2,
  Download,
  FileCode2,
  GitBranch,
  GitPullRequest,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { api, connect, downloadBundle, externalUrl } from "./api";
import type { Publication, Run, RunSummary } from "./types";

const activeStates = new Set([
  "queued",
  "implementing",
  "repairing",
  "sealing",
  "verifying",
  "reviewing",
]);
const labels: Record<string, string> = {
  ready_local: "Ready to deliver",
  needs_attention: "Needs attention",
  implementing: "Coding",
  repairing: "Repairing",
  verifying: "Running checks",
  reviewing: "In review",
  sealing: "Saving candidate",
  queued: "Queued",
  cancelled: "Cancelled",
  published: "Draft published",
  publishing: "Publishing",
  prepared: "Awaiting approval",
  ambiguous: "Reconcile publication",
  passed: "Passed",
  success: "Passed",
  pending: "Pending",
  failed: "Failed",
  no_checks: "No checks reported",
  awaiting_branch: "Awaiting branch",
  awaiting_pull_request: "Awaiting draft",
};
const label = (state: string) => labels[state] || state.replaceAll("_", " ");
const short = (sha: string | null | undefined) => sha?.slice(0, 9) || "Pending";
function date(at: number | null | undefined) {
  return at
    ? new Date(at * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Pending";
}
function duration(start: number | null, end: number | null) {
  if (start == null || end == null) return "In progress";
  const seconds = Math.max(0, Math.round(end - start));
  return seconds >= 60
    ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    : `${seconds}s`;
}
export function Status({ state }: { state: string }) {
  const success = ["ready_local", "published", "passed", "success"].includes(
    state,
  );
  const running = activeStates.has(state) || state === "publishing";
  return (
    <span
      className={`status ${success ? "success" : running ? "running" : ["needs_attention", "failed", "ambiguous"].includes(state) ? "attention" : "neutral"}`}
    >
      {success ? (
        <Check size={12} />
      ) : running ? (
        <LoaderCircle size={12} className="spin" />
      ) : (
        <Circle size={8} fill="currentColor" />
      )}
      {label(state)}
    </span>
  );
}

export function App() {
  const [role, setRole] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [unavailable, setUnavailable] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("overview");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const authorize = () => {
      connect();
      api<{ role: string }>("/session", undefined, controller.signal)
        .then((s) => {
          setRole(s.role);
          setSessionError("");
          setRefresh((n) => n + 1);
        })
        .catch((e) => {
          if (e.name !== "AbortError") {
            setRole(null);
            setSessionError(e.message);
          }
        });
    };
    authorize();
    window.addEventListener("hashchange", authorize);
    return () => {
      controller.abort();
      window.removeEventListener("hashchange", authorize);
    };
  }, []);
  useEffect(() => {
    if (!role) return;
    const controller = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api<{ items: RunSummary[]; unavailable: number }>(
          "/workflows",
          undefined,
          controller.signal,
        );
        setRuns(data.items);
        setUnavailable(data.unavailable);
        setSelected((current) =>
          current && data.items.some((item) => item.id === current)
            ? current
            : data.items[0]?.id || null,
        );
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [role, refresh]);
  useEffect(() => {
    if (!selected) {
      setRun(null);
      return;
    }
    const controller = new AbortController();
    let pending = false;
    setRun((current) => (current?.id === selected ? current : null));
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api<Run>(
          `/workflows/${selected}`,
          undefined,
          controller.signal,
        );
        setRun(data);
        setError("");
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [selected, refresh]);
  const reload = useCallback(() => setRefresh((n) => n + 1), []);
  const pick = (id: string) => {
    setSelected(id);
    setTab("overview");
    setError("");
  };
  const counts = {
    all: runs.length,
    active: runs.filter((r) => activeStates.has(r.state)).length,
    ready: runs.filter((r) => r.state === "ready_local").length,
    attention: runs.filter((r) => r.state === "needs_attention").length,
  };
  const shown = runs.filter(
    (r) =>
      (filter === "all" ||
        (filter === "active" && activeStates.has(r.state)) ||
        (filter === "ready" && r.state === "ready_local") ||
        (filter === "attention" && r.state === "needs_attention")) &&
      `${r.title} ${r.key || ""} ${r.repository}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const cancel = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await api(`/workflows/${run.id}/cancel`, {});
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Layers3 size={23} />
          </div>
          <div>
            Development<small>WORKSPACE CONTROL</small>
          </div>
        </div>
        <div className="workspace-card">
          <span className="workspace-icon">W</span>
          <div>
            Local workspace<small>Flue agent workflows</small>
          </div>
          <LockKeyhole size={13} />
        </div>
        <p className="nav-caption">WORKSPACE</p>
        <button
          className="nav-item selected"
          onClick={() => setTab("overview")}
        >
          <GitBranch size={17} /> Development runs <span>{runs.length}</span>
        </button>
        <button
          className="nav-item"
          onClick={() => setTab("activity")}
          disabled={!run}
        >
          <Activity size={17} /> Run activity
        </button>
        <button
          className="nav-item"
          onClick={() => setTab("publication")}
          disabled={!run}
        >
          <GitPullRequest size={17} /> Delivery
        </button>
        <div className="sidebar-note">
          <div className="live-dot" />
          <span>
            Local execution<small>Durable state, isolated workers</small>
          </span>
        </div>
        <div className="sidebar-footer">
          <span className="avatar">{role === "viewer" ? "V" : "O"}</span>
          <div>
            {role === "viewer" ? "Viewer session" : "Operator session"}
            <small>Connected on this device</small>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            Workspace <ChevronRight size={12} />
            <strong>Development runs</strong>
          </div>
          <div className="topbar-right">
            <span className="live-indicator">
              <i /> Live updates
            </span>
            <button
              className="icon-button"
              aria-label="Refresh workspace"
              onClick={reload}
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </header>
        {!role ? (
          <section className="connection-screen">
            <div className="empty-symbol">
              <LockKeyhole />
            </div>
            <h1>
              {sessionError
                ? "Connect to your workspace"
                : "Connecting to your workspace"}
            </h1>
            <p>{sessionError || "Loading the local control panel."}</p>
            {sessionError && <code>swe-platform ui</code>}
          </section>
        ) : (
          <div className="workspace-content">
            <div className="page-heading">
              <div>
                <p className="eyebrow">DEVELOPMENT OPERATIONS</p>
                <h1>
                  From task to pull request<span>.</span>
                </h1>
                <p>Follow the work. Inspect the evidence. Ship the change.</p>
              </div>
              <div className="runtime-tag">
                <Box size={16} />
                <span>
                  Flue runtime<small>Isolated agent execution</small>
                </span>
              </div>
            </div>
            <div className="metrics">
              <Metric
                icon={<GitBranch size={16} />}
                label="Development runs"
                value={counts.all}
                detail={`${counts.active} in progress`}
              />
              <Metric
                icon={<CheckCheck size={16} />}
                label="Ready to deliver"
                value={counts.ready}
                detail="Checks and review passed"
              />
              <Metric
                icon={<CircleAlert size={16} />}
                label="Needs attention"
                value={counts.attention}
                detail="Inspect saved run evidence"
              />
              <Metric
                icon={<Activity size={16} />}
                label="Model requests"
                value={runs.reduce(
                  (n, r) => n + r.requests_used_or_reserved,
                  0,
                )}
                detail="Used or reserved across runs"
              />
            </div>
            {error && (
              <div className="error-banner" role="alert">
                <CircleAlert size={16} />
                {error}
                <button aria-label="Dismiss error" onClick={() => setError("")}>
                  <X size={14} />
                </button>
              </div>
            )}
            {unavailable > 0 && (
              <div className="error-banner">
                {unavailable} saved run{unavailable !== 1 ? "s" : ""} could not
                be read.
              </div>
            )}
            <div className="workbench">
              <section className="run-list">
                <div className="list-heading">
                  <h2>
                    Runs <span>{runs.length}</span>
                  </h2>
                  <span className="eyebrow">RECENT FIRST</span>
                </div>
                <label className="search">
                  <Search size={15} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Find a run..."
                    aria-label="Find a run"
                  />
                </label>
                <div className="filters">
                  {[
                    ["all", "All"],
                    ["active", "Active"],
                    ["ready", "Ready"],
                    ["attention", "Attention"],
                  ].map(([key, text]) => (
                    <button
                      key={key}
                      className={filter === key ? "active" : ""}
                      onClick={() => setFilter(key)}
                    >
                      {text}
                    </button>
                  ))}
                </div>
                <div className="run-items">
                  {shown.map((r) => (
                    <button
                      key={r.id}
                      className={`run-item ${selected === r.id ? "selected" : ""}`}
                      onClick={() => pick(r.id)}
                      aria-pressed={selected === r.id}
                    >
                      <div className="run-item-top">
                        <GitBranch size={14} />
                        <span>{r.key || short(r.id)}</span>
                        <time>{date(r.updated_at)}</time>
                      </div>
                      <h3>{r.title}</h3>
                      <Status state={r.state} />
                      <div className="run-item-meta">
                        <span>
                          {r.requests_used_or_reserved}/{r.request_budget}{" "}
                          requests
                        </span>
                        <span>{r.repairs_used} repairs</span>
                      </div>
                    </button>
                  ))}
                  {!shown.length && (
                    <div className="list-empty">
                      {runs.length
                        ? "No matching runs."
                        : "Your development runs will appear here."}
                    </div>
                  )}
                </div>
              </section>
              <section className="run-detail">
                {run ? (
                  <>
                    <div className="detail-heading">
                      <div>
                        <div className="detail-kicker">
                          <GitBranch size={14} />
                          {run.key || short(run.id)}
                          <span>/</span>
                          <code>{short(run.base_revision)}</code>
                        </div>
                        <h2>{run.title}</h2>
                      </div>
                      <Status
                        state={
                          run.publications.some((p) => p.state === "published")
                            ? "published"
                            : run.state
                        }
                      />
                    </div>
                    <div
                      className="tabs"
                      role="tablist"
                      aria-label="Run detail"
                    >
                      {[
                        ["overview", "Overview"],
                        ["diff", "Changes"],
                        ["checks", "Checks & review"],
                        ["activity", "Activity"],
                        ["publication", "Publication"],
                      ].map(([id, text]) => (
                        <button
                          role="tab"
                          aria-selected={tab === id}
                          key={id}
                          className={tab === id ? "selected" : ""}
                          onClick={() => setTab(id)}
                        >
                          {text}
                          {id === "diff" && run.candidate && (
                            <span>{run.candidate.changed_paths.length}</span>
                          )}
                        </button>
                      ))}
                    </div>
                    <div className="tab-content" role="tabpanel">
                      {tab === "overview" && (
                        <Overview run={run} navigate={setTab} />
                      )}
                      {tab === "diff" && <DiffView run={run} />}
                      {tab === "checks" && <Checks run={run} />}
                      {tab === "activity" && <ActivityView run={run} />}
                      {tab === "publication" && (
                        <PublicationView
                          key={run.id}
                          run={run}
                          role={role}
                          reload={reload}
                        />
                      )}
                    </div>
                    <div className="detail-footer">
                      {run.candidate && (
                        <button
                          className="text-button"
                          onClick={() =>
                            downloadBundle(run.id).catch((e) =>
                              setError(e.message),
                            )
                          }
                        >
                          <Download size={12} />
                          Export evidence
                        </button>
                      )}

                      <span>
                        <Clock3 size={12} /> Updated {date(run.updated_at)}
                      </span>
                      <span>
                        Candidate <code>{short(run.candidate_sha256)}</code>
                      </span>
                      {run.can_cancel && role === "operator" && (
                        <button
                          className="text-button danger"
                          onClick={cancel}
                          disabled={busy}
                        >
                          <Square size={11} />
                          {busy ? "Stopping..." : "Cancel run"}
                        </button>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="empty-detail">
                    <div className="empty-symbol">
                      <GitBranch size={28} />
                    </div>
                    <h2>
                      {selected
                        ? "Loading run"
                        : "A clear view of every change"}
                    </h2>
                    <p>
                      Start a development workflow from the CLI.
                      <br />
                      Its stages, checks, and candidate will appear here.
                    </p>
                    <code>swe-platform workflow run --help</code>
                  </div>
                )}
              </section>
            </div>
            <footer className="workspace-footer">
              <span>
                <ShieldCheck size={13} /> Checks and review stay bound to the
                exact candidate.
              </span>
              <span>Local control panel · Updates every 2s</span>
            </footer>
          </div>
        )}
      </main>
    </div>
  );
}
function Metric({
  icon,
  label: name,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  detail: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {icon}
        {name}
      </div>
      <strong>{value.toLocaleString()}</strong>
      <small>{detail}</small>
    </div>
  );
}
function Overview({
  run,
  navigate,
}: {
  run: Run;
  navigate: (tab: string) => void;
}) {
  const candidate = run.candidate;
  const published = run.publications.find((p) => p.state === "published");
  const pipeline = [
    {
      name: "Code",
      detail: "Flue coding agent",
      icon: Code2,
      done: !!candidate,
      active: ["implementing", "repairing", "sealing"].includes(run.state),
    },
    {
      name: "Check",
      detail: "Fresh verification",
      icon: Terminal,
      done: !!candidate?.verification?.passed,
      active: run.state === "verifying",
    },
    {
      name: "Review",
      detail: "Independent agent",
      icon: ShieldCheck,
      done: !!candidate?.review && !candidate.review.blocked,
      active: run.state === "reviewing",
    },
    {
      name: "Deliver",
      detail: "GitHub draft PR",
      icon: GitPullRequest,
      done: !!published,
      active: run.publications.some((p) => p.state === "publishing"),
    },
  ];
  return (
    <>
      <div className="section-label">WORKFLOW PROGRESS</div>
      <div className="pipeline">
        {pipeline.map((p, index) => (
          <div className="pipeline-step" key={p.name}>
            <div
              className={`pipeline-node ${p.done ? "done" : p.active ? "active" : ""}`}
            >
              {p.done ? <Check size={18} /> : <p.icon size={18} />}
            </div>
            <strong>{p.name}</strong>
            <small>{p.detail}</small>
            {index < 3 && <ArrowRight className="pipeline-arrow" size={16} />}
          </div>
        ))}
      </div>
      <div
        className={`delivery-banner ${run.state === "ready_local" ? "ready" : ""}`}
      >
        <div className="delivery-icon">
          {run.state === "ready_local" ? (
            <CheckCheck size={22} />
          ) : (
            <Activity size={22} />
          )}
        </div>
        <div>
          <h3>
            {published
              ? "The reviewed change is on GitHub"
              : run.state === "ready_local"
                ? "This change is ready for delivery"
                : label(run.state)}
          </h3>
          <p>
            {published
              ? `Draft PR #${published.pull_request?.number} is tied to this candidate.`
              : run.state === "ready_local"
                ? "Repository checks passed and independent review is clear."
                : "The saved timeline records every attempt and its evidence."}
          </p>
        </div>
        <button
          className="text-button"
          onClick={() =>
            navigate(
              published || run.state === "ready_local"
                ? "publication"
                : "activity",
            )
          }
        >
          {published || run.state === "ready_local"
            ? "View delivery"
            : "View activity"}
          <ArrowRight size={14} />
        </button>
      </div>
      <div className="overview-grid">
        <div className="info-panel">
          <h3>
            <FileCode2 size={15} /> Candidate
          </h3>
          <div className="key-value">
            <span>Revision</span>
            <code>{short(run.candidate_sha256)}</code>
          </div>
          <div className="key-value">
            <span>Changed files</span>
            <strong>{candidate?.changed_paths.length || 0}</strong>
          </div>
          <div className="file-tags">
            {candidate?.changed_paths.map((path) => (
              <button key={path} onClick={() => navigate("diff")}>
                <FileCode2 size={12} />
                {path}
              </button>
            ))}
          </div>
        </div>
        <div className="info-panel">
          <h3>
            <Activity size={15} /> Run budget
          </h3>
          <div className="key-value">
            <span>Model requests</span>
            <strong>
              {run.requests_used_or_reserved}{" "}
              <span>/ {run.request_budget}</span>
            </strong>
          </div>
          <progress
            value={run.requests_used_or_reserved}
            max={run.request_budget}
            aria-label="Model request budget"
          />
          <div className="key-value">
            <span>Repair attempts</span>
            <strong>
              {run.repairs_used} <span>/ {run.max_repairs}</span>
            </strong>
          </div>
        </div>
      </div>
      <details className="task-brief">
        <summary>
          Task brief <ChevronRight size={14} />
        </summary>
        <p>{run.task}</p>
      </details>
      <div className="recent-event">
        <span className="section-label">LATEST EVENT</span>
        <p>
          <span className="event-dot" />
          {run.events.at(-1)?.event.replaceAll(".", " / ").replaceAll("_", " ")}
          <time>{date(run.updated_at)}</time>
        </p>
      </div>
    </>
  );
}
function DiffView({ run }: { run: Run }) {
  const [candidate, setCandidate] = useState(run.candidate_sha256 || "");
  const [patch, setPatch] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    setCandidate(run.candidate_sha256 || "");
  }, [run.id, run.candidate_sha256]);
  useEffect(() => {
    if (!candidate) return;
    const controller = new AbortController();
    setPatch("");
    setError("");
    api<{ diff: string }>(
      `/workflows/${run.id}/diff?candidate=${candidate}`,
      undefined,
      controller.signal,
    )
      .then((r) => setPatch(r.diff))
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [run.id, candidate]);
  if (!run.candidates.length)
    return (
      <Empty text="The diff will appear when the coding agent saves a candidate." />
    );
  const lines = patch.split("\n");
  const added = lines.filter(
    (l) => l.startsWith("+") && !l.startsWith("+++"),
  ).length;
  const removed = lines.filter(
    (l) => l.startsWith("-") && !l.startsWith("---"),
  ).length;
  return (
    <>
      <div className="diff-toolbar">
        <label>
          Candidate{" "}
          <select
            value={candidate}
            onChange={(e) => setCandidate(e.target.value)}
            aria-label="Candidate revision"
          >
            {run.candidates.map((c, index) => (
              <option value={c} key={c}>
                {short(c)} ·{" "}
                {index === run.candidates.length - 1
                  ? "Current"
                  : `Attempt ${index + 1}`}
              </option>
            ))}
          </select>
        </label>
        <div className="diff-stats">
          <span className="added">
            <ArrowUpRight size={13} />
            {added}
          </span>
          <span className="removed">
            <ArrowDown size={13} />
            {removed}
          </span>
        </div>
      </div>
      {error ? (
        <div role="alert" className="inline-error">
          {error}
        </div>
      ) : (
        <div className="diff-code" aria-label="Candidate diff">
          {patch ? (
            lines.map((line, index) => (
              <div
                key={index}
                className={
                  line.startsWith("diff --git")
                    ? "file-header"
                    : line.startsWith("@@")
                      ? "hunk"
                      : line.startsWith("+")
                        ? "addition"
                        : line.startsWith("-")
                          ? "deletion"
                          : ""
                }
              >
                <span className="line-number">{index + 1}</span>
                <code>{line || " "}</code>
              </div>
            ))
          ) : (
            <p>Loading the exact candidate diff...</p>
          )}
        </div>
      )}
    </>
  );
}
function Checks({ run }: { run: Run }) {
  const c = run.candidate;
  return (
    <>
      <div className="evidence-heading">
        <div className="section-label">EXACT CANDIDATE EVIDENCE</div>
        <code>{short(c?.sha256)}</code>
      </div>
      <div className="check-card">
        <div className="check-card-heading">
          <Terminal size={18} />
          <h3>Repository checks</h3>
          <Status
            state={
              c?.verification
                ? c.verification.passed
                  ? "passed"
                  : "failed"
                : "pending"
            }
          />
        </div>
        {c?.verification && (
          <p>
            Process exit code {c.verification.exit_code}
            {c.verification.reason ? ` · ${c.verification.reason}` : ""}
          </p>
        )}
        {c?.check_output ? (
          <pre className="terminal-output">{c.check_output}</pre>
        ) : (
          <Empty text="Check output will appear after verification." />
        )}
      </div>
      <div className="check-card">
        <div className="check-card-heading">
          <ShieldCheck size={18} />
          <h3>Independent review</h3>
          <Status
            state={
              c?.review
                ? c.review.blocked
                  ? "needs_attention"
                  : "passed"
                : "pending"
            }
          />
        </div>
        {c?.review ? (
          <>
            <p className="review-rationale">{c.review.rationale}</p>
            {c.review.findings.length ? (
              c.review.findings.map((f, i) => (
                <div className="finding" key={i}>
                  <div>
                    <span className="severity">{f.severity}</span>
                    <code>
                      {f.file}:{f.line}
                    </code>
                  </div>
                  <p>{f.detail}</p>
                </div>
              ))
            ) : (
              <div className="clear-review">
                <Check size={15} />
                No blocking findings.
              </div>
            )}
          </>
        ) : (
          <Empty text="A separate Flue agent reviews the complete patch after checks pass." />
        )}
      </div>
    </>
  );
}
function ActivityView({ run }: { run: Run }) {
  return (
    <>
      <div className="activity-heading">
        <div>
          <div className="section-label">DURABLE RUN HISTORY</div>
          <h3>{run.events.length} recorded events</h3>
        </div>
        <span>
          {duration(
            run.created_at,
            activeStates.has(run.state) ? null : run.updated_at,
          )}
        </span>
      </div>
      <ol className="timeline">
        {[...run.events].reverse().map((event) => (
          <li key={event.seq}>
            <span
              className={`timeline-dot ${event.event.endsWith("completed") || event.event.endsWith("ready") ? "complete" : ""}`}
            />
            <div>
              <strong>
                {event.event.replaceAll(".", " / ").replaceAll("_", " ")}
              </strong>
              <span>Event {String(event.seq).padStart(2, "0")}</span>
            </div>
            <time>{date(event.at)}</time>
          </li>
        ))}
      </ol>
    </>
  );
}
function PublicationView({
  run,
  role,
  reload,
}: {
  run: Run;
  role: string;
  reload: () => void;
}) {
  const [repository, setRepository] = useState(
    run.publications[0]?.plan.repository || "",
  );
  const [branch, setBranch] = useState(
    `development/${(run.key || "change").replace(/[^a-zA-Z0-9-]/g, "-").slice(0, 50)}-${short(run.candidate_sha256)}`,
  );
  const [title, setTitle] = useState(run.title);
  const [body, setBody] = useState(
    `${run.title}\n\nRepository checks passed and the exact patch received an independent review.`,
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [prepared, setPrepared] = useState<Publication | null>(null);
  const [compose, setCompose] = useState(false);
  const latest =
    run.publications.find((p) => p.plan_sha256 === prepared?.plan_sha256) ||
    prepared ||
    run.publications[0];
  const plan = compose ? undefined : latest;
  const act = async (kind: string) => {
    setBusy(kind);
    setError("");
    try {
      const path = `/workflows/${run.id}/publications`;
      const result =
        kind === "prepare"
          ? await api<Publication>(path, {
              candidate: run.candidate_sha256,
              repository,
              branch,
              title,
              body,
            })
          : await api<Publication>(
              `${path}/${plan!.plan_sha256}/${kind}`,
              kind === "publish" ? { plan_sha256: plan!.plan_sha256 } : {},
            );
      setPrepared(result);
      setCompose(false);
      reload();
    } catch (e) {
      setError((e as Error).message);
      reload();
    } finally {
      setBusy("");
    }
  };
  const ready = run.candidate?.state === "ready_local";
  return (
    <>
      <div className="publication-heading">
        <GitPullRequest size={24} />
        <div>
          <div className="section-label">DELIVERY</div>
          <h3>Publish the reviewed change</h3>
        </div>
      </div>
      {error && (
        <div className="inline-error" role="alert">
          {error}
        </div>
      )}
      {role === "viewer" && (
        <p className="viewer-note">
          <LockKeyhole size={14} />
          Viewer access. An operator can prepare and publish changes.
        </p>
      )}
      {plan ? (
        <div className="publication-plan">
          <div className="plan-heading">
            <Status state={plan.state} />
            <code title="Publication plan digest">
              {short(plan.plan_sha256)}
            </code>
          </div>
          <h3>{plan.plan.title}</h3>
          <div className="plan-repo">
            <GitBranch size={15} />
            {plan.plan.repository}
          </div>
          <div className="branch-flow">
            <code>{plan.plan.branch}</code>
            <ArrowRight size={14} />
            <code>{plan.plan.base_branch}</code>
          </div>
          <div className="key-value">
            <span>Candidate</span>
            <code>{short(plan.plan.candidate_sha256)}</code>
          </div>
          <div className="key-value">
            <span>Commit</span>
            <code>{short(plan.plan.head_sha)}</code>
          </div>
          <details className="plan-body">
            <summary>Pull request description</summary>
            <pre>{plan.plan.body}</pre>
          </details>
          {plan.pull_request && externalUrl(plan.pull_request.url) && (
            <a
              className="pr-link"
              href={externalUrl(plan.pull_request.url)}
              target="_blank"
              rel="noreferrer"
            >
              <GitPullRequest size={18} />
              <div>
                Draft pull request #{plan.pull_request.number}
                <small>Open the exact published change on GitHub</small>
              </div>
              <ArrowUpRight size={18} />
            </a>
          )}
          {plan.checks && (
            <div className="remote-checks">
              <div className="key-value">
                <span>
                  GitHub checks · <code>{short(plan.checks.head_sha)}</code>
                </span>
                <Status state={plan.checks.state} />
              </div>
              {plan.checks.checks.map((c, i) => (
                <div className="remote-check" key={i}>
                  <span>{c.name}</span>
                  <Status state={c.state} />
                </div>
              ))}
            </div>
          )}
          {plan.error && <p className="inline-error">{plan.error}</p>}
          <div className="plan-actions">
            {role === "operator" && (
              <>
                {!plan.pull_request && (
                  <button
                    className="primary-button"
                    disabled={
                      !!busy ||
                      !ready ||
                      Date.now() >= plan.plan.expires_at * 1000 ||
                      plan.plan.candidate_sha256 !== run.candidate_sha256
                    }
                    onClick={() => act("publish")}
                  >
                    {busy === "publish" ? (
                      <LoaderCircle size={15} className="spin" />
                    ) : (
                      <GitPullRequest size={15} />
                    )}
                    {busy === "publish"
                      ? "Publishing..."
                      : "Approve & publish draft"}
                  </button>
                )}
                <button
                  className="secondary-button"
                  disabled={!!busy}
                  onClick={() => act("reconcile")}
                >
                  <RefreshCw
                    size={14}
                    className={busy === "reconcile" ? "spin" : ""}
                  />
                  Reconcile & refresh
                </button>
                {ready &&
                  !plan.pull_request &&
                  plan.state !== "publishing" &&
                  plan.state !== "ambiguous" && (
                    <button
                      className="secondary-button"
                      disabled={!!busy}
                      onClick={() => setCompose(true)}
                    >
                      Prepare a new plan
                    </button>
                  )}
              </>
            )}
          </div>
          <p className="plan-caption">
            Approval is bound to this candidate, commit, and destination.
          </p>
        </div>
      ) : ready ? (
        <form
          className="publication-form"
          onSubmit={(e) => {
            e.preventDefault();
            void act("prepare");
          }}
        >
          <p>
            Prepare a draft PR from the exact verified candidate. Review its
            destination and commit before publishing.
          </p>
          <label>
            GitHub repository
            <input
              value={repository}
              onChange={(e) => setRepository(e.target.value)}
              placeholder="owner/repository"
              required
              disabled={role !== "operator"}
            />
          </label>
          <label>
            New branch
            <input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              required
              disabled={role !== "operator"}
            />
          </label>
          <label>
            Pull request title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              required
              disabled={role !== "operator"}
            />
          </label>
          <label>
            Description
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={4}
              maxLength={16000}
              disabled={role !== "operator"}
            />
          </label>
          <button
            className="primary-button"
            disabled={!!busy || role !== "operator"}
          >
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <GitPullRequest size={15} />
            )}
            {busy ? "Preparing plan..." : "Prepare publication plan"}
          </button>
          {compose && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => setCompose(false)}
            >
              Back to saved plan
            </button>
          )}
        </form>
      ) : (
        <Empty text="Publication becomes available when the current candidate passes checks and independent review." />
      )}
    </>
  );
}
function Empty({ text }: { text: string }) {
  return <div className="inline-empty">{text}</div>;
}
