const BONDS = [
  {code: "132026.SH", name: "G三峡EB2"},
  {code: "132024.SH", name: "26江铜EB"},
];

const state = {
  snapshots: {},
  modelId: "maker_priority_v1_1",
  actionFilter: "all",
  mode: "live",
  replayMeta: null,
  replayDate: null,
  replayTs: null,
  replayPlaying: false,
  replayTimer: null,
  replayLoadTimer: null,
  poller: null,
  loading: false,
  requestId: 0,
  requestController: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fmtPrice = value => Number.isFinite(Number(value)) ? Number(value).toFixed(3) : "—";
const fmtQty = value => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits: 0});
const fmtPnl = value => {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
};
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const bondName = code => BONDS.find(item => item.code === code)?.name || code;
const BOOK_PRICE_EPSILON = .0005;

function bookRowKey(side, row) {
  return `${side}:${row.level}`;
}

function placeBookOrders(book, orders) {
  const asks = (book?.asks || [])
    .filter(row => Number(row.price) > 0)
    .map(row => ({...row, numericPrice: Number(row.price)}))
    .sort((left, right) => left.numericPrice - right.numericPrice);
  const bids = (book?.bids || [])
    .filter(row => Number(row.price) > 0)
    .map(row => ({...row, numericPrice: Number(row.price)}))
    .sort((left, right) => right.numericPrice - left.numericPrice);
  const byRow = {};
  const outside = [];

  for (const order of orders || []) {
    const price = Number(order.limit_price);
    const isBuy = order.side === "buy";
    const rows = isBuy ? bids : asks;
    if (!Number.isFinite(price) || !rows.length) {
      outside.push(order);
      continue;
    }

    let target = rows.find(row => Math.abs(price - row.numericPrice) < BOOK_PRICE_EPSILON);
    if (!target && isBuy) {
      const lowestVisibleBid = bids.at(-1)?.numericPrice;
      const bestAsk = asks[0]?.numericPrice;
      const isInVisibleBook = price >= lowestVisibleBid - BOOK_PRICE_EPSILON
        && (!Number.isFinite(bestAsk) || price < bestAsk - BOOK_PRICE_EPSILON);
      if (isInVisibleBook) target = bids.find(row => price >= row.numericPrice - BOOK_PRICE_EPSILON);
    }
    if (!target && !isBuy) {
      const highestVisibleAsk = asks.at(-1)?.numericPrice;
      const bestBid = bids[0]?.numericPrice;
      const isInVisibleBook = price <= highestVisibleAsk + BOOK_PRICE_EPSILON
        && (!Number.isFinite(bestBid) || price > bestBid + BOOK_PRICE_EPSILON);
      if (isInVisibleBook) target = asks.find(row => price <= row.numericPrice + BOOK_PRICE_EPSILON);
    }

    if (!target) {
      outside.push(order);
      continue;
    }
    const key = bookRowKey(isBuy ? "bid" : "ask", target);
    (byRow[key] ||= []).push(order);
  }
  return {byRow, outside};
}

function replayClock(ts) {
  if (!Number.isFinite(Number(ts))) return "--:--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour12: false,
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date(Number(ts)));
}

function replayLunchWindow(marketDate) {
  const start = Date.parse(`${marketDate}T11:30:00+08:00`);
  const end = Date.parse(`${marketDate}T13:00:00+08:00`);
  return Number.isFinite(start) && Number.isFinite(end) ? {start, end} : null;
}

function advanceReplayTimestamp(ts, elapsedMarketMs, marketDate) {
  let current = Number(ts);
  const elapsed = Math.max(0, Number(elapsedMarketMs) || 0);
  const lunch = replayLunchWindow(marketDate);
  if (!Number.isFinite(current) || !lunch) return current + elapsed;
  if (current > lunch.start && current < lunch.end) current = lunch.end;
  let next = current + elapsed;
  if (current <= lunch.start && next > lunch.start) {
    next += lunch.end - lunch.start;
  }
  return next;
}

function normalizeReplayScrubTimestamp(ts, previousTs, marketDate) {
  const candidate = Number(ts);
  const lunch = replayLunchWindow(marketDate);
  if (!Number.isFinite(candidate) || !lunch || candidate <= lunch.start || candidate >= lunch.end) {
    return candidate;
  }
  return candidate >= Number(previousTs) ? lunch.end : lunch.start;
}

