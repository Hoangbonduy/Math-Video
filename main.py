"""Local project workflow CLI. AI generation requires explicit --ai."""

import argparse
import json

from src.shared._workflow_store import WorkflowError
from src.shared.env import load_local_env
from src.shared.review import record_review
from src.shared.workflow import STAGES, create_project, get_status, run_workflow, submit_artifact


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorkflowError("INVALID_INPUT", message)


def main(argv=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a new project")
    create.add_argument("--topic", required=True)
    create.add_argument("--brief", help="Optional JSON brief file")
    commands.add_parser("status", help="Show saved progress and pending reviews")
    run = commands.add_parser("run", help="Continue without passing any approval gate")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true", help="Use fixed local test data only")
    modes.add_argument("--ai", action="store_true", help="Research/write scripts using the configured AI backend")
    run.add_argument("--retry-uncertain", help="Explicitly retry the selected uncertain call_id within its remaining budget")
    run.add_argument("--scene-id", help="Process only this scene in AI design/clip stages")
    run.add_argument("--scene-trial", action="store_true", help="Try one scene design once, without automatic repairs or approval")
    serve = commands.add_parser("serve-review", help="Open local browser review server")
    serve.add_argument("--port", type=int, default=8765)
    avrun = commands.add_parser("av", help="Start/continue C7 speech, AV scenes or assembly")
    avrun.add_argument("--scene-id")
    avrun.add_argument("--retry-uncertain")
    avconfig = commands.add_parser("audio-config", help="Configure Microsoft Edge or FPT speech")
    avconfig.add_argument("--provider", choices=("edge","fpt"), default="edge")
    avconfig.add_argument("--voice", choices=("vi-VN-HoaiMyNeural","vi-VN-NamMinhNeural","leminh","giahuy"))
    avconfig.add_argument("--speed", type=int, default=0)
    avconfig.add_argument("--vnd-per-million-chars", type=float)
    avconfig.add_argument("--vnd-per-usd", type=float)
    sample = commands.add_parser("voice-sample", help="Synthesize one approved narration phrase")
    sample.add_argument("--retry-uncertain")
    preview = commands.add_parser("design-preview", help="Render silent motion sketch")
    preview.add_argument("--scene-id", required=True)
    preview.add_argument("--version", required=True)
    preview.add_argument("--sha256", required=True)
    submit = commands.add_parser("submit", help="Register an immutable artifact version")
    submit.add_argument("--file", required=True, help="Absolute path, or relative to project directory")
    submit.add_argument("--scene-ids", nargs="+", help="Ordered scene IDs; required for a script")
    review = commands.add_parser("review", help="Approve or request changes to one exact version")
    review.add_argument("--decision", choices=("approve", "request_changes"), required=True)
    review.add_argument("--feedback")
    review.add_argument("--read-seconds", type=float, help="Measured reading time for this AI script version")
    for command in (submit, review):
        command.add_argument("--stage", choices=STAGES[:-1], required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--scene-id")
    for command in commands.choices.values():
        command.add_argument("--project-dir", required=True)
        command.add_argument("--summary", action="store_true", help="Print a compact status instead of full JSON")
    try:
        args = parser.parse_args(argv)
        load_local_env()
        if args.command == "serve-review":
            from src.shared.review_server import serve_review
            serve_review(args.project_dir, args.port)
            return 0
        if args.command == "av":
            from src.preview.av_workflow import generate
            result = generate(args.project_dir, scene_id=args.scene_id, retry_uncertain=args.retry_uncertain)
        elif args.command in ("audio-config", "voice-sample", "design-preview"):
            if args.command == "audio-config":
                from src.preview.av_workflow import configure
                configure(args.project_dir, dict(provider=args.provider,voice=args.voice or ('vi-VN-HoaiMyNeural' if args.provider=='edge' else 'leminh'),speed=args.speed,vnd_per_million_chars=args.vnd_per_million_chars,vnd_per_usd=args.vnd_per_usd))
            elif args.command == "voice-sample":
                from src.preview.av_workflow import sample
                sample(args.project_dir,args.retry_uncertain)
            else:
                from src.scene_design.design_preview import generate
                generate(args.project_dir,args.scene_id,args.version,args.sha256)
            result = get_status(args.project_dir)
        elif args.command == "create":
            brief = None
            if args.brief:
                from pathlib import Path
                try:
                    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise WorkflowError("INVALID_BRIEF", str(exc)) from None
            result = create_project(args.project_dir, args.topic, brief=brief)
        elif args.command == "status":
            result = get_status(args.project_dir)
        elif args.command == "run":
            result = run_workflow(args.project_dir, mock=args.mock, ai=args.ai, retry_uncertain=args.retry_uncertain, scene_id=args.scene_id, scene_trial=args.scene_trial)
        elif args.command == "submit":
            result = submit_artifact(args.project_dir, args.stage, args.version, args.file,
                                     scene_id=args.scene_id, scene_ids=args.scene_ids)
        else:
            result = record_review(args.project_dir, args.stage, args.version, args.decision,
                                   scene_id=args.scene_id, feedback=args.feedback, read_seconds=args.read_seconds)
        response = dict(status="success", project=result)
        code = 0
    except WorkflowError as exc:
        response = dict(status="failed", error=dict(code=exc.code, message=exc.message))
        code = 1
    except (TypeError, ValueError) as exc:
        response = dict(status="failed", error=dict(code="INVALID_INPUT", message=str(exc)))
        code = 1
    if "args" in locals() and args.summary and response["status"] == "success":
        state = response["project"]
        print(f"{state['topic']} — {state['stage']} / {state['status']}")
        for item in state["pending_reviews"]:
            print(f"{item.get('scene_id') or item['stage']} {item['version']}: chờ duyệt — {item.get('preview_path') or item['path']}")
    else:
        print(json.dumps(response, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
