#!/usr/bin/env python3
"""PoliTrend Research — local political-trends investment research dashboard.

Data sources (all free, no API keys):
  - Congressional stock trades: official House Clerk financial disclosures
    (STOCK Act periodic transaction reports, parsed from the public PDFs)
  - Legislation: GovTrack API
  - Political news: Politico / The Hill RSS feeds
  - Market prices: Yahoo Finance chart endpoint

Run:  python3 server.py   ->  http://localhost:8642
"""
import html
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
CACHE_DIR = os.path.join(BASE, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PORT = 8642
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

FD_YEAR = "2026"
FD_ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{}FD.zip".format(FD_YEAR)
PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{}/{}.pdf"
GOVTRACK_URL = "https://www.govtrack.us/api/v2/bill?order_by=-current_status_date&limit=40"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval=1d"
NEWS_FEEDS = [
    ("Politico", "https://rss.politico.com/politics-news.xml"),
    ("Politico Congress", "https://rss.politico.com/congress.xml"),
    ("The Hill", "https://thehill.com/feed/"),
]

MAX_FILINGS_PARSED = 30  # most recent PTR filings to download & parse

# Sector keyword map used to tag bills and news with possibly-affected sectors.
# ETFs listed are broad sector funds people commonly research — shown as
# starting points for research, never as recommendations.
SECTORS = {
    "Defense & Aerospace": {
        "keywords": ["defense", "military", "pentagon", "nato", "missile", "drone",
                     "weapons", "army", "navy", "air force", "ukraine", "national security"],
        "etfs": "ITA, XAR, PPA",
    },
    "Energy & Oil": {
        "keywords": ["energy", "oil", "gas", "drilling", "opec", "pipeline", "petroleum",
                     "solar", "wind", "nuclear", "climate", "epa", "emissions"],
        "etfs": "XLE, ICLN, URA",
    },
    "Tech & Semiconductors": {
        "keywords": ["semiconductor", "chip", "artificial intelligence", " ai ", "tech",
                     "data center", "export control", "tiktok", "broadband", "cyber"],
        "etfs": "XLK, SOXX, SMH",
    },
    "Healthcare & Pharma": {
        "keywords": ["health", "medicare", "medicaid", "drug", "pharma", "fda",
                     "hospital", "insurance", "vaccine"],
        "etfs": "XLV, IHE",
    },
    "Financials & Crypto": {
        "keywords": ["bank", "financial", "sec ", "crypto", "bitcoin", "stablecoin",
                     "federal reserve", "interest rate", "tax", "tariff", "trade deal"],
        "etfs": "XLF, KRE",
    },
    "Industrials & Infrastructure": {
        "keywords": ["infrastructure", "highway", "manufacturing", "steel", "railroad",
                     "aviation", "shipping", "construction", "housing"],
        "etfs": "XLI, PAVE, ITB",
    },
}

_mem = {}
_mem_lock = threading.Lock()


def fetch(url, timeout=30, ua=UA):
    """HTTP GET via curl (uses macOS system trust store). Returns bytes or None."""
    r = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), "-A", ua, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    return r.stdout


def cached_fetch(key, url, ttl, timeout=30):
    """Disk-cached fetch. Serves stale copy if the network fails."""
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path) and (time.time() - os.path.getmtime(path) < ttl):
        with open(path, "rb") as f:
            return f.read()
    data = fetch(url, timeout=timeout)
    if data:
        with open(path, "wb") as f:
            f.write(data)
        return data
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def mem_cached(key, ttl, builder):
    with _mem_lock:
        hit = _mem.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    data = builder()
    with _mem_lock:
        _mem[key] = (time.time() + ttl, data)
    return data


# ---------------------------------------------------------------- trades ----

TRADE_RE = re.compile(
    r"\(([A-Z][A-Z.\-]{0,7})\)\s*\[ST\]\s*"      # ticker, stock assets only
    r"([PSE])\s*(?:\(partial\))?\s*"              # P=buy S=sell E=exchange
    r"(\d{2}/\d{2}/\d{4})\s*(\d{2}/\d{2}/\d{4})\s*"  # trade date, notified date
    r"\$([\d,]+)\s*(?:-\s*\$?([\d,]+)|\+)"        # amount range
)
OWNER_SPLIT = re.compile(r"\b(?:SP|JT|DC)\b")


