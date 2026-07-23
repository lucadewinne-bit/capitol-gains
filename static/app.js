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

// ----------------------------------------------------------------- tabs --
const loaded = {};
$("#tabs").addEventListener("click", e => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  $$(".tab").forEach(t => t.classList.toggle("active", t === btn));
  $$(".tabpanel").forEach(p => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
  loadTab(btn.dataset.tab);
});
function loadTab(name) {
  if (loaded[name]) return;
  loaded[name] = true;
  ({ overview: loadOverview, trades: loadTrades, bills: loadBills,
     news: loadNews, plan: loadPlan }[name] || (() => {}))();
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

async function loadOverview() {
  drawSpx();
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

// ------------------------------------------------------------------ init --
loadTab("overview");