function marketTradesAscending(trades) {
  return [...(trades || [])].sort((left, right) => Number(left.ts) - Number(right.ts));
}

async function loadSnapshots({manual = false} = {}) {
  if (state.mode === "replay" && (!state.replayDate || state.replayTs === null)) return;
  const requestId = ++state.requestId;
  if (state.requestController) state.requestController.abort();
  state.requestController = new AbortController();
  state.loading = true;
  if (manual) $("#refreshButton").classList.add("loading");
  try {
    const requests = BONDS.map(async bond => {
      const url = state.mode === "replay"
        ? `/api/replay/snapshot?bond=${encodeURIComponent(bond.code)}&date=${encodeURIComponent(state.replayDate)}&ts=${Math.round(state.replayTs)}&model=${encodeURIComponent(state.modelId)}`
        : `/api/snapshot?bond=${encodeURIComponent(bond.code)}&model=${encodeURIComponent(state.modelId)}`;
      const response = await fetch(url, {cache: "no-store", signal: state.requestController.signal});
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(`${bond.name}：${payload.error || "读取失败"}`);
      return payload;
    });
    const payloads = await Promise.all(requests);
    if (requestId !== state.requestId) return;
    state.snapshots = Object.fromEntries(payloads.map(item => [item.bond.code, item]));
    if (state.mode === "replay") {
      $("#replayTimeline").value = String(state.replayTs);
      $("#replayClock").textContent = replayClock(state.replayTs);
    }
    render();
  } catch (error) {
    if (error.name === "AbortError") return;
    $("#sourceLabel").textContent = `连接失败 · ${error.message}`;
    showToast(error.message);
  } finally {
    if (requestId === state.requestId) {
      state.loading = false;
      $("#refreshButton").classList.remove("loading");
    }
  }
}

