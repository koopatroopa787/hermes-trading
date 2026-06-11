#!/usr/bin/env python3
"""
News Fetcher — pulls recent news headlines for tracked stocks + market.
Uses Brave Search API (free tier) for news.
Outputs JSON to stdout.

Usage:
  python3 news_fetcher.py --symbols RGTI,QBTS,IONQ
  python3 news_fetcher.py --symbols RGTI,QBTS,IONQ --freshness pd
  BRAVE_API_KEY=<key> python3 news_fetcher.py --symbols AAPL
"""

import os, sys, json, subprocess, re, html
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

ET = ZoneInfo("America/New_York")

def brave_news_search(query, freshness="pd", count=10, api_key=None):
    """Search Brave News API and return articles."""
    if not api_key:
        api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return [], "No BRAVE_API_KEY found in env"

    url = f"https://api.search.brave.com/res/v1/news/search?q={quote(query)}&freshness={freshness}&count={count}"

    try:
        result = subprocess.run(
            ["curl", "-s", "-H", f"x-subscription-token: {api_key}", url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return [], f"curl error: {result.stderr[:200]}"

        import json as json_mod
        data = json_mod.loads(result.stdout)

        articles = []
        for item in data.get("results", [])[:count]:
            title = html.unescape(item.get("title", ""))
            desc = html.unescape(item.get("description", ""))
            articles.append({
                "title": re.sub(r"<[^>]+>", "", title).strip(),
                "link": item.get("url", ""),
                "snippet": re.sub(r"<[^>]+>", "", desc).strip()[:400],
                "source": item.get("source", ""),
                "published": item.get("age", ""),
                "is_breaking": item.get("breaking", False),
            })
        return articles, None
    except json.JSONDecodeError as e:
        return [], f"JSON parse error: {e}"
    except subprocess.TimeoutExpired:
        return [], "Timeout"
    except Exception as e:
        return [], str(e)


def fetch_per_symbol(symbols, freshness="pd"):
    """Fetch news for each symbol individually."""
    all_news = []
    for sym in symbols:
        articles, err = brave_news_search(f"{sym} stock", freshness=freshness, count=8)
        for a in articles:
            a["symbol"] = sym
            a["query"] = f"{sym} stock"
        all_news.extend(articles)

        # Also check sector news for major symbols
        if sym in ("SOXL", "SMCI", "ARM", "COIN"):
            sector_map = {"SOXL": "semiconductor stocks", "SMCI": "AI server stocks",
                          "ARM": "chip stocks", "COIN": "crypto stocks"}
            sector = sector_map.get(sym, f"{sym} news")
            articles2, _ = brave_news_search(sector, freshness=freshness, count=5)
            for a in articles2:
                a["symbol"] = sym
                a["query"] = sector
                a["sector_news"] = True
            all_news.extend(articles2)

    return all_news


def fetch_market_news(freshness="pd"):
    """General market news."""
    queries = [
        ("stock market today", "MARKET"),
        ("S&P 500 outlook", "MARKET"),
        ("Federal Reserve", "MACRO"),
        ("US economy", "MACRO"),
    ]
    all_news = []
    for q, tag in queries:
        articles, err = brave_news_search(q, freshness=freshness, count=5)
        for a in articles:
            a["symbol"] = tag
            a["query"] = q
        all_news.extend(articles)
    return all_news


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch stock news via Brave API")
    parser.add_argument("--symbols", required=True, help="Comma-separated stock symbols")
    parser.add_argument("--freshness", default="pd", choices=["pd", "pw", "pm"],
                        help="pd=past day, pw=past week, pm=past month")
    parser.add_argument("--market", action="store_true", default=True,
                        help="Include general market news")
    parser.add_argument("--output", help="Output file path (default: stdout)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    all_news = fetch_per_symbol(symbols, args.freshness)
    if args.market:
        all_news.extend(fetch_market_news(args.freshness))

    # Deduplicate by title
    seen = set()
    deduped = []
    for item in all_news:
        key = item["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)

    result = {
        "fetched_at": datetime.now(ET).isoformat(),
        "fetch_ts": datetime.now().timestamp(),
        "symbols": symbols,
        "freshness": args.freshness,
        "article_count": len(deduped),
        "articles": deduped,
    }

    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Saved {len(deduped)} articles to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
