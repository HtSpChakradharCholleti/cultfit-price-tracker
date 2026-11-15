/**
 * Cult.fit Price Tracker — dashboard.js
 * Vanilla JS, no build tools required.
 */

/* ------------------------------------------------------------------ */
/* State                                                                 */
/* ------------------------------------------------------------------ */
let chartInstance = null;
let currentDays = 7;
let _tableData = [];
let _sortKey = 'observed_at';
let _sortAsc = false; // newest first by default
const mobileChart = window.matchMedia('(max-width: 600px)');

/* ------------------------------------------------------------------ */
/* 1. Plan Selection                                                     */
/* ------------------------------------------------------------------ */
function selectPlan(planId) {
  // Update active tab UI
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.planId) === planId);
  });

  // Update tracked state
  window.TRACKER_DATA.selectedPlanId = planId;
  updateSelectedPlanLabel(planId);
  setPlanSelectorOpen(false);

  // Push URL change without reload
  history.pushState({ planId }, '', `?plan=${planId}`);

  // Load data for the newly selected plan
  loadPlanData(planId, currentDays);
}

function updateSelectedPlanLabel(planId) {
  const label = document.getElementById('selectedPlanLabel');
  const plan = (window.TRACKER_DATA.plansJson || []).find(p => p.id === planId);
  if (label && plan) {
    label.textContent = `${plan.name} · ${plan.duration_label}`;
  }
}

function setPlanSelectorOpen(isOpen) {
  const panel = document.getElementById('planSelectorPanel');
  const button = document.getElementById('changePlanBtn');
  if (!panel || !button) return;
  panel.hidden = !isOpen;
  button.setAttribute('aria-expanded', String(isOpen));
  button.textContent = isOpen ? 'Close plans' : 'Change plan';
}

function togglePlanSelector() {
  const panel = document.getElementById('planSelectorPanel');
  if (panel) setPlanSelectorOpen(panel.hidden);
}

/* ------------------------------------------------------------------ */
/* 2. Data Fetching                                                      */
/* ------------------------------------------------------------------ */
async function loadPlanData(planId, days = null) {
  if (!planId) return;

  try {
    const [historyData, statsData, offerData, offerHistory] = await Promise.all([
      fetchHistory(planId, days),
      fetchStatistics(planId),
      fetchOffers(planId),
      fetchOfferHistory(planId, days),
    ]);

    // Store for sorting
    _tableData = historyData || [];

    updatePriceCard(historyData, statsData, planId, offerData);
    updateOffersPanel(offerData?.offers || [], offerData?.summary || {});
    updateStatsCard(statsData);
    initChart(historyData);
    renderTable(historyData, statsData);
    renderOfferHistory(offerHistory || []);
  } catch (err) {
    console.error('loadPlanData error:', err);
    showToast('Failed to load plan data. Please try again.', 'error');
  }
}

async function fetchHistory(planId, days) {
  const params = days ? `?days=${days}` : '';
  const resp = await fetch(`/api/plans/${planId}/history/${params}`);
  if (!resp.ok) throw new Error(`History fetch failed: ${resp.status}`);
  return resp.json();
}

async function fetchStatistics(planId) {
  const resp = await fetch(`/api/plans/${planId}/statistics/`);
  if (!resp.ok) throw new Error(`Statistics fetch failed: ${resp.status}`);
  return resp.json();
}

async function fetchOffers(planId) {
  const resp = await fetch(`/api/plans/${planId}/offers/`);
  if (!resp.ok) throw new Error(`Offers fetch failed: ${resp.status}`);
  return resp.json();
}

async function fetchOfferHistory(planId, days) {
  const params = days ? `?days=${days}` : '';
  const resp = await fetch(`/api/plans/${planId}/offers/history/${params}`);
  if (!resp.ok) throw new Error(`Offer history fetch failed: ${resp.status}`);
  return resp.json();
}

