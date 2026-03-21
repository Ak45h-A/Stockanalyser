/**
 * live.js — Real-time price streaming via Server-Sent Events
 * Place in: static/live.js
 *
 * Usage (after chart candleSeries is built):
 *   LivePrice.init({
 *     symbol:       "^NSEI",
 *     candleSeries: candleSer,   // LW chart candlestick series
 *     barSecs:      300,         // 5m=300, 1d=86400, etc.
 *     isDaily:      false,
 *     onUpdate:     (data) => {} // called on every 1s push
 *   });
 *   LivePrice.destroy();         // call on timeframe change
 */
const LivePrice = (() => {
  let _es=null, _opts={}, _lastBar=null, _barSecs=300, _raf=null;

  function _el(s){return!s?null:typeof s==='string'?document.querySelector(s):s;}

  function _parseNum(el){return el?parseFloat(el.textContent.replace(/[^0-9.\-]/g,''))||0:0;}

  // Patch the current in-progress candle on the LW chart every second
  function _patchCandle(d){
    const series=_opts.candleSeries;
    if(!series||!d.candleUpdate)return;
    const cu=d.candleUpdate;
    const nowSecs=Math.floor(Date.now()/1000);

    if(_opts.isDaily){
      if(_lastBar){
        const u={time:_lastBar.time,open:_lastBar.open,
          high:Math.max(_lastBar.high,cu.close),
          low:Math.min(_lastBar.low,cu.close),close:cu.close};
        try{series.update(u);}catch(e){}
        _lastBar=u;
      }
      return;
    }

    const slot=Math.floor(nowSecs/_barSecs)*_barSecs;
    if(!_lastBar||_lastBar.time<slot){
      const prev=_lastBar?_lastBar.close:cu.close;
      _lastBar={time:slot,open:prev,
        high:Math.max(prev,cu.close),low:Math.min(prev,cu.close),close:cu.close};
    }else{
      _lastBar={..._lastBar,close:cu.close,
        high:Math.max(_lastBar.high,cu.close),
        low:Math.min(_lastBar.low,cu.close)};
    }
    try{series.update(_lastBar);}catch(e){}
  }

  function _connect(sym){
    if(_es){try{_es.close();}catch(e){}_es=null;}
    if(!sym)return;
    _es=new EventSource('/stream/price?symbol='+encodeURIComponent(sym));
    _es.onmessage=e=>{
      try{
        const d=JSON.parse(e.data);
        if(!d||!d.price)return;
        _patchCandle(d);
        if(typeof _opts.onUpdate==='function')_opts.onUpdate(d);
      }catch(err){}
    };
    _es.onerror=()=>{
      if(_es){try{_es.close();}catch(e){}_es=null;}
      setTimeout(()=>{if(_opts.symbol)_connect(_opts.symbol);},3000);
    };
  }

  return {
    init(opts){
      _opts=opts||{};_barSecs=_opts.barSecs||300;_lastBar=null;
      if(!_opts.symbol){console.warn('LivePrice: symbol required');return;}
      if(_opts.candleSeries){
        try{const data=_opts.candleSeries.data();if(data&&data.length)_lastBar={...data[data.length-1]};}catch(e){}
      }
      _connect(_opts.symbol);
    },
    setSeries(series,barSecs){
      _opts.candleSeries=series;
      if(barSecs){_opts.barSecs=barSecs;_barSecs=barSecs;}
      _lastBar=null;
      if(series){try{const data=series.data();if(data&&data.length)_lastBar={...data[data.length-1]};}catch(e){}}
    },
    destroy(){
      if(_es){try{_es.close();}catch(e){}_es=null;}
      if(_raf){cancelAnimationFrame(_raf);_raf=null;}
      _lastBar=null;
    },
    setSymbol(sym){_opts.symbol=sym;_lastBar=null;_connect(sym);},
    get lastBar(){return _lastBar;},
  };
})();
