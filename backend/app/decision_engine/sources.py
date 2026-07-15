"""Conservative CPU-light strategy and quant sources for Active Drive V2."""
from math import isfinite

def num(f, key):
    try: value = float(f.get(key))
    except (TypeError, ValueError): return None
    return value if isfinite(value) else None

def vote(direction, confidence, reason):
    return {"direction": direction, "confidence": max(0., min(100., confidence)), "reason": reason, "status": "limited"}

def strategy_votes(f):
    price,e20,e50,e200=(num(f,k) for k in ("price","ema20","ema50","ema200")); rsi,macd,atr,bbw=(num(f,k) for k in ("rsi","macd_hist","atr","bb_width")); vol,vavg=(num(f,k) for k in ("volume","volume_sma20")); out=[]
    def add(n,fam,d,c,r): out.append((n,fam,vote(d,c,r)))
    if None not in (price,e20,e50):
        d="LONG" if price>e20>e50 else "SHORT" if price<e20<e50 else "NO_TRADE"; add("ema_20_50_continuation","trend",d,58 if d!="NO_TRADE" else 0,"Price and EMA20/50 structure")
    if None not in (e50,e200):
        d="LONG" if e50>e200 else "SHORT" if e50<e200 else "NO_TRADE"; add("ema_50_200_structure","trend",d,52,"EMA50 versus EMA200")
    if None not in (price,e20,atr) and atr and abs(price-e20)<=atr*.5:
        d="LONG" if e20>(e50 or e20) else "SHORT" if e20<(e50 or e20) else "NO_TRADE"; add("ema_pullback","trend",d,48,"Price within 0.5 ATR of EMA20")
    else: add("ema_pullback","trend","NO_TRADE",0,"No eligible EMA pullback")
    if rsi is not None:
        d="LONG" if rsi>=58 else "SHORT" if rsi<=42 else "NO_TRADE"; add("rsi_momentum","momentum",d,min(65,abs(rsi-50)*2),f"RSI {rsi:.1f}")
        d="LONG" if rsi<=30 else "SHORT" if rsi>=70 else "NO_TRADE"; add("rsi_extreme_reversal","mean_reversion",d,min(60,abs(rsi-50)*1.5),f"RSI extreme {rsi:.1f}")
    if macd is not None: add("macd_momentum","momentum","LONG" if macd>0 else "SHORT" if macd<0 else "NO_TRADE",min(60,35+abs(macd)*10),"MACD histogram sign")
    if None not in (price,e20,atr) and atr:
        z=(price-e20)/atr; d="LONG" if z>1 else "SHORT" if z<-1 else "NO_TRADE"; add("atr_breakout","breakout",d,min(62,abs(z)*35),f"Displacement {z:.2f} ATR")
    if None not in (bbw,macd):
        d=("LONG" if macd>0 else "SHORT") if bbw>=.02 and macd!=0 else "NO_TRADE"; add("bollinger_breakout","breakout",d,55 if d!="NO_TRADE" else 0,f"Band width {bbw:.4f}")
        add("volatility_squeeze","volatility","NEUTRAL" if bbw<=.008 else "NO_TRADE",35 if bbw<=.008 else 0,"Compression state")
    if None not in (vol,vavg) and vavg and macd is not None:
        ratio=vol/vavg; d=("LONG" if macd>0 else "SHORT") if ratio>=1.5 and macd!=0 else "NO_TRADE"; add("volume_confirmed_momentum","momentum",d,min(62,ratio*30),f"Volume {ratio:.2f}x average")
    return out

def quant_votes(f):
    price,e20,e50=(num(f,k) for k in ("price","ema20","ema50")); atr,rv,bbw=(num(f,k) for k in ("atr","realized_volatility","bb_width")); rsi,vol,vavg=(num(f,k) for k in ("rsi","volume","volume_sma20")); out=[]
    def add(n,fam,d,c,value,reason):
        score=0 if d not in ("LONG","SHORT") else c/100*(1 if d=="LONG" else -1); out.append((n,fam,vote(d,c,reason),{"current_value":value,"normalized_score":round(score,4),"explanation":reason}))
    if None not in (e20,e50) and e50:
        slope=(e20-e50)/abs(e50); d="LONG" if slope>0 else "SHORT" if slope<0 else "NEUTRAL"; add("regression_slope_proxy","quant_statistical",d,min(60,abs(slope)*5000),slope,"Causal EMA-spread slope proxy"); add("kalman_trend_proxy","quant_statistical",d,min(52,abs(slope)*4000),slope,"Smoothed trend-state proxy")
    if None not in (price,e20,atr) and atr:
        z=(price-e20)/atr; add("mean_reversion_zscore","quant_statistical","SHORT" if z>1.5 else "LONG" if z<-1.5 else "NEUTRAL",min(60,abs(z)*30),z,"ATR-normalized deviation"); add("atr_expected_move","volatility","NEUTRAL",0,atr/price if price else None,"Expected-move scale only")
    if rv is not None: add("realized_volatility","volatility","NEUTRAL",0,rv,"Realized-volatility context")
    if bbw is not None: add("compression_state","volatility","NEUTRAL",0,bbw,"Bollinger-width context")
    if None not in (vol,vavg) and vavg: add("volume_anomaly","order_flow","NEUTRAL",0,vol/vavg,"Volume anomaly; direction unavailable")
    if rsi is not None: add("persistence_proxy","quant_statistical","NEUTRAL",0,abs(rsi-50)/50,"Persistence proxy, not a Hurst claim")
    for n,fam,reason in (("funding_divergence","derivatives","Funding unavailable this cycle"),("open_interest_divergence","derivatives","Open interest unavailable this cycle"),("order_book_imbalance","order_flow","Order book unavailable this cycle"),("correlation_beta_context","quant_statistical","Cross-asset history unavailable this cycle")): add(n,fam,"NO_TRADE",0,None,reason)
    return out
