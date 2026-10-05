#!/usr/bin/env python3
"""Time the platform page endpoints over HTTP: this checkout vs a baseline ref.

Measures what a caller sees: `GET /runs/{run_id}/page` and
`GET /projects/{project_id}/page` end to end, including the platform's fan-out
to the upstream biosimulations.org API. Requests are sent one at a time and
interleaved across servers, so upstream latency drift affects both equally.

By default the script manages both servers itself:

  1. fetches the baseline ref (default origin/main) and checks it out into a
     temporary git worktree;
  2. starts one API from this working tree (uncommitted changes included) and
     one from the baseline worktree, each on a free local port;
  3. waits for both to answer `GET /version`, then runs the benchmark;
  4. stops both servers and removes the worktree -- also on failure, Ctrl+C,
     or SIGTERM.

The baseline reuses this checkout's virtualenv when `uv.lock` and
`pyproject.toml` match, and otherwise gets its own environment inside the
worktree (slower on first run). Both servers read `backend/.env` if present.
The API needs local Mongo and Temporal: run `docker compose up -d` first.

Usage:
    uv run python scripts/bench_pages.py [--baseline-ref REF] [--no-fetch]
        [--endpoint run|project|all] [--requests N] [--run-id ID] [--project-id ID]

To time servers that are already running instead, pass them explicitly; nothing
is started or stopped:
    uv run python scripts/bench_pages.py \\
        --target baseline=http://127.0.0.1:8001 --target branch=http://127.0.0.1:8000

Every page request makes 3-4 GETs against the public biosimulations.org API, so
keep --requests small; more than 50 requires --force.

When it manages the servers itself it also prints a per-phase breakdown for each
page: median page time split into the identity phase and the satellite phase,
plus the decoded bytes the page had to buffer. Those come from the API's own
structured page records (biosim_server/pages/service.py, common/upstream.py) in
the managed servers' logs -- end-to-end latency alone cannot say where the time
or the bytes went, and a byte budget cannot be chosen without them. A baseline
ref that predates the instrumentation simply has no such records, and the
breakdown says so instead of printing zeros.
"""

import argparse
import json
import math
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from urllib.parse import quote

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ID = "61fea483f499ccf25faafc4d"
DEFAULT_PROJECT_ID = "Yeast-cell-cycle-Irons-J-Theor-Biol-2009"
_MAX_POLITE_REQUESTS = 50
_INFRA_HINT = "The API needs local Mongo and Temporal: run `docker compose up -d` from the repo root."


@dataclass
class Samples:
    millis: list[float] = field(default_factory=list)
    statuses: set[int] = field(default_factory=set)
    size: int = 0
    errors: int = 0


@dataclass
class Phases:
    """What one server's own structured page records say, for one page."""
    requests: int = 0
    page_millis: list[float] = field(default_factory=list)
    identity_millis: list[float] = field(default_factory=list)
    satellites_millis: list[float] = field(default_factory=list)
    bytes_read: list[int] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)


# `--endpoint` labels to the page name the API logs, for the phase breakdown.
_PAGE_BY_LABEL = {"run page": "run", "project page": "project"}


# --- benchmark ---------------------------------------------------------------

def _target(value: str) -> tuple[str, str]:
    name, sep, url = value.partition("=")
    if not sep or not name or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError(f"expected NAME=http(s)://HOST:PORT, got {value!r}")
    return name, url.rstrip("/")


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _get(client: httpx.Client, path: str) -> tuple[float, int, int]:
    """Time one request, including reading the full response body."""
    start = time.monotonic()
    response = client.get(path)
    return (time.monotonic() - start) * 1000, response.status_code, len(response.content)


