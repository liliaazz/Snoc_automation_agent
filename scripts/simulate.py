#!/usr/bin/env python3
"""End-to-end simulation launcher for the SNOC email agent.

Modes:
  demo     Quick replay with offline demo backend (no GPU/API/credentials)
  full     Full pipeline: trained SVM + real LLM analyzer/verifier
  live     Same as full, but polls a real IMAP inbox instead of fixtures

Examples:
  # Demo mode (no setup needed)
  python scripts/simulate.py demo

  # Full mode with Ollama (see --llm-help first)
  python scripts/simulate.py full --llm ollama


  # Live IMAP polling with full pipeline
  python scripts/simulate.py live \
    --imap-host imap.gmail.com --imap-user you@gmail.com \
    --imap-pass "app-password" \
    --authorized-senders "me@example.com" \
    --llm ollama
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulate")

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "emails"

SCENARIO_EMAILS = [
    ("Complete unblock", "scenario_a_complete_unblock/01_complete_unblock.eml"),
    ("Incomplete OTP (clarification)", "scenario_b_otp_clarification/01_incomplete_otp.eml"),
    ("Multi-operation", "scenario_c_multi_operation/01_three_operations_one_incomplete.eml"),
    ("Reused chain", "scenario_d_reused_chain/02_reused_chain_new_reset.eml"),
    ("Edge: irrelevant message", "edge_cases/17_irrelevant_reporting.eml"),
]

ALL_FIXTURE_SENDERS = (
    "animateur.alpha@example.invalid,superviseur.beta@example.invalid,"
    "animateur.gamma@example.invalid,superviseur.delta@example.invalid,"
    "reporting@example.invalid,user@example.test"
)


# ── LLM provider helpers ──────────────────────────────────────────────────


def _ollama_instructions() -> str:
    return """\
  To use Ollama as your local LLM backend:

    1. Install Ollama:  https://ollama.com
    2. Pull a model:    ollama pull qwen2.5:7b
    3. Start the server: ollama serve
    4. Run the simulation:

       python scripts/simulate.py full --llm ollama --model qwen2.5:7b
