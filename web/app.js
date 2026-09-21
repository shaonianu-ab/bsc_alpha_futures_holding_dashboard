const state = {
  dashboard: null,
  holderComparison: null,
  replenishmentSort: { field: "fdv", direction: "asc" },
  view: "overview",
};

const content = document.querySelector("#content");
const notice = document.querySelector("#notice");
const modal = document.querySelector("#modal");
const refreshButton = document.querySelector("#refresh-button");
const snapshotStatus = document.querySelector("#snapshot-status");
const numberFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const integerFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
let noticeTimeoutId = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  return `$${numberFormatter.format(Number(value || 0))}`;
}

function moneyCompact(value) {
  const number = Number(value || 0);
  if (number >= 1_000_000_000) return `$${numberFormatter.format(number / 1_000_000_000)}B`;
  if (number >= 1_000_000) return `$${numberFormatter.format(number / 1_000_000)}M`;
  if (number >= 1_000) return `$${numberFormatter.format(number / 1_000)}K`;
  return money(number);
}

function amount(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  if (number === 0) return "0";
  if (Math.abs(number) < 0.0001) return number.toExponential(3);
  return numberFormatter.format(number);
}

function price(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  if (number < 0.0001) return `$${number.toExponential(3)}`;
  return money(number);
}

function holderCount(value) {
  return value === null || value === undefined ? "-" : integerFormatter.format(Number(value));
}

function shortContract(value) {
  const contract = String(value || "");
  return contract ? `$${contract.slice(0, 8)}...${contract.slice(-5)}` : "-";
}

function showNotice(message, isError = false, autoDismiss = !isError) {
  if (noticeTimeoutId !== null) {
    window.clearTimeout(noticeTimeoutId);
    noticeTimeoutId = null;
  }
  notice.hidden = !message;
  notice.textContent = message || "";
  notice.classList.toggle("error", isError);
  if (!message || !autoDismiss) return;

  noticeTimeoutId = window.setTimeout(() => {
    notice.hidden = true;
    notice.textContent = "";
    notice.classList.remove("error");
    noticeTimeoutId = null;
  }, 5_000);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

function setRefreshing(active) {
  refreshButton.disabled = active;
  refreshButton.textContent = active ? "正在刷新，请稍候…" : "刷新市场与链上余额";
}

function updateSnapshotStatus() {
  const snapshot = state.dashboard?.snapshot;
  snapshotStatus.textContent = snapshot
    ? `快照：${new Date(snapshot.fetched_at).toLocaleString("zh-CN")} · ${snapshot.wallet_count} 个钱包`
    : "尚未获取快照";
}

function updateNavigation() {
  document.querySelectorAll("[data-view]").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === state.view);
  });
}

async function loadDashboard() {
  state.dashboard = await request("/api/dashboard");
  updateSnapshotStatus();
  render();
}

function stateBadge(token) {
  if (token.holding_state === "held") return '<span class="badge badge-green">已确认持仓</span>';
  if (token.holding_state === "pending_confirmation") {
    return `<span class="badge badge-amber">交易所待确认 ${token.pending_manual_count}</span>`;
  }
  return '<span class="badge badge-neutral">无已记录持仓</span>';
}

function matchBadge(token) {
  if (token.match_quality === "unique_symbol") return '<span class="badge badge-green">唯一 Symbol</span>';
  if (token.match_quality === "duplicate_symbol_nearest_price") {
    return '<span class="badge badge-amber">重名价格匹配</span>';
  }
  return '<span class="badge badge-red">FDV 回退匹配</span>';
}

function tokenCell(token) {
  return `
    <span class="token-symbol">${escapeHtml(token.symbol)}</span>
    <span class="token-name">${escapeHtml(token.name || token.futures_symbol)}</span>
    <span class="contract" title="${escapeHtml(token.contract_address)}">${shortContract(token.contract_address)}</span>
  `;
}

function riskCell(token) {
  const gap = token.price_gap_pct === null ? "价格无参考" : `偏离 ${numberFormatter.format(token.price_gap_pct)}%`;
  const alert = token.price_gap_alert ? '<span class="badge badge-red">偏离提醒</span>' : "";
  return `<div class="status-line">${matchBadge(token)}${alert}<span class="hint">${gap}</span></div>`;
}

function sourceCell(token) {
  if (!token.manual_sources.length) return '<span class="hint">仅链上</span>';
  return token.manual_sources
    .map((source) => `<span class="badge badge-neutral">${escapeHtml(source.source_name)} · ${amount(source.amount)}</span>`)
    .join(" ");
}

function filterToolbar(type, title, description) {
  return `
    <div class="table-toolbar">
      <div><h2>${title}</h2><p class="hint">${description}</p></div>
      <input class="filter-input" data-filter="${type}" placeholder="筛选 Symbol 或名称">
    </div>
  `;
}

