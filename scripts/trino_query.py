#!/usr/bin/env python3
"""Simple Trino query runner using the REST API (polling). Usage: python trino_query.py "SELECT ..." """
import json
import sys
import time
import urllib.request
import urllib.parse

BASE = "http://localhost:8080"
USER = "finops"

def run(query: str, timeout_s: float = 90.0) -> list[dict]:
    body = query.encode()
    headers = {"X-Trino-User": USER, "Content-Type": "text/plain"}
    req = urllib.request.Request(BASE + "/v1/statement", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.load(resp)
    columns: list[tuple[str, str]] = []
    rows: list[dict] = []
    deadline = time.monotonic() + timeout_s
    while True:
        if data.get("columns"):
            columns = [(c["name"], c["type"]) for c in data["columns"]]
        for r in data.get("data", []):
            rows.append({name: val for (name, _), val in zip(columns, r)})
        stats = data.get("stats", {})
        if data.get("nextUri"):
            if time.monotonic() > deadline:
                raise TimeoutError("query timeout")
            nreq = urllib.request.Request(data["nextUri"], headers={"X-Trino-User": USER})
            with urllib.request.urlopen(nreq, timeout=timeout_s) as resp:
                data = json.load(resp)
        else:
            break
    return rows

if __name__ == "__main__":
    q = sys.argv[1]
    t = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    start = time.monotonic()
    rows = run(q, t)
    print(f"--- {len(rows)} rows in {time.monotonic()-start:.1f}s ---")
    if rows:
        print("cols:", ", ".join(rows[0].keys()))
    for r in rows:
        print(r)