function renderOfferHistory(historyData) {
  const tbody = document.getElementById('offerHistoryBody');
  const count = document.getElementById('offerHistoryCount');
  if (!tbody) return;
  if (!historyData.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No meaningful offer changes yet.</td></tr>';
    if (count) count.textContent = '';
    return;
  }
  const dateFormatter = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'long',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
  const dateKeyFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const groups = new Map();
  historyData
    .map((offer, index) => ({ offer, index, timestamp: new Date(offer.observed_at) }))
    .sort((a, b) => {
      const timestampOrder = b.timestamp - a.timestamp;
      return timestampOrder || b.index - a.index;
    })
    .forEach(({ offer, timestamp }) => {
      const dateKey = dateKeyFormatter.format(timestamp);
      if (!groups.has(dateKey)) {
        groups.set(dateKey, {
          label: dateFormatter.format(timestamp),
          records: [],
        });
      }
      groups.get(dateKey).records.push(offer);
    });

  tbody.innerHTML = Array.from(groups.values()).map(group => `
    <tr class="offer-history-date-group">
      <th colspan="5" scope="rowgroup">${escapeHtml(group.label)}
        <span>${group.records.length} ${group.records.length === 1 ? 'record' : 'records'}</span>
      </th>
    </tr>
    ${group.records.map(offer => `<tr>
      <td>${formatDateIST(offer.observed_at)}</td>
      <td>${escapeHtml(offer.event_type || offer.status)}</td>
      <td>${escapeHtml(offer.category_group || offer.category || 'Other')}</td>
      <td>${escapeHtml(offer.description || offer.title || '—')}</td>
      <td>${escapeHtml(offer.coupon_code || '—')}</td>
    </tr>`).join('')}
  `).join('');
  if (count) count.textContent = `${historyData.length} records`;
}

/* ------------------------------------------------------------------ */
/* 3. Price Card Update (dynamic, after plan switch)                     */
/* ------------------------------------------------------------------ */
function renderOfferItem(offer) {
  const category = String(offer.category_group || 'Other benefits').toLowerCase().replace(/\s+/g, '-');
  let html = `<div class="offer-item" data-category="${escapeHtml(category)}"><strong>${escapeHtml(offer.title || 'Offer')}</strong>`;
  html += `<span>${escapeHtml(offer.description || 'No description provided.')}</span>`;
  html += `<small>${escapeHtml(offer.category_group || 'Other benefits')} · ${escapeHtml(offer.availability || 'available')}</small>`;
  if (offer.coupon_code) {
    html += `<code>${escapeHtml(offer.coupon_code)}</code>`;
  }
  return `${html}</div>`;
}

