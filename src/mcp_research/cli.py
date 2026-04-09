"""CLI entrypoint for mcp-research — run tools directly or start MCP server."""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(prog="mcp-research", description="Web research MCP server and CLI tools")
    sub = parser.add_subparsers(dest="command")

    # serve (default)
    sub.add_parser("serve", help="Run MCP stdio server (default)")

    # search
    p_search = sub.add_parser("search", help="Search the web")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--max-results", type=int, default=5)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch a URL and convert to markdown")
    p_fetch.add_argument("url", help="URL to fetch")
    p_fetch.add_argument("--summarize", action="store_true")

    # youtube
    p_yt = sub.add_parser("youtube", help="Extract essence from a YouTube video")
    p_yt.add_argument("url", help="YouTube URL")
    p_yt.add_argument("--mode", choices=["quick", "standard", "deep"], default="standard")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Extract text from files in a directory")
    p_ingest.add_argument("path", help="Directory or file path")
    p_ingest.add_argument("--summarize", action="store_true")
    p_ingest.add_argument("--max-files", type=int, default=200)
    p_ingest.add_argument("--types", default="", help="Comma-separated type filter (text,pdf,audio,video,image,office)")

    # academic
    p_acad = sub.add_parser("academic", help="Resolve DOI, ArXiv ID, or PubMed ID")
    p_acad.add_argument("identifier", help="DOI, ArXiv ID, PubMed ID, or publisher URL")
    p_acad.add_argument("--no-fulltext", action="store_true", help="Skip full text fetch")

    # tweet
    p_tweet = sub.add_parser("tweet", help="Extract tweet from X.com/Twitter")
    p_tweet.add_argument("url", help="Tweet URL")
    p_tweet.add_argument("--thread", action="store_true", help="Fetch full conversation thread")

    # vault
    sub.add_parser("vault", help="Show vault status and loaded profiles")

    args = parser.parse_args()

    if args.command is None or args.command == "serve":
        import asyncio
        from .server import server
        asyncio.run(server.run_stdio_async())
        return

    def _progress(d):
        stage = d.get("stage", "")
        pct = d.get("progress", 0)
        print(f"  [{pct:.0%}] {stage}", file=sys.stderr)

    if args.command == "search":
        from .search import web_search
        results, hint = web_search(args.query, args.max_results)
        if hint:
            print(f"Note: {hint}\n", file=sys.stderr)
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}\n   {r['url']}\n   {r.get('snippet', '')}\n")

    elif args.command == "fetch":
        from .fetch import fetch_url
        result = fetch_url(args.url, summarize=args.summarize)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"# {result.get('title', '')}\n")
        if result.get("summary"):
            print(f"## Summary\n{result['summary']}\n")
        print(result.get("content_md", ""))

    elif args.command == "youtube":
        from .youtube import youtube_essence
        result = youtube_essence(args.url, mode=args.mode, on_progress=_progress)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"# {result.get('title', '')}")
        print(f"Duration: {result.get('duration', '')} | Mode: {args.mode}\n")
        if result.get("summary"):
            print(f"## Summary\n{result['summary']}\n")
        if result.get("key_points"):
            print("## Key Points")
            for kp in result["key_points"]:
                print(f"- {kp}")
            print()
        if result.get("chapters"):
            print("## Chapters")
            for ch in result["chapters"]:
                print(f"- [{ch['time']}] {ch['title']}")
            print()
        if result.get("quotes"):
            print("## Quotes")
            for q in result["quotes"]:
                print(f"> {q}")

    elif args.command == "ingest":
        from .ingest import deep_ingest
        result = deep_ingest(
            args.path, include_types=args.types,
            max_files=args.max_files, summarize=args.summarize,
            on_progress=_progress,
        )
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Processed: {result['files_processed']} | Skipped: {result['files_skipped']}")
        if result.get("by_type"):
            print("\nBy type:")
            for ftype, counts in result["by_type"].items():
                print(f"  {ftype}: {counts['ok']} ok, {counts.get('skip', 0)} skipped")
        if result.get("summary"):
            print(f"\n## Summary\n{result['summary']}")
        if result.get("errors"):
            print(f"\nErrors ({len(result['errors'])}):")
            for err in result["errors"][:10]:
                print(f"  - {err}")

    elif args.command == "academic":
        from .academic import academic_lookup
        result = academic_lookup(args.identifier, fetch_fulltext=not args.no_fulltext)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            hint = result.get("hint", "")
            if hint:
                print(hint, file=sys.stderr)
            sys.exit(1)
        print(f"# {result.get('title', 'Unknown')}")
        if result.get("authors"):
            print(f"Authors: {', '.join(result['authors'][:10])}")
        if result.get("journal"):
            print(f"Journal: {result['journal']}")
        if result.get("year"):
            print(f"Year: {result['year']}")
        if result.get("access_method"):
            print(f"Access: {result['access_method']}")
        if result.get("abstract"):
            print(f"\n## Abstract\n{result['abstract']}")
        if result.get("full_text_md"):
            print(f"\n## Full Text\n{result['full_text_md'][:2000]}...")

    elif args.command == "tweet":
        from .twitter import extract_tweet, extract_thread
        if args.thread:
            result = extract_thread(args.url)
        else:
            result = extract_tweet(args.url)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            for h in result.get("hints", []):
                print(f"  - {h}", file=sys.stderr)
            sys.exit(1)
        if "thread" in result:
            for i, t in enumerate(result["thread"], 1):
                print(f"[{i}] {t.get('author', '')}: {t.get('text', '')}\n")
        else:
            print(f"@{result.get('author_id') or result.get('author', '')}")
            print(result.get("text", ""))
            metrics = result.get("metrics", {})
            parts = [f"{k}: {v}" for k, v in metrics.items() if v is not None]
            if parts:
                print(f"\n{' | '.join(parts)}")

    elif args.command == "vault":
        from .vault import get_vault
        from . import config
        profiles = get_vault()
        if not profiles:
            print(f"No vault profiles loaded.")
            print(f"Vault file: {config.VAULT_FILE}")
            print(f"Exists: {config.VAULT_FILE.exists()}")
        else:
            print(f"Vault: {len(profiles)} profiles from {config.VAULT_FILE}\n")
            for name, p in profiles.items():
                auth_type = p.auth.type if p.auth else "-"
                ezproxy = p.ezproxy.mode if p.ezproxy else "-"
                print(f"  {name:20s}  match={p.match:30s}  auth={auth_type:12s}  ezproxy={ezproxy}")


if __name__ == "__main__":
    main()