function renderEmpty() {
  content.innerHTML = `
    <section class="empty-state">
      <p class="eyebrow">先建立当前快照</p>
      <h1>开始本地持仓盘点</h1>
      <p class="subtle">设置钱包地址后，刷新会抓取 BSC 链上余额和当前市场数据。本地交易所持仓只保存在本机数据库中。</p>
      <div class="form-actions">
        <button class="button" data-view="settings">先检查设置</button>
        <button class="button button-primary" data-action="refresh">获取第一份快照</button>
      </div>
    </section>
  `;
}

function renderOverview() {
  const { metrics, fdv_distribution: buckets, settings, tokens } = state.dashboard;
  const maximum = Math.max(1, ...buckets.map((bucket) => bucket.token_count));
  const reviewTokens = tokens
    .filter((token) => token.price_gap_alert || token.match_quality === "fallback")
    .sort((left, right) => (right.price_gap_pct || 0) - (left.price_gap_pct || 0))
    .slice(0, 5);
  content.innerHTML = `
    <header class="view-heading">
      <div>
        <p class="eyebrow">行动优先</p>
        <h1>从持仓盘点到补仓判断</h1>
        <p class="subtle">低 FDV 阈值：${moneyCompact(settings.low_fdv_limit_usd)} · 补仓目标：${money(settings.default_target_value_usd)}</p>
      </div>
      <button class="button" data-view="settings">调整全局规则</button>
    </header>
    <section class="metrics">
      <article class="metric-card"><span class="metric-label">低 FDV 无已记录持仓</span><div class="metric-value">${integerFormatter.format(metrics.recorded_unheld_low_fdv_count)}</div><span class="metric-detail">可从机会池开始检查</span></article>
      <article class="metric-card warning"><span class="metric-label">补仓候选</span><div class="metric-value">${integerFormatter.format(metrics.replenishment_count)}</div><span class="metric-detail">待补 ${money(metrics.replenishment_shortfall_usd)}</span></article>
      <article class="metric-card attention"><span class="metric-label">交易所待确认</span><div class="metric-value">${integerFormatter.format(metrics.pending_confirmation_count)}</div><span class="metric-detail">不计入未持仓或补仓结论</span></article>
      <article class="metric-card"><span class="metric-label">已确认总持仓价值</span><div class="metric-value">${moneyCompact(metrics.total_holding_value_usd)}</div><span class="metric-detail">${integerFormatter.format(metrics.held_token_count)} 个已确认代币</span></article>
    </section>
    <section class="dashboard-grid">
      <article class="panel">
        <div class="panel-title"><div><p class="eyebrow">FDV 分布</p><h2>候选资产分层</h2></div><span class="hint">${integerFormatter.format(metrics.token_count)} 个合约匹配代币</span></div>
        <div class="bars">${buckets.map((bucket) => `
          <div class="bar-row"><span>${escapeHtml(bucket.label)}</span><div class="bar-track"><div class="bar-fill" style="width:${(bucket.token_count / maximum) * 100}%"></div></div><strong>${bucket.token_count} 个</strong></div>
        `).join("")}</div>
      </article>
      <article class="panel">
        <p class="eyebrow">当前规则</p><h2>计算口径</h2>
        <dl class="definition-list">
          <dt>链上余额</dt><dd>${state.dashboard.wallet_addresses.length} 个 BSC 钱包汇总</dd>
          <dt>本地交易所余额</dt><dd>仅已确认合约映射计入</dd>
          <dt>默认初始买入金额</dt><dd>${money(settings.default_initial_purchase_usd)}</dd>
          <dt>默认补仓目标</dt><dd>${money(settings.default_target_value_usd)}</dd>
          <dt>价格偏离提醒</dt><dd>≥ ${numberFormatter.format(settings.price_gap_alert_pct)}%</dd>
          <dt>每日自动刷新</dt><dd>每日 ${settings.scheduled_refresh_time}（新加坡时间）</dd>
        </dl>
      </article>
    </section>
    <section class="panel table-panel" style="margin-top:16px">
      <div class="table-toolbar"><div><p class="eyebrow">数据复核</p><h2>优先人工检查</h2></div><button class="button button-small" data-view="maintenance">进入本地维护</button></div>
      ${reviewTokens.length ? `
        <div class="table-wrap"><table><thead><tr><th>代币</th><th class="numeric">FDV</th><th>匹配与价格</th><th>持仓状态</th><th></th></tr></thead>
        <tbody>${reviewTokens.map((token) => `
          <tr><td class="token-cell">${tokenCell(token)}</td><td class="numeric">${moneyCompact(token.fdv_usd)}</td><td>${riskCell(token)}</td><td>${stateBadge(token)}</td><td><button class="button button-small" data-action="edit-token" data-contract="${token.contract_address}">规则</button></td></tr>
        `).join("")}</tbody></table></div>
      ` : '<p class="hint" style="padding:18px">当前没有触发价格偏离提醒或 FDV 回退匹配的代币。</p>'}
    </section>
  `;
}

