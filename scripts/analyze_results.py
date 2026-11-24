#!/usr/bin/env python3

import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS = Path("results")
OUTPUT = RESULTS / "analysis"

PATTERN = re.compile(
    r"^formal-(delay|loss|bandwidth|failure)(\d+)-(rr|lor)-r([1-3])\.json$"
)

METRICS = {
    "measured_requests": "measured_requests",
    "successful_requests": "successful_requests",
    "failed_requests": "failed_requests",
    "successful_rps": "successful_rps",
    "error_rate_percent": "error_rate_percent",
    "latency_mean_ms": ("latency_ms", "mean"),
    "latency_p50_ms": ("latency_ms", "p50"),
    "latency_p95_ms": ("latency_ms", "p95"),
    "all_attempt_mean_ms": ("all_attempt_latency_ms", "mean"),
    "all_attempt_p50_ms": ("all_attempt_latency_ms", "p50"),
    "all_attempt_p95_ms": ("all_attempt_latency_ms", "p95"),
}

REQUIRED = {
    "concurrency", "warmup_seconds", "measurement_seconds",
    "measured_requests", "successful_requests", "failed_requests",
    "successful_rps", "error_rate_percent", "latency_ms",
    "all_attempt_latency_ms", "backend_distribution",
    "status_distribution",
}


def get_value(data, path):
    if isinstance(path, tuple):
        return data[path[0]][path[1]]
    return data[path]


def calculate_stats(values):
    return {
        "mean": round(statistics.mean(values), 3),
        "sample_sd": round(statistics.stdev(values), 3)
        if len(values) > 1 else 0.0,
    }


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    groups = defaultdict(list)
    errors = []

    files = sorted(RESULTS.glob("formal-*.json"))

    for path in files:
        match = PATTERN.match(path.name)
        if not match:
            errors.append(f"{path.name}: unexpected filename")
            continue

        category, condition, policy, repetition = match.groups()

        try:
            data = json.loads(path.read_text())
            missing = REQUIRED - data.keys()
            if missing:
                raise ValueError(f"missing fields: {sorted(missing)}")

            measured = data["measured_requests"]
            successful = data["successful_requests"]
            failed = data["failed_requests"]

            if measured != successful + failed:
                raise ValueError("measured request count mismatch")

            if sum(data["status_distribution"].values()) != measured:
                raise ValueError("status distribution count mismatch")

            if sum(data["backend_distribution"].values()) != measured:
                raise ValueError("backend distribution count mismatch")

            data["_file"] = path.name
            data["_repetition"] = int(repetition)

            key = (category, int(condition), policy)
            groups[key].append(data)

        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    if len(files) != 63:
        errors.append(f"Expected 63 files, found {len(files)}")

    if len(groups) != 21:
        errors.append(f"Expected 21 conditions, found {len(groups)}")

    for key, runs in groups.items():
        if len(runs) != 3:
            errors.append(f"{key}: expected 3 runs, found {len(runs)}")

        repetitions = sorted(run["_repetition"] for run in runs)
        if repetitions != [1, 2, 3]:
            errors.append(f"{key}: repetitions are {repetitions}")

    if errors:
        print("VALIDATION FAILED:")
        for error in errors:
            print(" -", error)
        raise SystemExit(1)

    summaries = []

    for (category, condition, policy), runs in sorted(groups.items()):
        row = {
            "category": category,
            "condition": condition,
            "policy": policy,
            "repetitions": len(runs),
            "measurement_seconds": runs[0]["measurement_seconds"],
            "concurrency": runs[0]["concurrency"],
        }

        for output_name, source in METRICS.items():
            values = [get_value(run, source) for run in runs]
            stats = calculate_stats(values)
            row[f"{output_name}_mean"] = stats["mean"]
            row[f"{output_name}_sd"] = stats["sample_sd"]

        row["http_502_total"] = sum(
            int(run["status_distribution"].get("502", 0))
            for run in runs
        )

        summaries.append(row)

    performance = [
        row for row in summaries
        if row["category"] != "failure"
    ]
    failure = [
        row for row in summaries
        if row["category"] == "failure"
    ]

    fields = list(summaries[0].keys())

    for name, rows in (
        ("performance", performance),
        ("failure-recovery", failure),
    ):
        with (OUTPUT / f"summary-{name}.csv").open(
            "w", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

        (OUTPUT / f"summary-{name}.json").write_text(
            json.dumps(rows, indent=2) + "\n"
        )

    report = {
        "source_files": len(files),
        "conditions": len(groups),
        "repetitions_per_condition": 3,
        "performance_conditions": len(performance),
        "failure_recovery_conditions": len(failure),
        "validation_errors": 0,
        "output_directory": str(OUTPUT),
    }

    (OUTPUT / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )

    print(json.dumps(report, indent=2))
    print("\nGenerated:")
    for path in sorted(OUTPUT.glob("*")):
        print(f" - {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
