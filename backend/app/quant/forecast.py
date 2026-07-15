"""Chart-ready forecasts derived from measured market inputs.

Forecast availability is independent from execution eligibility. A V2
NO_TRADE may expose a labelled, non-actionable projection when fresh data
and independent source composition exist. This module never executes.
"""
from __future__ import annotations
import math

HORIZON_BARS={"1m":24,"3m":16,"5m":12,"15m":8,"30m":6,"1h":6,"4h":4,"1d":4}
FALLBACK_BAND_PCT=.006

def _ease_out(t): return 1-(1-t)**2
def horizon_bars(interval): return HORIZON_BARS.get(interval,0)
def _positive(v): return isinstance(v,(int,float)) and math.isfinite(v) and v>0

def _spread(price,atr,rv):
    if _positive(atr): return float(atr),"atr"
    if _positive(rv): return price*min(float(rv),.25),"realized_volatility"
    return price*FALLBACK_BAND_PCT,"fallback_pct"

def _unavailable(interval,interval_ms,reason):
    bars=horizon_bars(interval)
    return {"available":False,"trade_actionable":False,"forecast_type":"unavailable","direction":"NEUTRAL",
      "median_path":[],"upper_band":[],"lower_band":[],"forecast_points":[],"upper_band_points":[],"lower_band_points":[],
      "bars":bars,"horizon_ms":bars*interval_ms,"horizon_seconds":bars*interval_ms//1000,"band_basis":None,
      "confidence_level":None,"target_price":None,"invalidation_price":None,"reason":reason}

def build_forecast(*,interval,interval_ms,last_candle_time,price,direction,confidence,target,stop,
                   atr=None,realized_volatility=None,informational_direction=None,
                   informational_strength=None,data_fresh=True,candle_count=0):
    """Return finite, ordered millisecond points beginning after the last bar."""
    bars=horizon_bars(interval)
    if not bars or interval_ms<=0: return _unavailable(interval,interval_ms,"Unsupported or invalid forecast timeframe")
    if candle_count<30: return _unavailable(interval,interval_ms,"Insufficient candle history for an informational forecast")
    if not data_fresh: return _unavailable(interval,interval_ms,"Market data is stale")
    if not _positive(price) or not _positive(last_candle_time): return _unavailable(interval,interval_ms,"No finite reference candle and price")
    actionable=direction in ("LONG","SHORT")
    if actionable:
        forecast_direction="BULLISH" if direction=="LONG" else "BEARISH"
        end=float(target) if _positive(target) else float(price)
        conf=max(0.,min(1.,float(confidence or 0)/100.)); kind="actionable"
        reason="Actionable forecast derived from the authoritative V2 decision"
    else:
        forecast_direction=informational_direction if informational_direction in ("BULLISH","BEARISH","NEUTRAL") else None
        if forecast_direction is None or informational_strength is None or not math.isfinite(informational_strength):
            return _unavailable(interval,interval_ms,"No finite independent source composition for an informational forecast")
        strength=max(0.,min(1.,abs(float(informational_strength))))
        measured,_=_spread(float(price),atr,realized_volatility)
        sign=1. if forecast_direction=="BULLISH" else -1. if forecast_direction=="BEARISH" else 0.
        end=float(price)+sign*measured*strength; conf=None; kind="informational"
        reason="Forecast available, but it is informational and not a trade signal"
    measured,basis=_spread(float(price),atr,realized_volatility)
    width=measured*max(.5,1.5-(conf if conf is not None else .5))
    median=[]; upper=[]; lower=[]
    for i in range(1,bars+1):
        e=_ease_out(i/bars); timestamp=int(last_candle_time+i*interval_ms)
        midpoint=float(price)+(end-float(price))*e; band=width*math.sqrt(i/bars)
        median.append({"time":timestamp,"price":round(midpoint,8)})
        upper.append({"time":timestamp,"price":round(midpoint+band,8)})
        lower.append({"time":timestamp,"price":round(max(1e-12,midpoint-band),8)})
    return {"available":True,"trade_actionable":actionable,"forecast_type":kind,"direction":forecast_direction,
      "median_path":median,"upper_band":upper,"lower_band":lower,"forecast_points":median,
      "upper_band_points":upper,"lower_band_points":lower,"bars":bars,"horizon_ms":bars*interval_ms,
      "horizon_seconds":bars*interval_ms//1000,"band_basis":basis,"confidence_level":conf,
      "target_price":float(target) if actionable and _positive(target) else None,
      "invalidation_price":float(stop) if actionable and _positive(stop) else None,"reason":reason}