function renderOpportunities() {
  const tokens = state.dashboard.opportunities;
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">发现</p><h1>低 FDV 未记录</h1><p class="subtle">仅列出 FDV 不高于 ${moneyCompact(state.dashboard.settings.low_fdv_limit_usd)}、链上和已确认本地交易所持仓均为零的代币。</p></div><span class="badge badge-neutral">${tokens.length} 个候选</span></header>
    <section class="panel table-panel">
      ${filterToolbar("opportunity", "可检查的候选", "“无已记录持仓”不等同于交易所实际没有资产；未录入的交易所余额无法自动识别。")}
      <div class="table-wrap"><table data-table="opportunity"><thead><tr><th>代币</th><th class="numeric">FDV</th><th class="numeric">持有人数</th><th class="numeric">Alpha 价格</th><th>匹配与价格</th><th>持仓状态</th><th></th></tr></thead>
      <tbody>${tokens.map((token) => `
        <tr data-search="${escapeHtml(`${token.symbol} ${token.name}`.toLowerCase())}"><td class="token-cell">${tokenCell(token)}</td><td class="numeric">${moneyCompact(token.fdv_usd)}</td><td class="numeric">${holderCount(token.holder_count)}</td><td class="numeric">${price(token.token_price)}</td><td>${riskCell(token)}</td><td>${stateBadge(token)}</td><td class="row-actions"><button class="button button-small" data-action="add-manual" data-contract="${token.contract_address}">登记交易所</button><button class="button button-small" data-action="edit-token" data-contract="${token.contract_address}">规则</button></td></tr>
      `).join("")}</tbody></table></div>
      ${tokens.length ? "" : '<div class="empty-state"><h2>当前没有符合条件的代币</h2><p class="subtle">可以调整低 FDV 阈值，或确认本地交易所持仓。</p></div>'}
    </section>
  `;
}

function renderReplenishments() {
  const { field, direction } = state.replenishmentSort;
  const tokens = [...state.dashboard.replenishments].sort((left, right) => {
    const primary = field === "fdv"
      ? Number(left.fdv_usd) - Number(right.fdv_usd)
      : Number(left.shortfall_usd) - Number(right.shortfall_usd);
    const secondary = field === "fdv"
      ? Number(right.shortfall_usd) - Number(left.shortfall_usd)
      : Number(left.fdv_usd) - Number(right.fdv_usd);
    const result = primary || secondary || left.symbol.localeCompare(right.symbol);
    return direction === "asc" ? result : -result;
  });
  const sortButton = (sortField, label) => {
    const active = field === sortField;
    const arrow = active ? (direction === "asc" ? "↑" : "↓") : "";
    return `<button class="button button-small sort-button ${active ? "active" : ""}" data-action="sort-replenishments" data-sort-field="${sortField}" aria-pressed="${active}">${label}${arrow ? ` ${arrow}` : ""}</button>`;
  };
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">补仓</p><h1>低 FDV 补仓清单</h1><p class="subtle">当前价值大于零且低于其目标值的已确认持仓。该清单是资金缺口，不构成买入建议。</p></div><span class="badge badge-amber">待补 ${money(state.dashboard.metrics.replenishment_shortfall_usd)}</span></header>
    <section class="panel table-panel">
      <div class="table-toolbar"><div><h2>补仓候选</h2><p class="hint">待确认的交易所余额会阻止代币进入本清单。</p></div><div class="table-toolbar-actions"><input class="filter-input" data-filter="replenishment" placeholder="筛选 Symbol 或名称"><div class="sort-controls" aria-label="补仓清单排序"><span class="sort-label">排序</span>${sortButton("fdv", "FDV")}${sortButton("shortfall", "待补金额")}</div></div></div>
      <div class="table-wrap"><table data-table="replenishment"><thead><tr><th>代币</th><th class="numeric">FDV</th><th class="numeric">合计价值</th><th class="numeric">目标值</th><th class="numeric">待补金额</th><th>匹配与价格</th><th></th></tr></thead>
      <tbody>${tokens.map((token) => `
        <tr data-search="${escapeHtml(`${token.symbol} ${token.name}`.toLowerCase())}"><td class="token-cell">${tokenCell(token)}</td><td class="numeric">${moneyCompact(token.fdv_usd)}</td><td class="numeric">${money(token.total_value_usd)}<span class="token-name">链上 ${money(token.onchain_value_usd)} · 本地 ${money(token.manual_value_usd)}</span></td><td class="numeric">${money(token.target_value_usd)}</td><td class="numeric"><strong>${money(token.shortfall_usd)}</strong></td><td>${riskCell(token)}</td><td><button class="button button-small" data-action="edit-token" data-contract="${token.contract_address}">调整规则</button></td></tr>
      `).join("")}</tbody></table></div>
      ${tokens.length ? "" : '<div class="empty-state"><h2>当前没有补仓候选</h2><p class="subtle">检查目标金额、FDV 阈值，或先刷新链上余额。</p></div>'}
    </section>
  `;
}

