import React from "react";
import { List, TrendingUp, Bell } from "lucide-react";

const items = [
  { key: "market", label: "Market", icon: <TrendingUp /> },
  { key: "monitor", label: "Monitor", icon: <List /> },
  { key: "alerts", label: "Alerts", icon: <Bell /> },
  { key: "diagnostics", label: "Diagnostics", icon: <List /> },
];

export default function Sidebar({ current, onChange }) {
  return (
    <aside className="w-72 border-r border-gray-800 p-3">
      <div className="mb-4 text-sm opacity-80">Navigation</div>
      <ul className="space-y-2">
        {items.map((it) => (
          <li key={it.key}>
            <button
              onClick={() => onChange(it.key)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded ${current === it.key ? "bg-gray-800" : "hover:bg-gray-800"}`}
            >
              <span className="w-5 h-5">{it.icon}</span>
              <span className="flex-1 text-left">{it.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
