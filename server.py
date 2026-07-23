#!/usr/bin/env python3
"""Capitol Gains — local political-trends investment research dashboard.

Data sources (all free, no API keys):
  - Congressional stock trades:
      * U.S. House — official Clerk financial disclosures (STOCK Act periodic
        transaction reports), parsed from the public PDFs.
      * U.S. Senate — official Senate eFD periodic transaction reports, parsed
        from the public electronic filings (efdsearch.senate.gov).
    Every parsed trade is stored once in a local SQLite database (capitol.db)
    so filings are never re-parsed, and signals are computed from the full
    database rather than only the most recent filings.
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
import sqlite3
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
DB_PATH = os.path.join(BASE, "capitol.db")
os.makedirs(CACHE_DIR, exist_ok=True)

PORT = 8642
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Years of disclosures to ingest. Both chambers publish per-calendar-year.
YEARS = ("2025", "2026")
FD_ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{}FD.zip"
PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{}/{}.pdf"

SENATE_BASE = "https://efdsearch.senate.gov"
SENATE_HOME = SENATE_BASE + "/search/home/"
SENATE_DATA = SENATE_BASE + "/search/report/data/"
SENATE_REPORT_START = "01/01/{} 00:00:00".format(YEARS[0])

GOVTRACK_URL = "https://www.govtrack.us/api/v2/bill?order_by=-current_status_date&limit=40"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval=1d"
NEWS_FEEDS = [
    ("Politico", "https://rss.politico.com/politics-news.xml"),
    ("Politico Congress", "https://rss.politico.com/congress.xml"),
    ("The Hill", "https://thehill.com/feed/"),
]

# Trailing window (days) over which congressional buying counts toward a signal,
# so scores reflect *current* attention even though the database holds years.
SIGNAL_WINDOW_DAYS = 90

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


def fetch(url, timeout=30, ua=UA, extra=None):
    """HTTP GET via curl (uses macOS system trust store). Returns bytes or None."""
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", ua]
    for h in (extra or []):
        cmd += ["-H", h]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True)
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


# ================================================================ database ==

_db_write_lock = threading.Lock()


def db():
    """A fresh connection (safe to use from any thread). Rows as dicts."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS filings (
        id           TEXT PRIMARY KEY,   -- House DocID or Senate report UUID
        chamber      TEXT,
        member       TEXT,
        member_key   TEXT,
        state        TEXT,
        filed        TEXT,               -- MM/DD/YYYY
        filed_key    TEXT,               -- YYYY-MM-DD
        year         TEXT,
        doc_url      TEXT,
        parsed       INTEGER DEFAULT 0,  -- 1 = parse attempted
        paper        INTEGER DEFAULT 0,  -- 1 = scanned paper (unparseable)
        trade_count  INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id   TEXT,
        chamber     TEXT,
        member      TEXT,
        member_key  TEXT,
        state       TEXT,
        ticker      TEXT,
        asset       TEXT,
        side        TEXT,                -- BUY / SELL / EXCHANGE
        owner       TEXT,                -- SELF / JOINT / SPOUSE / CHILD / ''
        traded      TEXT,                -- MM/DD/YYYY
        traded_key  TEXT,                -- YYYY-MM-DD
        notified    TEXT,
        amount_low  INTEGER,
        amount_high INTEGER,
        doc_url     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
    CREATE INDEX IF NOT EXISTS idx_trades_member ON trades(member_key);
    CREATE INDEX IF NOT EXISTS idx_trades_traded ON trades(traded_key);
    CREATE INDEX IF NOT EXISTS idx_trades_filing ON trades(filing_id);
    CREATE INDEX IF NOT EXISTS idx_filings_key   ON filings(member_key);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
    conn.close()


def get_meta(key, default=None):
    conn = db()
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_meta(key, value):
    with _db_write_lock:
        conn = db()
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(value)))
        conn.commit()
        conn.close()


