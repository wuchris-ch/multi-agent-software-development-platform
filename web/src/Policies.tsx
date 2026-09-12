import { useEffect, useState } from "react";
import { api } from "./api";

type Comparison = {
  gate_sha256: string;
  split: string;
  state: string;
  expected_pairs: number;
  completed_pairs: number;
  baseline_passes: number;
  candidate_passes: number;
  regressions: number;
};
export function Policies() {
  const [data, setData] = useState<{
    active: {
      policy_sha256: string;
      generation: number;
      events: { action: string; to: string; at: number }[];
    } | null;
    comparisons: Comparison[];
  } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<NonNullable<typeof data>>("/policies", undefined, controller.signal)
      .then(setData)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="policy-comparisons info-panel">
      <h2>Policy comparisons</h2>
      <p>
        Paired evidence, reserved task families, and versioned rollout
        decisions.
      </p>
      {error && <p role="alert">{error}</p>}
      {data?.active && (
        <div className="key-value">
          <span>Active policy · generation {data.active.generation}</span>
          <code>{data.active.policy_sha256.slice(0, 16)}</code>
        </div>
      )}
      {data?.comparisons.length ? (
        <div className="comparison-table">
          <table>
            <thead>
              <tr>
                <th>Gate</th>
                <th>Split</th>
                <th>Pairs</th>
                <th>Baseline passes</th>
                <th>Candidate passes</th>
                <th>Regressions</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {data.comparisons.map((row) => (
                <tr key={row.gate_sha256 + row.split}>
                  <td>
                    <code>{row.gate_sha256.slice(0, 9)}</code>
                  </td>
                  <td>{row.split.replaceAll("_", " ")}</td>
                  <td>
                    {row.completed_pairs}/{row.expected_pairs}
                  </td>
                  <td>{row.baseline_passes}</td>
                  <td>{row.candidate_passes}</td>
                  <td>{row.regressions}</td>
                  <td>{row.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="inline-empty">
          Frozen comparison results will appear here after evaluation.
        </div>
      )}
      {data?.active && (
        <details>
          <summary>Rollout history</summary>
          {data.active.events.map((event, index) => (
            <div className="key-value" key={index}>
              <span>{event.action}</span>
              <code>{event.to.slice(0, 16)}</code>
              <time>{new Date(event.at * 1000).toLocaleString()}</time>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
