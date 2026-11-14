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

  // Push URL change without reload
  history.pushState({ planId }, '', `?plan=${planId}`);

  // Load data for the newly selected plan
  loadPlanData(planId, currentDays);
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
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No offer observations yet.</td></tr>';
    if (count) count.textContent = '';
    return;
  }
  tbody.innerHTML = historyData.map(offer => `<tr>
    <td>${formatDateIST(offer.observed_at)}</td>
    <td>${escapeHtml(offer.status)}</td>
    <td>${escapeHtml(offer.category || 'Other')}</td>
    <td>${escapeHtml(offer.description || offer.title || '—')}</td>
    <td>${escapeHtml(offer.coupon_code || '—')}</td>
  </tr>`).join('');
  if (count) count.textContent = `${historyData.length} records`;
}

/* ------------------------------------------------------------------ */
/* 3. Price Card Update (dynamic, after plan switch)                     */
/* ------------------------------------------------------------------ */
function updatePriceCard(historyData, stats, planId, offerData = {}) {
  const card = document.getElementById('priceCard');
  if (!card) return;

  // Find the plan info
  const plan = (window.TRACKER_DATA.plansJson || []).find(p => p.id === planId);
  const planName = plan ? plan.name : '—';
  const tier = plan ? plan.tier : '';

  // Latest price is the newest observation by timestamp
  const latest = historyData && historyData.length > 0
    ? historyData.slice().sort((a, b) => new Date(b.observed_at) - new Date(a.observed_at))[0]
    : null;

  const header = card.querySelector('.card-header');
  if (header) {
    header.innerHTML = `
      <span class="plan-name">${escapeHtml(planName)}</span>
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

  if (latest.original_price > latest.selling_price) {
    html += `<div class="price-mrp">MRP ₹${mrpFmt}</div>`;
  }

  if (discount > 0) {
    html += `<div class="discount-badge">${discount.toFixed(1)}% OFF</div>`;
  }

  const offers = offerData.offers || [];
  if (offers.length > 0) {
    html += '<div class="offer-list">';
    for (const offer of offers) {
      html += `<div class="offer-item"><strong>${escapeHtml(offer.title || 'Offer')}</strong>`;
      if (offer.description && offer.description !== offer.title) {
        html += `<span>${escapeHtml(offer.description)}</span>`;
      }
      if (offer.coupon_code) {
        html += `<code>${escapeHtml(offer.coupon_code)}</code>`;
      }
      html += `<small>${escapeHtml(offer.category || 'Other')}</small></div>`;
    }
    html += '</div>';
  } else if (latest.offer_description) {
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
    if (scraped && data.completed_at) {
      scraped.textContent = `Last scraped: ${formatDateIST(data.completed_at)}`;
    }

    const badge = document.querySelector('.status-badge');
    if (badge && data.status) {
      badge.className = `status-badge status-${data.status}`;
      badge.textContent = data.status.toUpperCase();
    }
  } catch (_) {}
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
    canvas.style.display = 'none';
    return;
  }

  if (noData) noData.style.display = 'none';
  canvas.style.display = 'block';

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
          labels: { color: '#e2e8f0', font: { family: 'Inter', size: 12 } },
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
          ticks: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, maxTicksLimit: 8 },
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
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No observations yet. Click Refresh Price to start tracking.</td></tr>';
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
    const tier = obs.tier || '';
    return `<tr>
      <td>${formatDateIST(obs.observed_at)}</td>
      <td>${escapeHtml(obs.plan_name || '—')}</td>
      <td><span class="tier-badge tier-${tier.toLowerCase()}">${escapeHtml(tier || '—')}</span></td>
      <td>₹${formatINR(obs.original_price)}</td>
      <td class="price-cell ${priceClass}">₹${formatINR(obs.selling_price)}</td>
      <td>${(obs.discount_percentage || 0).toFixed(1)}%</td>
      <td class="offer-cell">${escapeHtml(obs.offer_description || '—')}</td>
    </tr>`;
  }).join('');

  if (countEl) countEl.textContent = `${historyData.length} records`;
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
  const vsMinPctClass = (stats.vs_min_pct > 0) ? 'text-warning' : 'text-success';
  const vsMinAbsPrefix = stats.vs_min_abs > 0 ? '+' : '';
  const vsMinPctPrefix = stats.vs_min_pct > 0 ? '+' : '';

  let html = `<div class="stats-grid">
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
    </div>
    <div class="stat-item">
      <div class="stat-value ${vsMinPctClass}">${vsMinPctPrefix}${Number(stats.vs_min_pct).toFixed(1)}%</div>
      <div class="stat-label">vs Historical Low (%)</div>
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
    th.addEventListener('click', () => {
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
          h.textContent = `${baseText} ↕`;
        }
      });

      // Re-render with current data
      renderTable(_tableData, null);
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

/* ------------------------------------------------------------------ */
/* 11. Utilities                                                         */
/* ------------------------------------------------------------------ */
function formatINR(amount) {
  if (amount === null || amount === undefined) return '—';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(amount);
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
    loadPlanData(initialPlanId, currentDays);
  }
});