def member_key(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _date_key(mdy):
    try:
        m, d, y = mdy.split("/")
        return "{}-{:0>2}-{:0>2}".format(y, m, d)
    except Exception:
        return "0000-00-00"


def parsed_filing_ids():
    conn = db()
    ids = {r["id"] for r in conn.execute("SELECT id FROM filings WHERE parsed=1")}
    conn.close()
    return ids


def store_filing(f, trades):
    """Insert one filing and its trades atomically. `f` is a filing dict."""
    with _db_write_lock:
        conn = db()
        conn.execute(
            "INSERT OR REPLACE INTO filings"
            "(id,chamber,member,member_key,state,filed,filed_key,year,doc_url,parsed,paper,trade_count)"
            " VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
            (f["id"], f["chamber"], f["member"], member_key(f["member"]), f.get("state", ""),
             f.get("filed", ""), _date_key(f.get("filed", "")), f.get("year", ""),
             f.get("doc_url", ""), 1 if f.get("paper") else 0, len(trades)))
        conn.execute("DELETE FROM trades WHERE filing_id=?", (f["id"],))
        for t in trades:
            conn.execute(
                "INSERT INTO trades"
                "(filing_id,chamber,member,member_key,state,ticker,asset,side,owner,"
                "traded,traded_key,notified,amount_low,amount_high,doc_url)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f["id"], f["chamber"], f["member"], member_key(f["member"]), f.get("state", ""),
                 t["ticker"], t["asset"], t["side"], t.get("owner", ""),
                 t["traded"], _date_key(t["traded"]), t.get("notified", ""),
                 t["amount_low"], t["amount_high"], f.get("doc_url", "")))
        conn.commit()
        conn.close()


# ============================================================ house ingest ==

TRADE_RE = re.compile(
    r"\(([A-Z][A-Z.\-]{0,7})\)\s*\[ST\]\s*"          # ticker, stock assets only
    r"([PSE])\s*(?:\(partial\))?\s*"                  # P=buy S=sell E=exchange
    r"(\d{2}/\d{2}/\d{4})\s*(\d{2}/\d{2}/\d{4})\s*"   # trade date, notified date
    r"\$([\d,]+)\s*(?:-\s*\$?([\d,]+)|\+)"            # amount range
)
OWNER_SPLIT = re.compile(r"\b(?:SP|JT|DC)\b")


def _clean_asset_name(prefix):
    seg = OWNER_SPLIT.split(prefix)[-1]
    seg = seg.split("$200?")[-1]
    seg = re.sub(r"[^A-Za-z0-9&.,\-' ()/]", " ", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" -:,.")
    return seg[:80]


def _parse_house_pdf(docid, year):
    """Parse one House PTR PDF into a list of trades. Disk-cached as JSON."""
    jpath = os.path.join(CACHE_DIR, "trades_{}.json".format(docid))
    if os.path.exists(jpath):
        with open(jpath) as f:
            return json.load(f)
    pdf = cached_fetch("ptr_{}.pdf".format(docid),
                       PTR_PDF_URL.format(year, docid), ttl=10 ** 9)
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
                    "owner": "",
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


def house_filings(year):
    """List every PTR (FilingType 'P') filing in a year's disclosure zip."""
    zip_data = cached_fetch("{}FD.zip".format(year), FD_ZIP_URL.format(year), ttl=6 * 3600)
    if not zip_data:
        return None
    try:
        xml = zipfile.ZipFile(io.BytesIO(zip_data)).read("{}FD.xml".format(year))
    except Exception:
        return None
    root = ET.fromstring(xml.decode("utf-8-sig"))
    filings = []
    for m in root.findall("Member"):
        if m.findtext("FilingType") != "P":
            continue
        docid = m.findtext("DocID") or ""
        if not docid:
            continue
        member = "{} {}".format(m.findtext("First") or "", m.findtext("Last") or "").strip()
        filings.append({
            "id": docid,
            "chamber": "House",
            "member": member,
            "state": m.findtext("StateDst") or "",
            "filed": m.findtext("FilingDate") or "",
            "year": year,
            "doc_url": PTR_PDF_URL.format(year, docid),
        })
    return filings