def _report(label: str, path: str, results: dict[str, Samples]) -> None:
    width = max(6, *(len(name) for name in results))
    print(f"\n{label}: GET {path}")
    print(f"  {'target':<{width}} {'min':>6} {'median':>7} {'p90':>6} {'max':>6}  {'status':<8} {'bytes':>7}  change")
    reference: tuple[str, float] | None = None
    for name, samples in results.items():
        if not samples.millis:
            print(f"  {name:<{width}} no successful requests ({samples.errors} errors)")
            continue
        median = statistics.median(samples.millis)
        change = ""
        if reference is None:
            reference = (name, median)
        else:
            delta = median - reference[1]
            change = f"{delta:+.0f} ms ({delta / reference[1]:+.0%}) vs {reference[0]}"
        statuses = ",".join(str(status) for status in sorted(samples.statuses))
        print(
            f"  {name:<{width}} {min(samples.millis):>6.0f} {median:>7.0f} {_percentile(samples.millis, 90):>6.0f}"
            f" {max(samples.millis):>6.0f}  {statuses:<8} {samples.size:>7}  {change}"
        )
        if samples.statuses != {200}:
            print(f"  {'':<{width}} warning: timings include non-200 responses")
        if samples.errors:
            print(f"  {'':<{width}} warning: {samples.errors} requests failed to connect or timed out")


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _read_phase_samples(log_path: Path, page: str) -> Phases:
    """Parse one managed server's page-phase records out of its log.

    The API writes one JSON line per page request and one per upstream fetch;
    uvicorn's access lines are not JSON and are skipped, as is anything a
    baseline checkout without the instrumentation logs (no records, rather than
    invented zeros).
    """
    samples = Phases()
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return samples
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("page") != page:
            continue
        outcome = record.get("page_outcome")
        if outcome is None:
            # An upstream-fetch record: how much this page had to buffer.
            if isinstance(record.get("upstream_bytes"), int):
                samples.bytes_read.append(record["upstream_bytes"])
            continue
        samples.requests += 1
        samples.outcomes[str(outcome)] = samples.outcomes.get(str(outcome), 0) + 1
        for key, collected in (
            ("page_duration_ms", samples.page_millis),
            ("page_identity_duration_ms", samples.identity_millis),
            ("page_satellites_duration_ms", samples.satellites_millis),
        ):
            value = record.get(key)
            if isinstance(value, int):
                collected.append(float(value))
    return samples


def _report_phases(label: str, page: str, targets: list[tuple[str, str]], log_dir: Path) -> None:
    """Phase and byte breakdown of the same requests, from the servers' logs."""
    if not any(log_dir.glob("*.log")):
        return
    print(f"\n{label}: where the time and bytes went (from each server's own records)")
    for name, _ in targets:
        samples = _read_phase_samples(log_dir / f"{name.replace('/', '_')}.log", page)
        if not samples.requests:
            print(f"  {name:<6} no phase records (a baseline that predates the instrumentation emits none)")
            continue
        outcomes = ", ".join(f"{key}={count}" for key, count in sorted(samples.outcomes.items()))
        bytes_note = ""
        if samples.bytes_read:
            fetches = len(samples.bytes_read)
            bytes_note = (
                f", upstream median {_median([float(value) for value in samples.bytes_read]) / 1024:.0f} KiB"
                f" (total {sum(samples.bytes_read) / 1024:.0f} KiB over {fetches} fetch{'' if fetches == 1 else 'es'})"
            )
        print(
            f"  {name:<6} n={samples.requests} ({outcomes}), page median {_median(samples.page_millis):.0f} ms ="
            f" identity {_median(samples.identity_millis):.0f} ms + satellites {_median(samples.satellites_millis):.0f} ms"
            f"{bytes_note}"
        )
    print("  (medians of the same requests; the identity/satellite phases overlap on the run page)")


def _bench(targets: list[tuple[str, str]], paths: list[tuple[str, str]], requests: int, warmup: int, pause: float) -> None:
    print(f"{requests} timed requests per target, interleaved: " + ", ".join(f"{name}={url}" for name, url in targets))
    clients = {name: httpx.Client(base_url=url, timeout=90.0) for name, url in targets}
    try:
        for label, path in paths:
            # Warm-up opens each target's connection and is not counted.
            for name, client in clients.items():
                for _ in range(warmup):
                    try:
                        _get(client, path)
                    except httpx.RequestError as exc:
                        raise SystemExit(f"{name} ({client.base_url}) is unreachable: {exc!r}") from exc
            results = {name: Samples() for name in clients}
            for _ in range(requests):
                for name, client in clients.items():
                    samples = results[name]
                    try:
                        millis, status, size = _get(client, path)
                    except httpx.RequestError:
                        samples.errors += 1
                    else:
                        samples.millis.append(millis)
                        samples.statuses.add(status)
                        samples.size = size
                    time.sleep(pause)  # be polite to the upstream API we don't own
            _report(label, path, results)
    finally:
        for client in clients.values():
            client.close()