def _clean_asset_name(prefix):
    seg = OWNER_SPLIT.split(prefix)[-1]
    seg = seg.split("$200?")[-1]
    seg = re.sub(r"[^A-Za-z0-9&.,\-' ()/]", " ", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" -:,.")
    return seg[:80]


def _parse_ptr_pdf(docid):
    """Parse one PTR PDF into a list of trades. Disk-cached as JSON."""
    jpath = os.path.join(CACHE_DIR, "trades_{}.json".format(docid))
    if os.path.exists(jpath):
        with open(jpath) as f:
            return json.load(f)
    pdf = cached_fetch("ptr_{}.pdf".format(docid),
                       PTR_PDF_URL.format(FD_YEAR, docid), ttl=10 ** 9)
    trades = []
    if pdf and PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(pdf))
            text = " ".join((p.extract_text() or "") for p in reader.pages)
            text = re.sub(r"\s+", " ", text)
            pos = 0
            for m in TRADE_RE.finditer(text):
                low = int(m.group(5).replace(",", ""))
                high = int(m.group(6).replace(",", "")) if m.group(6) else low
                trades.append({
                    "ticker": m.group(1),
                    "asset": _clean_asset_name(text[max(pos, m.start() - 95):m.start()]),
                    "side": {"P": "BUY", "S": "SELL", "E": "EXCHANGE"}[m.group(2)],
                    "traded": m.group(3),
                    "notified": m.group(4),
                    "amount_low": low,
                    "amount_high": high,
                })
                pos = m.end()
        except Exception:
            trades = []
    with open(jpath, "w") as f:
        json.dump(trades, f)
    return trades


def _date_key(mdy):
    try:
        m, d, y = mdy.split("/")
        return "{}-{:0>2}-{:0>2}".format(y, m, d)
    except Exception:
        return "0000-00-00"


def build_trades():
    zip_data = cached_fetch("{}FD.zip".format(FD_YEAR), FD_ZIP_URL, ttl=6 * 3600)
    if not zip_data:
        return {"error": "Could not reach House disclosure site"}
    xml = zipfile.ZipFile(io.BytesIO(zip_data)).read("{}FD.xml".format(FD_YEAR))
    root = ET.fromstring(xml.decode("utf-8-sig"))
    filings = []
    for m in root.findall("Member"):
        if m.findtext("FilingType") != "P":
            continue
        filings.append({
            "member": "{} {}".format(m.findtext("First") or "", m.findtext("Last") or "").strip(),
            "state": m.findtext("StateDst") or "",
            "filed": m.findtext("FilingDate") or "",
            "docid": m.findtext("DocID") or "",
        })
    filings.sort(key=lambda f: _date_key(f["filed"]), reverse=True)
    recent = filings[:MAX_FILINGS_PARSED]

    with ThreadPoolExecutor(max_workers=6) as pool:
        parsed = list(pool.map(lambda f: _parse_ptr_pdf(f["docid"]), recent))

    all_trades, unparsed = [], 0
    for filing, trades in zip(recent, parsed):
        if not trades:
            unparsed += 1  # usually a scanned paper filing
        for t in trades:
            row = dict(t)
            row.update({
                "member": filing["member"], "state": filing["state"],
                "filed": filing["filed"],
                "doc_url": PTR_PDF_URL.format(FD_YEAR, filing["docid"]),
            })
            all_trades.append(row)
    all_trades.sort(key=lambda t: _date_key(t["traded"]), reverse=True)

    def top(side):
        agg = {}
        for t in all_trades:
            if t["side"] != side:
                continue
            a = agg.setdefault(t["ticker"], {"ticker": t["ticker"], "count": 0,
                                             "est_total": 0, "members": set()})
            a["count"] += 1
            a["est_total"] += (t["amount_low"] + t["amount_high"]) // 2
            a["members"].add(t["member"])
        rows = sorted(agg.values(), key=lambda a: (a["count"], a["est_total"]), reverse=True)[:10]
        for r in rows:
            r["members"] = len(r["members"])
        return rows

    return {
        "updated": int(time.time()),
        "total_ptr_filings_this_year": len(filings),
        "filings_parsed": len(recent) - unparsed,
        "filings_paper_only": unparsed,
        "trades": all_trades,
        "top_buys": top("BUY"),
        "top_sells": top("SELL"),
    }


