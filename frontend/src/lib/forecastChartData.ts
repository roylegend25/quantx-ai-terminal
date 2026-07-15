export type ForecastPoint={time:number;price:number};
export type ValidatedForecast={valid:boolean;reason:string|null;median:ForecastPoint[];upper:ForecastPoint[];lower:ForecastPoint[];anchorTime:number|null;lastTime:number|null};

const empty=(reason:string):ValidatedForecast=>({valid:false,reason,median:[],upper:[],lower:[],anchorTime:null,lastTime:null});
const validSeries=(value:any):value is ForecastPoint[]=>Array.isArray(value)&&value.length>=3&&value.every((p:any)=>Number.isInteger(p?.time)&&p.time>0&&Number.isFinite(p?.price)&&p.price>0);

export function validateForecastChartData(prediction:any,symbol:string,timeframeMs:number,lastCandleMs:number):ValidatedForecast{
  const forecast=prediction?.forecast; if(!forecast?.available)return empty(forecast?.reason??"Forecast unavailable");
  if(forecast.symbol!==symbol)return empty("Forecast symbol does not match the chart");
  if(forecast.timeframe!==prediction?.timeframe)return empty("Forecast timeframe does not match the active prediction");
  if(forecast.decision_id!==prediction?.decision_id)return empty("Forecast decision ID does not match the active decision");
  const median=forecast.median_path,upper=forecast.upper_band,lower=forecast.lower_band;
  if(!validSeries(median)||!validSeries(upper)||!validSeries(lower))return empty("Forecast series is missing or contains invalid values");
  if(median.length!==upper.length||median.length!==lower.length)return empty("Forecast band lengths do not match");
  const interval=Math.round(timeframeMs/1000),lastActual=Math.floor(lastCandleMs/1000);
  if(median[0].time!==lastActual)return empty("Forecast anchor does not match the final candle");
  for(const points of [median,upper,lower])for(let i=1;i<points.length;i++){
    if(points[i].time!==lastActual+i*interval)return empty("Forecast timestamps do not match the selected timeframe");
    if(points[i].time<=lastActual)return empty("Forecast point is not in future chart space");
  }
  return {valid:true,reason:null,median:median.slice(1),upper:upper.slice(1),lower:lower.slice(1),anchorTime:lastActual,lastTime:median.at(-1)?.time??null};
}