def ingest_house(progress):
    done = parsed_filing_ids()
    todo = []
    for year in YEARS:
        fs = house_filings(year)
        if fs is None:
            progress["errors"].append("House {}: could not reach disclosure site".format(year))
            continue
        for f in fs:
            if f["id"] not in done:
                todo.append(f)
    progress["house_total"] = len(done) + len(todo)  # rough; refined below by count
    # Count how many House filings already parsed for accurate progress.
    conn = db()
    house_done = conn.execute("SELECT COUNT(*) c FROM filings WHERE chamber='House'").fetchone()["c"]
    conn.close()
    progress["house_total"] = house_done + len(todo)
    progress["house_parsed"] = house_done

    def work(f):
        return f, _parse_house_pdf(f["id"], f["year"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        for f, trades in pool.map(work, todo):
            store_filing(f, trades)
            progress["house_parsed"] += 1
            bump_version()


# =========================================================== senate ingest ==

def senate_session():
    """Accept the eFD prohibition agreement; return a cookie-jar path, or None."""
    jar = os.path.join(CACHE_DIR, "senate_cookies.txt")
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    home = subprocess.run(
        ["curl", "-sL", "-c", jar, "-A", ua, "--max-time", "30", SENATE_HOME],
        capture_output=True)
    if home.returncode != 0 or not home.stdout:
        return None
    m = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', home.stdout)
    if not m:
        return None
    token = m.group(1).decode()
    ok = subprocess.run(
        ["curl", "-sL", "-b", jar, "-c", jar, "-A", ua, "--max-time", "30",
         "-H", "Referer: " + SENATE_HOME,
         "--data-urlencode", "csrfmiddlewaretoken=" + token,
         "--data-urlencode", "prohibition_agreement=1",
         SENATE_HOME], capture_output=True)
    if ok.returncode != 0:
        return None
    return jar


def _senate_csrf(jar):
    try:
        with open(jar) as f:
            for line in f:
                if "csrftoken" in line:
                    return line.split()[-1].strip()
    except Exception:
        pass
    return ""


def senate_report_rows(jar):
    """All PTR report rows since YEARS[0]. Each: (member, uuid, kind, filed)."""
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    rows, start, total = [], 0, None
    while total is None or start < total:
        csrf = _senate_csrf(jar)
        args = ["curl", "-sL", "-b", jar, "-A", ua, "--max-time", "40",
                "-H", "Referer: " + SENATE_BASE + "/search/",
                "-H", "X-CSRFToken: " + csrf,
                "-H", "X-Requested-With: XMLHttpRequest",
                "--data-urlencode", "start={}".format(start),
                "--data-urlencode", "length=100",
                "--data-urlencode", "report_types=[11]",     # 11 = Periodic Transaction Report
                "--data-urlencode", "filer_types=[]",
                "--data-urlencode", "submitted_start_date=" + SENATE_REPORT_START,
                "--data-urlencode", "submitted_end_date=",
                "--data-urlencode", "candidate_state=",
                "--data-urlencode", "senator_state=",
                "--data-urlencode", "office_id=",
                "--data-urlencode", "first_name=",
                "--data-urlencode", "last_name=",
                SENATE_DATA]
        r = subprocess.run(args, capture_output=True)
        if r.returncode != 0 or not r.stdout:
            break
        try:
            data = json.loads(r.stdout.decode("utf-8", "replace"))
        except Exception:
            break
        total = data.get("recordsTotal", 0)
        batch = data.get("data", [])
        if not batch:
            break
        for row in batch:
            first = (row[0] or "").strip()
            last = (row[1] or "").strip().strip(",")
            member = re.sub(r"\s+", " ", "{} {}".format(first, last)).strip()
            link_html = row[3] or ""
            filed = (row[4] or "").strip()
            m = re.search(r'href="/search/view/(ptr|paper)/([0-9a-f\-]+)/', link_html)
            if not m:
                continue
            rows.append({"member": member, "kind": m.group(1),
                         "uuid": m.group(2), "filed": filed})
        start += len(batch)
        if start > 5000:
            break
    return rows


SEN_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
SEN_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
SEN_TAG_RE = re.compile(r"<[^>]+>")
SEN_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")


def _cell_text(c):
    return html.unescape(re.sub(r"\s+", " ", SEN_TAG_RE.sub(" ", c))).strip()


def _senate_amount(s):
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", s.replace("$", ""))
            if n.replace(",", "").isdigit()]
    if not nums:
        return 0, 0
    if len(nums) == 1:
        return nums[0], nums[0]
    return nums[0], nums[1]


def _senate_side(t):
    t = t.lower()
    if "purchase" in t:
        return "BUY"
    if "sale" in t:
        return "SELL"
    if "exchange" in t:
        return "EXCHANGE"
    return None


def _senate_owner(o):
    o = (o or "").lower()
    if "joint" in o:
        return "JOINT"
    if "spouse" in o:
        return "SPOUSE"
    if "child" in o:
        return "CHILD"
    if "self" in o:
        return "SELF"
    return ""


def _parse_senate_report(uuid, jar):
    """Parse one electronic Senate PTR report into a list of trades. Cached JSON."""
    jpath = os.path.join(CACHE_DIR, "sen_trades_{}.json".format(uuid))
    if os.path.exists(jpath):
        with open(jpath) as f:
            return json.load(f)
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    raw = cached_fetch(
        "sen_{}.html".format(uuid),
        SENATE_BASE + "/search/view/ptr/{}/".format(uuid), ttl=10 ** 9) or b""
    # cached_fetch above uses the default UA; the report needs the session cookie,
    # so if the cache miss produced a login/redirect, refetch with the cookie jar.
    if b"Transaction" not in raw:
        got = subprocess.run(
            ["curl", "-sL", "-b", jar, "-A", ua, "--max-time", "40",
             "-H", "Referer: " + SENATE_BASE + "/search/",
             SENATE_BASE + "/search/view/ptr/{}/".format(uuid)],
            capture_output=True)
        if got.returncode == 0 and got.stdout:
            raw = got.stdout
            with open(os.path.join(CACHE_DIR, "sen_{}.html".format(uuid)), "wb") as f:
                f.write(raw)
    trades = []
    try:
        text = raw.decode("utf-8", "replace")
        for tr in SEN_TR_RE.findall(text):
            cells = [_cell_text(c) for c in SEN_TD_RE.findall(tr)]
            # columns: #, Date, Owner, Ticker, Asset, Asset Type, Type, Amount, Comment
            if len(cells) < 8 or not re.match(r"\d+", cells[0] or ""):
                continue
            date, owner, ticker, asset, atype, ttype, amount = cells[1:8]
            if not SEN_TICKER_RE.match(ticker or ""):
                continue
            if not (atype or "").lower().startswith("stock"):
                continue
            side = _senate_side(ttype)
            if not side:
                continue
            low, high = _senate_amount(amount)
            trades.append({
                "ticker": ticker,
                "asset": asset[:80],
                "side": side,
                "owner": _senate_owner(owner),
                "traded": date,
                "notified": "",
                "amount_low": low,
                "amount_high": high,
            })
    except Exception:
        trades = []
    with open(jpath, "w") as f:
        json.dump(trades, f)
    return trades


def ingest_senate(progress):
    jar = senate_session()
    if not jar:
        progress["errors"].append("Senate eFD: could not open a session (site may be "
                                  "blocking automated access right now).")
        progress["senate_ok"] = False
        return
    progress["senate_ok"] = True
    rows = senate_report_rows(jar)
    done = parsed_filing_ids()
    conn = db()
    sen_done = conn.execute("SELECT COUNT(*) c FROM filings WHERE chamber='Senate'").fetchone()["c"]
    conn.close()
    todo = [r for r in rows if r["uuid"] not in done]
    progress["senate_total"] = sen_done + len(todo)
    progress["senate_parsed"] = sen_done

    for r in todo:
        f = {"id": r["uuid"], "chamber": "Senate", "member": r["member"], "state": "",
             "filed": r["filed"], "year": (r["filed"] or "")[-4:],
             "doc_url": SENATE_BASE + "/search/view/{}/{}/".format(r["kind"], r["uuid"])}
        if r["kind"] == "paper":
            f["paper"] = True
            store_filing(f, [])
        else:
            trades = _parse_senate_report(r["uuid"], jar)
            store_filing(f, trades)
        progress["senate_parsed"] += 1
        bump_version()


# ====================================================== ingest orchestration ==

_ingest_lock = threading.Lock()
_ingest_version = 0
_ingest_state = {"ingesting": False, "phase": "idle", "errors": [],
                 "house_parsed": 0, "house_total": 0,
                 "senate_parsed": 0, "senate_total": 0, "senate_ok": None,
                 "started": 0, "finished": 0}


def bump_version():
    global _ingest_version
    _ingest_version += 1


def ingest_all():
    global _ingest_state
    if not _ingest_lock.acquire(blocking=False):
        return  # an ingest is already running
    try:
        init_db()
        _ingest_state.update({"ingesting": True, "phase": "house", "errors": [],
                              "started": int(time.time()), "finished": 0,
                              "senate_ok": None})
        try:
            ingest_house(_ingest_state)
        except Exception as e:
            _ingest_state["errors"].append("House ingest: {}: {}".format(type(e).__name__, e))
        _ingest_state["phase"] = "senate"
        try:
            ingest_senate(_ingest_state)
        except Exception as e:
            _ingest_state["errors"].append("Senate ingest: {}: {}".format(type(e).__name__, e))
        _ingest_state["phase"] = "done"
        _ingest_state["finished"] = int(time.time())
        set_meta("last_ingest", int(time.time()))
        bump_version()
    finally:
        _ingest_state["ingesting"] = False
        _ingest_lock.release()


def start_ingest_background():
    t = threading.Thread(target=ingest_all, daemon=True)
    t.start()


def build_status():
    conn = db()
    row = conn.execute(
        "SELECT COUNT(*) trades,"
        " (SELECT COUNT(DISTINCT member_key) FROM trades) members,"
        " (SELECT COUNT(*) FROM filings WHERE chamber='House') house_filings,"
        " (SELECT COUNT(*) FROM filings WHERE chamber='Senate') senate_filings,"
        " (SELECT COUNT(*) FROM filings WHERE paper=1) paper_filings"
        " FROM trades").fetchone()
    conn.close()
    s = dict(_ingest_state)
    s.update({
        "db_trades": row["trades"], "db_members": row["members"],
        "house_filings": row["house_filings"], "senate_filings": row["senate_filings"],
        "paper_filings": row["paper_filings"],
        "last_ingest": int(get_meta("last_ingest", 0) or 0),
        "years": list(YEARS), "window_days": SIGNAL_WINDOW_DAYS,
    })
    return s


# ============================================================ trades / DB ===

def _row(t):
    d = dict(t)
    d["amount_low"] = int(d.get("amount_low") or 0)
    d["amount_high"] = int(d.get("amount_high") or 0)
    return d


def build_trades():
    """Summary counts + top aggregates (full DB) + most-recent trades page."""
    conn = db()
    counts = conn.execute(
        "SELECT COUNT(*) trades,"
        " (SELECT COUNT(*) FROM filings WHERE chamber='House') house_filings,"
        " (SELECT COUNT(*) FROM filings WHERE chamber='Senate') senate_filings,"
        " (SELECT COUNT(*) FROM filings WHERE paper=1) paper_filings,"
        " (SELECT COUNT(DISTINCT member_key) FROM trades) members"
        " FROM trades").fetchone()

    def top(side):
        rows = conn.execute(
            "SELECT ticker, COUNT(*) count,"
            " SUM((amount_low+amount_high)/2) est_total,"
            " COUNT(DISTINCT member_key) members"
            " FROM trades WHERE side=? GROUP BY ticker"
            " ORDER BY count DESC, est_total DESC LIMIT 10", (side,)).fetchall()
        return [dict(r) for r in rows]

    recent = [_row(r) for r in conn.execute(
        "SELECT member,member_key,state,chamber,ticker,asset,side,owner,traded,"
        "traded_key,amount_low,amount_high,doc_url FROM trades"
        " ORDER BY traded_key DESC, id DESC LIMIT 600").fetchall()]
    top_buys, top_sells = top("BUY"), top("SELL")
    conn.close()
    return {
        "updated": int(time.time()),
        "total_trades": counts["trades"],
        "house_filings": counts["house_filings"],
        "senate_filings": counts["senate_filings"],
        "paper_filings": counts["paper_filings"],
        "members": counts["members"],
        "recent_limit": 600,
        "trades": recent,
        "top_buys": top_buys,
        "top_sells": top_sells,
        "status": build_status(),
    }


def trades_list(q="", side="", chamber="", limit=400):
    conn = db()
    where, params = [], []
    if side in ("BUY", "SELL", "EXCHANGE"):
        where.append("side=?"); params.append(side)
    if chamber in ("House", "Senate"):
        where.append("chamber=?"); params.append(chamber)
    if q:
        where.append("(UPPER(ticker) LIKE ? OR member_key LIKE ?)")
        params.append("%" + q.upper() + "%")
        params.append("%" + member_key(q) + "%")
    sql = ("SELECT member,member_key,state,chamber,ticker,asset,side,owner,traded,"
           "traded_key,amount_low,amount_high,doc_url FROM trades")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY traded_key DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = [_row(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"trades": rows, "count": len(rows), "limit": int(limit)}


def member_profile(key):
    conn = db()
    trows = [_row(r) for r in conn.execute(
        "SELECT member,member_key,state,chamber,ticker,asset,side,owner,traded,"
        "traded_key,notified,amount_low,amount_high,doc_url FROM trades"
        " WHERE member_key=? ORDER BY traded_key DESC, id DESC", (key,)).fetchall()]
    if not trows:
        conn.close()
        return {"error": "No trades on record for that member."}
    frows = [dict(r) for r in conn.execute(
        "SELECT id,chamber,filed,filed_key,doc_url,trade_count,paper FROM filings"
        " WHERE member_key=? ORDER BY filed_key DESC", (key,)).fetchall()]
    conn.close()

    name = trows[0]["member"]
    chambers = sorted({t["chamber"] for t in trows})
    states = sorted({t["state"] for t in trows if t["state"]})
    buys = [t for t in trows if t["side"] == "BUY"]
    sells = [t for t in trows if t["side"] == "SELL"]
    mid = lambda t: (t["amount_low"] + t["amount_high"]) // 2
    buy_est = sum(mid(t) for t in buys)
    sell_est = sum(mid(t) for t in sells)

    agg = {}
    for t in trows:
        a = agg.setdefault(t["ticker"], {"ticker": t["ticker"], "count": 0,
                                         "buy_est": 0, "sell_est": 0, "last": ""})
        a["count"] += 1
        if t["side"] == "BUY":
            a["buy_est"] += mid(t)
        elif t["side"] == "SELL":
            a["sell_est"] += mid(t)
        if t["traded_key"] > a["last"]:
            a["last"] = t["traded_key"]
    top_tickers = sorted(agg.values(), key=lambda a: (a["count"], a["buy_est"] + a["sell_est"]),
                         reverse=True)[:12]

    dates = [t["traded_key"] for t in trows if t["traded_key"] > "0000-00-00"]
    return {
        "key": key, "name": name, "chambers": chambers, "states": states,
        "totals": {
            "trades": len(trows), "buys": len(buys), "sells": len(sells),
            "buy_est": buy_est, "sell_est": sell_est,
            "est_volume": buy_est + sell_est,
            "first_trade": min(dates) if dates else "",
            "last_trade": max(dates) if dates else "",
            "filings": len(frows),
        },
        "top_tickers": top_tickers,
        "filings": frows[:60],
        "trades": trows[:400],
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


def _window_key(days):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))


def build_signals():
    bills_data = mem_cached("bills", 1800, build_bills)
    news_data = mem_cached("news", 900, build_news)

    since = _window_key(SIGNAL_WINDOW_DAYS)
    conn = db()
    # Aggregate congressional interest per ticker over the recent window.
    rows = conn.execute(
        "SELECT ticker, side, member_key, member, asset, chamber,"
        " (amount_low+amount_high)/2 mid, traded_key FROM trades"
        " WHERE traded_key >= ?", (since,)).fetchall()
    conn.close()

    agg = {}
    for r in rows:
        a = agg.setdefault(r["ticker"], {
            "ticker": r["ticker"], "asset": r["asset"], "buyers": {}, "sellers": set(),
            "buy_total": 0, "sell_total": 0, "last_trade": "", "chambers": set()})
        if not a["asset"]:
            a["asset"] = r["asset"]
        if r["side"] == "BUY":
            a["buyers"][r["member_key"]] = r["member"]
            a["buy_total"] += r["mid"]
            a["chambers"].add(r["chamber"])
        elif r["side"] == "SELL":
            a["sellers"].add(r["member_key"])
            a["sell_total"] += r["mid"]
        if r["traded_key"] > a["last_trade"]:
            a["last_trade"] = r["traded_key"]

    # policy activity per sector from bills + news sector tags
    sector_hits = {s: {"bills": 0, "news": 0} for s in SECTORS}
    for b in bills_data.get("bills", []):
        for s in b["sectors"]:
            sector_hits[s["sector"]]["bills"] += 1
    for n in news_data.get("items", []):
        for s in n["sectors"]:
            sector_hits[s["sector"]]["news"] += 1

    candidates = sorted(
        [a for a in agg.values() if a["buyers"]],
        key=lambda a: (len(a["buyers"]), a["buy_total"]), reverse=True)[:14]

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
            "buyer_names": [{"name": n, "key": k} for k, n in list(a["buyers"].items())[:8]],
            "chambers": sorted(a["chambers"]),
            "last_trade": a["last_trade"],
            "bills_hits": hits["bills"], "news_hits": hits["news"],
            "r1m": r1m, "r3m": r3m,
            "name": hist.get("name", a["ticker"]) if "error" not in hist else a["ticker"],
        })
    signals.sort(key=lambda s: s["score"], reverse=True)
    return {"updated": int(time.time()), "spx_r1m": spx_r1m, "spx_r3m": spx_r3m,
            "window_days": SIGNAL_WINDOW_DAYS, "since": since, "signals": signals}


