/* Gold Factory SaaS — Main JS */
'use strict';

/* ── Sidebar toggle (mobile) ─────────────────────────────── */
(function () {
  var MOBILE_BP = 768;
  var toggle   = document.getElementById('sidebarToggle');
  var sidebar  = document.getElementById('sidebar');
  var backdrop = document.getElementById('sidebarBackdrop');

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.add('open');
    if (backdrop) backdrop.classList.add('show');
    document.body.style.overflow = 'hidden';
  }
  function closeSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('show');
    document.body.style.overflow = '';
  }
  function handleResize() {
    if (!toggle) return;
    if (window.innerWidth <= MOBILE_BP) {
      toggle.style.display = 'flex';
    } else {
      toggle.style.display = 'none';
      closeSidebar();
    }
  }

  if (toggle) {
    toggle.addEventListener('click', function(e) {
      e.stopPropagation();
      if (sidebar && sidebar.classList.contains('open')) closeSidebar();
      else openSidebar();
    });
  }
  if (backdrop) {
    backdrop.addEventListener('click', closeSidebar);
  }

  /* Close when clicking nav links on mobile —
     skip nav-toggle buttons (they expand sub-menus, don't navigate) */
  document.querySelectorAll('.nav-link-item, .nav-sub-link').forEach(function(el) {
    el.addEventListener('click', function() {
      if (window.innerWidth <= MOBILE_BP) {
        if (el.classList.contains('nav-toggle')) return; // sub-menu toggle — keep sidebar open
        closeSidebar();
      }
    });
  });

  window.addEventListener('resize', handleResize);
  /* Make accessible globally */
  window.closeSidebar = closeSidebar;
  window.openSidebar  = openSidebar;

  /* Run on load */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      handleResize();
      wrapTables();
    });
  } else {
    handleResize();
    wrapTables();
  }

  function wrapTables() {
    document.querySelectorAll('.gtable').forEach(function(tbl) {
      var p = tbl.parentNode;
      if (p && !p.classList.contains('gtable-wrap') &&
          (p.getAttribute('style') || '').indexOf('overflow') === -1) {
        var w = document.createElement('div');
        w.className = 'gtable-wrap';
        w.style.cssText = 'overflow-x:auto;-webkit-overflow-scrolling:touch;width:100%;';
        p.insertBefore(w, tbl);
        w.appendChild(tbl);
      }
    });
  }
})();

/* ── Flash auto-dismiss ──────────────────────────────────── */
(function () {
  document.querySelectorAll('.alert-custom').forEach((el) => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 4500);
  });
})();

/* ── Confirm modal ───────────────────────────────────────── */
function askConfirm(title, msg, action, method) {
  const overlay = document.getElementById('gModal');
  document.getElementById('mTitle').textContent = title || 'Confirm';
  document.getElementById('mMsg').textContent   = msg   || 'Are you sure?';
  const btn = document.getElementById('mOkBtn');
  btn.onclick = () => {
    if (method === 'POST') {
      const f = document.createElement('form');
      f.method = 'POST'; f.action = action;
      document.body.appendChild(f); f.submit();
    } else {
      window.location.href = action;
    }
  };
  overlay.classList.add('show');
}
function closeModal() {
  document.getElementById('gModal').classList.remove('show');
}

/* ── Stone: carat ↔ gram conversion ─────────────────────── */
function ctToG() {
  const ct = parseFloat(document.getElementById('stone_ct')?.value || 0);
  const gEl = document.getElementById('stone_g_display');
  if (gEl) gEl.textContent = (ct * 0.2).toFixed(4) + ' g';
}
function gToCt() {
  const g  = parseFloat(document.getElementById('stone_g_input')?.value || 0);
  const ctEl = document.getElementById('stone_ct_calc');
  if (ctEl) ctEl.textContent = (g / 0.2).toFixed(4) + ' ct';
}

/* ── Setting stage show/hide ─────────────────────────────── */
function toggleStoneSection(stageName, settingStage) {
  const sec = document.getElementById('stoneSection');
  if (!sec) return;
  sec.style.display = (stageName === settingStage) ? 'block' : 'none';
}

