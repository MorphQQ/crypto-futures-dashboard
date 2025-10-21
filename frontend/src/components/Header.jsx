import React from "react";
import { useQuant } from "../context/QuantContext";
import { BellIcon, CloudIcon } from "lucide-react";
import { motion } from "framer-motion";

export default function Header({ onRouteChange, currentRoute }) {
  const { wsStatus, uptimePct, alerts } = useQuant();
  const statusColor = wsStatus === "open" ? "bg-emerald-500" : wsStatus === "connecting" ? "bg-amber-400" : "bg-rose-500";

  return (
    <header className="flex items-center justify-between px-4 py-3 border-b border-gray-800 bg-gray-950">
      <div className="flex items-center gap-4">
        <div className="text-xl font-semibold">Quant Command Dashboard v4</div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`inline-block w-3 h-3 rounded-full ${statusColor}`} title={`WS: ${wsStatus}`} />
          <span>WS: {wsStatus}</span>
          <span className="opacity-70">•</span>
          <CloudIcon className="w-4 h-4 inline" />
          <span>Uptime {uptimePct}%</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <nav className="flex gap-2">
          {["market", "monitor", "alerts", "diagnostics"].map((r) => (
            <button
              key={r}
              onClick={() => onRouteChange(r)}
              className={`px-3 py-1 rounded text-sm ${currentRoute === r ? "bg-gray-800" : "bg-transparent hover:bg-gray-800"}`}
            >
              {r.toUpperCase()}
            </button>
          ))}
        </nav>

        <button title={`${alerts.length} alerts`} className="relative">
          <BellIcon className="w-5 h-5" />
          {alerts.length > 0 && (
            <motion.span
              initial={{ scale: 0.7 }}
              animate={{ scale: 1 }}
              className="absolute -top-2 -right-2 bg-rose-500 text-xs rounded-full px-1"
            >
              {alerts.length}
            </motion.span>
          )}
        </button>
      </div>
    </header>
  );
}
