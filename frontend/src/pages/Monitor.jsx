import React from "react";
import { useQuant } from "../context/QuantContext";
import { Card } from "@tremor/react";
import {
  ResponsiveContainer,
  ComposedChart,
  XAxis,
  YAxis,
  Tooltip,
  Area,
  Line,
  Bar,
} from "recharts";

export default function Monitor() {
  const { selectedSymbol, metrics } = useQuant();
  const metric = metrics[selectedSymbol] || null;

  // placeholder timeseries generator if backend provides no series: adapt to your payload shape
  const mockTs = (metric) => {
    if (!metric || !metric.history) {
      // create small mock
      const now = Date.now();
      return new Array(30).fill(0).map((_, i) => ({ t: now - (30 - i) * 60_000, price: (metric?.price || 100) + Math.sin(i) * 2, oi: (metric?.oi || 0) * (1 + i / 100) }));
    }
    return metric.history;
  };

  const data = mockTs(metric);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">{selectedSymbol ? `Monitor — ${selectedSymbol}` : "Monitor — select a symbol"}</h2>
      <Card>
        <div style={{ height: 420 }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data}>
              <XAxis dataKey="t" tickFormatter={(ts) => new Date(ts).toLocaleTimeString()} />
              <YAxis yAxisId="left" domain={["auto", "auto"]} />
              <YAxis yAxisId="right" orientation="right" />
              <Tooltip labelFormatter={(ts) => new Date(ts).toLocaleString()} />
              <Area yAxisId="left" dataKey="oi" name="Open Interest" fillOpacity={0.08} strokeWidth={0} />
              <Line yAxisId="left" dataKey="price" name="Price" strokeWidth={2} dot={false} />
              <Bar yAxisId="right" dataKey="ls_delta" name="LS delta" barSize={8} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}
