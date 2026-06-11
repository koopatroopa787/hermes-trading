#!/usr/bin/env python3
"""
Enhanced Market Sentiment Analyzer — fetches news, scores sentiment per symbol,
and generates sentiment_override.json for both bots.

Key features:
- Fetches news for ALL tracked symbols via Brave Search API
- Scores each article and symbol 0-1 (bullish/bearish)
- Generates per-symbol position multipliers and recommendations
- Flags event-driven catalysts (earnings, product launches, conferences)
- Outputs machine-readable sentiment_override.json

Usage:
  python3 analyze_sentiment.py --output-bot hermes|wq|both
  python3 analyze_sentiment.py --all  (updates both bots)
"""

import os, sys, json, re, html, subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
from pathlib import Path

ET = ZoneInfo("America/New_York")
BASE = Path.home()

# ── Keyword-based sentiment scoring ──────────────────────────────────────
# These keyword clusters determine article sentiment direction and intensity
BULLISH_KEYWORDS = {
    # Earnings & Financial
    "beat earnings": 0.4, "earnings beat": 0.4, "record revenue": 0.35,
    "raised guidance": 0.35, "upgraded to": 0.3, "outperform": 0.3,
    "strong quarter": 0.3, "profit surge": 0.35, "dividend increase": 0.25,
    "buyback": 0.25, "positive outlook": 0.3,
    
    # Product & Innovation
    "product launch": 0.3, "new product": 0.25, "breakthrough": 0.3,
    "conference": 0.2, "wwdc": 0.4, "keynote": 0.3, "unveils": 0.3,
    "ai push": 0.3, "ai strategy": 0.25, "expansion": 0.2,
    
    # Market Dynamics
    "bullish": 0.35, "buying opportunity": 0.3, "institutional buying": 0.3,
    "insider buying": 0.3, "upgrade": 0.25, "accumulate": 0.25,
    
    # Macro Positive
    "rate cut": 0.25, "fed pivot": 0.3, "stimulus": 0.2,
    "rally": 0.2, "rebound": 0.2, "recovery": 0.2,
    "all-time high": 0.2,
}

BEARISH_KEYWORDS = {
    # Earnings & Financial
    "missed earnings": -0.4, "earnings miss": -0.4, "revenue miss": -0.35,
    "lowered guidance": -0.35, "downgraded": -0.3, "underperform": -0.3,
    "weak quarter": -0.3, "profit warning": -0.4, "loss": -0.2,
    "debt": -0.15, "bankruptcy": -0.5, "layoff": -0.3,
    
    # Product & Competition
    "recall": -0.35, "delay": -0.2, "setback": -0.25,
    "investigation": -0.3, "lawsuit": -0.3, "regulatory": -0.25,
    "antitrust": -0.35, "ban": -0.35,
    
    # Market Dynamics
    "bearish": -0.35, "sell-off": -0.35, "selloff": -0.35,
    "crash": -0.4, "plunge": -0.4, "tumble": -0.35,
    "insider selling": -0.3, "short seller": -0.3,
    "downgrade": -0.3, "risk-off": -0.25,
    
    # Macro Negative
    "recession": -0.3, "inflation": -0.2, "tariff": -0.2,
    "geopolitical": -0.2, "war": -0.3, "sanctions": -0.25,
    "crash": -0.4, "bear market": -0.35,
    "volatility": -0.15, "uncertainty": -0.15,
}

# Event catalysts that boost position sizing regardless of sentiment
EVENT_CATALYSTS = {
    "wwdc": {"boost": 0.3, "label": "WWDC"},       # Apple WWDC
    "earnings": {"boost": 0.2, "label": "Earnings"},
    "product launch": {"boost": 0.25, "label": "Product Launch"},
    "conference": {"boost": 0.15, "label": "Conference"},
    "investor day": {"boost": 0.2, "label": "Investor Day"},
    "fda approval": {"boost": 0.4, "label": "FDA Approval"},
    "merger": {"boost": 0.2, "label": "Merger"},
    "acquisition": {"boost": 0.2, "label": "Acquisition"},
    "partnership": {"boost": 0.15, "label": "Partnership"},
    "analyst upgrade": {"boost": 0.2, "label": "Analyst Upgrade"},
    "stock split": {"boost": 0.2, "label": "Stock Split"},
}