# ----------------------------------------------------------------- brief ----

def build_brief():
    bills_data = mem_cached("bills", 1800, build_bills)
    news_data = mem_cached("news", 900, build_news)
    week_ago = _window_key(7)

    spx = _hist_cached("^GSPC", "1mo")
    spx_close = spx_week = None
    if "error" not in spx and len(spx.get("closes", [])) >= 6:
        spx_close = spx["closes"][-1]
        spx_week = spx["closes"][-1] / spx["closes"][-6] - 1  # 5 trading days

    conn = db()
    new_filings = conn.execute(
        "SELECT COUNT(*) c FROM filings WHERE filed_key >= ?", (week_ago,)).fetchone()["c"]
    top_buy = conn.execute(
        "SELECT t.ticker, COUNT(*) c FROM trades t JOIN filings f ON t.filing_id=f.id"
        " WHERE f.filed_key >= ? AND t.side='BUY' GROUP BY t.ticker"
        " ORDER BY c DESC LIMIT 1", (week_ago,)).fetchone()
    top_sell = conn.execute(
        "SELECT t.ticker, COUNT(*) c FROM trades t JOIN filings f ON t.filing_id=f.id"
        " WHERE f.filed_key >= ? AND t.side='SELL' GROUP BY t.ticker"
        " ORDER BY c DESC LIMIT 1", (week_ago,)).fetchone()
    conn.close()

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
        "new_filings": new_filings,
        "top_buy": {"ticker": top_buy["ticker"], "count": top_buy["c"]} if top_buy else None,
        "top_sell": {"ticker": top_sell["ticker"], "count": top_sell["c"]} if top_sell else None,
        "bills_moved": len(week_bills),
        "highlight_bills": [{"number": b["number"], "title": b["title"][:120],
                             "status": b["status"], "link": b["link"],
                             "sectors": b["sectors"]} for b in tagged_bills],
        "top_news_sector": {"sector": top_sector[0], "count": top_sector[1]} if top_sector else None,
    }


