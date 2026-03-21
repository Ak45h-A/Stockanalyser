/**
 * live.js  —  Real-time price streaming + live candlestick updates
 * =================================================================
 * Drop this in  static/live.js  and add to every template:
 *   <script src="/static/live.js"></script>
 *
 * Usage:
 *   LivePrice.init({
 *     symbol:       "^NSEI",           // Yahoo symbol
 *     priceEl:      "#livePrice",       // element to write formatted price
 *     changeEl:     "#liveChange",      // element to write change/pct
 *     highEl:       "#dayHigh",         // optional
 *     lowEl:        "#dayLow",          // optional
 *     openEl:       "#dayOpen",         // optional
 *     bidEl:        "#bid",             // optional
 *     askEl:        "#ask",             // optional
 *     statusEl:     "#mktStatus",       // OPEN / PRE / POST / CLOSED
 *     candleSeries: myLWCandleSeries,   // TradingView LW chart series
 *     isDaily:      false,              // true for 1D/1W charts
 *     onUpdate:     (data) => {},       // optional callback
 *   });
 *
 *   LivePrice.destroy();   // clean up SSE connection
 */

const LivePrice = (() => {
  let _es        = null;   // EventSource
  let _opts      = {};
  let _lastBar   = null;   // last known OHLC bar (for candle patching)
  let _barSecs   = 300;    // interval width in seconds (default 5m)
  let _ticker    = null;   // animated ticker interval

  // ── helpers ────────────────────────────────────────────────────
  function _el(sel) {
    if (!sel) return null;
    return (typeof sel === "string") ? document.querySelector(sel) : sel;
  }

  function _set(sel, html, cls) {
    const el = _el(sel);
    if (!el) return;
    if (html !== undefined) el.textContent = html;
    if (cls !== undefined)  el.className   = cls;
  }

  function _color(change) {
    return change >= 0 ? "price-up" : "price-down";
  }

  function _sign(n) { return n >= 0 ? "+" : ""; }

  // ── animated number ticker (interpolates between old and new) ──
  function _animateTo(el, from, to, decimals, prefix) {
    if (!el) return;
    const dur   = 300;   // ms
    const start = performance.now();
    function step(now) {
      const t   = Math.min((now - start) / dur, 1);
      const val = from + (to - from) * t;
      el.textContent = prefix + val.toFixed(decimals);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ── parse current display value back to number ─────────────────
  function _parseDisplayNum(el) {
    if (!el) return 0;
    return parseFloat(el.textContent.replace(/[^0-9.\-]/g, "")) || 0;
  }

  // ── update DOM ─────────────────────────────────────────────────
  function _applyUpdate(d) {
    const priceEl  = _el(_opts.priceEl);
    const changeEl = _el(_opts.changeEl);
    const cls      = _color(d.change);

    // Animated price counter
    if (priceEl) {
      const from = _parseDisplayNum(priceEl);
      const to   = d.price;
      const decs = d.currency === "INR" ? 2 : 2;
      const pre  = d.symbol || "";
      _animateTo(priceEl, from, to, decs, pre);
      priceEl.className = cls;
    }

    // Change / changePct
    if (changeEl) {
      const pct = d.changePct != null ? d.changePct : (d.change / (d.prev || 1) * 100);
      changeEl.textContent =
        `${_sign(d.change)}${d.change.toFixed(2)}  (${_sign(pct)}${pct.toFixed(2)}%)`;
      changeEl.className = cls;
    }

    // Day stats
    _set(_opts.highEl,  d.high  != null ? d.display?.replace(/[\d,\.]+/, d.high.toFixed(2)) : undefined);
    _set(_opts.lowEl,   d.low   != null ? d.display?.replace(/[\d,\.]+/, d.low.toFixed(2))  : undefined);
    _set(_opts.openEl,  d.open  != null ? d.display?.replace(/[\d,\.]+/, d.open.toFixed(2)) : undefined);
    _set(_opts.bidEl,   d.bid   != null ? `${d.symbol}${d.bid.toFixed(2)}`  : undefined);
    _set(_opts.askEl,   d.ask   != null ? `${d.symbol}${d.ask.toFixed(2)}`  : undefined);

    // Market status badge
    if (_opts.statusEl) {
      const ms = (d.marketState || "").toUpperCase();
      const statusMap = {
        REGULAR: { label: "● LIVE",  cls: "badge-live"   },
        PRE:     { label: "◑ PRE",   cls: "badge-pre"    },
        POST:    { label: "◑ POST",  cls: "badge-post"   },
        PREPRE:  { label: "○ CLOSED",cls: "badge-closed" },
        CLOSED:  { label: "○ CLOSED",cls: "badge-closed" },
        POSTPOST:{ label: "○ CLOSED",cls: "badge-closed" },
      };
      const s = statusMap[ms] || { label: ms || "—", cls: "" };
      _set(_opts.statusEl, s.label, s.cls);
    }
  }

  // ── live candle update ─────────────────────────────────────────
  // Called every time a new price arrives.
  // Patches the CURRENT in-progress bar on the chart:
  //   • close  = latest price
  //   • high   = max(existing high, latest price)
  //   • low    = min(existing low, latest price)
  // When the bar interval rolls over, a NEW bar is started.
  function _applyCandle(d) {
    const series = _opts.candleSeries;
    if (!series || !d.candleUpdate) return;

    const cu      = d.candleUpdate;
    const nowSecs = Math.floor(Date.now() / 1000);

    if (_opts.isDaily) {
      // Daily chart: just update today's bar in place
      if (_lastBar) {
        const updated = {
          time:  _lastBar.time,
          open:  _lastBar.open,
          high:  Math.max(_lastBar.high, cu.close),
          low:   Math.min(_lastBar.low,  cu.close),
          close: cu.close,
        };
        try { series.update(updated); }
        catch(e) {}
        _lastBar = updated;
      }
      return;
    }

    // Intraday chart: calculate which bar slot we're in
    const barSlot = Math.floor(nowSecs / _barSecs) * _barSecs;

    if (!_lastBar || _lastBar.time < barSlot) {
      // New bar started
      _lastBar = {
        time:  barSlot,
        open:  cu.close,
        high:  cu.close,
        low:   cu.close,
        close: cu.close,
      };
    } else {
      // Update existing bar
      _lastBar = {
        time:  _lastBar.time,
        open:  _lastBar.open,
        high:  Math.max(_lastBar.high, cu.close),
        low:   Math.min(_lastBar.low,  cu.close),
        close: cu.close,
      };
    }

    try { series.update(_lastBar); }
    catch(e) {}
  }

  // ── SSE connection ─────────────────────────────────────────────
  function _connect(sym) {
    if (_es) { _es.close(); _es = null; }

    _es = new EventSource(`/stream/price?symbol=${encodeURIComponent(sym)}`);

    _es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (!d || !d.price) return;
        _applyUpdate(d);
        _applyCandle(d);
        if (typeof _opts.onUpdate === "function") _opts.onUpdate(d);
      } catch(err) {}
    };

    _es.onerror = () => {
      // SSE dropped — reconnect after 3s
      if (_es) { _es.close(); _es = null; }
      setTimeout(() => { if (_opts.symbol) _connect(_opts.symbol); }, 3000);
    };
  }

  // ── public API ─────────────────────────────────────────────────
  return {
    /**
     * Initialise live price streaming.
     * @param {object} opts  See JSDoc at top of file.
     */
    init(opts) {
      _opts    = opts || {};
      _barSecs = _opts.barSecs || 300;

      if (!_opts.symbol) { console.warn("LivePrice.init: no symbol"); return; }

      // Seed _lastBar from the chart if already loaded
      if (_opts.candleSeries) {
        try {
          const data = _opts.candleSeries.data();
          if (data && data.length) _lastBar = { ...data[data.length - 1] };
        } catch(e) {}
      }

      _connect(_opts.symbol);
    },

    /** Update the candle series reference after a chart rebuild */
    setSeries(series, barSecs) {
      _opts.candleSeries = series;
      if (barSecs) { _opts.barSecs = barSecs; _barSecs = barSecs; }
      if (series) {
        try {
          const data = series.data();
          if (data && data.length) _lastBar = { ...data[data.length - 1] };
        } catch(e) {}
      }
    },

    /** Force-feed a price update (e.g. from initial /price REST call) */
    seed(d) {
      if (!d || !d.price) return;
      _applyUpdate(d);
    },

    /** Close SSE connection */
    destroy() {
      if (_es) { _es.close(); _es = null; }
      if (_ticker) { clearInterval(_ticker); _ticker = null; }
    },

    /** Convenience: change tracked symbol without full re-init */
    setSymbol(sym) {
      _opts.symbol = sym;
      _lastBar     = null;
      _connect(sym);
    },
  };
})();