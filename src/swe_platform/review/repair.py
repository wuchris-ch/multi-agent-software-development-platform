from dataclasses import dataclass, field


@dataclass
class RepairPolicy:
    """Persist this record alongside each attempt. No retries reset the repair counter."""

    limit: int = 2
    candidates: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def decide(self, candidate: str, passed: bool, blocked: bool, finding_digest: str):
        if candidate in self.candidates:
            return "needs_attention", "Repair produced an identical candidate"
        repeated_findings = finding_digest in self.findings and blocked
        self.candidates.append(candidate)
        self.findings.append(finding_digest)
        if passed and not blocked:
            return "ready_local", "Candidate passed public checks and independent review"
        if repeated_findings:
            return "needs_attention", "Blocking findings repeated without improvement"
        if len(self.candidates) > self.limit:
            return "needs_attention", "Repair allowance exhausted"
        return "repairing", "New candidate requires fresh tests and review"