function renderHoldings() {
  const tokens = state.dashboard.holdings;
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">盘点</p><h1>已确认持仓</h1><p class="subtle">链上 BSC 余额与已确认本地交易所数量统一按 Alpha 价格估值。</p></div><span class="badge badge-green">${tokens.length} 个持仓</span></header>
    <section class="panel table-panel">
      ${filterToolbar("holding", "来源与目标值核对", "交易所本地余额按数量维护，价格刷新后自动重新估值。")}
      <div class="table-wrap"><table data-table="holding"><thead><tr><th>代币</th><th class="numeric">链上数量</th><th class="numeric">本地数量</th><th class="numeric">合计价值</th><th class="numeric">目标 / 差额</th><th>本地来源</th><th></th></tr></thead>
      <tbody>${tokens.map((token) => `
        <tr data-search="${escapeHtml(`${token.symbol} ${token.name}`.toLowerCase())}"><td class="token-cell">${tokenCell(token)}</td><td class="numeric">${amount(token.onchain_amount)}<span class="token-name">${money(token.onchain_value_usd)}</span></td><td class="numeric">${amount(token.manual_amount)}<span class="token-name">${money(token.manual_value_usd)}</span></td><td class="numeric"><strong>${money(token.total_value_usd)}</strong></td><td class="numeric">${money(token.target_value_usd)}<span class="token-name">待补 ${money(token.shortfall_usd)}</span></td><td>${sourceCell(token)}</td><td><button class="button button-small" data-action="edit-token" data-contract="${token.contract_address}">维护</button></td></tr>
      `).join("")}</tbody></table></div>
      ${tokens.length ? "" : '<div class="empty-state"><h2>没有已确认持仓</h2><p class="subtle">刷新钱包余额，或在本地维护页录入并确认交易所数量。</p></div>'}
    </section>
  `;
}

function dateOption(snapshot, selectedDate) {
  const label = `${snapshot.snapshot_date} · ${new Date(snapshot.fetched_at).toLocaleString("zh-CN")}`;
  return `<option value="${snapshot.snapshot_date}" ${snapshot.snapshot_date === selectedDate ? "selected" : ""}>${label}</option>`;
}

function holderChangeLabel(value) {
  if (value > 0) return `<span class="badge badge-green">+${integerFormatter.format(value)}</span>`;
  if (value < 0) return `<span class="badge badge-red">${integerFormatter.format(value)}</span>`;
  return '<span class="badge badge-neutral">0</span>';
}

function renderHolderComparison() {
  const snapshots = state.dashboard.snapshot_dates;
  if (snapshots.length < 2) {
    content.innerHTML = `
      <header class="view-heading"><div><p class="eyebrow">历史对比</p><h1>持币地址变化</h1><p class="subtle">按两个日期的 Alpha holders 数量比较，并按绝对变化值从高到低排序。</p></div></header>
      <section class="empty-state"><h2>至少需要两个不同日期的快照</h2><p class="subtle">每日定时刷新会保留一份快照。当前可继续主动刷新，但同一天的多次刷新会在日期对比中使用当天最后一份。</p></section>
    `;
    return;
  }

  const comparison = state.holderComparison;
  const fromDate = comparison?.from_snapshot.snapshot_date || snapshots[1].snapshot_date;
  const toDate = comparison?.to_snapshot.snapshot_date || snapshots[0].snapshot_date;
  const rows = comparison?.rows || [];
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">历史对比</p><h1>持币地址变化</h1><p class="subtle">数据来自 Alpha 返回的 holders 数量，不代表可枚举的具体地址名单。结果按绝对变化值降序。</p></div></header>
    <section class="panel">
      <form data-form="holder-comparison" class="form-grid">
        <div class="field"><label>起始日期</label><select name="from_date">${snapshots.map((snapshot) => dateOption(snapshot, fromDate)).join("")}</select></div>
        <div class="field"><label>结束日期</label><select name="to_date">${snapshots.map((snapshot) => dateOption(snapshot, toDate)).join("")}</select></div>
        <div class="field full form-actions"><button class="button button-primary">比较持币地址变化</button></div>
      </form>
    </section>
    ${comparison ? `
      <section class="panel table-panel" style="margin-top:16px">
        <div class="table-toolbar"><div><p class="eyebrow">比较结果</p><h2>${comparison.from_snapshot.snapshot_date} 至 ${comparison.to_snapshot.snapshot_date}</h2><p class="hint">${comparison.comparable_count} 个可比较代币；${comparison.unavailable_count} 个代币缺少至少一天的持币地址数量；${comparison.missing_token_count} 个代币仅出现在其中一天。</p></div><span class="badge badge-neutral">绝对变化排序</span></div>
        <div class="table-wrap"><table><thead><tr><th>代币</th><th class="numeric">起始持币地址</th><th class="numeric">结束持币地址</th><th class="numeric">变化</th><th class="numeric">变化比例</th><th class="numeric">结束日 FDV</th><th class="numeric">结束日价格</th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr><td class="token-cell"><span class="token-symbol">${escapeHtml(row.symbol)}</span><span class="token-name">${escapeHtml(row.name)}</span><span class="contract">${shortContract(row.contract_address)}</span></td><td class="numeric">${holderCount(row.from_holder_count)}</td><td class="numeric">${holderCount(row.to_holder_count)}</td><td class="numeric">${holderChangeLabel(row.holder_change)}</td><td class="numeric">${row.holder_change_pct === null ? "-" : `${numberFormatter.format(row.holder_change_pct)}%`}</td><td class="numeric">${moneyCompact(row.fdv_usd)}</td><td class="numeric">${price(row.token_price)}</td></tr>
        `).join("")}</tbody></table></div>
        ${rows.length ? "" : '<div class="empty-state"><h2>没有可比较的持币地址数据</h2><p class="subtle">两个日期的同一代币均需要存在 holders 数量。</p></div>'}
      </section>
    ` : ""}
  `;
}

function renderMaintenance() {
  const { manual_holdings: holdings, manual_holding_candidates: candidates } = state.dashboard;
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">本地维护</p><h1>交易所持仓匹配</h1><p class="subtle">不接交易所 API。输入交易所 Symbol 后，系统会自动匹配当前快照中的 BSC 合约；只有多个或没有候选时才需要人工处理。</p></div></header>
    <section class="maintenance-grid">
      <article class="panel"><p class="eyebrow">新增记录</p><h2>录入交易所持仓</h2>
        <form data-form="manual-holding" class="form-grid">
          <div class="field"><label>来源</label><input name="source_name" placeholder="例如 Binance" required></div>
          <div class="field"><label>交易所 Symbol</label><input name="asset_symbol" placeholder="例如 APT" required></div>
          <div class="field full"><label>数量</label><input name="amount" type="number" min="0" step="any" required><p class="hint">保存后会自动确认唯一候选；多个或无候选会保留在下方的待确认操作中。</p></div>
          <div class="field full"><label>备注</label><input name="note" placeholder="可选"></div>
          <div class="field full form-actions"><button class="button button-primary">保存本地持仓</button></div>
        </form>
      </article>
      <article class="panel"><p class="eyebrow">CSV 导入</p><h2>粘贴交易所余额</h2>
        <p class="hint">必需列：<code>source_name,asset_symbol,amount</code>。可选列：<code>contract_address,note</code>。未填写合约地址时，系统会自动确认唯一候选；其余记录会等待人工处理。</p>
        <form data-form="csv-import"><div class="field"><textarea name="csv_text" placeholder="source_name,asset_symbol,amount&#10;Binance,APT,12.5"></textarea></div><div class="form-actions"><button class="button">导入 CSV</button></div></form>
      </article>
    </section>
    <section class="panel table-panel" style="margin-top:16px">
      <div class="table-toolbar"><div><p class="eyebrow">维护队列</p><h2>本地交易所记录</h2></div><span class="badge badge-neutral">${holdings.length} 条记录</span></div>
      <div class="table-wrap"><table><thead><tr><th>来源 / Symbol</th><th class="numeric">数量</th><th>匹配结果</th><th>待确认操作</th><th>最近维护</th><th>备注</th><th></th></tr></thead>
      <tbody>${holdings.map((holding) => {
        const suggestions = candidates[String(holding.id)] || [];
        const matchResult = holding.mapping_status === "confirmed"
          ? `<span class="badge badge-green">已确认</span><span class="contract">${shortContract(holding.contract_address)}</span>`
          : '<span class="badge badge-amber">待确认</span><span class="hint">未计入持仓与补仓计算</span>';
        const candidate = holding.mapping_status === "confirmed"
          ? '<span class="hint">无需操作</span>'
          : suggestions.length
            ? `<div class="candidate-actions"><span class="hint">${suggestions.length === 1 ? "已找到唯一候选，请确认：" : `找到 ${suggestions.length} 个候选，请选择：`}</span>${suggestions.map((suggestion) => `<button class="button button-small" data-action="confirm-suggestion" data-id="${holding.id}" data-contract="${suggestion.contract_address}">确认 ${escapeHtml(suggestion.name)} · ${shortContract(suggestion.contract_address)}</button>`).join("")}</div>`
            : `<div class="candidate-actions"><span class="hint">未找到当前 BSC 合约</span><button class="button button-small" data-action="edit-manual" data-id="${holding.id}">人工关联</button></div>`;
        return `
          <tr><td><strong>${escapeHtml(holding.source_name)}</strong><span class="token-name">${escapeHtml(holding.asset_symbol)}</span></td><td class="numeric">${amount(holding.amount)}</td><td>${matchResult}</td><td>${candidate}</td><td>${new Date(holding.updated_at).toLocaleString("zh-CN")}</td><td>${escapeHtml(holding.note || "-")}</td><td class="row-actions"><button class="button button-small" data-action="edit-manual" data-id="${holding.id}">编辑</button><button class="button button-small button-danger" data-action="delete-manual" data-id="${holding.id}">删除</button></td></tr>
        `;
      }).join("")}</tbody></table></div>
      ${holdings.length ? "" : '<div class="empty-state"><h2>还没有本地交易所记录</h2><p class="subtle">先添加一条记录，或粘贴 CSV 导入。</p></div>'}
    </section>
  `;
}

