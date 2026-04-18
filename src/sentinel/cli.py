"""Command-line interface for operating Sentinel services and workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .db import connect, init_db
from .env import load_dotenv
from .server import run_server
from .service import SentinelService


def _build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser and all supported subcommands.

    Returns:
        Configured ``ArgumentParser`` instance.
    """
    parser = argparse.ArgumentParser(prog="sentinel", description="Sentinel de moderacao conversacional.")
    parser.add_argument("--config", default=None, help="Caminho para sentinel.toml")
    parser.add_argument("--env-file", default=".env", help="Arquivo .env para carregar variaveis de ambiente")
    parser.add_argument("--no-env-file", action="store_true", help="Nao carregar variaveis a partir de .env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_cmd = subparsers.add_parser("init-db", help="Inicializa o banco SQLite")
    init_cmd.add_argument("--db", default=None)

    serve_cmd = subparsers.add_parser("serve", help="Sobe a API HTTP do Sentinel")
    serve_cmd.add_argument("--db", default=None)
    serve_cmd.add_argument("--host", default=None)
    serve_cmd.add_argument("--port", type=int, default=None)

    ingest_cmd = subparsers.add_parser("ingest", help="Ingere um evento JSON")
    ingest_cmd.add_argument("--db", default=None)
    ingest_cmd.add_argument("--event-file", required=True)

    report_cmd = subparsers.add_parser("report-daily", help="Gera relatorio diario por grupo")
    report_cmd.add_argument("--db", default=None)
    report_cmd.add_argument("--group-id", required=True)
    report_cmd.add_argument("--date", required=True)

    feedback_cmd = subparsers.add_parser("feedback", help="Registra feedback do moderador")
    feedback_cmd.add_argument("--db", default=None)
    feedback_cmd.add_argument("--incident-id", required=True)
    feedback_cmd.add_argument("--feedback-type", required=True)
    feedback_cmd.add_argument("--note", default=None)
    feedback_cmd.add_argument("--reviewer-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the Sentinel CLI entrypoint.

    Args:
        argv: Optional argument vector. When ``None``, uses process arguments.

    Returns:
        Process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.no_env_file:
        try:
            load_dotenv(args.env_file)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    config = load_config(args.config)
    if getattr(args, "db", None):
        config.db_path = args.db

    if args.command == "init-db":
        connection = connect(config.db_path)
        init_db(connection)
        connection.close()
        print(config.db_path)
        return 0
    if args.command == "serve":
        if args.host:
            config.server.host = args.host
        if args.port:
            config.server.port = args.port
        run_server(config)
        return 0

    service = SentinelService(config)
    try:
        try:
            if args.command == "ingest":
                event = json.loads(Path(args.event_file).read_text(encoding="utf-8"))
                result = service.ingest_message(event)
                print(json.dumps(result, ensure_ascii=True))
                return 0
            if args.command == "report-daily":
                markdown, payload = service.build_daily_report(args.group_id, args.date)
                print(markdown)
                print(json.dumps(payload, ensure_ascii=True))
                return 0
            if args.command == "feedback":
                feedback_id = service.record_feedback(
                    args.incident_id,
                    args.feedback_type,
                    note=args.note,
                    reviewer_id=args.reviewer_id,
                )
                print(feedback_id)
                return 0
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    finally:
        service.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
