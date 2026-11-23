#!/usr/bin/env python3

import argparse
import csv
import json
import math
import statistics
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def percentile(values, percentage):
    if not values:
        return None

    ordered = sorted(values)
    index = math.ceil((percentage / 100) * len(ordered)) - 1

    return ordered[max(0, index)]


def worker(
    worker_id,
    start_barrier,
    timing,
    url,
    timeout,
):
    records = []
    warmup_requests = 0

    with httpx.Client(
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=1,
            max_keepalive_connections=1,
        ),
        trust_env=False,
    ) as client:

        start_barrier.wait()

        start_at = timing["start_at"]
        measure_start = timing["measure_start"]
        end_at = timing["end_at"]

        while time.monotonic() < start_at:
            time.sleep(0.001)

        while time.monotonic() < end_at:

            request_start = time.monotonic()

            try:
                response = client.get(url)

                latency_ms = (
                    time.monotonic() - request_start
                ) * 1000

                status_code = response.status_code
                backend_id = response.headers.get(
                    "x-backend-id", "unknown"
                )

                error_type = ""

            except httpx.HTTPError as exc:

                latency_ms = (
                    time.monotonic() - request_start
                ) * 1000

                status_code = None
                backend_id = "unknown"
                error_type = type(exc).__name__

            if request_start < measure_start:
                warmup_requests += 1
                continue

            if request_start >= end_at:
                break

            records.append({
                "worker_id": worker_id,
                "started_offset_seconds": round(
                    request_start - start_at, 6
                ),
                "latency_ms": round(latency_ms, 3),
                "status_code": status_code,
                "backend_id": backend_id,
                "success": (
                    status_code is not None
                    and 200 <= status_code < 300
                ),
                "error_type": error_type,
            })

    return records, warmup_requests


def main():

    parser = argparse.ArgumentParser(
        description="CloudForge concurrent HTTP workload generator"
    )

    parser.add_argument(
        "--url",
        default="http://10.10.1.20:8000/users/",
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--warmup",
        type=float,
        default=10,
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=60,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5,
    )

    parser.add_argument(
        "--output",
        default="results/workload.csv",
    )

    args = parser.parse_args()

    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    if args.warmup < 0 or args.duration <= 0:
        parser.error(
            "--warmup cannot be negative and "
            "--duration must be positive"
        )

    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    start_barrier = threading.Barrier(args.concurrency + 1)

    timing = {
        "start_at": None,
        "measure_start": None,
        "end_at": None,
    }

    all_records = []
    total_warmup_requests = 0

    print("========== CLOUDFORGE WORKLOAD ==========")
    print("URL:", args.url)
    print("Concurrency:", args.concurrency)
    print("Warm-up:", args.warmup, "seconds")
    print("Measurement:", args.duration, "seconds")
    print("Timeout:", args.timeout, "seconds")
    print("Output:", args.output)
    print("=========================================")

    with ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:

        futures = [
            executor.submit(
                worker,
                worker_id,
                start_barrier,
                timing,
                args.url,
                args.timeout,
            )
            for worker_id in range(1, args.concurrency + 1)
        ]

        timing["start_at"] = time.monotonic() + 0.5
        timing["measure_start"] = (
            timing["start_at"] + args.warmup
        )
        timing["end_at"] = (
            timing["measure_start"] + args.duration
        )

        start_barrier.wait()

        for future in futures:
            records, warmup_count = future.result()

            all_records.extend(records)
            total_warmup_requests += warmup_count

    all_records.sort(
        key=lambda record: record["started_offset_seconds"]
    )

    all_attempt_latencies = [
        record["latency_ms"]
        for record in all_records
    ]

    successful = [
        record for record in all_records
        if record["success"]
    ]

    successful_latencies = [
        record["latency_ms"]
        for record in successful
    ]

    failed = len(all_records) - len(successful)

    backend_distribution = {}

    for record in all_records:
        backend = record["backend_id"]

        backend_distribution[backend] = (
            backend_distribution.get(backend, 0) + 1
        )

    status_distribution = {}

    for record in all_records:
        status = (
            str(record["status_code"])
            if record["status_code"] is not None
            else "connection_error"
        )

        status_distribution[status] = (
            status_distribution.get(status, 0) + 1
        )

    measured_requests = len(all_records)

    summary = {
        "url": args.url,
        "concurrency": args.concurrency,
        "warmup_seconds": args.warmup,
        "measurement_seconds": args.duration,
        "warmup_requests": total_warmup_requests,
        "measured_requests": measured_requests,
        "successful_requests": len(successful),
        "failed_requests": failed,
        "successful_rps": round(
            len(successful) / args.duration, 3
        ),
        "error_rate_percent": round(
            failed / measured_requests * 100, 3
        ) if measured_requests else 0,
        "latency_ms": {
            "mean": round(statistics.fmean(successful_latencies), 3)
            if successful_latencies else None,
            "p50": round(percentile(successful_latencies, 50), 3)
            if successful_latencies else None,
            "p95": round(percentile(successful_latencies, 95), 3)
            if successful_latencies else None,
        },
        "all_attempt_latency_ms": {
            "mean": round(statistics.fmean(all_attempt_latencies), 3)
            if all_attempt_latencies else None,
            "p50": round(percentile(all_attempt_latencies, 50), 3)
            if all_attempt_latencies else None,
            "p95": round(percentile(all_attempt_latencies, 95), 3)
            if all_attempt_latencies else None,
        },
        "backend_distribution": backend_distribution,
        "status_distribution": status_distribution,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "worker_id",
        "started_offset_seconds",
        "latency_ms",
        "status_code",
        "backend_id",
        "success",
        "error_type",
    ]

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(all_records)

    summary_path = output_path.with_suffix(".json")

    with summary_path.open(
        "w",
        encoding="utf-8",
    ) as json_file:

        json.dump(
            summary,
            json_file,
            indent=4,
        )

    print("\n========== RESULTS ==========")
    print(json.dumps(summary, indent=4))
    print("\nCSV:", output_path)
    print("JSON:", summary_path)


if __name__ == "__main__":
    main()