function renderSettings() {
  const settings = state.dashboard.settings;
  const fdvLimitM = Number(settings.low_fdv_limit_usd) / 1_000_000;
  content.innerHTML = `
    <header class="view-heading"><div><p class="eyebrow">配置</p><h1>全局规则与钱包地址</h1><p class="subtle">修改后会立即保存到本地数据库；钱包地址在下一次刷新时生效。</p></div></header>
    <section class="settings-grid">
      <article class="panel"><p class="eyebrow">链上余额</p><h2>检查 BSC 钱包</h2>
        <form data-form="settings" class="form-grid">
          <div class="field full"><label>钱包地址（可选，每行一个）</label><textarea name="wallet_addresses">${escapeHtml(settings.wallet_addresses)}</textarea><p class="hint">填写后，刷新会按所有地址查询并汇总代币数量；留空时仅刷新市场数据。</p></div>
          <div class="field"><label>每日自动刷新时间（新加坡时间）</label><input name="scheduled_refresh_time" type="time" value="${escapeHtml(settings.scheduled_refresh_time)}" required></div>
          <div class="field"><label>默认初始买入金额（USD）</label><input name="default_initial_purchase_usd" type="number" min="0" step="any" value="${escapeHtml(settings.default_initial_purchase_usd)}" required></div>
          <div class="field"><label>默认补仓目标（USD）</label><input name="default_target_value_usd" type="number" min="0" step="any" value="${escapeHtml(settings.default_target_value_usd)}" required></div>
          <div class="field"><label>低 FDV 阈值（M USD）</label><input name="low_fdv_limit_m" type="number" min="0" step="any" value="${fdvLimitM}" required></div>
          <div class="field"><label>价格偏离提醒（%）</label><input name="price_gap_alert_pct" type="number" min="0" step="any" value="${escapeHtml(settings.price_gap_alert_pct)}" required></div>
          <div class="field full form-actions"><button class="button button-primary">保存全局设置</button></div>
        </form>
      </article>
      <article class="panel"><p class="eyebrow">口径说明</p><h2>规则如何影响清单</h2>
        <dl class="definition-list">
          <dt>低 FDV 未记录</dt><dd>FDV 不高于阈值，且没有链上或已确认本地余额</dd>
          <dt>补仓候选</dt><dd>已持有、低于目标值、低 FDV、未排除</dd>
          <dt>初始买入金额</dt><dd>用于对照，不作为已实现成本或盈亏</dd>
          <dt>单币覆盖</dt><dd>在任意代币的“规则”中设置，优先于全局默认值</dd>
          <dt>估值价格</dt><dd>Alpha Token Price；与合约价格差异会显式提示</dd>
        </dl>
        <div class="divider"></div><p class="hint">当前来源提供 FDV，而非已验证的流通市值。页面中的“低 FDV”不会表述为低市值。</p>
      </article>
    </section>
  `;
}