function updateOffersPanel(offers, summary = {}) {
  const body = document.getElementById('offersBody');
  const count = document.getElementById('offersCount');
  const sectionToggle = document.getElementById('offersSectionToggle');
  if (!body || !count || !sectionToggle) return;

  count.textContent = offers.length ? `${offers.length} current` : '';
  body.hidden = true;
  sectionToggle.setAttribute('aria-expanded', 'false');
  sectionToggle.textContent = 'Show offers';

  if (!offers.length) {
    body.innerHTML = '<div class="no-data">No current offers available for this plan.</div>';
    return;
  }

  const categorySummary = Object.entries(summary.counts || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<span>${escapeHtml(key)} <strong>${value}</strong></span>`)
    .join('');
  const verifiedAt = summary.verified_at ? formatDateIST(summary.verified_at) : 'Unknown';
  body.innerHTML = `
    <div id="offerCategorySummary" class="offer-category-summary">${categorySummary}</div>
    <p class="offer-verification">Last verified: <span>${escapeHtml(verifiedAt)}</span></p>
    <div id="offerList" class="offer-list">${offers.map(renderOfferItem).join('')}</div>`;
}

function toggleOffersSection(button) {
  const body = document.getElementById('offersBody');
  if (!body) return;
  const isOpen = !body.hidden;
  body.hidden = isOpen;
  button.setAttribute('aria-expanded', String(!isOpen));
  button.textContent = isOpen ? 'Show offers' : 'Hide offers';
}

function toggleAllOffers(button) {
  const extra = document.getElementById('offerExtraList');
  if (!extra) return;
  const isOpen = !extra.hidden;
  extra.hidden = isOpen;
  button.setAttribute('aria-expanded', String(!isOpen));
  button.textContent = isOpen ? 'View all offers' : 'Hide extra offers';
}

function updatePriceCard(historyData, stats, planId, offerData = {}) {
  const card = document.getElementById('priceCard');
  if (!card) return;

  // Find the plan info
  const plan = (window.TRACKER_DATA.plansJson || []).find(p => p.id === planId);
  const tier = plan ? plan.tier : '';

  // Latest price is the newest observation by timestamp
  const latest = historyData && historyData.length > 0
    ? historyData.slice().sort((a, b) => new Date(b.observed_at) - new Date(a.observed_at))[0]
    : null;

  const header = card.querySelector('.card-header');
  if (header) {
    header.innerHTML = `
      <span class="plan-name">Price summary</span>
      <span class="tier-badge tier-${tier.toLowerCase()}">${escapeHtml(tier)}</span>
    `;
  }

  const body = card.querySelector('.card-body');
  if (!body) return;

  if (!latest) {
    body.innerHTML = '<div class="no-data">No price data yet. Click Refresh Price.</div>';
    return;
  }

  const sellingFmt = formatINR(latest.selling_price);
  const mrpFmt = formatINR(latest.original_price);
  const discount = latest.discount_percentage || 0;
  const isLow = stats && stats.is_at_historical_low;
  const minFmt = stats && stats.min_price ? formatINR(stats.min_price) : null;

  let html = `<div class="price-main">₹${sellingFmt}</div>`;
  const monthlyCost = latest.monthly_cost ?? calculateMonthlyCost(
    latest.selling_price,
    plan?.duration_months,
  );
  if (monthlyCost !== null) {
    html += `<div class="monthly-cost">Regular monthly cost: ₹${formatINRDecimal(monthlyCost)}/month</div>`;
  }

  const previous = historyData && historyData.length > 1
    ? historyData.slice().sort((a, b) => new Date(b.observed_at) - new Date(a.observed_at))[1]
    : null;
  if (previous) {
    const delta = latest.selling_price - previous.selling_price;
    const changeClass = delta < 0 ? 'text-success' : delta > 0 ? 'text-danger' : 'text-muted';
    const changeText = delta < 0
      ? `↓ ₹${formatINR(Math.abs(delta))}`
      : delta > 0
        ? `↑ ₹${formatINR(delta)}`
        : 'No change';
    html += `<div class="price-change ${changeClass}"><span>Previous price: ₹${formatINR(previous.selling_price)}</span>${changeText}</div>`;
  } else {
    html += '<div class="price-change text-muted">Previous price: Not enough history</div>';
  }

  if (latest.original_price > latest.selling_price) {
    html += `<div class="price-mrp">MRP ₹${mrpFmt}</div>`;
  }

  if (discount > 0) {
    html += `<div class="discount-badge">${discount.toFixed(1)}% OFF</div>`;
  }

  if (latest.offer_description) {
    html += `<div class="offer-text">🎁 ${escapeHtml(latest.offer_description)}</div>`;
  }

  const effective = offerData.effective_price;
  if (effective) {
    html += '<div class="price-breakdown">';
    if (effective.instant_discount > 0) {
      html += `<div class="text-success">Instant discount: −₹${formatINR(effective.instant_discount)}</div>`;
    }
    if (effective.conditional_discount > 0) {
      html += `<div class="text-warning">Conditional discount: −₹${formatINR(effective.conditional_discount)}</div>`;
    }
    if (effective.cashback > 0) {
      html += `<div class="text-muted">Cashback: ₹${formatINR(effective.cashback)}</div>`;
    }
    if (effective.potential_effective_price !== null) {
      html += `<div class="price-effective">Potential effective price: ₹${formatINR(effective.potential_effective_price)}</div>`;
    }
    if (effective.effective_monthly_cost != null) {
      const duration = Number(plan?.duration_months);
      if (effective.advertised_price != null && Number.isFinite(duration) && duration > 0) {
        html += `<div class="effective-monthly-breakdown"><span>After instant discount:</span><span>(₹${formatINR(effective.advertised_price)} − ₹${formatINR(effective.instant_discount || 0)}) ÷ ${duration} = ₹${formatINRDecimal(effective.effective_monthly_cost)}/month</span></div>`;
      } else {
        html += `<div class="price-effective">Effective monthly cost: ₹${formatINRDecimal(effective.effective_monthly_cost)}</div>`;
      }
    }
    if (effective.excluded_benefit_count > 0) {
      html += '<div class="text-muted benefit-note">Coupons and non-monetary benefits are excluded from this calculation.</div>';
    }
    html += '</div>';
  }

  html += `<div class="observed-at text-muted">Last seen: ${formatDateIST(latest.observed_at)}</div>`;

  if (minFmt) {
    html += `<div class="hist-min text-muted">Historical low: ₹${minFmt}</div>`;
    if (isLow) {
      html += `<div class="hist-low-badge">🏆 Historical Low!</div>`;
    }
  }

  body.innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* 4. Scrape Trigger                                                     */
/* ------------------------------------------------------------------ */
async function triggerScrape() {
  const btn = document.getElementById('refreshBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Scraping…';
  }

  const overlay = document.getElementById('loadingOverlay');
  if (overlay) overlay.style.display = 'flex';

  try {
    const resp = await fetch('/api/scraper/run/', { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    showToast('Scrape started — polling for status…', 'info');

    await new Promise((resolve, reject) => pollScraperStatus(resolve, reject));

    showToast('Prices refreshed successfully!', 'success');

    // Reload current plan data
    if (window.TRACKER_DATA?.selectedPlanId) {
      await loadPlanData(window.TRACKER_DATA.selectedPlanId, currentDays);
    }

    // Refresh last-scraped label
    refreshScraperMeta();
  } catch (err) {
    console.error('triggerScrape error:', err);
    showToast(`Scrape failed: ${err.message}`, 'error');
    refreshScraperMeta();
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '↺ Refresh Price';
    }
    if (overlay) overlay.style.display = 'none';
  }
}

function pollScraperStatus(resolve, reject, attempt = 0) {
  const MAX_POLLS = 90; // 3 minutes max
  if (attempt > MAX_POLLS) {
    reject(new Error('Scrape timed out after 3 minutes'));
    return;
  }

  setTimeout(async () => {
    try {
      const resp = await fetch('/api/scraper/status/');
      if (!resp.ok) throw new Error(`Status ${resp.status}`);
      const data = await resp.json();

      if (!data.status || data.status === 'running') {
        pollScraperStatus(resolve, reject, attempt + 1);
      } else if (data.status === 'success') {
        resolve(data);
      } else {
        reject(new Error(data.error_message || 'Scrape failed'));
      }
    } catch (err) {
      pollScraperStatus(resolve, reject, attempt + 1);
    }
  }, 2000);
}

async function refreshScraperMeta() {
  try {
    const resp = await fetch('/api/scraper/status/');
    if (!resp.ok) return;
    const data = await resp.json();

    const scraped = document.querySelector('.last-scraped');
    const latestCompleted = data.last_successful?.completed_at;
    if (scraped && latestCompleted) {
      scraped.textContent = `Last scraped: ${formatDateIST(latestCompleted)}`;
    }

    const badge = document.querySelector('.status-badge');
    if (badge && data.today_state) {
      const badgeStatus = data.today_state === 'complete'
        ? 'success'
        : data.today_state === 'pending' ? 'running' : 'failed';
      badge.className = `status-badge status-${badgeStatus}`;
      badge.textContent = data.today_state.toUpperCase();
    }
    updateScrapeHealth(data);
  } catch (_) {}
}

function updateScrapeHealth(data) {
  const label = document.getElementById('scrapeHealthLabel');
  const dot = document.querySelector('#scrapeHealth .health-dot');
  const lastAttempt = document.getElementById('scrapeLastAttempt');
  const lastSuccess = document.getElementById('scrapeLastSuccess');
  const nextRun = document.getElementById('scrapeNextRun');
  if (!label || !dot) return;

  const state = data.today_state || 'pending';
  const dotStatus = state === 'complete' ? 'success' : state === 'pending' ? 'running' : 'failed';
  label.textContent = `Today's scrape: ${state.toUpperCase()}`;
  dot.className = `health-dot status-${dotStatus}`;
  if (lastAttempt) {
    lastAttempt.textContent = `Last attempt: ${
      data.last_attempt?.started_at
        ? formatDateIST(data.last_attempt.started_at)
        : 'Never'
    }`;
  }
  if (lastSuccess) {
    lastSuccess.textContent = `Last success: ${
      data.last_successful?.completed_at
        ? formatDateIST(data.last_successful.completed_at)
        : 'Never'
    }`;
  }
  if (nextRun && data.next_scheduled_run) {
    nextRun.textContent = `Next run: ${formatDateIST(data.next_scheduled_run)} IST`;
  }
}

/* ------------------------------------------------------------------ */
/* 5. Chart                                                              */
/* ------------------------------------------------------------------ */
function initChart(historyData) {
  const canvas = document.getElementById('priceHistoryChart');
  const noData = document.getElementById('chartNoData');

  if (!canvas) return;

  // Destroy previous instance
  if (chartInstance) {
    chartInstance.destroy();
    chartInstance = null;
  }

  if (!historyData || historyData.length === 0) {
    if (noData) noData.style.display = 'flex';
    canvas.parentElement.style.display = 'none';
    return;
  }

  if (noData) noData.style.display = 'none';
  canvas.parentElement.style.display = '';

  // Sort chronologically ascending for the chart timeline
  const sorted = historyData.slice().sort((a, b) => new Date(a.observed_at) - new Date(b.observed_at));

  const labels = sorted.map(o => formatDateIST(o.observed_at));
  const sellingPrices = sorted.map(o => o.selling_price);
  const mrpPrices = sorted.map(o => o.original_price);

  const ctx = canvas.getContext('2d');

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Selling Price',
          data: sellingPrices,
          borderColor: '#6c63ff',
          backgroundColor: 'rgba(108, 99, 255, 0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#6c63ff',
        },
        {
          label: 'MRP',
          data: mrpPrices,
          borderColor: '#475569',
          borderDash: [5, 5],
          fill: false,
          tension: 0.3,
          pointRadius: 2,
          pointBackgroundColor: '#475569',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          labels: { color: '#e2e8f0', boxWidth: 20, font: { family: 'Inter', size: 12 } },
        },
        tooltip: {
          backgroundColor: '#1a1d2e',
          borderColor: '#2d3148',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#94a3b8',
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ₹${formatINR(ctx.raw)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: '#94a3b8',
            font: { family: 'Inter', size: 11 },
            maxTicksLimit: mobileChart.matches ? 3 : 8,
            maxRotation: mobileChart.matches ? 0 : 50,
            callback(value) {
              const label = this.getLabelForValue(value);
              return mobileChart.matches ? label.split(',')[0] : label;
            },
          },
          grid: { color: '#2d3148' },
        },
        y: {
          ticks: {
            color: '#94a3b8',
            font: { family: 'Inter', size: 11 },
            callback: v => '₹' + formatINR(v),
          },
          grid: { color: '#2d3148' },
        },
      },
    },
  });
}