"""




def _llm_help() -> None:
    print("=" * 72)
    print("  LLM BACKEND OPTIONS")
    print("=" * 72)
    print()
    print("  The full pipeline needs a real LLM for analysis and verification.")
    print("  Choose one of these backends:")
    print()
    print(_ollama_instructions())
    print()
    print("  Local OpenAI-compatible server (llama.cpp, LocalAI, etc.):")
    print()
    print("    python scripts/simulate.py full \\")
    print("      --llm openai --base-url http://127.0.0.1:8080/v1 --model your-model")
    print()
    sys.exit(0)


def _settings_kwargs(llm: str, **overrides: str) -> dict[str, object]:
    """Return Settings kwargs for the chosen pipeline mode."""
    kwargs: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite:///./simulation.db",
        "workflow_engine": "langgraph",
        "dry_run": True,
        "dry_run_send_emails": False,
        "store_raw_eml": False,
        "authorized_senders": ALL_FIXTURE_SENDERS,
        "escalation_recipient": "human-support@example.invalid",
    }

    if llm == "demo":
        # No LLM_PROVIDER → DemoLLMBackend auto-selected
        pass

    elif llm == "ollama":
        model = overrides.pop("model", "qwen2.5:7b")
        base_url = overrides.pop("base_url", "http://localhost:11434/v1")
        kwargs["llm_provider"] = "openai_compatible"
        kwargs["llm_base_url"] = base_url
        kwargs["analyzer_model"] = model
        kwargs["verifier_model"] = model
        kwargs["llm_timeout_seconds"] = 120

    elif llm == "openai":
        model = overrides.pop("model", "gpt-4o-mini")
        base_url = overrides.pop("base_url", "http://127.0.0.1:8080/v1")
        api_key = overrides.pop("api_key", "not-needed")
        kwargs["llm_provider"] = "openai_compatible"
        kwargs["llm_base_url"] = base_url
        kwargs["llm_api_key"] = api_key
        kwargs["analyzer_model"] = model
        kwargs["verifier_model"] = model
        kwargs["llm_timeout_seconds"] = 120


    else:
        raise ValueError(f"Unknown LLM backend: {llm}")

    kwargs.update(overrides)
    return kwargs


def _venv_python() -> str:
    venv = PROJECT_ROOT / ".venv"
    if venv.exists():
        for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
            if candidate.exists():
                return str(candidate)
    return sys.executable


# ── Pipeline steps ────────────────────────────────────────────────────────


def step_init_db(**settings_kwargs: str) -> None:
    db_url = settings_kwargs.get("database_url", "sqlite:///./simulation.db")
    db_file = Path(db_url.replace("sqlite:///", "")).resolve()
    if db_file.exists():
        db_file.unlink()
        logger.info("Removed existing database: %s", db_file)

    from snoc_agent.cli.commands import db_init
    from snoc_agent.config import Settings

    logger.info("Initializing database...")
    db_init(Settings(**settings_kwargs))
    logger.info("Database initialized.")


def step_check_llm(**settings_kwargs: str) -> bool:
    """Check that the configured LLM backend is reachable."""
    from snoc_agent.config import Settings

    settings = Settings(**settings_kwargs)
    llm = settings.effective_llm_provider
    if llm is None or str(llm) == "demo":
        return True

    base = settings.llm_base_url or ""
    logger.info("Checking LLM backend: %s (%s)", llm, base)

    try:
        import httpx
        r = httpx.get(base.rstrip("/") + "/models", timeout=10)
        if r.status_code >= 500:
            logger.warning("LLM at %s returned %s — server may not be ready", base, r.status_code)
            return False
        logger.info("LLM backend is reachable: %s models available", len(r.json().get("data", [])))
        return True
    except Exception as exc:
        logger.warning("LLM backend at %s is not reachable: %s", base, exc)
        return False


def _inject_analyzer_for_full_pipeline(runtime: object, settings: object) -> None:
    """Replace the default analyzer with the SVM-backed FallbackAnalyzer."""
    from snoc_agent.ai.fallback_analyzer import FallbackAnalyzer
    from snoc_agent.ai.svm_classifier import SVMClassifier

    svm = SVMClassifier()
    fallback = FallbackAnalyzer(analyzer=runtime.analyzer, svm_classifier=svm)
    runtime.processor.analyzer = fallback


def step_replay_fixtures(enable_svm: bool, **settings_kwargs: str) -> list[dict[str, object]]:
    """Process fixture emails in-process and return results."""
    from snoc_agent.cli.runtime import build_runtime
    from snoc_agent.config import Settings

    settings = Settings(**settings_kwargs)
    runtime = build_runtime(settings)

    if enable_svm:
        _inject_analyzer_for_full_pipeline(runtime, settings)

    results = []
    for label, rel_path in SCENARIO_EMAILS:
        fixture = (FIXTURES / rel_path).resolve()
        if not fixture.exists():
            logger.warning("Fixture not found: %s", fixture)
            continue

        logger.info("Processing: %s", label)
        raw = fixture.read_bytes()
        try:
            result = runtime.processor.process_raw(raw)
        except Exception as exc:
            logger.error("  FAILED: %s", exc)
            results.append({"label": label, "status": "error", "detail": str(exc)})
            continue

        status = result.status
        detail = result.detail or ""
        logger.info("  Status: %s  %s", status, detail[:80] if detail else "")
        results.append({"label": label, "status": status, "detail": detail})

    return results


def step_start_dashboard(port: int, db_url: str) -> subprocess.Popen | None:
    """Start the Streamlit dashboard in a subprocess if streamlit is available."""
    python = _venv_python()
    rc = subprocess.run(
        [python, "-m", "streamlit", "--version"],
        capture_output=True, timeout=10,
    ).returncode
    if rc != 0:
        logger.warning("streamlit not installed; pip install streamlit for the dashboard")
        return None

    env = {"PATH": os.environ.get("PATH", "/usr/bin")}
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    env["SNOC_DASHBOARD_DATABASE_URL"] = db_url

    dashboard = PROJECT_ROOT / "dashboard.py"
    proc = subprocess.Popen(
        [python, "-m", "streamlit", "run", str(dashboard), "--server.port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    logger.info("Dashboard starting at http://localhost:%s", port)
    time.sleep(3)
    return proc


def print_summary(results: list[dict[str, object]]) -> None:
    sep = "=" * 72
    print(f"\n{sep}")
    print("  PROCESSING SUMMARY")
    print(sep)
    for r in results:
        icon_map = {
            "processed": " OK ", "ignored": "IGN", "failed": "FAIL",
            "quarantined": "QUAR", "error": "ERR", "duplicate": "DUP",
        }
        icon = icon_map.get(str(r.get("status", "")), " ???")
        detail = str(r.get("detail", ""))[:60]
        print(f"  [{icon}] {r['label']}")
        if detail:
            print(f"         {detail}")
    print(sep)


# ── Commands ──────────────────────────────────────────────────────────────


def cmd_demo(args: argparse.Namespace) -> None:
    """Run simulation with offline demo backend (no LLM needed)."""
    kwargs = _settings_kwargs("demo")
    step_init_db(**kwargs)
    results = step_replay_fixtures(enable_svm=False, **kwargs)
    print_summary(results)
    _dashboard_footer(kwargs, args.port)


def cmd_full(args: argparse.Namespace) -> None:
    """Run simulation with the full SVM + LLM pipeline."""
    llm_backend = args.llm
    llm_kwargs: dict[str, str] = {}

    if llm_backend == "ollama":
        llm_kwargs["model"] = getattr(args, "model", "qwen2.5:7b")
        llm_kwargs["base_url"] = getattr(args, "base_url", "http://localhost:11434/v1")
    elif llm_backend == "openai":
        llm_kwargs["model"] = getattr(args, "model", "")
        llm_kwargs["base_url"] = getattr(args, "base_url", "")
        llm_kwargs["api_key"] = getattr(args, "api_key", "")

    kwargs = _settings_kwargs(llm_backend, **llm_kwargs)
    step_init_db(**kwargs)

    if not step_check_llm(**kwargs):
        print()
        print("  WARNING: LLM backend is not reachable.")
        print("  The pipeline will fail. Check your configuration.")
        print()
        if llm_backend == "ollama":
            print(_ollama_instructions())
        print()

    results = step_replay_fixtures(enable_svm=True, **kwargs)
    print_summary(results)
    _dashboard_footer(kwargs, args.port)


def cmd_live(args: argparse.Namespace) -> None:
    """Poll a real IMAP inbox with the full pipeline."""
    imap_host = args.imap_host
    imap_user = args.imap_user
    imap_pass = args.imap_pass
    authorized = args.authorized_senders

    if not imap_host or not imap_user or not imap_pass:
        print("=" * 60)
        print("  IMAP CREDENTIALS REQUIRED")
        print("=" * 60)
        print()
        print("  python scripts/simulate.py live \\")
        print("    --imap-host imap.gmail.com --imap-user you@gmail.com \\")
        print('    --imap-pass "app-password" --authorized-senders "me@example.com"')
        print()
        sys.exit(1)

    llm_backend = args.llm
    llm_kwargs: dict[str, str] = {}
    if llm_backend == "ollama":
        llm_kwargs["model"] = getattr(args, "model", "qwen2.5:7b")
        llm_kwargs["base_url"] = getattr(args, "base_url", "http://localhost:11434/v1")
    elif llm_backend == "openai":
        llm_kwargs["model"] = getattr(args, "model", "")
        llm_kwargs["base_url"] = getattr(args, "base_url", "")
        llm_kwargs["api_key"] = getattr(args, "api_key", "")

    kwargs = _settings_kwargs(
        llm_backend,
        imap_host=imap_host,
        imap_username=imap_user,
        imap_password=imap_pass,
        authorized_senders=authorized or "user@example.test",
        imap_search_criterion="ALL",
        store_raw_eml="true",
        **llm_kwargs,
    )

    step_init_db(**kwargs)

    if not step_check_llm(**kwargs):
        print("\n  WARNING: LLM backend unreachable. Exiting.\n")
        sys.exit(1)

    db_url = str(kwargs["database_url"])
    port = args.port

    print()
    print(f"  IMAP: {imap_user}@{imap_host}")
    print(f"  Authorized senders: {authorized}")
    print(f"  Dashboard: http://localhost:{port}")
    print()
    print("  Send an email to this inbox from an authorized sender.")
    print("  The worker polls every %d seconds." % args.interval)
    print("  Press Ctrl+C to stop.")
    print()

    dash_proc = step_start_dashboard(port, db_url)

    from snoc_agent.cli.commands import _real_orchestrator
    from snoc_agent.config import Settings

    settings = Settings(**kwargs)
    runtime, orchestrator = _real_orchestrator(settings)

    if llm_backend != "demo":
        _inject_analyzer_for_full_pipeline(runtime, settings)

    try:
        while True:
            try:
                results = orchestrator.poll_once()
            except (OSError, TimeoutError) as exc:
                results = []
                logger.warning("Poll transient error: %s", type(exc).__name__)
            for r in results:
                logger.info("[%s] email=%s  detail=%s", r.status, r.email_message_id, r.detail[:80])
            sent, failed = runtime.outbox.send_once()
            if sent or failed:
                logger.info("Outbox: sent=%d  failed=%d", sent, failed)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if dash_proc:
            dash_proc.terminate()
            dash_proc.wait()


def _dashboard_footer(kwargs: dict[str, object], port: int) -> None:
    db_url = kwargs.get("database_url", "sqlite:///./simulation.db")
    print()
    print("  View results:")
    print()
    dash_proc = step_start_dashboard(port, str(db_url))
    if dash_proc is None:
        print(f"    pip install streamlit")
        print(f"    SNOC_DASHBOARD_DATABASE_URL={db_url} streamlit run dashboard.py --server.port={port}")
        print()
        print(f"  Or query the database directly:")
        db_file = str(db_url).replace("sqlite:///", "")
        print(f"    sqlite3 {db_file} '.tables'")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm", choices=["ollama", "openai", "demo"],
                        default="demo", help="LLM backend to use")
    parser.add_argument("--model", help="Model name (for ollama/openai)")
    parser.add_argument("--base-url", help="Base URL (for ollama/openai)")
    parser.add_argument("--api-key", help="API key (for openai)")


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(description="SNOC end-to-end simulation")
    parser.add_argument("--llm-help", action="store_true", help="Show LLM setup guide and exit")
    sub = parser.add_subparsers(dest="mode")

    demo = sub.add_parser("demo", help="Quick replay with offline demo backend")
    demo.add_argument("--port", type=int, default=8502)
    demo.set_defaults(func=cmd_demo)

    full = sub.add_parser("full", help="Full SVM + LLM pipeline (requires an LLM backend)")
    _add_llm_args(full)
    full.add_argument("--port", type=int, default=8502)
    full.set_defaults(func=cmd_full)

    live = sub.add_parser("live", help="Poll real IMAP inbox with the full pipeline")
    _add_llm_args(live)
    live.add_argument("--imap-host")
    live.add_argument("--imap-user")
    live.add_argument("--imap-pass")
    live.add_argument("--authorized-senders")
    live.add_argument("--port", type=int, default=8502)
    live.add_argument("--interval", type=int, default=30)
    live.set_defaults(func=cmd_live)

    return parser


def main() -> None:
    parser = _build_parser()
    args, _ = parser.parse_known_args()

    if args.llm_help:
        _llm_help()
        return

    # Re-parse with full validation (subcommand required)
    parser = _build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices is not None:
            action.required = True
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