function tokenByContract(contract) {
  return state.dashboard.tokens.find((token) => token.contract_address === contract);
}

function checkbox(name, checked, label) {
  return `<label class="checkbox-label"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}>${label}</label>`;
}

function openTokenModal(contract) {
  const token = tokenByContract(contract);
  if (!token) return;
  modal.innerHTML = `
    <section class="modal-content">
      <div class="modal-heading"><div><p class="eyebrow">单币规则</p><h2>${escapeHtml(token.symbol)} · ${escapeHtml(token.name)}</h2><p class="hint">${shortContract(token.contract_address)} · 当前合计 ${money(token.total_value_usd)}</p></div><button class="icon-button" data-action="close-modal" aria-label="关闭">×</button></div>
      <form data-form="preference" class="form-grid">
        <input type="hidden" name="contract_address" value="${token.contract_address}">
        <div class="field"><label>初始买入金额（USD）</label><input name="initial_purchase_usd" type="number" min="0" step="any" value="${token.initial_purchase_usd}"></div>
        <div class="field"><label>补仓目标（USD）</label><input name="target_value_usd" type="number" min="0" step="any" value="${token.target_value_usd}"></div>
        <div class="field full">${checkbox("replenish_enabled", token.replenish_enabled, "允许按规则进入补仓清单")}</div>
        <div class="field full">${checkbox("ignored", token.ignored, "从机会池与补仓清单中排除")}</div>
        <div class="field full"><label>备注</label><textarea name="note" placeholder="可选">${escapeHtml(token.note)}</textarea></div>
        <div class="field full form-actions"><button class="button button-primary">保存单币规则</button></div>
      </form>
      <div class="divider"></div>
      <p class="eyebrow">本地交易所持仓</p><h2>为 ${escapeHtml(token.symbol)} 登记数量</h2>
      <form data-form="manual-holding" class="form-grid">
        <input type="hidden" name="contract_address" value="${token.contract_address}"><input type="hidden" name="asset_symbol" value="${escapeHtml(token.symbol)}">
        <div class="field"><label>来源</label><input name="source_name" placeholder="例如 Binance" required></div>
        <div class="field"><label>数量</label><input name="amount" type="number" min="0" step="any" required></div>
        <div class="field full"><label>备注</label><input name="note" placeholder="可选"></div>
        <div class="field full form-actions"><button class="button">保存已确认的本地持仓</button></div>
      </form>
    </section>
  `;
  modal.showModal();
}

