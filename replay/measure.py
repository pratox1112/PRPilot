"""Measure one send (replay.send): outcomes, latency and cost, all from measured quantities.

    $env:GCP_ACCESS_TOKEN = gcloud auth print-access-token
    uv run python -m replay.measure prices                      # list the SKUs to pin (free)
    uv run python -m replay.measure report --label pilot --api https://prpilot-api-....run.app

- Outcomes and latency: the final runs recorded by the sender and the api's delivery times
  (server clocks): webhook received to triage stored, and worker start to triage stored.
- Usage: Cloud Monitoring over the send's window (first send to last final run, plus a
  margin for the metrics' delay): billable instance time and requests per Cloud Run service,
  Pub/Sub billed bytes, push answers by response class, dead-lettered messages. Run the
  report at least 10 minutes after the send ends, with no other traffic in the window.
- Sizes: each service's CPU and memory as deployed (Cloud Run API), the database tier and disk
  (Cloud SQL Admin API).
- Prices: list prices from the Cloud Billing price catalog, by the SKU ids pinned in SKUS
  (chosen from `prices`, recorded with their descriptions). The monthly free tier is not
  applied, so the cost is what the same usage costs once the free tier is used up.

Writes results/<label>.json (with the commit and whether the tree was clean) and the per-PR
records next to it, results/<label>.records.jsonl.
"""

import argparse
import json
import math
import os
from datetime import UTC, datetime, timedelta

import httpx

from prpilot_data.records import RESULTS_DIR, read_jsonl, run_metadata, write_result
from replay.send import SENDS_DIR, secret_values

PROJECT, REGION = "prpilot-513002", "us-east1"
SERVICES = ("prpilot-api", "prpilot-worker", "prpilot-model", "prpilot-executor")
CATALOG = "https://cloudbilling.googleapis.com/v1/services"
CATALOG_SERVICES = {"run": "152E-C115-5142", "sql": "9662-B51E-5089", "pubsub": "A1E8-BE35-7EBC"}
# SKU ids per priced quantity, pinned after reading `prices` (None: not chosen yet).
SKUS: dict[str, str | None] = {
    "run_cpu": None,  # vCPU-second, request-based billing, us-east1
    "run_memory": None,  # GiB-second, request-based billing, us-east1
    "run_requests": None,  # requests
    "sql_instance": None,  # the database tier, per hour
    "sql_storage": None,  # SSD storage, per GiB-month
    "pubsub_throughput": None,  # message delivery, per TiB
}
MARGIN = timedelta(minutes=10)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (no interpolation)."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def rfc3339(d: datetime) -> str:
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def gib(memory: str) -> float:
    units = {"Gi": 1.0, "Mi": 1 / 1024, "G": 1e9 / 2**30, "M": 1e6 / 2**30}
    for u, f in units.items():
        if memory.endswith(u):
            return float(memory[: -len(u)]) * f
    raise ValueError(f"memory limit {memory!r}")


class Google:
    def __init__(self, token: str, project: str = PROJECT):
        self.project = project
        self.http = httpx.Client(timeout=60, headers={
            "Authorization": f"Bearer {token}", "x-goog-user-project": project})  # fmt: skip

    def get(self, url: str, **params) -> dict:
        r = self.http.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def pages(self, url: str, key: str, **params) -> list[dict]:
        out, token = [], None
        while True:
            page = self.get(url, **params, **({"pageToken": token} if token else {}))
            out += page.get(key, [])
            token = page.get("nextPageToken")
            if not token:
                return out

    def metric_sum(self, metric: str, start: datetime, end: datetime, extra: str = "",
                   group_by: str | None = None) -> dict[str, float]:  # fmt: skip
        """Sum of a DELTA metric over [start, end], per value of `group_by` (or "all")."""
        params = {
            "filter": f'metric.type="{metric}"{extra}',
            "interval.startTime": rfc3339(start), "interval.endTime": rfc3339(end),
            "aggregation.alignmentPeriod": f"{int((end - start).total_seconds()) + 60}s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
        }  # fmt: skip
        if group_by:
            params["aggregation.groupByFields"] = group_by
        url = f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries"
        sums: dict[str, float] = {}
        for series in self.pages(url, "timeSeries", **params):
            labels = {**series["metric"].get("labels", {}), **series["resource"]["labels"]}
            key = labels.get(group_by.split(".")[-1], "?") if group_by else "all"
            for p in series["points"]:
                v = p["value"]
                sums[key] = sums.get(key, 0.0) + float(v.get("doubleValue", v.get("int64Value", 0)))
        return sums


