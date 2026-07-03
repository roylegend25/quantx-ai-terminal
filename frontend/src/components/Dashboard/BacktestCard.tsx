import { memo } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtAxisNumber } from "../../lib/format";

type Props = {
  backtest: any;
  runBacktest: () => void;
};

function BacktestCard({
  backtest,
  runBacktest,
}: Props) {
  return (
    <>
      <div className="controls">
        <button onClick={runBacktest}>
          📈 Run 5m Backtest
        </button>
      </div>

      {backtest && (
        <>
          <div className="backtest-grid">

            <div>
              <span>Final Balance</span>
              <b>${backtest.final_balance}</b>
            </div>

            <div>
              <span>Total Return</span>
              <b>{backtest.total_return_pct}%</b>
            </div>

            <div>
              <span>Total Trades</span>
              <b>{backtest.total_trades}</b>
            </div>

            <div>
              <span>Win Rate</span>
              <b>{backtest.win_rate}%</b>
            </div>

            <div>
              <span>Profit Factor</span>
              <b>{backtest.profit_factor}</b>
            </div>

            <div>
              <span>Max Drawdown</span>
              <b>{backtest.max_drawdown_pct}%</b>
            </div>

          </div>

          {backtest.chart && (

            <div style={{ height: 320, marginTop: 25 }}>

              <ResponsiveContainer width="100%" height="100%">

                <LineChart data={backtest.chart}>

                  <CartesianGrid strokeDasharray="3 3"/>

                  <XAxis dataKey="trade"/>

                  <YAxis tickFormatter={fmtAxisNumber} width={64} />

                  <Tooltip/>

                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="#00ff88"
                    strokeWidth={3}
                    dot={false}
                  />

                </LineChart>

              </ResponsiveContainer>

            </div>

          )}

        </>
      )}

    </>
  );
}

export default memo(BacktestCard);