function openManualModal(id) {
  const holding = state.dashboard.manual_holdings.find((item) => item.id === Number(id));
  if (!holding) return;
  modal.innerHTML = `
    <section class="modal-content">
      <div class="modal-heading"><div><p class="eyebrow">编辑本地持仓</p><h2>${escapeHtml(holding.source_name)} · ${escapeHtml(holding.asset_symbol)}</h2></div><button class="icon-button" data-action="close-modal" aria-label="关闭">×</button></div>
      <form data-form="manual-holding" data-id="${holding.id}" class="form-grid">
        <div class="field"><label>来源</label><input name="source_name" value="${escapeHtml(holding.source_name)}" required></div>
        <div class="field"><label>交易所 Symbol</label><input name="asset_symbol" value="${escapeHtml(holding.asset_symbol)}" required></div>
        <div class="field"><label>数量</label><input name="amount" type="number" min="0" step="any" value="${escapeHtml(holding.amount)}" required></div>
        <div class="field full"><label>BSC 合约地址（可选）</label><input name="contract_address" value="${escapeHtml(holding.contract_address)}" placeholder="0x..."><p class="hint">填写合约地址会人工确认；留空时系统会按 Symbol 自动确认唯一候选。</p></div>
        <div class="field full"><label>备注</label><textarea name="note">${escapeHtml(holding.note)}</textarea></div>
        <div class="field full form-actions"><button class="button button-primary">保存修改</button></div>
      </form>
    </section>
  `;
  modal.showModal();
}

function render() {
  updateNavigation();
  if (!state.dashboard?.has_snapshot) {
    if (state.view === "settings") return renderSettings();
    if (state.view === "maintenance") return renderMaintenance();
    return renderEmpty();
  }
  if (state.view === "overview") return renderOverview();
  if (state.view === "opportunities") return renderOpportunities();
  if (state.view === "replenishments") return renderReplenishments();
  if (state.view === "holdings") return renderHoldings();
  if (state.view === "holder-comparison") return renderHolderComparison();
  if (state.view === "maintenance") return renderMaintenance();
  return renderSettings();
}

async function refresh() {
  try {
    setRefreshing(true);
    showNotice("正在获取 Binance 市场数据并逐个查询配置的钱包余额。", false, false);
    const result = await request("/api/refresh", { method: "POST", body: "{}" });
    await loadDashboard();
    showNotice(`已保存快照：${result.total_count} 个合约匹配代币。${result.auto_confirmed_count ? `已自动确认 ${result.auto_confirmed_count} 条交易所记录。` : ""}`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    setRefreshing(false);
  }
}

async function saveForm(form) {
  const values = new FormData(form);
  const type = form.dataset.form;
  try {
    if (type === "settings") {
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          wallet_addresses: values.get("wallet_addresses"),
          default_initial_purchase_usd: values.get("default_initial_purchase_usd"),
          default_target_value_usd: values.get("default_target_value_usd"),
          low_fdv_limit_usd: Number(values.get("low_fdv_limit_m")) * 1_000_000,
          price_gap_alert_pct: values.get("price_gap_alert_pct"),
          scheduled_refresh_time: values.get("scheduled_refresh_time"),
        }),
      });
      await loadDashboard();
      showNotice("全局设置已保存。钱包地址会在下一次刷新时生效。");
      return;
    }
    if (type === "preference") {
      await request("/api/preferences", {
        method: "POST",
        body: JSON.stringify({
          contract_address: values.get("contract_address"),
          initial_purchase_usd: values.get("initial_purchase_usd"),
          target_value_usd: values.get("target_value_usd"),
          replenish_enabled: values.has("replenish_enabled"),
          ignored: values.has("ignored"),
          note: values.get("note"),
        }),
      });
      modal.close();
      await loadDashboard();
      showNotice("单币规则已保存。");
      return;
    }
    if (type === "holder-comparison") {
      const fromDate = values.get("from_date");
      const toDate = values.get("to_date");
      state.holderComparison = await request(
        `/api/holder-comparison?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`
      );
      render();
      return;
    }
    if (type === "manual-holding") {
      const payload = {
        source_name: values.get("source_name"),
        asset_symbol: values.get("asset_symbol"),
        amount: values.get("amount"),
        contract_address: values.get("contract_address"),
        note: values.get("note"),
      };
      const id = form.dataset.id;
      const result = await request(id ? `/api/manual-holdings/${id}` : "/api/manual-holdings", {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      if (modal.open) modal.close();
      await loadDashboard();
      if (result.auto_matched) {
        showNotice(`已自动匹配 ${shortContract(result.contract_address)}，余额已计入总持仓。`);
      } else if (result.mapping_status === "pending") {
        showNotice(result.candidate_count ? `找到 ${result.candidate_count} 个候选合约，请在维护队列中选择。` : "未找到当前 BSC 合约，请在维护队列中人工关联。");
      } else {
        showNotice("本地交易所持仓已保存。");
      }
      return;
    }
    if (type === "csv-import") {
      const result = await request("/api/manual-holdings/import", {
        method: "POST",
        body: JSON.stringify({ csv_text: values.get("csv_text") }),
      });
      await loadDashboard();
      showNotice(`已导入 ${result.imported} 条本地持仓记录。`);
    }
  } catch (error) {
    showNotice(error.message, true);
  }
}

