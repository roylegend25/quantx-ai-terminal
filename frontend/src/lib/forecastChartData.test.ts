import {describe,expect,it} from "vitest";
import {validateForecastChartData} from "./forecastChartData";
const series=[0,900,1800,2700,3600].map((d,i)=>({time:1_700_000_000+d,price:100+i*.1}));
const prediction={symbol:"BTCUSDT",timeframe:"15m",decision_id:"d1",forecast:{available:true,symbol:"BTCUSDT",timeframe:"15m",decision_id:"d1",median_path:series,upper_band:series.map(p=>({...p,price:p.price+1})),lower_band:series.map(p=>({...p,price:p.price-1}))}};
describe("forecast chart validator",()=>{
  it("returns multiple strictly future points after removing the real anchor",()=>{
    const out=validateForecastChartData(prediction,"BTCUSDT",900_000,1_700_000_000_000);
    expect(out.valid).toBe(true); expect(out.median).toHaveLength(4); expect(out.median[0].time).toBe(1_700_000_900); expect(out.lastTime).toBe(1_700_003_600);
  });
  it("rejects stale identity and wrong interval data",()=>{
    expect(validateForecastChartData(prediction,"ETHUSDT",900_000,1_700_000_000_000).valid).toBe(false);
    const bad={...prediction,forecast:{...prediction.forecast,median_path:series.map((p,i)=>({...p,time:p.time+(i===2?1:0)}))}};
    expect(validateForecastChartData(bad,"BTCUSDT",900_000,1_700_000_000_000).valid).toBe(false);
  });
});
