import {render,screen,waitFor} from "@testing-library/react";
import {describe,expect,it,vi} from "vitest";
import LiveDecisionPanel from "./LiveDecisionPanel";
import {api} from "../../services/api";

vi.mock("../../services/api",()=>({api:{liveDecision:vi.fn()}}));

describe("LiveDecisionPanel",()=>{
 it("renders an exact NO_TRADE snapshot with requirements and blockers",async()=>{
  vi.mocked(api.liveDecision).mockResolvedValue({
   symbol:"BTCUSDT",timeframe:"15m",decision_id:"decision-1",signal:"NO_TRADE",
   next_evaluation_at:new Date(Date.now()+10_000).toISOString(),
   long_points:6,short_points:2,execution_eligible:false,
   market_regime:{label:"High-volatility neutral market"},
   candidate_counts:{total:35,eligible:8},
   blocking_reasons:["Expected edge is not supported"],
   requirements:[{id:"evidence",label:"Evidence",value:12,required:8,unit:"points",status:"passed",formula:"sum(abs(points))",scope:"decision-1",explanation:"Validated signal strength"}],
   decision_history:[{timestamp:new Date().toISOString(),decision_id:"decision-1",signal:"NO_TRADE",long_points:6,short_points:2,point_margin:4,evidence:12,required_point_margin:4}],
  });
  render(<LiveDecisionPanel symbol="BTCUSDT" timeframe="15m" prediction={{decision_id:"decision-1",direction:"NO_TRADE",decision_engine:{decision_id:"decision-1"}}}/>);
  await waitFor(()=>expect(api.liveDecision).toHaveBeenCalledWith("BTCUSDT","15m"));
  expect(await screen.findByText("Expected edge is not supported")).toBeInTheDocument();
  expect(screen.getAllByText("Evidence").length).toBeGreaterThan(0);
  expect(screen.getByText("8 eligible / 35")).toBeInTheDocument();
  expect(screen.getByText("Blocked")).toBeInTheDocument();
 });
});
