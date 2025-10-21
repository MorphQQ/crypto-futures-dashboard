import React from "react";
import { useQuant } from "../context/QuantContext";
import { Card } from "@tremor/react";

export default function Alerts() {
  const { alerts } = useQuant();
  return (
    <div>
      <h2 className="text-lg font-medium">Alerts</h2>
      <div className="grid grid-cols-1 gap-3 mt-3">
        {alerts.length === 0 && <Card>No alerts — everything calm.</Card>}
        {alerts.map((a, i) => (
          <Card key={i}>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-semibold">{a.symbol}</div>
                <div className="text-xs opacity-70">{a.reason}</div>
              </div>
              <div className="text-sm opacity-60">{new Date(a.ts).toLocaleString()}</div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
