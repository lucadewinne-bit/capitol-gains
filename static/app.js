/* PoliTrend Research — dashboard logic */
"use strict";

// ------------------------------------------------------------- utilities --
const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}
const fmtUSD = n => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
const fmtUSD2 = n => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = n => (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%";

const jsonCache = {};
async function getJSON(url) {
  if (jsonCache[url]) return jsonCache[url];
  const res = await fetch(url);
  const data = await res.json();
  if (data && data.error) throw new Error(data.error);
  jsonCache[url] = data;
  return data;
}

const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ------------------------------------------------------------ line chart --
// series: [{name, colorVar, values:[num|null]}], one shared dates[] axis.
function lineChart(container, dates, series, opts) {
  opts = opts || {};
  const W = 820, H = 300;
  const padL = 58, padT = 16, padB = 30;
  const padR = series.length >= 2 ? 110 : 18;
  const iw = W - padL - padR, ih = H - padT - padB;

  let min = Infinity, max = -Infinity;
  series.forEach(s => s.values.forEach(v => {
    if (v != null) { if (v < min) min = v; if (v > max) max = v; }
  }));
  if (!isFinite(min)) { container.innerHTML = '<p class="loading">No data.</p>'; return; }
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  min -= span * 0.06; max += span * 0.06;

  const x = i => padL + (dates.length < 2 ? 0 : (i / (dates.length - 1)) * iw);
  const y = v => padT + ih - ((v - min) / (max - min)) * ih;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "line chart")}">`;
  // gridlines + y ticks
  const ticks = 4;
  for (let t = 0; t <= ticks; t++) {
    const v = min + ((max - min) * t) / ticks;
    const yy = y(v);
    svg += `<line class="gridline" x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}"/>`;
    svg += `<text class="axistext" x="${padL - 8}" y="${yy + 3.5}" text-anchor="end">${esc(opts.yFmt ? opts.yFmt(v) : Math.round(v).toLocaleString())}</text>`;
  }
  svg += `<line class="axisline" x1="${padL}" x2="${W - padR}" y1="${padT + ih}" y2="${padT + ih}"/>`;
  // x labels (~5)
  const nx = Math.min(5, dates.length);
  for (let t = 0; t < nx; t++) {
    const i = Math.round((t / Math.max(1, nx - 1)) * (dates.length - 1));
    const anchor = t === 0 ? "start" : t === nx - 1 ? "end" : "middle";
    svg += `<text class="axistext" x="${x(i)}" y="${H - 10}" text-anchor="${anchor}">${esc(shortDate(dates[i]))}</text>`;
  }
  // series paths (2px lines)
  series.forEach(s => {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " ";
      pen = true;
    });
    svg += `<path d="${d}" fill="none" stroke="var(${s.colorVar})" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  // direct labels at line ends (only when >= 2 series)
  if (series.length >= 2) {
    const placed = [];
    series.forEach(s => {
      let li = s.values.length - 1;
      while (li >= 0 && s.values[li] == null) li--;
      if (li < 0) return;
      let ly = y(s.values[li]);
      placed.forEach(p => { if (Math.abs(p - ly) < 14) ly = p + (ly >= p ? 14 : -14); });
      placed.push(ly);
      svg += `<text class="serieslabel" fill="var(${s.colorVar})" x="${W - padR + 8}" y="${ly + 4}">${esc(s.name)}</text>`;
    });
  }
  svg += `<line id="xh" class="crosshair" y1="${padT}" y2="${padT + ih}" x1="-10" x2="-10" style="display:none"/>`;
  series.forEach((s, si) => {
    svg += `<circle id="dot${si}" r="4" fill="var(${s.colorVar})" stroke="var(--surface)" stroke-width="2" style="display:none"/>`;
  });
  svg += `<rect id="hover" x="${padL}" y="${padT}" width="${iw}" height="${ih}" fill="transparent"/>`;
  svg += `</svg>`;

  let html = svg;
  if (series.length >= 2) {
    html += `<div class="legend">` + series.map(s =>
      `<span><span class="chip" style="background:var(${s.colorVar})"></span>${esc(s.name)}</span>`).join("") + `</div>`;
  }
  container.innerHTML = html;

  // hover layer: crosshair + tooltip
  const svgEl = $("svg", container);
  const hover = $("#hover", container);
  const xh = $("#xh", container);
  const tip = $("#tooltip");
  const dots = series.map((_, si) => $("#dot" + si, container));

  function onMove(evt) {
    const rect = svgEl.getBoundingClientRect();
    const sx = ((evt.clientX - rect.left) / rect.width) * W;
    const frac = (sx - padL) / iw;
    const i = Math.max(0, Math.min(dates.length - 1, Math.round(frac * (dates.length - 1))));
    const xx = x(i);
    xh.setAttribute("x1", xx); xh.setAttribute("x2", xx);
    xh.style.display = "";
    let rows = `<div class="t-date">${esc(dates[i])}</div>`;
    series.forEach((s, si) => {
      const v = s.values[i];
      if (v == null) { dots[si].style.display = "none"; return; }
      dots[si].setAttribute("cx", xx); dots[si].setAttribute("cy", y(v));
      dots[si].style.display = "";
      rows += `<div class="t-row"><span class="chip" style="background:var(${s.colorVar})"></span>` +
              `${esc(s.name)}: <b>${esc(opts.yFmt ? opts.yFmt(v) : v.toLocaleString())}</b></div>`;
    });
    tip.innerHTML = rows;
    tip.classList.remove("hidden");
    const tw = tip.offsetWidth;
    let left = evt.clientX + 14;
    if (left + tw > window.innerWidth - 8) left = evt.clientX - tw - 14;
    tip.style.left = left + "px";
    tip.style.top = Math.max(8, evt.clientY - 20) + "px";
  }
  function onLeave() {
    xh.style.display = "none";
    dots.forEach(d => d.style.display = "none");
    tip.classList.add("hidden");
  }
  hover.addEventListener("mousemove", onMove);
  hover.addEventListener("mouseleave", onLeave);
}

function shortDate(iso) {
  const [y, m] = iso.split("-");
  const mon = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1];
  return mon + " " + y.slice(2);
}

// ------------------------------------------------------- jargon tooltips --
const TERMS = {
  ticker: "A stock's short symbol on the exchange — NVDA is Nvidia, AAPL is Apple.",
  etf: "Exchange-Traded Fund: one share that holds a whole basket of stocks, so your money is spread across many companies at once.",
  dividend: "A cash payment some companies send shareholders (usually quarterly) just for holding the stock.",
  diversification: "Spreading money across many stocks/sectors so one bad pick can't sink you.",
  dca: "Dollar-cost averaging: investing the same amount on a schedule regardless of price — buys more shares when cheap, fewer when expensive.",
  momentum: "How a price has been trending recently. Here: the stock's 3-month move compared to the S&P 500's.",
  stockact: "2012 law requiring members of Congress to publicly disclose their stock trades within 45 days.",
  ptr: "Periodic Transaction Report — the official form a member of Congress files to disclose a trade.",
  benchmark: "The yardstick you compare results against — here, putting the same money into the S&P 500 instead.",
};
function showTermTip(el) {
  const def = TERMS[el.dataset.term];
  if (!def) return;
  const tip = $("#tooltip");
  tip.innerHTML = `<div>${esc(def)}</div>`;
  tip.classList.remove("hidden");
  const r = el.getBoundingClientRect();
  let left = r.left;
  if (left + tip.offsetWidth > window.innerWidth - 8) left = window.innerWidth - tip.offsetWidth - 8;
  tip.style.left = left + "px";
  tip.style.top = (r.bottom + 6) + "px";
}
document.addEventListener("mouseover", e => {
  const t = e.target.closest && e.target.closest(".term");
  if (t) showTermTip(t);
});
document.addEventListener("mouseout", e => {
  if (e.target.closest && e.target.closest(".term")) $("#tooltip").classList.add("hidden");
});

// ----------------------------------------------------------------- tabs --
const loaded = {};
function activateTab(name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tabpanel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  loadTab(name);
}
$("#tabs").addEventListener("click", e => {
  const btn = e.target.closest(".tab");
  if (btn) activateTab(btn.dataset.tab);
});
function loadTab(name) {
  if (loaded[name]) return;
  loaded[name] = true;
  ({ overview: loadOverview, signals: loadSignals, trades: loadTrades,
     bills: loadBills, news: loadNews, plan: loadPlan }[name] || (() => {}))();
}

// -------------------------------------------------------------- overview --
let spxRange = "1y";
async function drawSpx() {
  const el = $("#spx-chart");
  el.innerHTML = '<p class="loading">Loading S&amp;P 500 data&hellip;</p>';
  try {
    const h = await getJSON(`/api/history?symbol=%5EGSPC&range=${spxRange}`);
    lineChart(el, h.dates, [{ name: "S&P 500", colorVar: "--series-1", values: h.closes }],
      { yFmt: v => Math.round(v).toLocaleString(), label: "S&P 500 closing price" });
    renderOverviewTiles(h);
  } catch (err) {
    el.innerHTML = `<p class="error">Could not load market data: ${esc(err.message)}</p>`;
  }
}
function renderOverviewTiles(h) {
  const last = h.closes[h.closes.length - 1];
  const prev = h.closes[h.closes.length - 2] || last;
  const first = h.closes[0];
  const tiles = [
    { label: "S&P 500 close", value: last.toLocaleString("en-US", { maximumFractionDigits: 0 }) },
    { label: "1-day change", value: fmtPct((last - prev) / prev), delta: last - prev >= 0 ? "up" : "down" },
    { label: spxRange.toUpperCase() + " return", value: fmtPct((last - first) / first), delta: last - first >= 0 ? "up" : "down" },
    { label: "Congress PTR filings (YTD)", value: window._ptrCount || "…", id: "tile-ptr" },
  ];
  $("#ov-tiles").innerHTML = tiles.map(t =>
    `<div class="tile"${t.id ? ` id="${t.id}"` : ""}><div class="label">${esc(t.label)}</div>` +
    `<div class="value ${t.delta ? "delta " + t.delta : ""}">${esc(t.value)}</div></div>`).join("");
}
$("#spx-ranges").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#spx-ranges button").forEach(x => x.classList.toggle("active", x === b));
  spxRange = b.dataset.range;
  drawSpx();
});

function renderBrief(b) {
  const lines = [];
  if (b.spx_close != null) {
    const dir = b.spx_week >= 0 ? "up" : "down";
    lines.push(`The <b>S&P 500</b> closed at <b>${Math.round(b.spx_close).toLocaleString()}</b>, ` +
      `<b class="${b.spx_week >= 0 ? "up-txt" : "down-txt"}">${dir} ${(Math.abs(b.spx_week) * 100).toFixed(1)}%</b> over the past trading week.`);
  }
  if (b.new_filings) {
    let s = `Congress filed <b>${b.new_filings}</b> new trade disclosure${b.new_filings > 1 ? "s" : ""} this week`;
    if (b.top_buy) s += ` — most bought: <b>${esc(b.top_buy.ticker)}</b> (${b.top_buy.count}×)`;
    if (b.top_sell) s += `, most sold: <b>${esc(b.top_sell.ticker)}</b> (${b.top_sell.count}×)`;
    lines.push(s + ".");
  } else {
    lines.push("No new congressional trade filings in the parsed set this week.");
  }
  if (b.bills_moved) {
    let s = `<b>${b.bills_moved}</b> bill${b.bills_moved > 1 ? "s" : ""} changed status this week`;
    if (b.highlight_bills.length) {
      s += ", including " + b.highlight_bills.map(hb =>
        `<a href="${esc(hb.link)}" target="_blank" rel="noopener">${esc(hb.number)}</a>` +
        ` (${hb.sectors.map(x => esc(x.sector)).join(", ")})`).join("; ");
    }
    lines.push(s + ".");
  }
  if (b.top_news_sector) {
    lines.push(`Most active sector in political news right now: <b>${esc(b.top_news_sector.sector)}</b> ` +
      `(${b.top_news_sector.count} stories).`);
  }
  $("#brief-body").innerHTML = lines.map(l => `<p class="brief-line">${l}</p>`).join("") +
    `<p class="note">Auto-written from this week&rsquo;s data — see the Signals tab for what it adds up to.</p>`;
}

async function loadOverview() {
  drawSpx();
  getJSON("/api/brief").then(renderBrief)
    .catch(e => { $("#brief-body").innerHTML = `<p class="error">${esc(e.message)}</p>`; });
  getJSON("/api/news").then(n => {
    $("#ov-news").innerHTML = n.items.slice(0, 6).map(newsItemHTML).join("") || "No headlines.";
  }).catch(e => { $("#ov-news").innerHTML = `<p class="error">${esc(e.message)}</p>`; });
  getJSON("/api/trades").then(t => {
    window._ptrCount = String(t.total_ptr_filings_this_year);
    const tile = $("#tile-ptr .value"); if (tile) tile.textContent = window._ptrCount;
    $("#ov-topbuys").innerHTML = hbarsHTML(t.top_buys.slice(0, 6)) ||
      '<p class="loading">No parsed buys in the latest filings.</p>';
  }).catch(e => { $("#ov-topbuys").innerHTML = `<p class="error">${esc(e.message)}</p>`; });
}

function hbarsHTML(rows) {
  if (!rows || !rows.length) return "";
  const maxC = Math.max(...rows.map(r => r.count));
  return rows.map(r =>
    `<div class="hbar-row" title="${r.count} trade(s) by ${r.members} member(s), est. total ${fmtUSD(r.est_total)}">` +
    `<span class="hbar-tick">${esc(r.ticker)}</span>` +
    `<span class="hbar-track"><span class="hbar-fill" style="width:${(r.count / maxC) * 100}%"></span></span>` +
    `<span class="hbar-val">${r.count}× · est ${fmtUSD(r.est_total)}</span></div>`).join("");
}

// --------------------------------------------------------------- signals --
const pct1 = v => (v >= 0 ? "+" : "−") + (Math.abs(v) * 100).toFixed(1) + "%";

function signalWhy(s, spx3m) {
  const why = [];
  if (s.buyers) {
    let line = `<b>${s.buyers}</b> member${s.buyers > 1 ? "s" : ""} of Congress recently bought ` +
      `an estimated <b>${fmtUSD(s.buy_total)}</b>`;
    if (s.sellers) line += ` — but ${s.sellers} sold (est ${fmtUSD(s.sell_total)})`;
    why.push(line + ".");
  }
  if (s.sector) {
    why.push(`Its sector, <b>${esc(s.sector)}</b>, showed up in <b>${s.bills_hits}</b> recent bill${s.bills_hits === 1 ? "" : "s"} ` +
      `and <b>${s.news_hits}</b> news stor${s.news_hits === 1 ? "y" : "ies"} we track.`);
  } else {
    why.push(`Sector not in our tracking map — no policy points for this one.`);
  }
  if (s.r3m != null && spx3m != null) {
    why.push(`Price is <b>${pct1(s.r3m)}</b> over 3 months, vs <b>${pct1(spx3m)}</b> for the S&P 500.`);
  }
  return why;
}
function signalRisks(s) {
  const risks = ["Congress trades are disclosed up to 45 days late — the price may already reflect this news."];
  if (s.buyers === 1) risks.push("The whole congressional signal here rests on a single member's filing.");
  if (s.sellers >= s.buyers && s.sellers > 0) risks.push("As many members sold as bought — the signal is mixed.");
  if (s.parts.momentum >= 25) risks.push("A big recent run-up can also mean you're arriving late.");
  if (s.parts.momentum <= 5) risks.push("The price has been falling hard vs the market — attention isn't the same as recovery.");
  if (!s.sector) risks.push("Without sector tracking, no policy tailwind is confirmed.");
  return risks;
}
function partBar(label, val, max) {
  return `<div class="part"><span class="part-label">${label}</span>` +
    `<span class="part-track"><span class="part-fill" style="width:${(val / max) * 100}%"></span></span>` +
    `<span class="part-val">${val}/${max}</span></div>`;
}
async function loadSignals() {
  const el = $("#signals-list");
  try {
    const d = await getJSON("/api/signals");
    if (!d.signals.length) { el.innerHTML = '<p class="loading">No signals yet — no parsed congressional buys.</p>'; return; }
    el.innerHTML = d.signals.map((s, i) => {
      const why = signalWhy(s, d.spx_r3m).map(w => `<li>${w}</li>`).join("");
      const risks = signalRisks(s).map(r => `<li>${esc(r)}</li>`).join("");
      return `<div class="card signal-card">
        <div class="signal-head">
          <div class="signal-rank">#${i + 1}</div>
          <div class="signal-id">
            <div><span class="ticker big">${esc(s.ticker)}</span> <span class="sub">${esc(s.name)}</span></div>
            <div class="meta">${s.sector ? esc(s.sector) + " · sector ETFs to research: " + esc(s.sector_etfs) : "sector untracked"}</div>
          </div>
          <div class="score-badge" title="Signal strength out of 100">${s.score}</div>
        </div>
        <div class="parts">
          ${partBar("Congress buying", s.parts.congress, 40)}
          ${partBar("Policy activity", s.parts.policy, 30)}
          ${partBar("Momentum vs S&P", s.parts.momentum, 30)}
        </div>
        <div class="whywrap"><h4>Why it's getting attention</h4><ul class="why">${why}</ul></div>
        <div class="whywrap"><h4>What could go wrong</h4><ul class="why risks">${risks}</ul></div>
        <div class="signal-foot">
          <button class="ghost" data-goto-trades="${esc(s.ticker)}">See the actual trades &rarr;</button>
        </div>
      </div>`;
    }).join("") +
    `<p class="note">Scoring, openly: up to 40 pts for congressional buying (10 per distinct buyer, +5 if
     estimated total &ge; $50k, &minus;8 per seller), up to 30 pts for bills + news activity in the sector
     (3 per item), and up to 30 pts for 3-month price <span class="term" data-term="momentum">momentum</span>
     (15 = moving with the market, &plusmn;1 per percentage point vs the S&P 500).</p>`;
  } catch (e) {
    el.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-goto-trades]");
  if (!b) return;
  activateTab("trades");
  $("#trade-search").value = b.dataset.gotoTrades;
  if (allTrades.length) renderTradesTable();
});

// ---------------------------------------------------------------- trades --
let allTrades = [];
async function loadTrades() {
  try {
    const t = await getJSON("/api/trades");
    allTrades = t.trades;
    $("#trades-meta").innerHTML =
      `${t.total_ptr_filings_this_year} trade filings so far this year. Showing the ` +
      `${t.filings_parsed} most recent electronic filings (${t.trades.length} trades). ` +
      `${t.filings_paper_only ? t.filings_paper_only + " recent filings are scanned paper documents — open the PDF link to read them." : ""}` +
      ` Disclosures lag the actual trade by up to 45 days.`;
    $("#top-buys").innerHTML = hbarsHTML(t.top_buys) || '<p class="loading">None in latest filings.</p>';
    $("#top-sells").innerHTML = hbarsHTML(t.top_sells) || '<p class="loading">None in latest filings.</p>';
    renderTradesTable();
  } catch (e) {
    $("#trades-table tbody").innerHTML = `<tr><td colspan="7" class="error">${esc(e.message)}</td></tr>`;
  }
}
function renderTradesTable() {
  const q = $("#trade-search").value.trim().toLowerCase();
  const side = $("#trade-side").value;
  const rows = allTrades.filter(t =>
    (!side || t.side === side) &&
    (!q || t.ticker.toLowerCase().includes(q) || t.member.toLowerCase().includes(q))
  ).slice(0, 250);
  $("#trades-table tbody").innerHTML = rows.map(t =>
    `<tr><td>${esc(t.member)} <span class="sub">${esc(t.state)}</span></td>` +
    `<td class="ticker">${esc(t.ticker)}</td>` +
    `<td>${esc(t.asset)}</td>` +
    `<td class="${t.side === "BUY" ? "side-buy" : t.side === "SELL" ? "side-sell" : ""}">${esc(t.side)}</td>` +
    `<td class="num">${esc(t.traded)}</td>` +
    `<td class="num">${fmtUSD(t.amount_low)}–${fmtUSD(t.amount_high)}</td>` +
    `<td><a href="${esc(t.doc_url)}" target="_blank" rel="noopener">PDF</a></td></tr>`
  ).join("") || `<tr><td colspan="7" class="loading">No trades match.</td></tr>`;
}
$("#trade-search").addEventListener("input", renderTradesTable);
$("#trade-side").addEventListener("change", renderTradesTable);

// ----------------------------------------------------------------- bills --
async function loadBills() {
  try {
    const b = await getJSON("/api/bills");
    $("#bills-list").innerHTML = b.bills.map(bill =>
      `<div class="item">` +
      `<a href="${esc(bill.link)}" target="_blank" rel="noopener"><b>${esc(bill.number)}</b> — ${esc(bill.title.slice(0, 160))}${bill.title.length > 160 ? "…" : ""}</a>` +
      `<div class="meta">${esc(bill.status)} · ${esc(bill.status_date)}${bill.sponsor ? " · " + esc(bill.sponsor) : ""}</div>` +
      tagsHTML(bill.sectors) + `</div>`).join("");
  } catch (e) {
    $("#bills-list").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}
function tagsHTML(sectors) {
  if (!sectors || !sectors.length) return "";
  return `<div class="tags">` + sectors.map(s =>
    `<span class="tag"><b>${esc(s.sector)}</b> · research: ${esc(s.etfs)}</span>`).join("") + `</div>`;
}

// ------------------------------------------------------------------ news --
let newsItems = [];
async function loadNews() {
  try {
    const n = await getJSON("/api/news");
    newsItems = n.items;
    const sectors = [...new Set(newsItems.flatMap(i => i.sectors.map(s => s.sector)))].sort();
    $("#news-sector").innerHTML = '<option value="">All sectors</option>' +
      sectors.map(s => `<option>${esc(s)}</option>`).join("");
    renderNews();
  } catch (e) {
    $("#news-list").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}
function renderNews() {
  const f = $("#news-sector").value;
  const rows = newsItems.filter(i => !f || i.sectors.some(s => s.sector === f));
  $("#news-list").innerHTML = rows.map(newsItemHTML).join("") || '<p class="loading">No stories match.</p>';
}
$("#news-sector").addEventListener("change", renderNews);
function newsItemHTML(i) {
  return `<div class="item">` +
    `<a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a>` +
    `<div class="meta">${esc(i.source)} · ${esc(i.published.replace(/ \+\d{4}$/, ""))}</div>` +
    (i.summary ? `<div class="summary">${esc(i.summary)}</div>` : "") +
    tagsHTML(i.sectors) + `</div>`;
}

// ------------------------------------------------------------------ plan --
const PLAN_KEY = "politrend_plan_v1";
const loadEntries = () => { try { return JSON.parse(localStorage.getItem(PLAN_KEY)) || []; } catch { return []; } };
const saveEntries = e => localStorage.setItem(PLAN_KEY, JSON.stringify(e));

$("#pf-symbol-preset").addEventListener("change", e => {
  $("#pf-custom-wrap").classList.toggle("hidden", e.target.value !== "custom");
});
$("#pf-date").value = new Date().toISOString().slice(0, 10);

$("#plan-form").addEventListener("submit", e => {
  e.preventDefault();
  const preset = $("#pf-symbol-preset").value;
  const symbol = preset === "custom"
    ? $("#pf-custom").value.trim().toUpperCase() : preset;
  const amount = parseFloat($("#pf-amount").value);
  const date = $("#pf-date").value;
  if (!symbol || !amount || !date) return;
  const entries = loadEntries();
  entries.push({ id: Date.now(), date, amount, symbol });
  entries.sort((a, b) => a.date.localeCompare(b.date));
  saveEntries(entries);
  $("#pf-amount").value = "";
  renderPlan();
});

function closeIdx(hist, date) {
  // last index with dates[i] <= date, else 0
  let lo = 0, hi = hist.dates.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (hist.dates[mid] <= date) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans === -1 ? 0 : ans;
}

async function renderPlan() {
  const entries = loadEntries();
  const tilesEl = $("#plan-tiles"), chartEl = $("#plan-chart"), tbody = $("#plan-table tbody");
  if (!entries.length) {
    tilesEl.innerHTML = "";
    chartEl.innerHTML = '<p class="loading">Add a contribution to see your comparison.</p>';
    tbody.innerHTML = '<tr><td colspan="6" class="loading">No contributions yet.</td></tr>';
    return;
  }
  chartEl.innerHTML = '<p class="loading">Pricing your portfolio&hellip;</p>';

  const symbols = [...new Set(entries.map(e => e.symbol).concat("^GSPC"))];
  const hists = {}, failed = {};
  await Promise.all(symbols.map(async s => {
    try { hists[s] = await getJSON(`/api/history?symbol=${encodeURIComponent(s)}&range=10y`); }
    catch (err) { failed[s] = err.message; }
  }));
  const spx = hists["^GSPC"];
  if (!spx) { chartEl.innerHTML = `<p class="error">Could not load S&P 500 data: ${esc(failed["^GSPC"])}</p>`; return; }

  const valid = entries.filter(e => hists[e.symbol]);
  valid.forEach(e => {
    const h = hists[e.symbol];
    e._shares = e.amount / h.closes[closeIdx(h, e.date)];
    e._bench = e.amount / spx.closes[closeIdx(spx, e.date)];
  });

  const startIdx = valid.length ? closeIdx(spx, valid[0].date) : spx.dates.length - 1;
  const dates = spx.dates.slice(startIdx);
  const port = [], bench = [];
  dates.forEach(d => {
    let pv = 0, bv = 0, any = false;
    valid.forEach(e => {
      if (e.date > d) return;
      any = true;
      const h = hists[e.symbol];
      pv += e._shares * h.closes[closeIdx(h, d)];
      bv += e._bench * spx.closes[closeIdx(spx, d)];
    });
    port.push(any ? pv : null);
    bench.push(any ? bv : null);
  });

  const invested = valid.reduce((a, e) => a + e.amount, 0);
  const pNow = port[port.length - 1] || 0, bNow = bench[bench.length - 1] || 0;
  const tiles = [
    { label: "Total invested", value: fmtUSD2(invested) },
    { label: "Portfolio value", value: fmtUSD2(pNow) },
    { label: "Your gain/loss", value: `${fmtUSD2(pNow - invested)} (${invested ? fmtPct((pNow - invested) / invested) : "—"})`, delta: pNow >= invested ? "up" : "down" },
    { label: "If 100% S&P 500", value: fmtUSD2(bNow) },
    { label: "You vs. the index", value: fmtUSD2(pNow - bNow), delta: pNow >= bNow ? "up" : "down" },
  ];
  tilesEl.innerHTML = tiles.map(t =>
    `<div class="tile"><div class="label">${esc(t.label)}</div>` +
    `<div class="value ${t.delta ? "delta " + t.delta : ""}" style="font-size:20px">${esc(t.value)}</div></div>`).join("");

  if (dates.length >= 2 && valid.length) {
    lineChart(chartEl, dates, [
      { name: "My portfolio", colorVar: "--series-1", values: port },
      { name: "S&P 500 only", colorVar: "--series-2", values: bench },
    ], { yFmt: fmtUSD, label: "Portfolio value versus S&P 500 benchmark" });
  } else {
    chartEl.innerHTML = '<p class="loading">The comparison chart appears once your first contribution has at least a day of history.</p>';
  }

  tbody.innerHTML = entries.map(e => {
    const h = hists[e.symbol];
    let val = "—", bval = "—";
    if (h && e._shares != null) {
      val = fmtUSD2(e._shares * h.closes[h.closes.length - 1]);
      bval = fmtUSD2(e._bench * spx.closes[spx.closes.length - 1]);
    } else if (failed[e.symbol]) {
      val = `no data (${esc(failed[e.symbol]).slice(0, 40)})`;
    }
    return `<tr><td class="num">${esc(e.date)}</td><td class="num">${fmtUSD2(e.amount)}</td>` +
      `<td class="ticker">${esc(e.symbol === "^GSPC" ? "S&P 500" : e.symbol)}</td>` +
      `<td class="num">${val}</td><td class="num">${bval}</td>` +
      `<td><button class="ghost" data-del="${e.id}">Remove</button></td></tr>`;
  }).join("");
}
$("#plan-table").addEventListener("click", e => {
  const btn = e.target.closest("button[data-del]");
  if (!btn) return;
  saveEntries(loadEntries().filter(x => String(x.id) !== btn.dataset.del));
  renderPlan();
});
function loadPlan() { renderPlan(); }

// export / import (moves plan data between computers)
$("#plan-export").addEventListener("click", () => {
  const payload = { app: "capitol-gains", version: 1, exported: new Date().toISOString(), entries: loadEntries() };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "capitol-gains-plan.json";
  a.click();
  URL.revokeObjectURL(a.href);
});
$("#plan-import").addEventListener("click", () => $("#plan-import-file").click());
$("#plan-import-file").addEventListener("change", async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const incoming = Array.isArray(data) ? data : data.entries;
    if (!Array.isArray(incoming)) throw new Error("no contributions found in that file");
    const entries = loadEntries();
    const seen = new Set(entries.map(x => String(x.id)));
    let added = 0;
    incoming.forEach(x => {
      if (!x || !x.date || !(+x.amount > 0) || !x.symbol || seen.has(String(x.id))) return;
      entries.push({ id: x.id || Date.now() + added, date: String(x.date),
                     amount: +x.amount, symbol: String(x.symbol).toUpperCase().replace(/[^A-Z0-9^.\-]/g, "") || "^GSPC" });
      added++;
    });
    entries.sort((a, b) => a.date.localeCompare(b.date));
    saveEntries(entries);
    renderPlan();
    alert(added + " contribution(s) imported.");
  } catch (err) {
    alert("Could not import: " + err.message);
  }
});

// ------------------------------------------------------------------ init --
loadTab("overview");