document.addEventListener("click", async (event) => {
  const view = event.target.closest("[data-view]");
  if (view) {
    event.preventDefault();
    state.view = view.dataset.view;
    if (
      state.view === "holder-comparison" &&
      state.dashboard.snapshot_dates.length >= 2 &&
      state.holderComparison === null
    ) {
      try {
        const [toSnapshot, fromSnapshot] = state.dashboard.snapshot_dates;
        state.holderComparison = await request(
          `/api/holder-comparison?from_date=${encodeURIComponent(fromSnapshot.snapshot_date)}&to_date=${encodeURIComponent(toSnapshot.snapshot_date)}`
        );
      } catch (error) {
        showNotice(error.message, true);
      }
    }
    render();
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (target.dataset.action === "refresh") return refresh();
  if (target.dataset.action === "close-modal") return modal.close();
  if (target.dataset.action === "sort-replenishments") {
    const field = target.dataset.sortField;
    const current = state.replenishmentSort;
    state.replenishmentSort = current.field === field
      ? { field, direction: current.direction === "asc" ? "desc" : "asc" }
      : { field, direction: field === "fdv" ? "asc" : "desc" };
    return render();
  }
  if (target.dataset.action === "edit-token" || target.dataset.action === "add-manual") {
    return openTokenModal(target.dataset.contract);
  }
  if (target.dataset.action === "edit-manual") return openManualModal(target.dataset.id);
  if (target.dataset.action === "delete-manual") {
    const holding = state.dashboard.manual_holdings.find((item) => item.id === Number(target.dataset.id));
    if (!holding || !window.confirm(`删除 ${holding.source_name} 的 ${holding.asset_symbol} 记录？`)) return;
    try {
      await request(`/api/manual-holdings/${holding.id}`, { method: "DELETE" });
      await loadDashboard();
      showNotice("本地持仓记录已删除。");
    } catch (error) {
      showNotice(error.message, true);
    }
    return;
  }
  if (target.dataset.action === "confirm-suggestion") {
    const holding = state.dashboard.manual_holdings.find((item) => item.id === Number(target.dataset.id));
    if (!holding) return;
    try {
      await request(`/api/manual-holdings/${holding.id}`, {
        method: "PUT",
        body: JSON.stringify({
          source_name: holding.source_name,
          asset_symbol: holding.asset_symbol,
          amount: holding.amount,
          contract_address: target.dataset.contract,
          note: holding.note,
        }),
      });
      await loadDashboard();
      showNotice("候选合约已确认，余额已计入总持仓。");
    } catch (error) {
      showNotice(error.message, true);
    }
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-form]");
  if (!form) return;
  event.preventDefault();
  saveForm(form);
});

document.addEventListener("input", (event) => {
  const input = event.target.closest("[data-filter]");
  if (!input) return;
  const table = document.querySelector(`table[data-table="${input.dataset.filter}"]`);
  if (!table) return;
  const query = input.value.trim().toLowerCase();
  table.querySelectorAll("tbody tr").forEach((row) => {
    row.hidden = Boolean(query) && !row.dataset.search.includes(query);
  });
});

refreshButton.addEventListener("click", refresh);
loadDashboard().catch((error) => {
  showNotice(error.message, true);
  content.innerHTML = '<section class="empty-state"><h2>无法连接本地服务</h2><p class="subtle">请确认 dashboard_server.py 正在运行。</p></section>';
});