# ----------------------------------------------------------------- bills ----

def tag_sectors(text):
    low = " " + (text or "").lower() + " "
    hits = []
    for name, info in SECTORS.items():
        if any(k in low for k in info["keywords"]):
            hits.append({"sector": name, "etfs": info["etfs"]})
    return hits


def build_bills():
    raw = fetch(GOVTRACK_URL)
    if not raw:
        return {"error": "Could not reach GovTrack"}
    data = json.loads(raw.decode("utf-8", "replace"))
    bills = []
    for b in data.get("objects", []):
        title = b.get("title_without_number") or b.get("title") or ""
        sponsor = (b.get("sponsor") or {}).get("name", "")
        bills.append({
            "number": b.get("display_number", ""),
            "title": title,
            "status": b.get("current_status_label", ""),
            "status_date": b.get("current_status_date", ""),
            "sponsor": sponsor,
            "link": b.get("link", ""),
            "sectors": tag_sectors(title),
        })
    return {"updated": int(time.time()), "bills": bills}


# ------------------------------------------------------------------ news ----

TAG_RE = re.compile(r"<[^>]+>")


def _parse_rss(source, raw):
    items = []
    try:
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title = html.unescape((item.findtext("title") or "").strip())
            desc = TAG_RE.sub(" ", item.findtext("description") or "")
            desc = html.unescape(re.sub(r"\s+", " ", desc).strip())[:240]
            items.append({
                "source": source,
                "title": title,
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "summary": desc,
                "sectors": tag_sectors(title + " " + desc),
            })
    except Exception:
        pass
    return items


def _pubdate_key(s):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return 0


def build_news():
    items = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        raws = list(pool.map(lambda f: fetch(f[1], timeout=20), NEWS_FEEDS))
    for (source, _url), raw in zip(NEWS_FEEDS, raws):
        if raw:
            items.extend(_parse_rss(source, raw))
    items.sort(key=lambda i: _pubdate_key(i["published"]), reverse=True)
    return {"updated": int(time.time()), "items": items[:50]}


# --------------------------------------------------------------- signals ----

# Sector assignment for tickers commonly seen in congressional filings. Unknown
# tickers still get congress + momentum scores; their policy score is 0.
TICKER_SECTORS = {}
for _sector, _tickers in {
    "Tech & Semiconductors": ["NVDA", "APH", "ADI", "INTU", "ANET", "ACN", "GOOGL",
                              "GOOG", "MSFT", "AAPL", "AMD", "AVGO", "NOK", "CSCO",
                              "CRM", "NFLX", "META", "AMZN", "AMCR", "COST", "TSLA"],
    "Defense & Aerospace": ["LHX", "LMT", "RTX", "NOC", "GD", "BA", "HII", "TXT"],
    "Healthcare & Pharma": ["ABT", "ZTS", "UNH", "JNJ", "PFE", "LLY", "MRK", "TMO",
                            "MDT", "ABBV", "BMY"],
    "Financials & Crypto": ["BAC", "JPM", "GS", "MS", "SPGI", "MKL", "V", "MA",
                            "WFC", "C", "BLK", "AXP"],
    "Energy & Oil": ["XOM", "CVX", "COP", "SLB", "OXY", "NEE", "DUK"],
    "Industrials & Infrastructure": ["CAT", "DE", "UNP", "GE", "ESAB", "FERG",
                                     "ETN", "EMR", "HON", "MMM", "UPS"],
}.items():
    for _t in _tickers:
        TICKER_SECTORS[_t] = _sector


def _hist_cached(symbol, rng):
    key = "hist_{}_{}".format(re.sub(r"[^A-Za-z0-9]", "_", symbol), rng)
    return mem_cached(key, 3600, lambda: build_history(symbol, rng))


def _returns(hist):
    closes = hist.get("closes") or []
    if len(closes) < 2:
        return None, None
    r3m = closes[-1] / closes[0] - 1
    i1m = max(0, len(closes) - 22)  # ~21 trading days per month
    r1m = closes[-1] / closes[i1m] - 1
    return r1m, r3m