def list_price(sku: dict) -> tuple[float, str]:
    """The first non-zero tier (the price once any free tier is used up) and its unit."""
    expr = sku["pricingInfo"][0]["pricingExpression"]
    for tier in expr["tieredRates"]:
        p = tier["unitPrice"]
        price = int(p.get("units", "0")) + p.get("nanos", 0) / 1e9
        if price > 0:
            return price, expr["usageUnit"]
    return 0.0, expr["usageUnit"]


def catalog(g: Google) -> list[dict]:
    skus = []
    for name, sid in CATALOG_SERVICES.items():
        for s in g.pages(f"{CATALOG}/{sid}/skus", "skus", currencyCode="USD", pageSize=5000):
            if REGION in s.get("serviceRegions", []) or "global" in s.get("serviceRegions", []):
                skus.append({**s, "catalog": name})
    return skus


def cmd_prices(g: Google) -> None:
    for s in catalog(g):
        price, unit = list_price(s)
        print(f"{s['catalog']:6} {s['skuId']}  {price:.10f}/{unit:8}  {s['description']}")


def pinned_prices(g: Google) -> dict:
    if missing := [k for k, v in SKUS.items() if v is None]:
        raise SystemExit(f"SKU ids not pinned yet: {missing}; run `prices` and set SKUS")
    by_id = {s["skuId"]: s for s in catalog(g)}
    out = {}
    for key, sid in SKUS.items():
        price, unit = list_price(by_id[sid])
        out[key] = {"sku": sid, "description": by_id[sid]["description"], "usd": price,
                    "unit": unit}  # fmt: skip
    return out


def service_sizes(g: Google) -> dict:
    sizes = {}
    for name in SERVICES:
        svc = g.get(f"https://run.googleapis.com/v2/projects/{g.project}/locations/{REGION}"
                    f"/services/{name}")  # fmt: skip
        c = svc["template"]["containers"][0]["resources"]
        sizes[name] = {"vcpu": float(c["limits"]["cpu"]), "gib": gib(c["limits"]["memory"]),
                       "cpu_idle": c.get("cpuIdle", False)}  # fmt: skip
    return sizes


def database(g: Google) -> dict:
    [db] = g.get(f"https://sqladmin.googleapis.com/v1/projects/{g.project}/instances")["items"]
    return {"name": db["name"], "tier": db["settings"]["tier"],
            "disk_gib": float(db["settings"]["dataDiskSizeGb"])}  # fmt: skip


def outcomes(sends: list[dict], deliveries: dict[str, str]) -> dict:
    states: dict[str, int] = {}
    e2e, worker, requests = [], [], []
    valid = rescued = 0
    for s in sends:
        states[s["outcome"]] = states.get(s["outcome"], 0) + 1
        run = s.get("final")
        if not run:
            continue
        done = ts(run["updated_at"])
        if s["delivery_id"] in deliveries:
            e2e.append((done - ts(deliveries[s["delivery_id"]])).total_seconds())
        worker.append((done - ts(run["created_at"])).total_seconds())
        t = run.get("triage") or {}
        if t:
            requests.append(t["requests"])
            valid += bool(t["valid_after_retries"])
            rescued += bool(t["partly_filtered"])  # invalid, valid parts kept by the fallback

    def summary(xs):
        return {"n": len(xs), "p50_s": percentile(xs, 50), "p95_s": percentile(xs, 95),
                "max_s": max(xs) if xs else None}  # fmt: skip

    return {
        "prs_sent": len(sends), "outcomes": states,
        "triaged": len(requests), "valid_after_retries": valid, "rescued_by_fallback": rescued,
        "model_requests_mean": sum(requests) / len(requests) if requests else None,
        "webhook_to_stored": summary(e2e), "worker_start_to_stored": summary(worker),
    }  # fmt: skip


