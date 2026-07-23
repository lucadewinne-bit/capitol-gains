# Capitol Gains

A dashboard for researching political trends that move markets, plus an honest
tracker for a monthly investing plan measured against the S&P 500.

**This is a research tool, not financial advice.** Strategies that aim to beat the
S&P 500 carry real risk of losing money — nothing here removes that risk. Signal
scores measure attention in public data; they are not predictions or recommendations.

## What it shows

- **Signals** — tickers ranked by a transparent 0–100 "signal strength" that combines
  congressional buying, policy activity in the ticker's sector, and price momentum
  vs. the S&P 500 — each with plain-English reasons why, and what could go wrong.
- **Weekly Brief** — a plain-English summary of the last 7 days: market move, new
  congressional trades, bills that moved, and the most active news sector.
- **Congress Trades** — stock trades by members of the U.S. House, parsed directly
  from the official STOCK Act disclosure PDFs (disclosures-clerk.house.gov).
  Disclosures lag the actual trade by up to 45 days by law.
- **Legislation** — bills with recent status changes (GovTrack), tagged with sectors
  they might affect and broad sector ETFs as research starting points.
- **News** — political headlines from Politico and The Hill, tagged by sector.
- **Learn** — plain-English explanations of every concept the app uses.
- **My Monthly Plan** — log each monthly contribution; the app prices your holdings
  and compares them against putting the same money, on the same dates, into the
  S&P 500. Stored in your browser; use Export/Import to move between computers.

## Run it

```bash
pip3 install --user pypdf   # one-time
python3 server.py           # then open http://localhost:8642
```

No API keys needed. All sources are free public data (House Clerk, GovTrack,
Politico/The Hill RSS, Yahoo Finance). Responses are cached in `.cache/`; the first
load of congressional trades downloads ~30 filing PDFs (~30 seconds), then it's fast.

## Hosting it later (website / app)

The server is dependency-light (Python stdlib + pypdf) on purpose, so it can run on
any small host that supports Python — e.g. Render, Fly.io, or a $5 VPS. The app
ships a PWA manifest, so once it's hosted with HTTPS it can be "installed" to a
phone or desktop home screen like a native app. Making the GitHub repo public later:
`gh repo edit --visibility public`.
