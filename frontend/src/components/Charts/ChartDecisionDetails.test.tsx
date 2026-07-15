import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChartDecisionChip, DecisionDetailsBottomSheet, DecisionDetailsPanel, ForecastLegend } from "./ChartDecisionDetails";

const prediction={direction:"NO_TRADE",forecast:{available:true,trade_actionable:false,forecast_type:"informational",reason:"Informational only",forecast_sources:[{type:"quant",name:"ATR Expected Move",direction:"BEARISH",weight:.4}]},decision_engine:{directional_confidence:null,abstention_confidence:.72,total_evidence:2.45,point_margin:2.45,required_point_margin:4,eligible_for_execution:false,blocking_reasons:["Insufficient evidence"],market_regime:{trend:"bearish",volatility:"high",liquidity:"normal",derivatives:"neutral",label:"High-volatility bearish trend"}}};

describe("chart decision presentation",()=>{
  it("labels a NO_TRADE forecast informational without fake confidence",()=>{
    render(<><ForecastLegend forecast={prediction.forecast} bands/><DecisionDetailsPanel prediction={prediction}/></>);
    expect(screen.getByText("AI Forecast — Informational")).toBeTruthy();
    expect(screen.getByText("Not established")).toBeTruthy();
    expect(screen.getByText("High-volatility bearish trend")).toBeTruthy();
    expect(screen.queryByText("[object Object]")).toBeNull();
    expect(screen.getByText("Blocked / informational only")).toBeTruthy();
  });
  it("opens decision details from the compact chip",()=>{
    const open=vi.fn(); render(<ChartDecisionChip prediction={prediction} onOpen={open}/>);
    fireEvent.click(screen.getByRole("button")); expect(open).toHaveBeenCalledOnce();
    const close=vi.fn(); render(<DecisionDetailsBottomSheet prediction={prediction} open onClose={close}/>);
    fireEvent.click(screen.getByLabelText("Close decision details")); expect(close).toHaveBeenCalledOnce();
  });
});
