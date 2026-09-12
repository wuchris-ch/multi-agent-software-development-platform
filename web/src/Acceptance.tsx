import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "./api";
import type { Run } from "./types";

export function Acceptance({
  run,
  role,
  reload,
}: {
  run: Run;
  role: string;
  reload: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const act = async (action: string) => {
    setBusy(action);
    setError("");
    try {
      const result = await api<{ state: string }>(
        `/workflows/${run.id}/acceptance/${action}`,
        {},
      );
      setNotice(result.state.replaceAll("_", " "));
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const assessment = run.acceptance;
  return (
    <div className="check-card">
      <div className="check-card-heading">
        <ShieldCheck size={19} />
        <h3>Independent acceptance</h3>
        <span
          className={`status ${assessment?.outcome === "pass" ? "success" : "neutral"}`}
        >
          {assessment?.outcome === "pass"
            ? "Accepted"
            : assessment?.outcome || "Awaiting assessment"}
        </span>
      </div>
      {assessment ? (
        <>
          <p>
            Bound to candidate{" "}
            <code>{assessment.candidate_manifest_sha256.slice(0, 12)}</code> and
            the reserved evaluator policy.
          </p>
          {assessment.checks.map((check) => (
            <div className="key-value" key={check.id}>
              <span>{check.id}</span>
              <strong>{check.passed ? "Passed" : "Failed"}</strong>
            </div>
          ))}
          {assessment.reasons.map((reason, index) => (
            <p key={index}>{reason}</p>
          ))}
        </>
      ) : (
        <p>
          {run.evaluation_reserved
            ? "Upload the exact evidence, submit after the evaluator issues its candidate contract, then retrieve the independent assessment."
            : "Independent evaluation uses a reserved trial ticket. Local checks and review are shown in their own tab."}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {role === "operator" && run.evaluation_reserved && (
        <div className="plan-actions">
          <button
            className="secondary-button"
            disabled={!!busy}
            onClick={() => act("upload")}
          >
            Upload evidence
          </button>
          <button
            className="secondary-button"
            disabled={!!busy}
            onClick={() => act("submit")}
          >
            Submit candidate
          </button>
          <button
            className="secondary-button"
            disabled={!!busy}
            onClick={() => act("refresh")}
          >
            Refresh assessment
          </button>
        </div>
      )}
    </div>
  );
}
