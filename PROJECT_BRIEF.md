# Capitol Gains — project brief

## What it is
Capitol Gains is a political-trends investment research web app I built (as a beginner,
with Claude Code). It tracks what U.S. politicians trade, what legislation is moving, and
political news — and turns that into transparent, plain-English "attention signals" on
stocks. It also has an honest personal tracker that benchmarks my monthly investing
against the S&P 500.

- Live site: https://capitol-gains.onrender.com (Render free tier; PWA — installable on phones)
- Code: github.com/lucadewinne-bit/capitol-gains (private for now)
- Hard rule baked into the product: it explains signals but NEVER gives buy
  recommendations or personalized financial advice. Risk honesty everywhere: nothing
  promises beating the market without risk of loss.

## What's built (all working)
1. **Congress Trades** — parses official STOCK Act disclosures from BOTH chambers at the
   source: U.S. House Clerk PDF filings and Senate eFD electronic filings. Every 2025–2026
   periodic transaction report (~1,000+ filings, 9,000+ trades, 113 members) is ingested
   into a SQLite database (capitol.db); nothing is parsed twice; scanned paper filings are
   flagged with links to originals. Searchable by ticker/member/chamber/side.
2. **Politician pages** — click any member: all their trades, top tickers, estimated
   buy/sell volume, links to every original filing.
3. **Signals tab** — each ticker scored 0–100 with the formula shown openly: up to 40 pts
   congressional buying (trailing 90 days from the database), 30 pts policy activity
   (bills + news tagged to the ticker's sector), 30 pts price momentum vs the S&P 500.
   Every card shows plain-English "why it's getting attention" bullets AND a "what could
   go wrong" list. A banner explains a signal measures attention, not a prediction.
4. **Weekly Brief** — auto-written plain-English summary of the last 7 days (market move,
   new filings, bills that moved, hottest news sector) + a "new since your last visit"
   alerts feed.
5. **Legislation & News tabs** — GovTrack bill status changes and Politico/The Hill
   headlines, keyword-tagged by sector with broad sector ETFs as research starting points.
6. **Learn tab + tooltips** — beginner explanations (stocks, ETFs, risk vs return,
   diversification, dollar-cost averaging, the STOCK Act, the 45-day disclosure lag);
   every jargon term in the app has a hover definition.
7. **My Monthly Plan** — I log each monthly contribution (date, amount, ticker); the app
   prices my holdings and compares against putting identical money on identical dates
   into the S&P 500. Browser-localStorage only, with JSON export/import.

## Tech stack (deliberately simple)
- Backend: single-file Python stdlib server (http.server) + pypdf; fetches via curl;
  SQLite for trades; in-memory + disk caching; background ingest with a progress banner.
- Frontend: vanilla HTML/CSS/JS, no frameworks; hand-rolled SVG charts with tooltips;
  accessible validated color palette; light/dark mode.
- Data sources (all free, no API keys): House Clerk disclosure PDFs, Senate eFD, GovTrack
  API, Politico/The Hill RSS, Yahoo Finance chart endpoint (unofficial).
- Deployed on Render free tier via render.yaml blueprint. Free-tier realities: sleeps
  after ~15 idle minutes; ephemeral disk, so the database auto-rebuilds on cold start
  (a few minutes, page stays usable).

## Roadmap already agreed
- Phase 3 (next): user accounts + Stripe monthly subscriptions, free vs paid tier split.
  Prerequisites on me: create Stripe account; decide the tier split. Known blocker to
  charging money: Yahoo's unofficial price API isn't licensed for commercial resale —
  needs a licensed provider (Polygon/Finnhub) before real customers pay.
- Phase 4: public launch — free tier public first, then paywall; terms of service,
  privacy policy, formal disclaimers; maybe custom domain + rename.

## What I want help brainstorming
- What would make this genuinely worth paying for vs existing competitors
  (Quiver Quantitative, Unusual Whales, Capitol Trades)?
- Smarter but still transparent signal scoring; how to backtest it honestly.
- Free vs paid feature split, pricing, and how to get first users.
- Feature ideas: alerts/notifications, committee-membership context (does a member sit
  on a committee overseeing the sector they're trading?), trade-vs-bill timing analysis.
- Anything that keeps the product honest: no advice, no hype, risk always visible.
