import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ModelVotesPanel from "./ModelVotesPanel";

const base={family:"tree_ml",version:"v1",calibrated_confidence:.6,resolved_samples:0,historical_accuracy:null,recent_accuracy:null,realized_edge:null,evidence_tier:"insufficient_evidence",reason:"current signal"};

describe("ModelVotesPanel",()=>{
  it("groups candidates into the four contributor buckets instead of by source type",()=>{
    render(<ModelVotesPanel finalDirection="LONG" candidates={[
      {...base,source_type:"ml",name:"logistic",version:"v1",status:"eligible",direction:"LONG",final_points:2,eligible:true},
      {...base,source_type:"strategy",name:"ema_pullback",status:"limited",direction:"NO_TRADE",final_points:0,eligible:false,rejection_code:"NO_TRIGGER",rejection_reason:"No eligible EMA pullback"},
      {...base,source_type:"quant",name:"atr_expected_move",status:"limited",direction:"NEUTRAL",final_points:0,eligible:false,rejection_code:"REGIME_MISMATCH",rejection_reason:"Volatility too high for this regime"},
      {...base,source_type:"ml",name:"logistic_regression",status:"shadow",direction:"LONG",final_points:0,eligible:false},
    ]}/>);
    expect(screen.getByText(/Active/)).toBeInTheDocument();
    expect(screen.getByText(/Vetoed/)).toBeInTheDocument();
    expect(screen.getByText(/Abstaining/)).toBeInTheDocument();
    expect(screen.getByText(/Inactive/)).toBeInTheDocument();
    expect(screen.getByText("logistic")).toBeInTheDocument();
    expect(screen.getByText(/Volatility too high for this regime/)).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for/)).not.toBeInTheDocument();
  });

  it("flags when the active group's point sum does not reconcile with the engine total", () => {
    render(<ModelVotesPanel finalDirection="LONG" enginePoints={{long:9,short:0}} candidates={[
      {...base,source_type:"ml",name:"logistic",status:"eligible",direction:"LONG",final_points:2,eligible:true},
    ]}/>);
    expect(screen.getByText(/does not reconcile/)).toBeInTheDocument();
  });

  it("shows a completed empty state",()=>{render(<ModelVotesPanel candidates={[]} />);expect(screen.getByText(/No candidates generated/)).toBeInTheDocument();});
});
