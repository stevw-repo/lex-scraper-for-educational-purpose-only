"""Command-line interface.

    python -m lex.cli login
    python -m lex.cli crawl-toc "<seed url>" --title "Agency" --volume "Volume 1 (2022)"
    python -m lex.cli extract --seed "<seed url>" --title "Agency"
    python -m lex.cli extract --seeds seeds.txt
    python -m lex.cli status
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import config
from .core.kernel import create_default_kernel
from .core.manifest import Manifest
from .plugins.writer import slug


def read_seeds(path: str | Path) -> list[tuple[str, str, str | None]]:
    """seeds.txt lines: ``Title | URL`` or ``Title | URL | Volume`` (or just URL).
    Blank lines and lines starting with '#' are ignored."""
    seeds: list[tuple[str, str, str | None]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 1:
            seeds.append(("Untitled", parts[0], None))
        else:
            title, url = parts[0], parts[1]
            volume = parts[2] if len(parts) > 2 else None
            seeds.append((title, url, volume))
    return seeds


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lex",
        description="Extract Halsbury's Laws of England from Lexis+ HK into Markdown.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Open the browser and wait for interactive login")

    pc = sub.add_parser("crawl-toc", help="Harvest a title's TOC and list sections")
    pc.add_argument("seed", help="The title's TOC/landing URL")
    pc.add_argument("--title", required=True)
    pc.add_argument("--volume", default=None)

    pe = sub.add_parser("extract", help="Crawl + fetch + parse + write the JSONL corpus")
    pe.add_argument("--seed", help="Root TOC URL; omit --title to extract the whole work")
    pe.add_argument("--title", help='Filter to one title by name, e.g. "Agency"')
    pe.add_argument("--volume", default=None)
    pe.add_argument("--seeds", help="Path to a seeds.txt (defaults to ./seeds.txt)")
    pe.add_argument("--headless", action="store_true",
                    help="Run Chromium headless (only after a profile is logged in)")

    pr = sub.add_parser("retry", help="Re-attempt only the sections that previously failed")
    pr.add_argument("--seed", help="Root TOC URL (same as extract)")
    pr.add_argument("--title")
    pr.add_argument("--volume", default=None)
    pr.add_argument("--seeds")
    pr.add_argument("--headless", action="store_true")

    sub.add_parser("status", help="Show manifest counts (done / pending / failed)")
    sub.add_parser("build", help="Rebuild the JSONL corpus from the manifest (no network)")
    psv = sub.add_parser("serve", help="Launch the local web UI in your browser")
    psv.add_argument("--host", default="127.0.0.1")
    psv.add_argument("--port", type=int, default=8765)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "status":
        print(Manifest().counts())
        return 0

    if args.cmd == "build":
        from .plugins.writer import Output
        print(Output().compile_jsonl(Manifest().done_records()))
        return 0

    if args.cmd == "serve":
        from .web.server import serve
        serve(host=args.host, port=args.port)
        return 0

    from .core.session import LexisSession

    session = LexisSession(headless=getattr(args, "headless", False))
    kernel = create_default_kernel(session=session)
    try:
        if args.cmd == "login":
            session.wait_for_login()
            print("Profile saved at", config.PROFILE_DIR)
        elif args.cmd == "crawl-toc":
            kernel["auth"].ensure_logged_in()   # opens the browser / waits for login if needed
            dump = config.STATE_DIR / f"toc_summary-{slug(args.title)}.txt"
            sections = kernel["toc"].harvest(
                args.seed, args.title, args.volume, dump_path=dump
            )
            for s in sections:
                crumb = " / ".join(s.hierarchy)
                print(f"{s.number:>6}  {s.urn}  [{crumb}] :: {s.heading}")
            titles = getattr(kernel["toc"], "last_titles", []) or []
            print(f"\n{len(sections)} sections across {len(titles)} title(s)  "
                  f"(summary -> {dump})")
            if not sections:
                print("\n[!] No sections harvested — check the seed TOC URL and that "
                      "login succeeded. Send me the terminal output.")
        elif args.cmd in ("extract", "retry"):
            only_failed = args.cmd == "retry"
            if args.seed:
                kernel["runner"].run_title(args.seed, args.title, args.volume,
                                           only_failed=only_failed)
            else:
                seeds_path = args.seeds or config.SEEDS_FILE
                if not Path(seeds_path).exists():
                    raise SystemExit(f"no --seed and no seeds file at {seeds_path}")
                kernel["runner"].run_seeds(read_seeds(seeds_path), only_failed=only_failed)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