/* ------------------------------------------------------------------ */
/* 6. Table Rendering                                                    */
/* ------------------------------------------------------------------ */
function renderTable(historyData, statistics) {
  const tbody = document.getElementById('observationsBody');
  const countEl = document.getElementById('observationCount');

  if (!tbody) return;

  if (!historyData || historyData.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No observations yet. Click Refresh Price to start tracking.</td></tr>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const minPrice = statistics?.min_price;
  const maxPrice = statistics?.max_price;

  // Sort
  const sorted = historyData.slice().sort((a, b) => {
    let aVal = a[_sortKey] ?? '';
    let bVal = b[_sortKey] ?? '';

    if (typeof aVal === 'string') aVal = aVal.toLowerCase();
    if (typeof bVal === 'string') bVal = bVal.toLowerCase();

    if (aVal < bVal) return _sortAsc ? -1 : 1;
    if (aVal > bVal) return _sortAsc ? 1 : -1;
    return 0;
  });

  tbody.innerHTML = sorted.map(obs => {
    const isLow = minPrice != null && obs.selling_price <= minPrice * 1.02;
    const isHigh = maxPrice != null && obs.selling_price >= maxPrice * 0.98;
    const priceClass = isLow ? 'text-success' : isHigh ? 'text-danger' : 'text-warning';
    const monthlyCost = obs.monthly_cost ?? calculateMonthlyCost(obs.selling_price, obs.duration_months);
    return `<tr>
      <td>${formatDateIST(obs.observed_at)}</td>
      <td>₹${formatINR(obs.original_price)}</td>
      <td class="price-cell selling-price ${priceClass}">₹${formatINR(obs.selling_price)}</td>
      <td>${(obs.discount_percentage || 0).toFixed(1)}%</td>
      <td class="monthly-cost-cell">${monthlyCost === null ? '—' : `₹${formatINRDecimal(monthlyCost)}`}</td>
      <td class="offer-cell">${renderObservationOffer(obs.offer_description)}</td>
    </tr>`;
  }).join('');

  if (countEl) countEl.textContent = `${historyData.length} records`;
}

function renderObservationOffer(description) {
  if (!description) return '<span class="offer-count-empty">0 offers</span>';
  const text = String(description);
  const count = Math.max(1, text.split(/\s*(?:\||;|\n)\s*/).filter(Boolean).length);
  const label = `${count} offer${count === 1 ? '' : 's'}`;
  return `<details class="offer-details">
    <summary>${label}</summary>
    <span>${escapeHtml(text)}</span>
  </details>`;
}

function calculateMonthlyCost(price, durationMonths) {
  const duration = Number(durationMonths);
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice) || !Number.isFinite(duration) || duration <= 0) {
    return null;
  }
  return Math.round((numericPrice / duration) * 100) / 100;
}

