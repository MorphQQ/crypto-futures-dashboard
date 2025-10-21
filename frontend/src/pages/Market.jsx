import React from "react";
import { useQuant } from "../context/QuantContext";
import { Card, Table, TableHead, TableRow, TableCell, TableBody } from "@tremor/react";

export default function Market() {
  const { pairsList, setSelectedSymbol } = useQuant();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Market — Pairs</h2>
      </div>

      <Card>
        <div className="overflow-auto max-h-[60vh]">
          <table className="w-full table-fixed text-sm">
            <thead className="text-left text-xs opacity-70 uppercase">
              <tr>
                <th className="p-2">Symbol</th>
                <th className="p-2">OI</th>
                <th className="p-2">LS</th>
                <th className="p-2">Funding</th>
                <th className="p-2">Z</th>
              </tr>
            </thead>
            <tbody>
              {pairsList.map((p) => (
                <tr key={p.symbol} className="hover:bg-gray-850 cursor-pointer" onClick={() => setSelectedSymbol(p.symbol)}>
                  <td className="p-2 font-medium">{p.symbol}</td>
                  <td className="p-2">{(p.oi || 0).toLocaleString()}</td>
                  <td className="p-2">{p.LS?.toFixed?.(2) ?? "-"}</td>
                  <td className="p-2">{p.funding?.toFixed?.(4) ?? "-"}</td>
                  <td className="p-2">{p.Z?.toFixed?.(2) ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
