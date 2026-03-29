"""Web search using DuckDuckGo — free, no API key, no limits."""

import logging

from ddgs import DDGS

logger = logging.getLogger(__name__)


def search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web and return results."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        logger.warning(f"Web search failed for query '{query[:80]}': {e}")
        return []


def news(query: str, max_results: int = 5) -> list[dict]:
    """Search recent news."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return results
    except Exception as e:
        logger.warning(f"News search failed for query '{query[:80]}': {e}")
        return []


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Web search via DuckDuckGo")
    parser.add_argument("query", help="Search query")
    parser.add_argument("-n", "--max-results", type=int, default=5)
    parser.add_argument("--news", action="store_true", help="Search news instead of web")
    args = parser.parse_args()

    fn = news if args.news else search
    results = fn(args.query, max_results=args.max_results)

    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("href", r.get("url", ""))
        body = r.get("body", r.get("description", ""))
        print(f"{i}. {title}")
        print(f"   {url}")
        print(f"   {body}")
        print()
