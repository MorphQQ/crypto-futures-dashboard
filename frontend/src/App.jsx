import React from "react";
import { QuantProvider } from "./context/QuantContext";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import QuantSummaryPanel from "./components/QuantSummaryPanel";
import Market from "./pages/Market";
import Monitor from "./pages/Monitor";
import Alerts from "./pages/Alerts";
import Diagnostics from "./pages/Diagnostics";
import "./index.css"; // Tailwind + Tremor styles

export default function App() {
  const [route, setRoute] = React.useState("market"); // market | monitor | alerts | diagnostics

  return (
    <QuantProvider>
      <div className="min-h-screen bg-slate-950 text-tremor-content dark:text-dark-tremor-content antialiased">
        {/* Main Container */}
        <div className="mx-auto max-w-screen-2xl p-4 md:p-10 space-y-6">
          
          {/* Header */}
          <Header onRouteChange={setRoute} currentRoute={route} />

          {/* QuantSummaryPanel (live stats) */}
          <div className="mt-2">
            <QuantSummaryPanel socket={window?.quantSocket} />
          </div>

          {/* Main Grid */}
          <div className="flex gap-6">
            <Sidebar current={route} onChange={setRoute} />
            <main className="flex-1 space-y-6">
              {route === "market" && <Market />}
              {route === "monitor" && <Monitor />}
              {route === "alerts" && <Alerts />}
              {route === "diagnostics" && <Diagnostics />}
            </main>
          </div>
        </div>
      </div>
    </QuantProvider>
  );
}
