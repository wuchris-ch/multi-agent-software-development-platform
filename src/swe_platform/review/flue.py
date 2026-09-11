import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..io import digest
from ..models import StrictModel
from ..process import bounded_run
from ..sandbox.docker import safe_path


class Finding(StrictModel):
    severity: Literal["blocker", "major", "minor", "info"]
    category: Literal["security", "correctness", "style", "performance"]
    file: str
    line: int = Field(gt=0, strict=True)
    detail: str = Field(min_length=1, max_length=16000)


class Review(StrictModel):
    schema_version: Literal["1.0"]
    input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    risk: Literal["low", "medium", "high"]
    blocked: bool = Field(strict=True)
    findings: list[Finding] = Field(max_length=1000)
    rationale: str = Field(min_length=1, max_length=16000)

    @model_validator(mode="after")
    def consistent(self):
        severity = {f.severity for f in self.findings}
        blocking = bool(severity & {"blocker", "major"})
        risk = "high" if "blocker" in severity else "medium" if "major" in severity else "low"
        if self.blocked != blocking or self.risk != risk:
            raise ValueError("Inconsistent review severity, block decision, or risk")
        if not self.rationale.strip() or any(not f.detail.strip() for f in self.findings):
            raise ValueError("Blank review text")
        return self


def changed_lines(diff: bytes):
    result = {}
    path = old_path = None
    old_line = new_line = 0
    in_hunk = False
    for line in diff.decode("utf-8", errors="strict").splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            path = old_path = None
        elif line.startswith("--- ") and not in_hunk:
            old_path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ ") and not in_hunk:
            path = line[6:] if line.startswith("+++ b/") else old_path
            if path:
                safe_path(path)
                result.setdefault(path, set())
        elif match := re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line):
            old_line, new_line = map(int, match.groups())
            in_hunk = True
        elif in_hunk and path:
            if line.startswith("+"):
                result[path].add(new_line)
                new_line += 1
            elif line.startswith("-"):
                # Existing Flue references deleted lines using their old-side line number.
                result[path].add(old_line)
                old_line += 1
            elif line.startswith(" "):
                old_line += 1
                new_line += 1
    return result


def validate(raw: bytes, diff: bytes) -> Review:
    if not 0 < len(diff) <= 1024 * 1024 or len(raw) > 512 * 1024:
        raise ValueError("Review input or output exceeds its complete-document limit")
    review = Review.model_validate_json(raw)
    if review.input_sha256 != digest(diff):
        raise ValueError("Review belongs to a different candidate diff")
    lines = changed_lines(diff)
    for finding in review.findings:
        safe_path(finding.file)
        if finding.line not in lines.get(finding.file, set()):
            raise ValueError("Review finding is outside changed lines")
    return review


def review(cli: Path, diff: bytes, *, node="node", model_environment=None, timeout=300):
    if cli.name != "cli.js" or not cli.is_file():
        raise ValueError("Expected the trusted built Flue dist/cli.js")
    if not 0 < len(diff) <= 1024 * 1024:
        raise ValueError("Complete diff must be between 1 byte and 1 MiB")
    allowed = {"MODEL_GATEWAY_API_KEY", "MODEL_GATEWAY_BASE_URL", "REVIEW_AGENT_MODEL"}
    supplied = model_environment or {}
    if set(supplied) - allowed:
        raise ValueError("Unsupported reviewer environment field")
    with tempfile.TemporaryDirectory(prefix="swe-review-") as root:
        env = {"PATH": os.defpath, "HOME": root, "OTEL_TRACES_EXPORTER": "none", **supplied}
        code, stdout, _ = bounded_run(
            [node, str(cli.resolve())],
            payload=diff,
            cwd=root,
            env=env,
            timeout=timeout,
            limit=512 * 1024,
        )
    if code != 0:
        raise ValueError("Flue could not produce a valid verdict; raw stderr is not exported")
    return validate(stdout, diff)