/* ------------------------------------------------------------------ */
/* 7. Stats Card Update (dynamic)                                        */
/* ------------------------------------------------------------------ */
function updateStatsCard(stats) {
  const card = document.getElementById('statsCard');
  if (!card) return;

  const body = card.querySelector('.card-body');
  if (!body) return;

  if (!stats) {
    body.innerHTML = `
      <div class="insufficient-data">
        <span>📊</span>
        <p>No price data yet. Click Refresh Price to collect the first observation.</p>
      </div>`;
    return;
  }

  if (!stats.has_sufficient_data) {
    if (stats.observation_count === 1) {
      body.innerHTML = `
        <div class="insufficient-data">
          <span>📈</span>
          <p>Only 1 observation. Collecting more data to show trends...</p>
        </div>`;
    } else {
      body.innerHTML = `
        <div class="insufficient-data">
          <span>📊</span>
          <p>No price data yet. Click Refresh Price to collect the first observation.</p>
        </div>`;
    }
    return;
  }

  const vsMinAbsClass = (stats.vs_min_abs > 0) ? 'text-warning' : 'text-success';
  const vsMinAbsPrefix = stats.vs_min_abs > 0 ? '+' : '';

  let html = `<div class="stats-grid">
    <div class="stat-item stat-current">
      <div class="stat-value text-primary">₹${formatINR(stats.current_price)}</div>
      <div class="stat-label">Current Price</div>
    </div>
    <div class="stat-item">
      <div class="stat-value text-success">₹${formatINR(stats.min_price)}</div>
      <div class="stat-label">All-time Low</div>
    </div>
    <div class="stat-item">
      <div class="stat-value text-danger">₹${formatINR(stats.max_price)}</div>
      <div class="stat-label">All-time High</div>
    </div>
    <div class="stat-item">
      <div class="stat-value">₹${formatINR(stats.avg_price)}</div>
      <div class="stat-label">Historical Average</div>
    </div>
    <div class="stat-item">
      <div class="stat-value text-muted">${stats.observation_count}</div>
      <div class="stat-label">Observations</div>
    </div>`;

  if (stats.vs_min_abs != null) {
    html += `
    <div class="stat-item">
      <div class="stat-value ${vsMinAbsClass}">${vsMinAbsPrefix}₹${formatINR(Math.abs(stats.vs_min_abs))}</div>
      <div class="stat-label">vs Historical Low (₹)</div>
    </div>`;
  }

  html += `</div>`;
  body.innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* 8. Toast Notifications                                               */
/* ------------------------------------------------------------------ */
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : 'ℹ';
  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.animation = 'toastOut 0.25s ease forwards';
    setTimeout(() => toast.remove(), 260);
  }, 4000);
}

