import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import io from "socket.io-client"; // socket.io-client
import { motion } from "framer-motion";

const QuantContext = createContext();
export const useQuant = () => useContext(QuantContext);

/**
 * Config notes:
 * - SOCKET_URL: set in .env (Vite: VITE_API_BASE)
 * - REST fallback every 30s if no WS messages (auto switching)
 * - Exposes: metrics (map), selectedSymbol, wsStatus, uptimePct, alerts[], refresh() helper
 */

const SOCKET_URL = import.meta.env.VITE_WS_URL || "http://localhost:5173"; // replace with your ws host
const REST_BASE = import.meta.env.VITE_API_BASE || "/api";

export const QuantProvider = ({ children }) => {
  const [metrics, setMetrics] = useState({}); // {PAIR: {...}}
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [wsStatus, setWsStatus] = useState("idle"); // connecting | open | closed | error
  const [uptimePct, setUptimePct] = useState(0);
  const [alerts, setAlerts] = useState([]);
  const socketRef = useRef(null);
  const lastWsTsRef = useRef(Date.now());
  const fallbackTimerRef = useRef(null);

  // Helper to merge incoming metric rows (from WS or REST)
  const upsertMetrics = (rows = []) => {
    setMetrics(prev => {
      const next = { ...prev };
      rows.forEach(r => {
        const sym = r.symbol || r.pair || r.sym;
        if (!sym) return;
        next[sym] = { ...(next[sym] || {}), ...r, updatedAt: Date.now() };
      });
      return next;
    });
  };

  // Basic alert detection (Z > 2.5 etc.) — tweak to your rules
  const evaluateAlerts = (rows = []) => {
    const newAlerts = [];
    rows.forEach(r => {
      if (r.Z && r.Z > 2.5) newAlerts.push({ symbol: r.symbol, reason: `Z=${r.Z}`, ts: Date.now(), severity: "high" });
      if (r.LS && r.LS > 2) newAlerts.push({ symbol: r.symbol, reason: `LS=${r.LS}`, ts: Date.now(), severity: "medium" });
      if (r.imb && Math.abs(r.imb) > 0.03) newAlerts.push({ symbol: r.symbol, reason: `imb=${(r.imb*100).toFixed(2)}%`, ts: Date.now(), severity: "low" });
    });
    if (newAlerts.length) setAlerts(prev => [...newAlerts, ...prev].slice(0, 200));
  };

  // REST fetch for all metrics (fallback or boot)
  const fetchMetricsRest = async () => {
    try {
      const res = await fetch(`${REST_BASE}/metrics`);
      if (!res.ok) throw new Error("rest metrics failed");
      const data = await res.json();
      // expecting array of metric rows
      upsertMetrics(data);
      evaluateAlerts(data);
      setWsStatus(s => (s === "open" ? s : "polling"));
      lastWsTsRef.current = Date.now();
      return data;
    } catch (e) {
      console.warn("REST metrics error", e);
      setWsStatus("error");
      return [];
    }
  };

  // Start socket, with event handling
  useEffect(() => {
    setWsStatus("connecting");
    const socket = io(SOCKET_URL, { transports: ["websocket"], path: "/socket.io" });
    socketRef.current = socket;

    socket.on("connect", () => {
      setWsStatus("open");
      lastWsTsRef.current = Date.now();
    });

    socket.on("metrics_update", (payload) => {
      // payload: array or single object
      lastWsTsRef.current = Date.now();
      setWsStatus("open");
      const rows = Array.isArray(payload) ? payload : [payload];
      upsertMetrics(rows);
      evaluateAlerts(rows);
    });

    socket.on("connect_error", (err) => {
      console.warn("WS connect_error", err);
      setWsStatus("error");
    });

    socket.on("disconnect", (reason) => {
      setWsStatus("closed");
      console.warn("WS disconnected", reason);
    });

    // optional: health/uptime event
    socket.on("health", (h) => {
      if (typeof h.uptime_pct === "number") setUptimePct(h.uptime_pct);
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, []);

  // Fallback poll loop: if WS idle for >30s, call REST every 30s.
  useEffect(() => {
    const pollInterval = 30_000;
    const check = async () => {
      const idleMs = Date.now() - lastWsTsRef.current;
      if (!socketRef.current || socketRef.current.disconnected || idleMs > pollInterval) {
        // perform fallback REST poll
        await fetchMetricsRest();
      }
    };
    // immediate warm REST fetch (boot)
    fetchMetricsRest();
    fallbackTimerRef.current = setInterval(check, pollInterval);
    return () => clearInterval(fallbackTimerRef.current);
  }, []);

  // Small public API
  const refresh = () => fetchMetricsRest();

  // Derived: nice lightweight metrics list (sorted)
  const pairsList = React.useMemo(() => {
    return Object.values(metrics).sort((a, b) => (b.oi || 0) - (a.oi || 0)).slice(0, 1000);
  }, [metrics]);

  return (
    <QuantContext.Provider
      value={{
        metrics,
        pairsList,
        selectedSymbol,
        setSelectedSymbol,
        wsStatus,
        uptimePct,
        alerts,
        refresh,
      }}
    >
      {children}
    </QuantContext.Provider>
  );
};