def build_signals():
    trades_data = mem_cached("trades", 1800, build_trades)
    bills_data = mem_cached("bills", 1800, build_bills)
    news_data = mem_cached("news", 900, build_news)
    if "error" in trades_data:
        return trades_data

    # aggregate congressional interest per ticker
    agg = {}
    for t in trades_data["trades"]:
        a = agg.setdefault(t["ticker"], {
            "ticker": t["ticker"], "asset": t["asset"], "buyers": set(),
            "sellers": set(), "buy_total": 0, "sell_total": 0, "last_trade": ""})
        mid = (t["amount_low"] + t["amount_high"]) // 2
        if t["side"] == "BUY":
            a["buyers"].add(t["member"])
            a["buy_total"] += mid
        elif t["side"] == "SELL":
            a["sellers"].add(t["member"])
            a["sell_total"] += mid
        dk = _date_key(t["traded"])
        if dk > a["last_trade"]:
            a["last_trade"] = dk

    # policy activity per sector from bills + news sector tags
    sector_hits = {s: {"bills": 0, "news": 0} for s in SECTORS}
    for b in bills_data.get("bills", []):
        for s in b["sectors"]:
            sector_hits[s["sector"]]["bills"] += 1
    for n in news_data.get("items", []):
        for s in n["sectors"]:
            sector_hits[s["sector"]]["news"] += 1

    # top candidates by congressional buying, then score with momentum
    candidates = sorted(
        [a for a in agg.values() if a["buyers"]],
        key=lambda a: (len(a["buyers"]), a["buy_total"]), reverse=True)[:12]

    spx = _hist_cached("^GSPC", "3mo")
    spx_r1m, spx_r3m = _returns(spx)
    with ThreadPoolExecutor(max_workers=6) as pool:
        hists = list(pool.map(lambda a: _hist_cached(a["ticker"], "3mo"), candidates))

    signals = []
    for a, hist in zip(candidates, hists):
        buyers, sellers = len(a["buyers"]), len(a["sellers"])
        congress = max(0, min(40, 10 * buyers
                              + (5 if a["buy_total"] >= 50000 else 0)
                              - 8 * sellers))
        sector = TICKER_SECTORS.get(a["ticker"])
        hits = sector_hits.get(sector, {"bills": 0, "news": 0})
        policy = min(30, 3 * (hits["bills"] + hits["news"]))
        r1m, r3m = _returns(hist) if "error" not in hist else (None, None)
        if r3m is not None and spx_r3m is not None:
            # 15 = moving with the market; +/-1 point per pct-pt vs S&P over 3 months
            momentum = max(0, min(30, round(15 + (r3m - spx_r3m) * 100)))
        else:
            momentum = 15
        etfs = SECTORS.get(sector, {}).get("etfs", "") if sector else ""
        signals.append({
            "ticker": a["ticker"], "asset": a["asset"], "sector": sector,
            "sector_etfs": etfs,
            "score": congress + policy + momentum,
            "parts": {"congress": congress, "policy": policy, "momentum": momentum},
            "buyers": buyers, "sellers": sellers,
            "buy_total": a["buy_total"], "sell_total": a["sell_total"],
            "last_trade": a["last_trade"],
            "bills_hits": hits["bills"], "news_hits": hits["news"],
            "r1m": r1m, "r3m": r3m,
            "name": hist.get("name", a["ticker"]) if "error" not in hist else a["ticker"],
        })
    signals.sort(key=lambda s: s["score"], reverse=True)
    return {"updated": int(time.time()), "spx_r1m": spx_r1m, "spx_r3m": spx_r3m,
            "signals": signals}


# ----------------------------------------------------------------- brief ----

