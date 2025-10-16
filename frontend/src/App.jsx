import { useState, useEffect } from 'react';
import io from 'socket.io-client';
import { Title, Card, Grid } from '@tremor/react';  // Valid exports: No Header/Layout
import './App.css';  // Tailwind directives

const socket = io('http://localhost:5000');  // Backend WS

function App() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    socket.on('connect', () => {
      console.log('WS connected (EIO=4)');  // Handshake match
    });
    socket.on('metrics_update', (data) => {
      console.log('Received metrics_update:', data);  // 3 pairs batch
      console.log('Refreshing...');  // Stub: P2 useQuery
      window.location.reload();  // Simple MVP reload
    });
    return () => {
      socket.off('connect');
      socket.off('metrics_update');
    };
  }, []);

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">  {/* Tailwind dark body */}
      {/* Header: Tremor Title in Card */}
      <Card className="mb-4 bg-gray-800">
        <Title className="text-2xl">Crypto Futures Dashboard</Title>
        <p className="text-gray-400">Phase 1 MVP: Live table + WS refresh</p>
      </Card>

      {/* Sidebar Stub: Tailwind flex (no Tremor Sidebar; expand P2) */}
      <div className="flex gap-4">
        <aside className="w-64 bg-gray-800 p-4 rounded-lg">  {/* Sidebar */}
          <nav>
            <ul className="space-y-2">
              <li><a href="#" className="text-blue-400 hover:text-blue-300">Metrics</a></li>
              <li><a href="#" className="text-gray-400">Alerts (P3)</a></li>
            </ul>
          </nav>
        </aside>

        {/* Main: Grid for content (Tremor Grid; HMR counter for test) */}
        <main className="flex-1">
          <Grid numCols={1} className="gap-4">  {/* Responsive; add table P1 */}
            <Card className="bg-gray-800">
              <button
                className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
                onClick={() => setCount((count) => count + 1)}
              >
                Count: {count} (HMR Test)
              </button>
              <p className="mt-2 text-gray-400">Edit src/App.jsx → Save for hot-reload.</p>
            </Card>
            {/* Next: <MetricsTable /> 3 rows from /api/metrics */}
            <div className="text-gray-400">WS listening—expect refresh on scrape.</div>
          </Grid>
        </main>
      </div>
    </div>
  );
}

export default App;