def cmd_report(g: Google, label: str, api: str) -> None:
    sends = read_jsonl(SENDS_DIR / f"{label}.sends.jsonl")
    _, admin = secret_values()
    r = httpx.get(f"{api}/deliveries", params={"limit": 20000},
                  headers={"Authorization": f"Bearer {admin}"}, timeout=60)  # fmt: skip
    r.raise_for_status()
    deliveries = {d["delivery_id"]: d["received_at"] for d in r.json()}
    start = min(ts(s["sent_at"]) for s in sends)
    end = max(ts(s.get("seen_final_at") or s["sent_at"]) for s in sends)
    window = (start, end + MARGIN)
    if datetime.now(UTC) < window[1]:
        raise SystemExit(f"wait until {rfc3339(window[1])}: the metrics arrive with a delay")

    run_filter = ' AND resource.type="cloud_run_revision"'
    billable = g.metric_sum("run.googleapis.com/container/billable_instance_time", *window,
                            run_filter, "resource.labels.service_name")  # fmt: skip
    requests = g.metric_sum("run.googleapis.com/request_count", *window, run_filter,
                            "resource.labels.service_name")  # fmt: skip
    pubsub_bytes = sum(g.metric_sum("pubsub.googleapis.com/topic/byte_cost", *window).values())
    pubsub_bytes += sum(
        g.metric_sum("pubsub.googleapis.com/subscription/byte_cost", *window).values()
    )
    push = g.metric_sum("pubsub.googleapis.com/subscription/push_request_count", *window,
                        "", "metric.labels.response_class")  # fmt: skip
    dead = sum(
        g.metric_sum("pubsub.googleapis.com/subscription/dead_letter_message_count",
                     *window).values()
    )  # fmt: skip

    sizes, db, prices = service_sizes(g), database(g), pinned_prices(g)
    usd = {k: v["usd"] for k, v in prices.items()}
    per_service = {}
    for name in SERVICES:
        sec = billable.get(name, 0.0)
        cpu_s, gib_s = sec * sizes[name]["vcpu"], sec * sizes[name]["gib"]
        reqs = requests.get(name, 0.0)
        cost = cpu_s * usd["run_cpu"] + gib_s * usd["run_memory"] + reqs * usd["run_requests"]
        per_service[name] = {"billable_instance_s": sec, "vcpu_s": cpu_s, "gib_s": gib_s,
                             "requests": reqs, "usd": cost, **sizes[name]}  # fmt: skip
    hours = (window[1] - window[0]).total_seconds() / 3600
    db_usd = hours * usd["sql_instance"] + db["disk_gib"] * usd["sql_storage"] * hours / 730
    pubsub_usd = pubsub_bytes / 2**40 * usd["pubsub_throughput"]
    serving_usd = sum(s["usd"] for s in per_service.values()) + pubsub_usd
    out = outcomes(sends, deliveries)
    n = max(out["prs_sent"], 1)
    minutes = (end - start).total_seconds() / 60
    write_result(label, {
        "label": label, "meta": run_metadata(["httpx"]),
        "window_utc": [rfc3339(w) for w in window], "send_minutes": minutes,
        "throughput_prs_per_min": out["prs_sent"] / minutes if minutes else None,
        **out,
        "pubsub": {"push_answers": push, "dead_lettered": dead, "billed_bytes": pubsub_bytes},
        "usage": per_service, "database": {**db, "hours": hours, "usd": db_usd},
        "prices": prices, "free_tier_applied": False,
        "cost_usd": {"serving": serving_usd, "database": db_usd,
                     "total": serving_usd + db_usd, "serving_per_pr": serving_usd / n,
                     "total_per_pr": (serving_usd + db_usd) / n},
    })  # fmt: skip
    with (RESULTS_DIR / f"{label}.records.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(s, default=str) + "\n" for s in sends)
    print(json.dumps({k: out[k] for k in ("outcomes", "webhook_to_stored")}, indent=1))
    print(f"serving ${serving_usd:.4f} (${serving_usd / n:.5f}/PR), database ${db_usd:.4f}")
    print(f"-> {RESULTS_DIR / (label + '.json')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prices")
    rep = sub.add_parser("report")
    rep.add_argument("--label", required=True)
    rep.add_argument("--api", required=True)
    a = ap.parse_args()
    token = os.environ.get("GCP_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("set GCP_ACCESS_TOKEN (gcloud auth print-access-token)")
    g = Google(token)
    if a.cmd == "prices":
        cmd_prices(g)
    else:
        cmd_report(g, a.label, a.api.rstrip("/"))


if __name__ == "__main__":
    main()
