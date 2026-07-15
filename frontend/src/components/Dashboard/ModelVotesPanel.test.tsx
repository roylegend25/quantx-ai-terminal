import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ModelVotesPanel from "./ModelVotesPanel";

const base={family:"tree_ml",version:"v1",status:"limited",direction:"LONG" as const,calibrated_confidence:.6,final_points:2,resolved_samples:0,historical_accuracy:null,recent_accuracy:null,realized_edge:null,evidence_tier:"insufficient_evidence",reason:"current signal"};
describe("ModelVotesPanel",()=>{
  it("renders all three V2 pillars instead of a waiting message",()=>{
    render(<ModelVotesPanel finalDirection="NO_TRADE" candidates={[{...base,source_type:"ml",name:"logistic"},{...base,source_type:"strategy",name:"ema"},{...base,source_type:"quant",name:"zscore"}]}/>);
    expect(screen.getByText("ML Models")).toBeInTheDocument();expect(screen.getByText("Strategies")).toBeInTheDocument();expect(screen.getByText("Quant Signals")).toBeInTheDocument();expect(screen.queryByText(/Waiting for/)).not.toBeInTheDocument();
  });
  it("shows a completed empty state",()=>{render(<ModelVotesPanel candidates={[]} />);expect(screen.getByText(/No candidates generated/)).toBeInTheDocument();});
});
