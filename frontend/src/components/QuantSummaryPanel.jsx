
import React, { useEffect, useState } from "react";
import { Card } from "@tremor/react";
import { motion } from "framer-motion";
import { Activity, TrendingUp, TrendingDown } from "lucide-react";

export default function QuantSummaryPanel({ socket }) {
  const [summary, setSummary] = useState({
    totalOI: 0,
    avgZ: 0,
    strongestBias: "-",
    biasDir: "neutral",
    updated: null,
  });

  const [pulse, setPulse] = useState(false);

  // REST fallback
  const fetchSummary = async () => {
    try {
      const res = await fetch(import.meta.env.VITE_API_BASE + "/quant_summary");
      if (res.ok) {
        const data = await res.json();
        updateSummary(data);
      }
    } catch (err) {
      console.warn("QuantSummary REST failed", err);
    }
  };

  const updateSummary = (data) => {
    setSummary({
      totalOI: data.total_oi ?? 0,
      avgZ: data.avg_z ?? 0,
      strongestBias: data.strongest_symbol ?? "-",
      biasDir: data.bias ?? "neutral",
      updated: new Date().toLocaleTimeString(),
    });
    setPulse(true);
    setTimeout(() => setPulse(false), 400);
  };

  useEffect(() => {
    if (!socket) return;
    socket.on("quant_summary", (data) => updateSummary(data));
    fetchSummary();
    const interval = setInterval(fetchSummary, 30000);
    return () => {
      socket.off("quant_summary");
      clearInterval(interval);
    };
  }, [socket]);

  const biasColor =
    summary.biasDir === "long"
      ? "text-emerald-400"
      : summary.biasDir === "short"
      ? "text-rose-400"
      : "text-gray-400";

  return (
    <Card className="bg-gray-950 border-gray-800">
      <motion.div
        animate={pulse ? { scale: 1.03 } : { scale: 1 }}
        transition={{ duration: 0.3 }}
        className="flex items-center justify-between"
      >
        <div className="flex flex-col">
          <span className="text-xs opacity-70">Total OI</span>
          <span className="text-lg font-mono">{summary.totalOI.toLocaleString()}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-xs opacity-70">Avg Z</span>
          <span className="text-lg font-mono">{summary.avgZ.toFixed(2)}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-xs opacity-70">Bias</span>
          <div className={`flex items-center gap-1 ${biasColor}`}>
            {summary.biasDir === "long" && <TrendingUp className="w-4 h-4" />}
            {summary.biasDir === "short" && <TrendingDown className="w-4 h-4" />}
            <span className="font-mono">{summary.strongestBias}</span>
          </div>
        </div>
        <div className="flex flex-col items-end text-xs opacity-60">
          <Activity className="w-4 h-4" />
          <span>{summary.updated}</span>
        </div>
      </motion.div>
    </Card>
  );
}