# ---------------------------------------------------------------- alerts ----

def build_alerts(since):
    """What changed in the trade database since `since` (YYYY-MM-DD)."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", since or ""):
        since = _window_key(7)
    conn = db()
    filings = conn.execute(
        "SELECT COUNT(*) c FROM filings WHERE filed_key >= ?", (since,)).fetchone()["c"]
    trows = [_row(r) for r in conn.execute(
        "SELECT t.member,t.member_key,t.state,t.chamber,t.ticker,t.asset,t.side,"
        "t.traded,t.amount_low,t.amount_high,t.doc_url, f.filed_key"
        " FROM trades t JOIN filings f ON t.filing_id=f.id"
        " WHERE f.filed_key >= ? ORDER BY (t.amount_low+t.amount_high) DESC, f.filed_key DESC"
        " LIMIT 40", (since,)).fetchall()]
    total_new = conn.execute(
        "SELECT COUNT(*) c FROM trades t JOIN filings f ON t.filing_id=f.id"
        " WHERE f.filed_key >= ?", (since,)).fetchone()["c"]

    def top(side):
        rows = conn.execute(
            "SELECT t.ticker, COUNT(*) count, SUM((t.amount_low+t.amount_high)/2) est_total,"
            " COUNT(DISTINCT t.member_key) members"
            " FROM trades t JOIN filings f ON t.filing_id=f.id"
            " WHERE f.filed_key >= ? AND t.side=? GROUP BY t.ticker"
            " ORDER BY count DESC, est_total DESC LIMIT 6", (since, side)).fetchall()
        return [dict(r) for r in rows]
    top_buys, top_sells = top("BUY"), top("SELL")
    conn.close()
    return {"since": since, "new_filings": filings, "new_trades": total_new,
            "top_buys": top_buys, "top_sells": top_sells,
            "notable": trows[:20], "updated": int(time.time())}


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


def _v(key):  # version-scoped mem cache key so new data busts the cache
    return "{}_{}".format(key, _ingest_version)


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
            if path == "/api/status":
                self.send_json(build_status())
            elif path == "/api/trades":
                self.send_json(mem_cached(_v("trades"), 300, build_trades))
            elif path == "/api/trades/list":
                q = (qs.get("q") or [""])[0][:40]
                side = (qs.get("side") or [""])[0]
                chamber = (qs.get("chamber") or [""])[0]
                limit = min(1000, max(1, int((qs.get("limit") or ["400"])[0] or 400)))
                self.send_json(trades_list(q, side, chamber, limit))
            elif path == "/api/member":
                key = (qs.get("key") or [""])[0] or member_key((qs.get("name") or [""])[0])
                if not key:
                    self.send_json({"error": "no member specified"}, 400)
                    return
                self.send_json(member_profile(key))
            elif path == "/api/signals":
                self.send_json(mem_cached(_v("signals"), 900, build_signals))
            elif path == "/api/brief":
                self.send_json(mem_cached(_v("brief"), 900, build_brief))
            elif path == "/api/alerts":
                since = (qs.get("since") or [""])[0]
                self.send_json(mem_cached(_v("alerts_" + since), 300,
                                          lambda: build_alerts(since)))
            elif path == "/api/bills":
                self.send_json(mem_cached("bills", 1800, build_bills))
            elif path == "/api/news":
                self.send_json(mem_cached("news", 900, build_news))
            elif path == "/api/history":
                symbol = (qs.get("symbol") or ["^GSPC"])[0]
                rng = (qs.get("range") or ["5y"])[0]
                if not SYMBOL_RE.match(symbol) or rng not in RANGES:
                    self.send_json({"error": "bad symbol or range"}, 400)
                    return
                key = "hist_{}_{}".format(re.sub(r"[^A-Za-z0-9]", "_", symbol), rng)
                self.send_json(mem_cached(key, 3600,
                                          lambda: build_history(symbol, rng)))
            elif path == "/api/reingest":
                start_ingest_background()
                self.send_json({"ok": True})
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
    init_db()
    start_ingest_background()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Capitol Gains running at http://localhost:{}".format(PORT))
    print("Ingesting House + Senate disclosures in the background (first run ~a few minutes).")
    server.serve_forever()


if __name__ == "__main__":
    main()