# --- managed servers ---------------------------------------------------------

def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=BACKEND_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _tail(path: Path, lines: int = 30) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "    (no server log)"
    return "\n".join(f"    | {line}" for line in text.splitlines()[-lines:])


def _ignore_interrupts() -> None:
    """Once cleanup starts, let it finish.

    `uv run` forwards Ctrl+C to this process on top of the terminal's own, and
    impatient users press it twice; a second KeyboardInterrupt landing inside a
    cleanup block would abandon it and leak a server. The process exits right
    after cleanup, so the handlers are never restored.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


@contextmanager
def _worktree(ref: str) -> Iterator[Path]:
    """Check ``ref`` out into a temporary worktree and remove it afterwards."""
    parent = Path(tempfile.mkdtemp(prefix="bench-pages-"))
    path = parent / "baseline"
    try:
        _git("worktree", "add", "--detach", str(path), ref)
        yield path
    finally:
        _ignore_interrupts()
        created = path.exists()
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=BACKEND_DIR, capture_output=True)
        shutil.rmtree(parent, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=BACKEND_DIR, capture_output=True)
        if created:
            print("removed the baseline worktree")


def _stop(process: "subprocess.Popen[bytes]", port: int) -> None:
    """Stop the server's whole process group: `uv run` and the uvicorn it spawned."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while _port_open(port) and time.monotonic() < deadline:
        time.sleep(0.2)
    try:
        os.killpg(process.pid, signal.SIGKILL)  # anything that ignored SIGTERM
    except ProcessLookupError:
        pass
    process.wait()


