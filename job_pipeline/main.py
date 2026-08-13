"""
CLI entry point.

Usage:
  python main.py apply --url "https://jobs.company.com/posting"
  python main.py apply --paste                     (opens $EDITOR-free prompt to paste JD text)
  python main.py apply --file jd.txt
  python main.py record-outcome <app_id> <status>   status: applied|no_response|interview|rejected|offer
  python main.py learn
  python main.py list                               list logged applications and their status
"""
import argparse
import sys

import orchestrator


def cmd_apply(args):
    if args.url:
        orchestrator.run_pipeline(args.url, is_url=True,
                                   job_title_hint=args.title or "", company_hint=args.company or "")
    elif args.file:
        with open(args.file) as f:
            text = f.read()
        orchestrator.run_pipeline(text, is_url=False,
                                   job_title_hint=args.title or "", company_hint=args.company or "")
    elif args.paste:
        print("Paste the job description text, then press Ctrl-D (Ctrl-Z on Windows) when done:")
        text = sys.stdin.read()
        orchestrator.run_pipeline(text, is_url=False,
                                   job_title_hint=args.title or "", company_hint=args.company or "")
    else:
        print("Provide one of --url, --file, or --paste")


def cmd_record_outcome(args):
    orchestrator.record_outcome(args.app_id, args.status)


def cmd_learn(args):
    orchestrator.learn()


def cmd_list(args):
    apps = orchestrator.load_applications()
    if not apps:
        print("No applications logged yet.")
        return
    for a in apps:
        print(f"{a['id']}  {a['created_at'][:10]}  {a['company']:<25} {a['job_title']:<30} "
              f"match={a.get('match_score')}  outcome={a.get('outcome')}")


def main():
    parser = argparse.ArgumentParser(description="Job application pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_apply = sub.add_parser("apply", help="Run the pipeline for a job posting")
    p_apply.add_argument("--url", help="Job posting URL to fetch")
    p_apply.add_argument("--file", help="Path to a text file containing the JD")
    p_apply.add_argument("--paste", action="store_true", help="Paste JD text via stdin")
    p_apply.add_argument("--title", help="Optional job title hint")
    p_apply.add_argument("--company", help="Optional company name hint")
    p_apply.set_defaults(func=cmd_apply)

    p_outcome = sub.add_parser("record-outcome", help="Record what happened with an application")
    p_outcome.add_argument("app_id")
    p_outcome.add_argument("status", choices=["applied", "no_response", "interview", "rejected", "offer"])
    p_outcome.set_defaults(func=cmd_record_outcome)

    p_learn = sub.add_parser("learn", help="Analyze outcomes and update agent learnings")
    p_learn.set_defaults(func=cmd_learn)

    p_list = sub.add_parser("list", help="List logged applications")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