def build_brief():
    trades_data = mem_cached("trades", 1800, build_trades)
    bills_data = mem_cached("bills", 1800, build_bills)
    news_data = mem_cached("news", 900, build_news)
    week_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))

    spx = _hist_cached("^GSPC", "1mo")
    spx_close = spx_week = None
    if "error" not in spx and len(spx.get("closes", [])) >= 6:
        spx_close = spx["closes"][-1]
        spx_week = spx["closes"][-1] / spx["closes"][-6] - 1  # 5 trading days

    new_filings, buy_counts, sell_counts = set(), {}, {}
    for t in trades_data.get("trades", []):
        if _date_key(t["filed"]) < week_ago:
            continue
        new_filings.add((t["member"], t["filed"]))
        if t["side"] == "BUY":
            buy_counts[t["ticker"]] = buy_counts.get(t["ticker"], 0) + 1
        elif t["side"] == "SELL":
            sell_counts[t["ticker"]] = sell_counts.get(t["ticker"], 0) + 1
    top_buy = max(buy_counts.items(), key=lambda kv: kv[1]) if buy_counts else None
    top_sell = max(sell_counts.items(), key=lambda kv: kv[1]) if sell_counts else None

    week_bills = [b for b in bills_data.get("bills", [])
                  if (b.get("status_date") or "") >= week_ago]
    tagged_bills = [b for b in week_bills if b["sectors"]][:3]

    sector_news = {}
    for n in news_data.get("items", []):
        for s in n["sectors"]:
            sector_news[s["sector"]] = sector_news.get(s["sector"], 0) + 1
    top_sector = max(sector_news.items(), key=lambda kv: kv[1]) if sector_news else None

    return {
        "updated": int(time.time()),
        "spx_close": spx_close, "spx_week": spx_week,
        "new_filings": len(new_filings),
        "top_buy": {"ticker": top_buy[0], "count": top_buy[1]} if top_buy else None,
        "top_sell": {"ticker": top_sell[0], "count": top_sell[1]} if top_sell else None,
        "bills_moved": len(week_bills),
        "highlight_bills": [{"number": b["number"], "title": b["title"][:120],
                             "status": b["status"], "link": b["link"],
                             "sectors": b["sectors"]} for b in tagged_bills],
        "top_news_sector": {"sector": top_sector[0], "count": top_sector[1]} if top_sector else None,
    }


# --------------------------------------------------------------- history ----

SYMBOL_RE = re.compile(r"^[A-Za-z0-9^.\-=]{1,12}$")
RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"}


def build_history(symbol, rng):
    # Yahoo's edge rejects full browser UA strings from curl; a plain one works.
    raw = fetch(YAHOO_URL.format(urllib.parse.quote(symbol), rng), ua="Mozilla/5.0")
    if not raw:
        return {"error": "Could not reach Yahoo Finance"}
    data = json.loads(raw.decode("utf-8", "replace"))
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        err = (data.get("chart", {}).get("error") or {}).get("description", "unknown symbol")
        return {"error": "No data for {}: {}".format(symbol, err)}
    ts = result.get("timestamp") or []
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close")) or []
    dates, vals = [], []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        dates.append(time.strftime("%Y-%m-%d", time.gmtime(t)))
        vals.append(round(c, 2))
    return {"symbol": symbol, "range": rng, "dates": dates, "closes": vals,
            "name": (result.get("meta") or {}).get("shortName") or symbol}


# ---------------------------------------------------------------- server ----

CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
                 ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/trades":
                self.send_json(mem_cached("trades", 1800, build_trades))
            elif path == "/api/bills":
                self.send_json(mem_cached("bills", 1800, build_bills))
            elif path == "/api/news":
                self.send_json(mem_cached("news", 900, build_news))
            elif path == "/api/signals":
                self.send_json(mem_cached("signals", 1800, build_signals))
            elif path == "/api/brief":
                self.send_json(mem_cached("brief", 1800, build_brief))
            elif path == "/api/history":
                symbol = (qs.get("symbol") or ["^GSPC"])[0]
                rng = (qs.get("range") or ["5y"])[0]
                if not SYMBOL_RE.match(symbol) or rng not in RANGES:
                    self.send_json({"error": "bad symbol or range"}, 400)
                    return
                key = "hist_{}_{}".format(re.sub(r"[^A-Za-z0-9]", "_", symbol), rng)
                self.send_json(mem_cached(key, 3600,
                                          lambda: build_history(symbol, rng)))
            else:
                self.serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self.send_json({"error": "{}: {}".format(type(e).__name__, e)}, 500)
            except Exception:
                pass

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        fpath = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not fpath.startswith(os.path.realpath(STATIC)) or not os.path.isfile(fpath):
            self.send_json({"error": "not found"}, 404)
            return
        with open(fpath, "rb") as f:
            body = f.read()
        ext = os.path.splitext(fpath)[1]
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if PdfReader is None:
        print("WARNING: pypdf not installed — run: pip3 install --user pypdf")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("PoliTrend Research running at http://localhost:{}".format(PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