/* ------------------------------------------------------------------ */
/* 9. Table Sorting                                                      */
/* ------------------------------------------------------------------ */
function setupTableSorting() {
  document.querySelectorAll('.data-table th.sortable').forEach(th => {
    const sort = () => {
      const key = th.dataset.sort;
      if (!key) return;

      if (_sortKey === key) {
        _sortAsc = !_sortAsc;
      } else {
        _sortKey = key;
        _sortAsc = true;
      }

      // Update header indicators
      document.querySelectorAll('.data-table th.sortable').forEach(h => {
        const baseText = h.textContent.replace(/[↑↓↕]/g, '').trim();
        if (h === th) {
          h.textContent = `${baseText} ${_sortAsc ? '↑' : '↓'}`;
        } else {
          h.textContent = baseText;
        }
      });

      // Re-render with current data
      renderTable(_tableData, null);
    };
    th.addEventListener('click', sort);
    th.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        sort();
      }
    });
  });
}

/* ------------------------------------------------------------------ */
/* 10. Date Filter Buttons                                               */
/* ------------------------------------------------------------------ */
function setupDateFilters() {
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentDays = btn.dataset.days ? parseInt(btn.dataset.days) : null;
      if (window.TRACKER_DATA?.selectedPlanId) {
        loadPlanData(window.TRACKER_DATA.selectedPlanId, currentDays);
      }
    });
  });
}

