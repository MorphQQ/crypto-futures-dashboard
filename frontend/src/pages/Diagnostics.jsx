import React from "react";
import { useQuant } from "../context/QuantContext";
import { Card } from "@tremor/react";

export default function Diagnostics() {
  const { wsStatus, uptimePct, refresh } = useQuant();
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Diagnostics</h2>
      <Card>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <div className="text-xs opacity-70">WS Status</div>
            <div className="font-mono">{wsStatus}</div>
          </div>
          <div>
            <div className="text-xs opacity-70">Uptime</div>
            <div className="font-mono">{uptimePct}%</div>
          </div>
          <div>
            <div className="text-xs opacity-70">&nbsp;</div>
            <button className="px-3 py-1 bg-gray-800 rounded" onClick={refresh}>Force refresh</button>
          </div>
        </div>
      </Card>
    </div>
  );
}