async function loadReplayMetadata() {
  stopReplay();
  const results = await Promise.all(BONDS.map(async bond => {
    const response = await fetch(`/api/replay/meta?bond=${encodeURIComponent(bond.code)}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(`${bond.name}：${payload.error || "没有回看数据"}`);
    return payload;
  }));
  const secondByDate = new Map(results[1].dates.map(item => [item.date, item]));
  const commonDates = results[0].dates.flatMap(first => {
    const second = secondByDate.get(first.date);
    if (!second) return [];
    const start = Math.max(first.start_ts_ms, second.start_ts_ms);
    const end = Math.min(first.end_ts_ms, second.end_ts_ms);
    if (start > end) return [];
    return [{
      date: first.date,
      start_ts_ms: start,
      end_ts_ms: end,
      has_accounts: first.has_accounts && second.has_accounts,
    }];
  });
  if (!commonDates.length) throw new Error("两只债券没有共同的回看交易日");
  state.replayMeta = {dates: commonDates};
  const selected = commonDates.find(item => item.date === state.replayDate)
    || commonDates.find(item => item.has_accounts)
    || commonDates[0];
  $("#replayDate").innerHTML = commonDates.map(item =>
    `<option value="${escapeHtml(item.date)}">${escapeHtml(item.date)}${item.has_accounts ? " · 双债六模型" : " · 仅行情"}</option>`
  ).join("");
  $("#replayDate").value = selected.date;
  configureReplayDate(selected);
  await loadSnapshots({manual: true});
}

function configureReplayDate(item) {
  state.replayDate = item.date;
  state.replayTs = Number(item.start_ts_ms);
  const timeline = $("#replayTimeline");
  timeline.min = String(item.start_ts_ms);
  timeline.max = String(item.end_ts_ms);
  timeline.value = String(item.start_ts_ms);
  timeline.step = "1000";
  $("#replayClock").textContent = replayClock(item.start_ts_ms);
}

function scheduleReplayLoad() {
  clearTimeout(state.replayLoadTimer);
  state.replayLoadTimer = setTimeout(() => loadSnapshots(), 90);
}

function stopReplay() {
  state.replayPlaying = false;
  clearInterval(state.replayTimer);
  state.replayTimer = null;
  const button = $("#replayPlay");
  if (button) {
    button.textContent = "▶";
    button.classList.remove("playing");
  }
}

function toggleReplay() {
  if (state.replayPlaying) return stopReplay();
  const timeline = $("#replayTimeline");
  if (Number(timeline.value) >= Number(timeline.max)) {
    state.replayTs = Number(timeline.min);
    timeline.value = timeline.min;
  }
  state.replayPlaying = true;
  $("#replayPlay").textContent = "Ⅱ";
  $("#replayPlay").classList.add("playing");
  let lastRealTime = performance.now();
  state.replayTimer = setInterval(() => {
    const now = performance.now();
    const elapsed = now - lastRealTime;
    lastRealTime = now;
    const speed = Number($("#replaySpeed").value || 60);
    const next = Math.min(
      Number(timeline.max),
      advanceReplayTimestamp(state.replayTs, elapsed * speed, state.replayDate),
    );
    state.replayTs = next;
    timeline.value = String(next);
    $("#replayClock").textContent = replayClock(next);
    scheduleReplayLoad();
    if (next >= Number(timeline.max)) stopReplay();
  }, 250);
}

function render() {
  const data = BONDS.map(bond => state.snapshots[bond.code]).filter(Boolean);
  if (data.length !== BONDS.length) return;
  ensureModelOptions(data[0].accounts);
  const latest = data.reduce((best, item) => item.market.market_ts_ms > best.market.market_ts_ms ? item : best, data[0]);
  $("#sourceLabel").textContent = `SQLite 只读${state.mode === "replay" ? "回看" : ""} · 双债同屏`;
  $("#marketDate").textContent = latest.market.market_date;
  $("#marketTime").textContent = latest.market.market_time.slice(0, 8);
  $("#refreshState").textContent = state.mode === "replay" ? "历史模拟回看" : latest.refresh.label;
  $("#refreshState").classList.toggle("active", state.mode === "live" && latest.refresh.active);
  $("#marketGrid").innerHTML = data.map(renderBondDesk).join("");
  $$(".market-trades").forEach(list => {
    list.scrollTop = list.scrollHeight;
  });
  data.forEach(item => renderChart(item));
  renderSelectedAccounts(data);
  renderActions(data);
  $("#servedAt").textContent = state.mode === "replay"
    ? `双债因果截断 ${latest.market.market_date} ${replayClock(state.replayTs)}`
    : `双债快照 ${latest.refresh.served_at.replace("T", " ")}`;
}

function renderBondDesk(data) {
  const {market, assessment} = data;
  const selectedOrders = data.open_orders.filter(order => order.model_id === state.modelId);
  const placedOrders = placeBookOrders(data.book, selectedOrders);
  const asks = data.book.asks.map(row => renderBookRow(row, "ask", placedOrders.byRow[bookRowKey("ask", row)] || [])).join("");
  const bids = data.book.bids.map(row => renderBookRow(row, "bid", placedOrders.byRow[bookRowKey("bid", row)] || [])).join("");
  const outsideStrip = placedOrders.outside.length ? `<div class="outside-order-strip"><span>盘口外（超出五档）</span>${placedOrders.outside.map(order =>
    `<strong class="${order.side === "buy" ? "order-buy" : "order-sell"}">${order.side === "buy" ? "B" : "S"} ${fmtPrice(order.limit_price)} · ${fmtQty(order.remaining)}张</strong>`
  ).join("")}</div>` : "";
  const trades = data.market_trades.length
    ? marketTradesAscending(data.market_trades).map(renderMarketTrade).join("")
    : `<div class="empty-inline">当前时点前暂无市场成交增量</div>`;
  const changeClass = market.change >= 0 ? "up" : "down";
  const trendClass = ["rising", "possible_rise"].includes(assessment.state) ? "up" : ["falling", "possible_fall"].includes(assessment.state) ? "down" : "";
  return `<article class="bond-desk" data-bond="${data.bond.code}">
    <header class="bond-heading">
      <div>
        <div class="bond-name-row"><h2>${escapeHtml(data.bond.name)}</h2><span class="bond-code">${escapeHtml(data.bond.code)}</span></div>
        <div class="bond-price-row"><strong class="last-price">${fmtPrice(market.last_price)}</strong><span class="price-change ${changeClass}">${market.change >= 0 ? "+" : ""}${fmtPrice(market.change)} · ${market.change_pct >= 0 ? "+" : ""}${market.change_pct.toFixed(2)}%</span></div>
        <div class="desk-stats">
          <div class="desk-stat"><span>买一</span><b class="bid">${fmtPrice(market.bid1)}</b></div>
          <div class="desk-stat"><span>卖一</span><b class="ask">${fmtPrice(market.ask1)}</b></div>
          <div class="desk-stat"><span>价差</span><b>${fmtPrice(market.spread)}</b></div>
        </div>
      </div>
      <div class="bond-heading-right">
        <div class="bond-time">${escapeHtml(market.market_time.slice(0, 8))}</div>
        <div class="fair-line">合理区 <strong>${fmtPrice(assessment.reference_low)}—${fmtPrice(assessment.reference_high)}</strong></div>
        <div class="fair-line">市场状态 <span class="${trendClass}">${escapeHtml(assessment.state_label)}</span> · ${Math.round(Number(assessment.state_confidence || 0) * 100)}%</div>
      </div>
    </header>
    <div class="desk-body">
      <section class="chart-card">
        <div class="micro-head"><h3>近一小时分时</h3><span>${data.history[0]?.time || "--:--:--"}—${data.history.at(-1)?.time || "--:--:--"}</span></div>
        <div class="chart-wrap"><canvas class="price-chart" id="chart-${data.bond.code.replace(".", "-")}" aria-label="${escapeHtml(data.bond.name)}近一小时分时"></canvas></div>
      </section>
      <div class="market-core">
        <section class="book-card">
          <div class="micro-head"><h3>五档盘口</h3><span>当前模型活动单 ${selectedOrders.length} 笔</span></div>
          ${outsideStrip}
          <div class="book-columns"><span>档位</span><span>价格</span><span>市场量</span><span>模拟挂单</span></div>
          ${asks}
          <div class="spread-row"><strong>买卖价差 ${fmtPrice(market.spread)}</strong></div>
          ${bids}
        </section>
        <section class="market-tape-card">
          <div class="micro-head"><h3>市场成交</h3><span>B/S方向为本地推断</span></div>
          <div class="trade-columns"><span>时间</span><span>价格</span><span>数量</span><span>B/S</span></div>
          <div class="market-trades">${trades}</div>
        </section>
      </div>
    </div>
  </article>`;
}

function renderBookRow(row, side, orders) {
  const chips = orders.map(order =>
    `<span class="order-chip ${order.side === "buy" ? "order-buy" : "order-sell"}" title="${escapeHtml(order.kind_label)} · ${fmtPrice(order.limit_price)} · ${fmtQty(order.remaining)}张"><b>${order.side === "buy" ? "B" : "S"}</b><span>${fmtPrice(order.limit_price)}</span><small>${fmtQty(order.remaining)}张</small></span>`
  ).join("");
  const maxQuantity = 5000;
  return `<div class="book-row ${side}-row" style="--depth:${Math.max(2, Math.min(100, Number(row.quantity) / maxQuantity * 100))}%">
    <span class="book-level">${side === "ask" ? "卖" : "买"}${row.level}</span>
    <span class="book-price ${side}">${fmtPrice(row.price)}</span>
    <span class="book-qty">${fmtQty(row.quantity)}</span>
    <div class="book-overlays">${chips}</div>
  </div>`;
}

function renderMarketTrade(item) {
  const side = item.inferred_side === "buy" ? "trade-buy" : item.inferred_side === "sell" ? "trade-sell" : "";
  const label = item.inferred_side === "buy" ? "B" : item.inferred_side === "sell" ? "S" : "—";
  return `<div class="trade-row"><span>${escapeHtml(item.time)}</span><span class="trade-price ${side}">${fmtPrice(item.price)}</span><span class="trade-qty">${fmtQty(item.quantity)}</span><span class="trade-side ${side}">${label}</span></div>`;
}

function renderChart(data) {
  const canvas = $(`#chart-${data.bond.code.replace(".", "-")}`);
  if (!canvas || !data.history.length) return;
  const history = data.history;
  const assessment = data.assessment;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  const pad = {left: 6, right: 55, top: 6, bottom: 20};
  const prices = history.map(item => Number(item.last)).filter(Number.isFinite);
  prices.push(Number(assessment.reference_low), Number(assessment.reference_high));
  let min = Math.min(...prices), max = Math.max(...prices);
  const margin = Math.max(.025, (max - min) * .15);
  min -= margin; max += margin;
  const firstTs = Number(history[0].ts), lastTs = Number(history.at(-1).ts);
  const x = ts => pad.left + (width - pad.left - pad.right) * (Number(ts) - firstTs) / Math.max(1, lastTs - firstTs);
  const y = price => pad.top + (max - Number(price)) / Math.max(.001, max - min) * (height - pad.top - pad.bottom);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(255,255,255,.065)";
  ctx.fillStyle = "#82909c";
  ctx.font = "9px Consolas";
  for (let index = 0; index < 3; index++) {
    const yy = pad.top + index * (height - pad.top - pad.bottom) / 2;
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillText((max - index * (max - min) / 2).toFixed(3), width - pad.right + 7, yy + 3);
  }
  const fairTop = y(assessment.reference_high), fairBottom = y(assessment.reference_low);
  ctx.fillStyle = "rgba(214,174,109,.09)";
  ctx.fillRect(pad.left, fairTop, width - pad.left - pad.right, fairBottom - fairTop);
  ctx.strokeStyle = "rgba(214,174,109,.4)";
  ctx.setLineDash([5,5]);
  [fairTop, fairBottom].forEach(yy => { ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width-pad.right, yy); ctx.stroke(); });
  ctx.setLineDash([]);
  const gradient = ctx.createLinearGradient(0, pad.top, 0, height-pad.bottom);
  gradient.addColorStop(0, "rgba(104,183,255,.22)"); gradient.addColorStop(1, "rgba(104,183,255,0)");
  ctx.beginPath();
  history.forEach((item, index) => index ? ctx.lineTo(x(item.ts), y(item.last)) : ctx.moveTo(x(item.ts), y(item.last)));
  ctx.lineTo(x(history.at(-1).ts), height-pad.bottom); ctx.lineTo(x(history[0].ts), height-pad.bottom); ctx.closePath();
  ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath();
  history.forEach((item, index) => index ? ctx.lineTo(x(item.ts), y(item.last)) : ctx.moveTo(x(item.ts), y(item.last)));
  ctx.strokeStyle = "#c9e2ff"; ctx.lineWidth = 1.6; ctx.stroke();
  const last = history.at(-1);
  ctx.beginPath(); ctx.arc(x(last.ts), y(last.last), 3.4, 0, Math.PI*2); ctx.fillStyle = "#f1eee7"; ctx.fill();
  ctx.fillStyle = "#82909c"; ctx.font = "9px Consolas";
  ctx.fillText(history[0].time, pad.left, height-5);
  ctx.fillText(last.time, width-pad.right-43, height-5);
}

function ensureModelOptions(accounts) {
  const select = $("#modelSelect");
  const currentIds = [...select.options].map(option => option.value);
  const nextIds = accounts.map(account => account.model_id);
  if (currentIds.join("|") !== nextIds.join("|")) {
    select.innerHTML = accounts.map(account => `<option value="${escapeHtml(account.model_id)}">${escapeHtml(account.short)} · ${escapeHtml(account.status)}</option>`).join("");
  }
  if (!nextIds.includes(state.modelId)) state.modelId = nextIds[0];
  select.value = state.modelId;
  const selected = accounts.find(account => account.model_id === state.modelId);
  $("#modelStatus").textContent = selected?.status || "—";
  $("#modelNote").textContent = selected?.note || "两债账户互不合并";
}

function renderSelectedAccounts(data) {
  $("#selectedAccounts").innerHTML = data.map(item => {
    const account = item.accounts.find(candidate => candidate.model_id === state.modelId);
    if (!account) return `<div class="account-summary"><span>${escapeHtml(item.bond.name)} 无该模型账户</span></div>`;
    const pnlClass = account.trading_pnl >= 0 ? "up" : "down";
    const orderText = account.orders.length ? account.action : "当前无活动挂单";
    return `<article class="account-summary">
      <div class="account-summary-main"><span class="account-bond">${escapeHtml(item.bond.name)}</span><span class="account-action">${escapeHtml(orderText)}</span></div>
      <div class="account-summary-stats">
        <div class="account-stat"><span>库存 / 上限</span><b>${fmtQty(account.inventory)} / ${fmtQty(account.maximum_inventory)}</b></div>
        <div class="account-stat"><span>底仓缺口</span><b class="${account.customer_base_short ? "sell" : ""}">${fmtQty(account.customer_base_short)}</b></div>
        <div class="account-stat"><span>交易PnL</span><b class="${pnlClass}">${fmtPnl(account.trading_pnl)}</b></div>
        <div class="account-stat"><span>成交记录</span><b>${fmtQty(account.fills)}</b></div>
      </div>
    </article>`;
  }).join("");
}

function renderActions(data) {
  let actions = data.flatMap(item => item.actions).filter(item => item.model_id === state.modelId);
  if (state.actionFilter !== "all") {
    actions = actions.filter(item => state.actionFilter === "fill" ? item.event_type === "fill" : item.event_type === state.actionFilter);
  }
  actions.sort((left, right) => Number(right.ts) - Number(left.ts));
  $("#actionCount").textContent = `${actions.length} 条`;
  if (!actions.length) {
    $("#actionStream").innerHTML = `<div class="empty-inline">当前模型在该时点前暂无此类动作</div>`;
    return;
  }
  $("#actionStream").innerHTML = actions.map(item => {
    const eventClass = `event-${item.event_type}`;
    const sideClass = item.side === "buy" ? "buy" : "sell";
    const orderText = `${item.side === "buy" ? "买" : "卖"} ${fmtPrice(item.price)} × ${fmtQty(item.quantity)}张`;
    return `<div class="action-row">
      <span class="action-time">${escapeHtml(item.time)}</span>
      <span class="action-bond">${escapeHtml(bondName(item.bond_code))}</span>
      <span class="event-badge ${eventClass}">${escapeHtml(item.event_label)}</span>
      <span class="action-order ${sideClass}">${orderText}</span>
      <span class="action-detail">${escapeHtml(item.detail)}<code>#${item.order_id ?? "—"}</code></span>
    </div>`;
  }).join("");
}

async function setMode(mode) {
  if (mode === state.mode) return;
  state.mode = mode;
  stopReplay();
  $$("#modeSwitch button").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
  $(".command-bar").classList.toggle("replay-active", mode === "replay");
  try {
    if (mode === "replay") await loadReplayMetadata();
    else await loadSnapshots({manual: true});
  } catch (error) {
    showToast(`模式切换失败：${error.message}`);
  }
}

let toastTimer;
function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2800);
}

function bindEvents() {
  $$("#modeSwitch button").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("#refreshButton").addEventListener("click", () => loadSnapshots({manual: true}));
  $("#replayDate").addEventListener("change", () => {
    stopReplay();
    const selected = state.replayMeta?.dates.find(item => item.date === $("#replayDate").value);
    if (!selected) return;
    configureReplayDate(selected);
    loadSnapshots({manual: true});
  });
  $("#replayTimeline").addEventListener("input", event => {
    stopReplay();
    state.replayTs = normalizeReplayScrubTimestamp(
      event.target.value,
      state.replayTs,
      state.replayDate,
    );
    event.target.value = String(state.replayTs);
    $("#replayClock").textContent = replayClock(state.replayTs);
    scheduleReplayLoad();
  });
  $("#replayPlay").addEventListener("click", toggleReplay);
  $("#modelSelect").addEventListener("change", event => {
    state.modelId = event.target.value;
    render();
    loadSnapshots({manual: true});
  });
  $("#actionFilter").addEventListener("click", event => {
    const button = event.target.closest("button[data-action-filter]");
    if (!button) return;
    state.actionFilter = button.dataset.actionFilter;
    $$("#actionFilter button").forEach(item => item.classList.toggle("active", item === button));
    renderActions(BONDS.map(bond => state.snapshots[bond.code]).filter(Boolean));
  });
  window.addEventListener("resize", () => BONDS.forEach(bond => {
    const snapshot = state.snapshots[bond.code];
    if (snapshot) renderChart(snapshot);
  }));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    advanceReplayTimestamp,
    bookRowKey,
    marketTradesAscending,
    normalizeReplayScrubTimestamp,
    placeBookOrders,
    renderBookRow,
    replayLunchWindow,
  };
}

if (typeof document !== "undefined") {
  bindEvents();
  loadSnapshots();
  state.poller = setInterval(() => {
    if (state.mode === "live") loadSnapshots();
  }, 3000);
}