function updateTableScrollHints() {
  document.querySelectorAll('.table-wrapper').forEach(wrapper => {
    const hint = wrapper.previousElementSibling;
    if (hint?.classList.contains('table-scroll-hint')) {
      hint.hidden = wrapper.scrollWidth <= wrapper.clientWidth;
    }
  });
}

/* ------------------------------------------------------------------ */
/* 11. Utilities                                                         */
/* ------------------------------------------------------------------ */
function formatINR(amount) {
  if (amount === null || amount === undefined) return '—';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(amount);
}

function formatINRDecimal(amount) {
  if (amount === null || amount === undefined || !Number.isFinite(Number(amount))) return '—';
  return new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(amount));
}

function formatDateIST(utcStr) {
  if (!utcStr) return '—';
  const d = new Date(utcStr);
  if (isNaN(d.getTime())) return utcStr;
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ------------------------------------------------------------------ */
/* 12. Browser Back/Forward Support                                      */
/* ------------------------------------------------------------------ */
window.addEventListener('popstate', event => {
  const planId = event.state?.planId;
  if (planId) {
    window.TRACKER_DATA.selectedPlanId = planId;
    updateSelectedPlanLabel(planId);
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', Number(btn.dataset.planId) === planId);
    });
    loadPlanData(planId, currentDays);
  }
});

/* ------------------------------------------------------------------ */
/* 13. DOMContentLoaded — Init                                           */
/* ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', () => {
  setupDateFilters();
  setupTableSorting();
  updateTableScrollHints();
  renderOfferHistory(window.TRACKER_DATA?.offerHistory || []);
  window.addEventListener('resize', updateTableScrollHints);
  mobileChart.addEventListener('change', event => {
    if (!chartInstance) return;
    chartInstance.options.scales.x.ticks.maxTicksLimit = event.matches ? 3 : 8;
    chartInstance.options.scales.x.ticks.maxRotation = event.matches ? 0 : 50;
    chartInstance.update('none');
  });

  // Check for plan override in URL first
  const params = new URLSearchParams(location.search);
  const planFromUrl = params.get('plan');

  let initialPlanId = window.TRACKER_DATA?.selectedPlanId;

  if (planFromUrl && parseInt(planFromUrl) !== initialPlanId) {
    initialPlanId = parseInt(planFromUrl);
    window.TRACKER_DATA.selectedPlanId = initialPlanId;
    // Update active tab UI to match URL plan
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', Number(btn.dataset.planId) === initialPlanId);
    });
  }

  if (initialPlanId) {
    updateSelectedPlanLabel(initialPlanId);
    loadPlanData(initialPlanId, currentDays);
  }
});