@contextmanager
def _server(name: str, command: list[str], cwd: Path, env: dict[str, str], log_dir: Path, timeout: float) -> Iterator[str]:
    """Start one API server, wait until it answers, and stop it on exit."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log_path = log_dir / f"{name.replace('/', '_')}.log"
    print(f"starting {name} on {url} ...")
    with log_path.open("wb") as log:
        # A new session keeps Ctrl+C from reaching the servers directly, so the
        # cleanup below is what stops them, in order.
        process = subprocess.Popen(
            [*command, "--host", "127.0.0.1", "--port", str(port)],
            cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    try:
        deadline = time.monotonic() + timeout
        while True:
            if process.poll() is not None:
                raise SystemExit(f"{name} server exited during startup (code {process.returncode}):\n{_tail(log_path)}\n{_INFRA_HINT}")
            try:
                if httpx.get(f"{url}/version", timeout=2).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            if time.monotonic() > deadline:
                raise SystemExit(f"{name} server was not ready after {timeout:.0f}s:\n{_tail(log_path)}\n{_INFRA_HINT}")
            time.sleep(0.5)
        yield url
    finally:
        _ignore_interrupts()
        _stop(process, port)
        print(f"stopped {name}")


def _compare(baseline_ref: str, fetch: bool, startup_timeout: float,
             paths: list[tuple[str, str]], requests: int, warmup: int, pause: float) -> None:
    remote, sep, remote_branch = baseline_ref.partition("/")
    if fetch and sep and remote in _git("remote").split():
        result = subprocess.run(["git", "fetch", "--quiet", remote, remote_branch], cwd=BACKEND_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"warning: could not fetch {baseline_ref}, using the local copy: {result.stderr.strip()}")
    baseline_sha = _git("rev-parse", "--short", f"{baseline_ref}^{{commit}}")
    branch_name = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch_sha = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", "."))
    print(f"baseline: {baseline_ref} @ {baseline_sha}")
    print(f"branch:   {branch_name} @ {branch_sha}{' + uncommitted changes' if dirty else ''}")
    if branch_name == baseline_ref:
        branch_name = f"{branch_name} (working tree)"

    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env_file = BACKEND_DIR / ".env"
    env_args = ["--env-file", str(env_file)] if env_file.exists() else []
    if not env_file.exists():
        # Page endpoints are public; without .env there is no Auth0 config to validate.
        env.setdefault("AUTH_REQUIRED", "false")
        print("note: backend/.env not found; starting both servers with AUTH_REQUIRED=false")
    uvicorn = ["uvicorn", "biosim_server.api.main:app"]

    with ExitStack() as stack:
        worktree = stack.enter_context(_worktree(baseline_ref))
        baseline_backend = worktree / "backend"
        if not (baseline_backend / "biosim_server").is_dir():
            raise SystemExit(f"{baseline_ref} has no backend/biosim_server to run")
        shared_env = all(
            (BACKEND_DIR / name).read_bytes() == (baseline_backend / name).read_bytes()
            for name in ("uv.lock", "pyproject.toml")
        )
        if shared_env:
            # Same dependencies: reuse this venv, importing the baseline's code via --app-dir.
            baseline_command = ["uv", "run", "--project", str(BACKEND_DIR), *env_args, *uvicorn, "--app-dir", "."]
        else:
            print(f"note: {baseline_ref} has different dependencies; building its own environment (slower)")
            baseline_command = ["uv", "run", "--project", str(baseline_backend), *env_args, *uvicorn]
        branch_command = ["uv", "run", "--project", str(BACKEND_DIR), *env_args, *uvicorn]

        log_dir = worktree.parent
        baseline_url = stack.enter_context(
            _server(baseline_ref, baseline_command, baseline_backend, env, log_dir, startup_timeout))
        branch_url = stack.enter_context(
            _server(branch_name, branch_command, BACKEND_DIR, env, log_dir, startup_timeout))
        targets = [(baseline_ref, baseline_url), (branch_name, branch_url)]
        _bench(targets, paths, requests, warmup, pause)
        for label, _path in paths:
            page = _PAGE_BY_LABEL.get(label)
            if page is not None:
                _report_phases(label, page, targets, log_dir)


def _exit_on_sigterm(signum: int, _frame: FrameType | None) -> None:
    sys.exit(128 + signum)  # unwinds through the cleanup, like Ctrl+C does


def main() -> None:
    # Show progress as it happens when output is piped (tee, CI logs), not only at exit.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-ref", default="origin/main", help="Git ref to compare against (default origin/main).")
    parser.add_argument("--no-fetch", action="store_true", help="Use the local copy of the baseline ref without fetching.")
    parser.add_argument("--startup-timeout", type=float, default=180.0, help="Seconds to wait for each server to start (default 180).")
    parser.add_argument("--target", action="append", type=_target, metavar="NAME=URL",
                        help="Time an already-running API instead, repeatable; starts no servers.")
    parser.add_argument("--endpoint", choices=["run", "project", "all"], default="all",
                        help="Which page endpoint to time (default all).")
    parser.add_argument("--requests", "-n", type=int, default=10, help="Timed requests per server per endpoint (default 10).")
    parser.add_argument("--warmup", type=int, default=1, help="Untimed requests per server before timing (default 1).")
    parser.add_argument("--pause", type=float, default=0.25, help="Seconds between requests (default 0.25).")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID, help=f"Run id for the run page (default {DEFAULT_RUN_ID}).")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help=f"Project id for the project page (default {DEFAULT_PROJECT_ID}).")
    parser.add_argument("--force", action="store_true", help=f"Allow more than {_MAX_POLITE_REQUESTS} requests.")
    args = parser.parse_args()

    if args.requests < 1 or args.warmup < 0 or args.pause < 0:
        parser.error("--requests must be at least 1; --warmup and --pause cannot be negative")
    if args.requests > _MAX_POLITE_REQUESTS and not args.force:
        parser.error(f"--requests above {_MAX_POLITE_REQUESTS} loads the public biosimulations.org API; pass --force if you're sure")
    if args.target and len({name for name, _ in args.target}) != len(args.target):
        parser.error("each --target needs a distinct NAME")

    paths = []
    if args.endpoint in ("run", "all"):
        paths.append(("run page", f"/runs/{quote(args.run_id, safe='')}/page"))
    if args.endpoint in ("project", "all"):
        paths.append(("project page", f"/projects/{quote(args.project_id, safe='')}/page"))

    if args.target:
        _bench(args.target, paths, args.requests, args.warmup, args.pause)
        return
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        _compare(args.baseline_ref, not args.no_fetch, args.startup_timeout, paths, args.requests, args.warmup, args.pause)
    except KeyboardInterrupt:
        # Cleanup has already run by the time the interrupt reaches here.
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