def score_article(title, snippet=""):
    """Score a single article on -1 to +1 scale."""
    text = f"{title} {snippet}".lower()
    score = 0.0
    signals_found = []
    
    for keyword, impact in BULLISH_KEYWORDS.items():
        if keyword in text:
            score += impact
            signals_found.append(("bullish", keyword, impact))
    
    for keyword, impact in BEARISH_KEYWORDS.items():
        if keyword in text:
            score += impact
            signals_found.append(("bearish", keyword, impact))
    
    # Detect event catalysts
    catalysts = []
    for keyword, info in EVENT_CATALYSTS.items():
        if keyword in text:
            catalysts.append(info["label"])
    
    # Clamp to [-1, 1]
    score = max(-1.0, min(1.0, score))
    
    return round(score, 3), signals_found[:5], catalysts


def score_article_bull_bear(title, snippet=""):
    """Score an article with separate bull and bear scores (TradingAgents-inspired)."""
    text = f"{title} {snippet}".lower()
    bull_score = 0.0
    bear_score = 0.0
    catalysts = []
    
    for keyword, impact in BULLISH_KEYWORDS.items():
        if keyword in text:
            bull_score += impact
    for keyword, impact in BEARISH_KEYWORDS.items():
        if keyword in text:
            bear_score += abs(impact)  # bear_score is always positive
    
    for keyword, info in EVENT_CATALYSTS.items():
        if keyword in text:
            catalysts.append(info["label"])
    
    bull_score = min(1.0, bull_score)
    bear_score = min(1.0, bear_score)
    net_score = round(bull_score - bear_score, 3)
    bull_score = round(bull_score, 3)
    bear_score = round(bear_score, 3)
    
    return bull_score, bear_score, net_score, catalysts


def score_symbol(articles):
    """Aggregate article scores with bull/bear debate scores (TradingAgents-inspired)."""
    if not articles:
        return 0.0, 0.0, [], [], 0.0, 0.0
    
    bull_scores = []
    bear_scores = []
    net_scores = []
    all_catalysts = set()
    top_picks = []
    
    for a in articles:
        bull, bear, net, catalysts = score_article_bull_bear(
            a.get("title", ""), 
            a.get("snippet", "")
        )
        bull_scores.append(bull)
        bear_scores.append(bear)
        net_scores.append(net)
        all_catalysts.update(catalysts)
        top_picks.append({
            "title": a.get("title", ""),
            "bull_score": bull,
            "bear_score": bear,
            "net_score": net,
            "catalysts": catalysts,
            "source": a.get("source", ""),
        })
    
    avg_bull = sum(bull_scores) / len(bull_scores)
    avg_bear = sum(bear_scores) / len(bear_scores)
    avg_net = sum(net_scores) / len(net_scores)
    
    # Confidence: high when there's a clear winner (bull or bear)
    if avg_bull > 0.3 or avg_bear > 0.3:
        confidence = min(1.0, max(avg_bull, avg_bear) * 2)
    else:
        confidence = min(0.5, (avg_bull + avg_bear) * 1.5)
    
    # Sort top picks by highest individual score
    top_picks.sort(key=lambda x: max(x["bull_score"], x["bear_score"]), reverse=True)
    
    return round(avg_net, 3), round(confidence, 2), top_picks[:5], list(all_catalysts), round(avg_bull, 3), round(avg_bear, 3)


def determine_symbol_multiplier(sentiment_score, confidence, catalysts):
    """Determine position multiplier and recommendation for a symbol."""
    base = 1.0
    recommendation = "normal"
    
    # Sentiment-driven adjustments
    if sentiment_score > 0.3 and confidence > 0.3:
        base = 1.25
        recommendation = "boost"
    elif sentiment_score > 0.15 and confidence > 0.2:
        base = 1.1
        recommendation = "slight_boost"
    elif sentiment_score < -0.3 and confidence > 0.3:
        base = 0.0
        recommendation = "skip"
    elif sentiment_score < -0.15 and confidence > 0.2:
        base = 0.5
        recommendation = "reduce"
    elif confidence < 0.15:
        base = 1.0
        recommendation = "normal_low_conf"
    
    # Event catalyst boost (can override negative sentiment partially)
    if catalysts:
        catalyst_boost = min(0.3, 0.1 * len(catalysts))
        # If sentiment is negative but catalyst exists, don't skip completely
        if base == 0.0 and catalyst_boost >= 0.2:
            base = 0.5
            recommendation = "reduce_with_catalyst"
        elif base < 1.0 and recommendation != "skip":
            base = min(1.0, base + catalyst_boost * 0.5)
        elif base >= 1.0:
            base = min(1.5, base + catalyst_boost)
            if recommendation == "normal" or recommendation == "normal_low_conf":
                recommendation = "catalyst_boost"
    
    return round(base, 2), recommendation


