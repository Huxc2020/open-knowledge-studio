"""Offline, deterministic evaluation for the goal-aware recall engine."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from knowledge_studio.recall import recall_knowledge
from knowledge_studio.store import _atomic_write, repo_root

DATASET_SCHEMA = "recall-case/v1"
RUN_SCHEMA = "recall-eval-run/v1"
COMPARISON_SCHEMA = "recall-eval-comparison/v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kb_snapshot(root: Path) -> str:
    """Hash persistent memory state to prove evaluation is read-only."""
    digest = hashlib.sha256()
    for bucket in ("wiki", "drafts", "raw", "profiles"):
        base = root / bucket
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _kb_commit(root: Path) -> str | None:
    """Commit of the knowledge-base instance, not of the CLI code."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _code_version() -> str | None:
    try:
        return version("open-knowledge-studio")
    except PackageNotFoundError:
        return None


def load_dataset(path: str | Path) -> dict[str, Any]:
    dataset_path = Path(path).expanduser().resolve()
    data = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Evaluation dataset must be a YAML object")
    required = {"schema_version", "dataset_id", "version", "cases"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"Dataset missing required fields: {', '.join(missing)}")
    if data["schema_version"] != DATASET_SCHEMA:
        raise ValueError(f"Unsupported dataset schema: {data['schema_version']}")
    if not isinstance(data["cases"], list) or not data["cases"]:
        raise ValueError("Dataset cases must be a non-empty list")

    seen: set[str] = set()
    for index, case in enumerate(data["cases"], start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {index} must be an object")
        for field in ("case_id", "query", "relevant"):
            if not case.get(field):
                raise ValueError(f"Case {index} missing required field: {field}")
        if case["case_id"] in seen:
            raise ValueError(f"Duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
        if not isinstance(case["relevant"], list) or not case["relevant"]:
            raise ValueError(f"Case {case['case_id']} relevant must be non-empty")
        if not isinstance(case.get("forbidden", []), list):
            raise ValueError(f"Case {case['case_id']} forbidden must be a list")
    return data


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _case_metrics(slugs: list[str], relevant: set[str], forbidden: set[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for k in (1, 3, 5):
        metrics[f"recall_at_{k}"] = len(set(slugs[:k]) & relevant) / len(relevant)
    first = next((i for i, slug in enumerate(slugs, start=1) if slug in relevant), None)
    metrics["mrr"] = 1.0 / first if first else 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i, slug in enumerate(slugs[:5], start=1) if slug in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(5, len(relevant)) + 1))
    metrics["ndcg_at_5"] = dcg / ideal if ideal else 0.0
    metrics["no_result"] = float(not slugs)
    metrics["stale_leakage"] = float(bool(set(slugs[:5]) & forbidden))
    return metrics


def run_evaluation(dataset_path: str | Path, output_path: str | Path, *, limit: int = 5, search_backend: str | None = None) -> dict[str, Any]:
    if limit < 5:
        raise ValueError("limit must be >= 5 so recall_at_5 and ndcg_at_5 stay meaningful")
    dataset_path = Path(dataset_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    dataset = load_dataset(dataset_path)
    root = repo_root()
    before = _kb_snapshot(root)
    cases: list[dict[str, Any]] = []
    latencies: list[float] = []

    for case in dataset["cases"]:
        started = time.perf_counter()
        hits = recall_knowledge(
            query=str(case["query"]), limit=limit,
            scope=case.get("scope"), goal=str(case.get("goal", "none")),
            explain=True, type_filter=case.get("type_filter"),
            search_backend=search_backend,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        slugs = [str(hit["slug"]) for hit in hits]
        metrics = _case_metrics(slugs, set(case["relevant"]), set(case.get("forbidden", [])))
        cases.append({
            "case_id": case["case_id"], "query": case["query"],
            "goal": case.get("goal", "none"), "retrieved": slugs,
            "relevant": case["relevant"], "forbidden": case.get("forbidden", []),
            "latency_ms": round(latency_ms, 3), "metrics": metrics,
        })

    after = _kb_snapshot(root)
    if before != after:
        raise RuntimeError("Recall evaluation mutated persistent knowledge-base state")
    metric_names = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "ndcg_at_5", "no_result", "stale_leakage")
    aggregate = {
        name: round(sum(case["metrics"][name] for case in cases) / len(cases), 6)
        for name in metric_names
    }
    aggregate.update({
        "latency_p50_ms": round(_percentile(latencies, 0.50), 3),
        "latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "case_count": len(cases),
    })
    result = {
        "schema_version": RUN_SCHEMA,
        "generated_at": _utc_now(),
        "manifest": {
            "code_version": _code_version(),
            "kb_commit": _kb_commit(root),
            "dataset_path": str(dataset_path),
            "dataset_sha256": _sha256(dataset_path),
            "dataset_id": dataset["dataset_id"],
            "dataset_version": str(dataset["version"]),
            "limit": limit,
            "goal_modes": sorted({str(case.get("goal", "none")) for case in dataset["cases"]}),
            "kb_snapshot_before": before,
            "kb_snapshot_after": after,
        },
        "metrics": aggregate,
        "cases": cases,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def compare_runs(baseline_path: str | Path, candidate_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    if baseline.get("schema_version") != RUN_SCHEMA or candidate.get("schema_version") != RUN_SCHEMA:
        raise ValueError("Both inputs must use recall-eval-run/v1")
    if baseline["manifest"]["dataset_sha256"] != candidate["manifest"]["dataset_sha256"]:
        raise ValueError("Cannot compare runs from different dataset snapshots")
    if baseline["manifest"].get("limit") != candidate["manifest"].get("limit"):
        raise ValueError("Cannot compare runs evaluated with different limits")
    keys = sorted(set(baseline["metrics"]) & set(candidate["metrics"]) - {"case_count"})
    result = {
        "schema_version": COMPARISON_SCHEMA,
        "generated_at": _utc_now(),
        "baseline": str(Path(baseline_path).resolve()),
        "candidate": str(Path(candidate_path).resolve()),
        "deltas": {key: round(candidate["metrics"][key] - baseline["metrics"][key], 6) for key in keys},
    }
    result["goal_lift"] = {
        key: result["deltas"][key]
        for key in ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")
        if key in result["deltas"]
    }
    if output_path:
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
