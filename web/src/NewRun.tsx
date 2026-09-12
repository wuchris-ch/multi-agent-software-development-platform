import { useEffect, useState } from "react";
import { LoaderCircle, Plus, X } from "lucide-react";
import { api } from "./api";

type Project = {
  id: string;
  title: string;
  allowed_paths: string[];
  max_requests: number;
  max_total_tokens: number;
  timeout: number;
  requires_ticket: boolean;
};
export function NewRun({ onStarted }: { onStarted: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [task, setTask] = useState("");
  const [mode, setMode] = useState("review");
  const [hasActive, setHasActive] = useState(false);
  const [ticket, setTicket] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    api<{ items: Project[]; active_policy?: unknown }>(
      "/projects",
      undefined,
      controller.signal,
    )
      .then((data) => {
        setProjects(data.items);
        setHasActive(!!data.active_policy);
        setProjectId((value) => value || data.items[0]?.id || "");
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [open]);
  useEffect(() => {
    if (!pending) return;
    const controller = new AbortController();
    let loading = false;
    const poll = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await api<{ workflow_id: string | null; state: string }>(
          `/launches/${pending}`,
          undefined,
          controller.signal,
        );
        if (result.workflow_id) {
          onStarted(result.workflow_id);
          setPending(null);
          setBusy(false);
          setOpen(false);
          setTask("");
        } else if (result.state === "needs_attention") {
          setPending(null);
          setBusy(false);
          setError(
            "The run could not start. Check the configured repository and saved launch evidence.",
          );
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError((e as Error).message);
          setBusy(false);
          setPending(null);
        }
      } finally {
        loading = false;
      }
    };
    void poll();
    const timer = setInterval(poll, 1000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [pending, onStarted]);
  const project = projects.find((value) => value.id === projectId);
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ id: string }>("/launches", {
        key: crypto.randomUUID(),
        project_id: projectId,
        task,
        mode,
        execution_id: ticket || null,
      });
      setPending(result.id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <>
      <button className="primary-button" onClick={() => setOpen(true)}>
        <Plus size={15} />
        New run
      </button>
      {open && (
        <div className="modal-shade">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-run-title"
            className="run-dialog"
          >
            <div className="check-card-heading">
              <h2 id="new-run-title">Start a development run</h2>
              <button
                className="text-button"
                aria-label="Close new run"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <p>
              Choose a repository, describe the change, and follow its saved
              evidence.
            </p>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            {projects.length ? (
              <form
                className="publication-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <label>
                  Repository
                  <select
                    value={projectId}
                    disabled={busy}
                    onChange={(e) => setProjectId(e.target.value)}
                  >
                    {projects.map((value) => (
                      <option value={value.id} key={value.id}>
                        {value.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Requested change
                  <textarea
                    value={task}
                    onChange={(e) => setTask(e.target.value)}
                    rows={5}
                    maxLength={12000}
                    required
                    disabled={busy}
                    placeholder="Describe the behavior you want and the checks that should pass."
                  />
                </label>
                <label>
                  Coordination
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                    disabled={busy}
                  >
                    <option value="review">
                      Coding and independent review
                    </option>
                    {hasActive && (
                      <option value="active">Current production policy</option>
                    )}
                    <option value="selective">
                      Selective planning and specialists
                    </option>
                    <option value="single">Single coding agent</option>
                  </select>
                </label>
                {project?.requires_ticket && (
                  <label>
                    Reserved evaluation ticket
                    <input
                      value={ticket}
                      onChange={(e) => setTicket(e.target.value)}
                      required
                      disabled={busy}
                      placeholder="Execution ID issued by the evaluator"
                    />
                  </label>
                )}
                <div className="info-panel">
                  <div className="key-value">
                    <span>Request allowance</span>
                    <strong>{project?.max_requests}</strong>
                  </div>
                  <div className="key-value">
                    <span>Token admission allowance</span>
                    <strong>
                      {project?.max_total_tokens.toLocaleString()}
                    </strong>
                  </div>
                  <div className="section-label">CHANGE SCOPE</div>
                  <div className="file-tags">
                    {project?.allowed_paths.map((path) => (
                      <code key={path}>{path}</code>
                    ))}
                  </div>
                </div>
                <button className="primary-button" disabled={busy}>
                  {busy ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Plus size={16} />
                  )}
                  {busy ? "Preparing repository..." : "Start run"}
                </button>
              </form>
            ) : (
              <div className="inline-empty">
                Add a repository to the private console configuration, then
                start the console with its configuration file.
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}