/* ── Live loss calculator ────────────────────────────────── */
function calcLoss() {
  const hw  = parseFloat(document.getElementById('hw_val')?.dataset.val || 0);
  const sw  = parseFloat(document.getElementById('stone_weight_g')?.dataset.val || 0);
  const pw  = parseFloat(document.getElementById('produced_weight')?.value || 0);
  const scr = parseFloat(document.getElementById('scrap_weight')?.value    || 0);
  const retStone = parseFloat(document.getElementById('returned_stone_ct')?.value || 0) * 0.2;

  const netReceived = hw - sw;
  const raw = netReceived - pw - scr;
  const loss = raw > 0 ? raw : 0;
  const gain = raw < 0 ? Math.abs(raw) : 0;
  const pct  = netReceived > 0 ? (loss / netReceived * 100) : 0;

  const lossEl = document.getElementById('calc_loss');
  const gainEl = document.getElementById('calc_gain');
  const pctEl  = document.getElementById('calc_pct');
  const warnEl = document.getElementById('loss_warn');
  const gainBoxEl = document.getElementById('gain_box');

  if (lossEl) { lossEl.textContent = loss.toFixed(3) + ' g'; lossEl.style.color = pct > 2 ? 'var(--danger)' : 'var(--success)'; }
  if (gainEl) { gainEl.textContent = gain.toFixed(3) + ' g'; gainEl.style.color = gain > 0 ? 'var(--success)' : 'var(--text-muted)'; }
  if (pctEl)  { pctEl.textContent  = pct.toFixed(2)  + '%';  pctEl.style.color  = pct > 2 ? 'var(--danger)' : 'var(--success)'; }
  if (warnEl)    warnEl.style.display    = pct > 2  ? 'flex' : 'none';
  if (gainBoxEl) gainBoxEl.style.display = gain > 0 ? 'flex' : 'none';
}

/* ── Active nav highlight ────────────────────────────────── */
(function () {
  var path  = window.location.pathname;
  var links = document.querySelectorAll('.sidebar-nav a[href]');
  var best  = null;
  var bestLen = 0;

  links.forEach(function(a) {
    var href = a.getAttribute('href') || '';
    if (!href || href === '#') return;
    // Exact match wins immediately
    if (href === path) { best = a; bestLen = href.length + 1000; return; }
    // Prefix match only if href ends with / or next char is /
    if (path.startsWith(href) && href.length > bestLen) {
      var nextChar = path[href.length];
      if (nextChar === '/' || nextChar === undefined || href.endsWith('/')) {
        best = a; bestLen = href.length;
      }
    }
  });

  if (best) {
    best.classList.add('active');
    // Open parent collapsible if inside one
    var parentLi = best.closest('.has-sub');
    if (parentLi) {
      parentLi.classList.add('open');
      // Scroll sidebar to show the active item
      setTimeout(function() {
        var nav = document.querySelector('.sidebar-nav');
        if (nav && best) {
          var itemTop = best.getBoundingClientRect().top;
          var navTop  = nav.getBoundingClientRect().top;
          var navH    = nav.clientHeight;
          if (itemTop > navTop + navH * 0.7) {
            nav.scrollTop += (itemTop - navTop - navH * 0.5);
          }
        }
      }, 320); // after sub-menu animation
    }
  }
})();





/* ── Dark / Light Mode Toggle ────────────────────────────── */
(function () {
  var KEY = 'goldErpMode';

  function apply(mode) {
    if (mode === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    localStorage.setItem(KEY, mode);
    var icon = document.getElementById('modeIcon');
    if (icon) icon.className = mode === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
  }

  window.toggleMode = function () {
    var cur = localStorage.getItem(KEY) || 'light';
    apply(cur === 'light' ? 'dark' : 'light');
  };

  /* Run immediately before paint */
  apply(localStorage.getItem(KEY) || 'light');

  /* Sync icon after DOM ready */
  document.addEventListener('DOMContentLoaded', function () {
    var icon = document.getElementById('modeIcon');
    var cur  = localStorage.getItem(KEY) || 'light';
    if (icon) icon.className = cur === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
  });
})();
