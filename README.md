# Capitol Gains

A dashboard for researching political trends that move markets, plus an honest
tracker for a monthly investing plan measured against the S&P 500.

**This is a research tool, not financial advice.** Strategies that aim to beat the
S&P 500 carry real risk of losing money — nothing here removes that risk. Signal
scores measure attention in public data; they are not predictions or recommendations.

## What it shows

- **Signals** — tickers ranked by a transparent 0–100 "signal strength" that combines
  congressional buying (over a trailing 90-day window, computed from the full trade
  database), policy activity in the ticker's sector, and price momentum vs. the S&P 500 —
  each with plain-English reasons why, and what could go wrong.
- **Ticker scorecard** — click any ticker anywhere in the app for a one-page card:
  full-database congressional buy vs. sell volume, the Signals score and its breakdown,
  most active members, and recent trades. It only re-presents Signals + Congress Trades
  data — not a new rating or a recommendation. Deep-linkable as `#ticker=NVDA`.
- **Ticker vs. ticker** — a shareable side-by-side of two tickers' attention scores
  (`#compare=NVDA,LMT`, "Copy link"). The higher score means more attention right now,
  not a better investment.
- **Weekly Brief + "New since your last visit"** — a plain-English summary of the last
  7 days, plus an alerts feed showing exactly what trades and filings are new since you
  last opened the app (tracked in your browser).
- **Congress Trades** — stock trades by members of **both** the U.S. House (official
  Clerk STOCK Act PDFs) and the U.S. Senate (official eFD electronic filings), parsed
  directly from the source and stored once in a local SQLite database (`capitol.db`), so
  nothing is re-parsed twice. Every 2025 and 2026 periodic transaction report is ingested,
  tagged with its chamber, and searchable across the whole database. Scanned paper filings
  can't be parsed and are flagged as such with a link to the original. Disclosures lag the
  actual trade by up to 45 days by law.
- **Politician pages** — click any member's name anywhere in the app to see their profile:
  every trade, most-traded tickers, estimated buy/sell volume, and links to every original
  filing. Public-record research only — not a claim of wrongdoing. **Follow** a member from
  their profile to get a filtered "New from members you follow" feed on the Overview tab
  (watch list stored in your browser, like My Monthly Plan — no account).
- **Legislation** — bills with recent status changes (GovTrack), tagged with sectors
  they might affect and broad sector ETFs as research starting points.
- **News** — political headlines from Politico and The Hill, tagged by sector.
- **Learn** — plain-English explanations of every concept the app uses.
- **My Monthly Plan** — log each monthly contribution; the app prices your holdings
  and compares them against putting the same money, on the same dates, into the
  S&P 500. Stored in your browser; use Export/Import to move between computers.

The interface is a premium dark theme by default (one warm amber accent); a toggle in
the top bar switches to light and remembers the choice. The homepage leads with a hero
headline and a live stat strip (trades parsed / filings ingested / members tracked)
read straight from the database. Still zero build step — plain HTML/CSS/JS in `static/`.

## Run it

```bash
pip3 install --user pypdf   # one-time
python3 server.py           # then open http://localhost:8642
```

No API keys needed. All sources are free public data (House Clerk, Senate eFD, GovTrack,
Politico/The Hill RSS, Yahoo Finance). PDFs/HTML are cached in `.cache/` and parsed trades
are stored in `capitol.db`. On first run the server ingests **every** 2025–2026 House and
Senate periodic transaction report in the background (~1,000+ filings, a few minutes) — the
page stays usable while a progress banner fills in, and subsequent runs are instant because
nothing is re-parsed. `.cache/` and `capitol.db` are git-ignored.

## Hosting it later (website / app)

The server is dependency-light (Python stdlib + pypdf) on purpose, so it can run on
any small host that supports Python — e.g. Render, Fly.io, or a $5 VPS. The app
ships a PWA manifest, so once it's hosted with HTTPS it can be "installed" to a
phone or desktop home screen like a native app. Making the GitHub repo public later:
`gh repo edit --visibility public`.