def brave_news_search(query, freshness="pd", count=10):
    """Search Brave News API and return articles."""
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return [], "No BRAVE_API_KEY found"
    
    url = f"https://api.search.brave.com/res/v1/news/search?q={quote(query)}&freshness={freshness}&count={count}"
    
    try:
        result = subprocess.run(
            ["curl", "-s", "-H", f"x-subscription-token: {api_key}", url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return [], f"curl error: {result.stderr[:200]}"
        
        data = json.loads(result.stdout)
        articles = []
        for item in data.get("results", [])[:count]:
            title = html.unescape(item.get("title", ""))
            desc = html.unescape(item.get("description", ""))
            articles.append({
                "title": re.sub(r"<[^>]+>", "", title).strip(),
                "snippet": re.sub(r"<[^>]+>", "", desc).strip()[:400],
                "source": item.get("source", ""),
                "published": item.get("age", ""),
                "link": item.get("url", ""),
                "breaking": item.get("breaking", False),
            })
        return articles, None
    except Exception as e:
        return [], str(e)


def fetch_symbol_news(symbol, freshness="pd"):
    """Fetch news for a single symbol with market/sector context."""
    articles, err = brave_news_search(f"{symbol} stock", freshness=freshness, count=8)
    if err:
        return []
    
    # Also fetch sector/company news for better context
    sector_map = {
        "AAPL": "Apple technology news", "MSFT": "Microsoft technology news",
        "NVDA": "Nvidia AI chip news", "AMD": "AMD semiconductor news",
        "GOOGL": "Google Alphabet news", "META": "Meta technology news",
        "AMZN": "Amazon technology news", "TSLA": "Tesla electric vehicle news",
        "SOXL": "semiconductor stocks", "SMCI": "AI server stocks",
        "ARM": "chip design stocks", "IONQ": "quantum computing stocks",
        "RGTI": "quantum computing stocks", "QBTS": "quantum computing stocks",
        "QUBT": "quantum computing stocks", "COIN": "cryptocurrency stocks",
        "CRWD": "cybersecurity stocks", "PLTR": "defense technology stocks",
        "HOOD": "retail trading stocks", "MSTR": "Bitcoin corporate treasury",
        "AFRM": "fintech buy now pay later", "UPST": "AI lending fintech",
        "BBAI": "AI defense technology", "AI": "artificial intelligence stocks",
        "LABU": "biotech stocks", "FNGU": "FAANG tech stocks",
        "TECL": "technology stocks", "WEBL": "social media stocks",
        "SNAP": "social media stocks", "LCID": "electric vehicle stocks",
        "DKNG": "sports betting stocks", "SOFI": "fintech banking stocks",
        "RIVN": "electric vehicle stocks",
    }
    sector = sector_map.get(symbol)
    if sector:
        extra, _ = brave_news_search(sector, freshness=freshness, count=5)
        for a in extra:
            a["sector_news"] = True
        articles.extend(extra)
    
    # Deduplicate by title
    seen = set()
    deduped = []
    for a in articles:
        key = a["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(a)
    
    return deduped


def fetch_market_news(freshness="pd"):
    """General market context."""
    queries = [
        ("stock market today", "MARKET"),
        ("S&P 500 outlook", "MARKET"),
        ("Federal Reserve interest rates", "MACRO"),
        ("US economy news", "MACRO"),
        ("Nasdaq tech stocks", "MARKET"),
    ]
    all_news = []
    for q, tag in queries:
        articles, _ = brave_news_search(q, freshness=freshness, count=5)
        for a in articles:
            a["symbol"] = tag
            a["query"] = q
        all_news.extend(articles)
    return all_news


def fetch_fundamentals(symbol):
    """Fetch fundamental data for a symbol using yfinance (TradingAgents-inspired)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        
        fundamentals = {
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "short_ratio": info.get("shortRatio"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        }
        return fundamentals, None
    except ImportError:
        return {}, "yfinance not installed"
    except Exception as e:
        return {}, str(e)


def analyze_symbols(symbols, freshness="pd"):
    """Analyze all symbols and return per-symbol sentiment data + fundamentals."""
    results = {}
    all_market_articles = []
    
    for sym in symbols:
        articles = fetch_symbol_news(sym, freshness)
        all_market_articles.extend(articles)
        
        avg_score, confidence, top_picks, catalysts, bull_score, bear_score = score_symbol(articles)
        multiplier, recommendation = determine_symbol_multiplier(avg_score, confidence, catalysts)
        
        # Fetch fundamentals
        fundamentals, fund_err = fetch_fundamentals(sym)
        
        results[sym] = {
            "symbol": sym,
            "sentiment_score": avg_score,
            "bullish_score": bull_score,
            "bearish_score": bear_score,
            "confidence": confidence,
            "position_multiplier": multiplier,
            "recommendation": recommendation,
            "catalysts": catalysts,
            "article_count": len(articles),
            "top_articles": top_picks,
            "fundamentals": fundamentals,
            "fundamentals_error": fund_err,
            "notes": generate_symbol_note(sym, avg_score, bull_score, bear_score, confidence, catalysts, recommendation, fundamentals),
        }
    
    return results


def generate_symbol_note(sym, score, bull, bear, confidence, catalysts, recommendation, fundamentals=None):
    """Generate a human-readable note for a symbol (TradingAgents-inspired bull/bear format)."""
    parts = []
    parts.append(f"Bull: {bull:.2f} | Bear: {bear:.2f} | Net: {score:+.2f}")
    
    if score > 0.2:
        parts.append(f"Bullish ({score:+.2f})")
    elif score < -0.2:
        parts.append(f"Bearish ({score:+.2f})")
    else:
        parts.append(f"Neutral ({score:+.2f})")
    
    if catalysts:
        parts.append(f"Catalysts: {', '.join(catalysts)}")
    
    # Add fundamental highlights (TradingAgents-inspired)
    if fundamentals:
        pe = fundamentals.get("pe_ratio")
        rev_growth = fundamentals.get("revenue_growth")
        if pe:
            parts.append(f"P/E: {pe:.1f}")
        if rev_growth is not None:
            parts.append(f"Rev growth: {rev_growth*100:.1f}%")
    
    rec_labels = {
        "boost": "Boost position size",
        "slight_boost": "Slightly increase position",
        "normal": "Standard position",
        "normal_low_conf": "Standard (low confidence)",
        "reduce": "Reduce position",
        "reduce_with_catalyst": "Reduce (catalyst present)",
        "skip": "SKIP — negative sentiment",
        "catalyst_boost": "Event catalyst — increase size",
    }
    parts.append(f"→ {rec_labels.get(recommendation, recommendation)}")
    
    return " | ".join(parts)


def build_global_sentiment(symbol_results, market_articles):
    """Aggregate symbol-level data into global market sentiment."""
    scores = [r["sentiment_score"] for r in symbol_results.values()]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    
    # Count boosts vs skips
    boosts = sum(1 for r in symbol_results.values() if r["recommendation"] in ("boost", "catalyst_boost", "slight_boost"))
    skips = sum(1 for r in symbol_results.values() if r["recommendation"] == "skip")
    reduces = sum(1 for r in symbol_results.values() if r["recommendation"] in ("reduce", "reduce_with_catalyst"))
    
    # Determine market regime
    if avg_score > 0.15:
        regime = "bullish"
        base_mult = 1.0
    elif avg_score > 0.05:
        regime = "cautiously_bullish"
        base_mult = 0.85
    elif avg_score > -0.05:
        regime = "neutral"
        base_mult = 0.75
    elif avg_score > -0.15:
        regime = "cautiously_bearish"
        base_mult = 0.65
    else:
        regime = "bearish"
        base_mult = 0.5
    
    # Extract key headlines from market articles
    headlines = []
    for a in market_articles[:10]:
        t = a.get("title", "").strip()
        if t and t not in headlines:
            headlines.append(t)
    
    return {
        "market_regime": regime,
        "sentiment_score": round(avg_score, 3),
        "position_multiplier": base_mult,
        "boost_count": boosts,
        "skip_count": skips,
        "reduce_count": reduces,
        "key_headlines": headlines[:10],
        "analyzed_at": datetime.now(ET).isoformat(),
    }


def generate_output(symbol_results, global_sentiment, target_bot):
    """Generate the sentiment_override.json file content."""
    # Determine skip and reduced symbols
    skip_syms = []
    reduced_syms = []
    boost_syms = []
    
    for sym, data in symbol_results.items():
        rec = data["recommendation"]
        if rec == "skip":
            skip_syms.append(sym)
        elif rec in ("reduce", "reduce_with_catalyst"):
            reduced_syms.append(sym)
        elif rec in ("boost", "catalyst_boost", "slight_boost"):
            boost_syms.append(sym)
    
    # Per-symbol multipliers
    per_symbol = {}
    for sym, data in symbol_results.items():
        per_symbol[sym] = {
            "sentiment_score": data["sentiment_score"],
            "bullish_score": data.get("bullish_score", 0),
            "bearish_score": data.get("bearish_score", 0),
            "confidence": data["confidence"],
            "position_multiplier": data["position_multiplier"],
            "recommendation": data["recommendation"],
            "catalysts": data["catalysts"],
            "notes": data["notes"],
        }
    
    # Build summary notes
    notes_parts = []
    notes_parts.append(f"Regime: {global_sentiment['market_regime']} ({global_sentiment['sentiment_score']:+.2f})")
    notes_parts.append(f"Base mult: {global_sentiment['position_multiplier']:.2f}")
    if boost_syms:
        notes_parts.append(f"🔺 Boost: {', '.join(boost_syms)}")
    if reduced_syms:
        notes_parts.append(f"🔻 Reduced: {', '.join(reduced_syms)}")
    if skip_syms:
        notes_parts.append(f"⛔ Skipped: {', '.join(skip_syms)}")
    
    return {
        "analyzed_at": global_sentiment["analyzed_at"],
        "market_regime": global_sentiment["market_regime"],
        "sentiment_score": global_sentiment["sentiment_score"],
        "position_multiplier": global_sentiment["position_multiplier"],
        "stop_multiplier": max(0.5, min(1.0, global_sentiment["position_multiplier"])),
        "target_multiplier": max(0.65, min(1.0, global_sentiment["position_multiplier"] + 0.1)),
        "skip_symbols": skip_syms,
        "reduced_symbols": reduced_syms,
        "boost_symbols": boost_syms,
        "per_symbol": per_symbol,
        "notes": " | ".join(notes_parts),
        "key_headlines": global_sentiment["key_headlines"],
        "summary": f"{global_sentiment['market_regime'].title()} ({global_sentiment['sentiment_score']:+.2f}). "
                   f"{boost_syms} boosted. {reduced_syms} reduced. {skip_syms} skipped."
    }


def get_symbols_for_bot(bot_name):
    """Get the tracked symbols for a bot from its champion.json."""
    if bot_name == "hermes":
        path = BASE / "trading-bot-hermes" / "config" / "champion.json"
    else:
        path = BASE / "trading-bot" / "config" / "champion.json"
    
    if not path.exists():
        return []
    
    with open(path) as f:
        data = json.load(f)
    
    return [r["symbol"] for r in data.get("symbol_results", []) if "error" not in r]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Market Sentiment Analyzer")
    parser.add_argument("--output-bot", choices=["hermes", "wq", "both"], default="both",
                        help="Which bot's sentiment file to update")
    parser.add_argument("--freshness", default="pd", choices=["pd", "pw", "pm"],
                        help="News freshness: pd=past day, pw=past week")
    args = parser.parse_args()
    
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        print("❌ BRAVE_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    
    # Get symbols for target bots
    all_symbols = set()
    if args.output_bot in ("hermes", "both"):
        all_symbols.update(get_symbols_for_bot("hermes"))
    if args.output_bot in ("wq", "both"):
        all_symbols.update(get_symbols_for_bot("wq"))
    
    if not all_symbols:
        print("❌ No symbols found", file=sys.stderr)
        sys.exit(1)
    
    symbols = sorted(all_symbols)
    print(f"📡 Analyzing {len(symbols)} symbols: {', '.join(symbols)}", file=sys.stderr)
    
    # Fetch market news first (for context)
    print("📰 Fetching market news...", file=sys.stderr)
    market_articles = fetch_market_news(args.freshness)
    print(f"   Got {len(market_articles)} market articles", file=sys.stderr)
    
    # Fetch and score per-symbol news
    symbol_results = analyze_symbols(symbols, args.freshness)
    
    # Build global sentiment
    global_sentiment = build_global_sentiment(symbol_results, market_articles)
    
    # Generate output
    output = generate_output(symbol_results, global_sentiment, args.output_bot)
    
    # Save to appropriate bot configs
    saved = []
    if args.output_bot in ("hermes", "both"):
        path = BASE / "trading-bot-hermes" / "config" / "sentiment_override.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"✅ Saved to {path}", file=sys.stderr)
        saved.append(path)
    
    if args.output_bot in ("wq", "both"):
        path = BASE / "trading-bot" / "config" / "sentiment_override.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"✅ Saved to {path}", file=sys.stderr)
        saved.append(path)
    
    # Print summary
    print(f"\n📊 Market: {global_sentiment['market_regime']} ({global_sentiment['sentiment_score']:+.2f})", file=sys.stderr)
    print(f"   Boost: {output['boost_symbols']}", file=sys.stderr)
    print(f"   Reduced: {output['reduced_symbols']}", file=sys.stderr)
    print(f"   Skipped: {output['skip_symbols']}", file=sys.stderr)
    
    # Also output to stdout for cron delivery
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
