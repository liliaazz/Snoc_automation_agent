"""Unified command-line entry point for workers, replay, evaluation, and inspection."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from snoc_agent.ai.errors import InferenceError
from snoc_agent.cli import commands
from snoc_agent.config import load_settings
from snoc_agent.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snoc-agent")
    parser.add_argument("--env-file", type=Path, help="optional environment file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database commands")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="upgrade the database to the latest Alembic revision")

    mail = subparsers.add_parser("mail", help="IMAP commands")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)
    poll = mail_sub.add_parser("poll", help="poll IMAP with UID-based fetching")
    poll_mode = poll.add_mutually_exclusive_group()
    poll_mode.add_argument("--once", action="store_true", help="poll one batch (default)")
    poll_mode.add_argument("--loop", action="store_true", help="poll continuously")

    worker = subparsers.add_parser("worker", help="combined synchronous worker")
    worker_sub = worker.add_subparsers(dest="worker_command", required=True)
    worker_sub.add_parser("run", help="poll, process, and send the outbox continuously")

    processing = subparsers.add_parser("processing", help="stored-message processing")
    processing_sub = processing.add_subparsers(dest="processing_command", required=True)
    processing_sub.add_parser("retry-failed", help="retry failed messages from stored raw MIME")

    outbox = subparsers.add_parser("outbox", help="outbound email delivery")
    outbox_sub = outbox.add_subparsers(dest="outbox_command", required=True)
    send = outbox_sub.add_parser("send", help="send persisted outbox entries")
    send_mode = send.add_mutually_exclusive_group()
    send_mode.add_argument("--once", action="store_true", help="send one batch (default)")
    send_mode.add_argument("--loop", action="store_true", help="send continuously")

    replay_email = subparsers.add_parser("replay-email", help="replay one local RFC email")
    replay_email.add_argument("path", type=Path)
    replay_directory = subparsers.add_parser(
        "replay-directory", help="replay a directory recursively in filename order"
    )
    replay_directory.add_argument("path", type=Path)
    replay_directory.add_argument(
        "--scenario", help="named scenario from scenario.json when a directory defines several"
    )

    evaluate = subparsers.add_parser("evaluate", help="offline dataset evaluation")
    evaluate.add_argument("--dataset", required=True, type=Path)
    evaluate.add_argument("--analyzer-model")
    evaluate.add_argument("--verifier-model")
    evaluate.add_argument(
        "--matrix",
        action="store_true",
        help="run and compare all configured provider analyzer-verifier pairings",
    )
    evaluate.add_argument("--output-dir", required=True, type=Path)
    evaluate.add_argument("--limit", type=int)
    cache_group = evaluate.add_mutually_exclusive_group()
    cache_group.add_argument("--use-cache", action="store_true")
    cache_group.add_argument("--no-cache", action="store_true")
    cache_group.add_argument("--refresh-cache", action="store_true")
    evaluate.add_argument("--resume", action="store_true")
    evaluate.add_argument("--budget-usd", type=Decimal)
    evaluate.add_argument("--stop-before-budget-usd", type=Decimal)
    evaluate.add_argument("--checkpoint-every", type=int)
    evaluate.add_argument(
        "--confirm-budget",
        action="store_true",
        help="explicitly confirm a configured provider evaluation budget",
    )

    models = subparsers.add_parser("models", help="configured model discovery and probes")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_list = models_sub.add_parser("list", help="list router model metadata")
    models_list.add_argument("--all", action="store_true", help="show the entire router catalog")
    models_list.add_argument("--refresh", action="store_true", help="bypass the model-list cache")
    models_list.add_argument("--json", action="store_true", dest="json_output")
    models_check = models_sub.add_parser("check", help="check configured routes and schemas")
    models_check.add_argument("--refresh", action="store_true")
    models_smoke = models_sub.add_parser(
        "smoke-test", help="run fake-data-only analyzer/verifier inference"
    )
    models_smoke.add_argument("--analyzer-model")
    models_smoke.add_argument("--verifier-model")
    models_smoke.add_argument(
        "--output-dir", type=Path, default=Path("outputs/evaluation/model_smoke")
    )

    evaluation = subparsers.add_parser(
        "evaluation", help="build evaluation subsets and calibration artifacts"
    )
    evaluation_sub = evaluation.add_subparsers(dest="evaluation_command", required=True)
    datasets = evaluation_sub.add_parser("datasets", help="evaluation dataset utilities")
    datasets_sub = datasets.add_subparsers(dest="datasets_command", required=True)
    datasets_build = datasets_sub.add_parser("build")
    datasets_build.add_argument("--source", required=True, type=Path)
    datasets_build.add_argument("--output-dir", required=True, type=Path)
    calibrate = evaluation_sub.add_parser("calibrate")
    calibrate.add_argument("--predictions", required=True, type=Path)
    calibrate.add_argument("--method", choices=("none", "logistic", "isotonic"), default="none")
    calibrate.add_argument("--split-manifest", type=Path)
    calibrate.add_argument("--output", required=True, type=Path)

    request = subparsers.add_parser("request", help="inspect request state")
    request_sub = request.add_subparsers(dest="request_command", required=True)
    request_show = request_sub.add_parser("show")
    request_show.add_argument("reference")

    conversation = subparsers.add_parser("conversation", help="inspect conversation state")
    conversation_sub = conversation.add_subparsers(dest="conversation_command", required=True)
    conversation_show = conversation_sub.add_parser("show")
    conversation_show.add_argument("conversation_id", type=uuid.UUID)

    operation = subparsers.add_parser("operation", help="inspect operation state")
    operation_sub = operation.add_subparsers(dest="operation_command", required=True)
    operation_show = operation_sub.add_parser("show")
    operation_show.add_argument("operation_id", type=uuid.UUID)

    audit = subparsers.add_parser("audit", help="inspect the persisted per-email processing audit")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_sub.add_parser("list", help="list recently treated inbound emails")
    audit_list.add_argument("--limit", type=int, default=50)
    audit_show = audit_sub.add_parser("show", help="show every persisted stage for one email")
    audit_show.add_argument("email_id", type=uuid.UUID)

    failures = subparsers.add_parser("failures", help="inspect failed messages")
    failures_sub = failures.add_subparsers(dest="failures_command", required=True)
    failures_sub.add_parser("list")
    quarantine = subparsers.add_parser("quarantine", help="inspect parse-fatal messages")
    quarantine_sub = quarantine.add_subparsers(dest="quarantine_command", required=True)
    quarantine_sub.add_parser("list")
    quarantine_retry = quarantine_sub.add_parser("retry")
    quarantine_retry.add_argument("email_id", type=uuid.UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(logging.INFO)
    try:
        settings = load_settings(args.env_file)
        if args.command == "db" and args.db_command == "init":
            commands.db_init(settings)
        elif args.command == "mail" and args.mail_command == "poll":
            commands.mail_poll(settings, loop=args.loop)
        elif args.command == "worker" and args.worker_command == "run":
            commands.worker_run(settings)
        elif args.command == "processing" and args.processing_command == "retry-failed":
            commands.retry_failures(settings)
        elif args.command == "outbox" and args.outbox_command == "send":
            commands.outbox_send(settings, loop=args.loop)
        elif args.command == "replay-email":
            commands.replay_email(settings, args.path)
        elif args.command == "replay-directory":
            commands.replay_directory(settings, args.path, scenario=args.scenario)
        elif args.command == "evaluate":
            if args.matrix:
                commands.evaluate_model_matrix(
                    settings,
                    dataset=args.dataset,
                    output_dir=args.output_dir,
                    limit=args.limit,
                    use_cache=args.use_cache,
                    no_cache=args.no_cache,
                    refresh_cache=args.refresh_cache,
                    resume=args.resume,
                    budget_usd=args.budget_usd,
                    stop_before_budget_usd=args.stop_before_budget_usd,
                    checkpoint_every=args.checkpoint_every,
                    confirm_budget=args.confirm_budget,
                    env_file=args.env_file,
                )
            elif args.analyzer_model and args.verifier_model:
                commands.evaluate_models(
                    settings,
                    dataset=args.dataset,
                    analyzer_model=args.analyzer_model,
                    verifier_model=args.verifier_model,
                    output_dir=args.output_dir,
                    limit=args.limit,
                    budget_usd=args.budget_usd,
                    stop_before_budget_usd=args.stop_before_budget_usd,
                    confirm_budget=args.confirm_budget,
                    use_cache=args.use_cache,
                    no_cache=args.no_cache,
                    refresh_cache=args.refresh_cache,
                    resume=args.resume,
                    checkpoint_every=args.checkpoint_every,
                    env_file=args.env_file,
                )
            else:
                parser.error(
                    "evaluate requires --matrix or both --analyzer-model and --verifier-model"
                )
        elif args.command == "models" and args.models_command == "list":
            commands.models_list(
                settings,
                show_all=args.all,
                refresh=args.refresh,
                json_output=args.json_output,
            )
        elif args.command == "models" and args.models_command == "check":
            commands.models_check(settings, refresh=args.refresh)
        elif args.command == "models" and args.models_command == "smoke-test":
            commands.models_smoke_test(
                settings,
                analyzer_model=args.analyzer_model,
                verifier_model=args.verifier_model,
                output_dir=args.output_dir,
            )
        elif (
            args.command == "evaluation"
            and args.evaluation_command == "datasets"
            and args.datasets_command == "build"
        ):
            commands.evaluation_datasets_build(
                settings, source=args.source, output_dir=args.output_dir
            )
        elif args.command == "evaluation" and args.evaluation_command == "calibrate":
            commands.evaluation_calibrate(
                settings,
                predictions=args.predictions,
                method=args.method,
                split_manifest=args.split_manifest,
                output=args.output,
            )
        elif args.command == "request" and args.request_command == "show":
            commands.request_show(settings, args.reference)
        elif args.command == "conversation" and args.conversation_command == "show":
            commands.conversation_show(settings, args.conversation_id)
        elif args.command == "operation" and args.operation_command == "show":
            commands.operation_show(settings, args.operation_id)
        elif args.command == "audit" and args.audit_command == "list":
            commands.audit_list(settings, limit=args.limit)
        elif args.command == "audit" and args.audit_command == "show":
            commands.audit_show(settings, args.email_id)
        elif args.command == "failures" and args.failures_command == "list":
            commands.failures_list(settings)
        elif args.command == "quarantine" and args.quarantine_command == "list":
            commands.quarantine_list(settings)
        elif args.command == "quarantine" and args.quarantine_command == "retry":
            commands.quarantine_retry(settings, args.email_id)
        else:
            parser.error("unsupported command")
    except InferenceError as exc:
        print(f"error [{exc.category.value}]: {exc}", file=sys.stderr)
        return 2
    except (ValueError, LookupError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
