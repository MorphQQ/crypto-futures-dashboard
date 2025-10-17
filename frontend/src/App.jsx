import { useState, useEffect } from 'react';
import io from 'socket.io-client';
import axios from 'axios';
import { 
  Card, Title, Text, Table, TableHead, TableRow, TableHeaderCell, TableBody, TableCell, BadgeDelta, 
  Dialog, TabGroup, TabList, Tab, TabPanel, TabPanels, DonutChart, Legend, Metric 
} from '@tremor/react';
import './App.css';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const safeFloat = (val) => {
  if (typeof val === 'string') {
    const cleaned = val.replace(/[^\d.-]/g, ''); // Strip $,% etc.
    return parseFloat(cleaned) || 0;
  }
  return Number(val) || 0;
};

const socket = io('http://localhost:5000');

function App() {
  const [metrics, setMetrics] = useState([]);
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('5m');  // P2 tf tease

  // Initial fetch /api/metrics?tf=activeTab
  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const response = await axios.get(`http://localhost:5000/api/metrics?tf=${activeTab}`);
        console.log('Fetch response:', response.status, response.data.length, 'pairs', response.data);  // Full array debug (8 items?)
        const parsedMetrics = response.data.map(m => ({
          ...m,
          oi_abs_usd: safeFloat(m.oi_abs_usd),
          formatted_oi: safeFloat(m.oi_abs_usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
          oi_delta_pct: safeFloat(m.oi_delta_pct),
          // Parse all LS keys (P2 tf)
          Global_LS_5m: safeFloat(m.Global_LS_5m),
          Global_LS_15m: safeFloat(m.Global_LS_15m),
          Global_LS_30m: safeFloat(m.Global_LS_30m),
          Global_LS_1h: safeFloat(m.Global_LS_1h)
        }));
        setMetrics(parsedMetrics);
      } catch (error) {
        console.error('Fetch error:', error.response?.status, error.response?.data || error.message);
      }
    };
    fetchMetrics();
  }, [activeTab]);

  // WS bind
  useEffect(() => {
    socket.on('connect', () => console.log('WS connected (EIO=4)'));
    socket.on('metrics_update', (update) => {
      const rawMetrics = update.data || update;
      console.log('WS update:', rawMetrics.length, 'pairs sample:', rawMetrics.slice(0,1));  // Sample only (spam reduce)
      const parsedMetrics = rawMetrics.map(m => ({
        ...m,
        oi_abs_usd: safeFloat(m.oi_abs_usd),
        formatted_oi: safeFloat(m.oi_abs_usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
        oi_delta_pct: safeFloat(m.oi_delta_pct),
        Global_LS_5m: safeFloat(m.Global_LS_5m),
        Global_LS_15m: safeFloat(m.Global_LS_15m),
        Global_LS_30m: safeFloat(m.Global_LS_30m),
        Global_LS_1h: safeFloat(m.Global_LS_1h)
      }));
      setMetrics(parsedMetrics);
    });
    return () => {
      socket.off('connect');
      socket.off('metrics_update');
    };
  }, []);  // Remove activeTab dep (WS global; tf filter client P2)

  // Modal history fetch
  useEffect(() => {
    if (selectedSymbol && modalOpen) {
      const fetchHistory = async () => {
        try {
          const response = await axios.get(`http://localhost:5000/api/metrics/${selectedSymbol}/history?tf=${activeTab}`);
          const lsKey = `global_ls_${activeTab}`;
          const mappedData = response.data.map(row => ({
            time: new Date(row.time * 1000).toLocaleString(),
            price: safeFloat(row.Price),
            oi: safeFloat(row.oi_abs_usd || row.oi_abs),
            ls: safeFloat(row[lsKey]) || 0  // Fallback 0 if None
          }));
          setChartData(mappedData);
        } catch (error) {
          console.error('History fetch error:', error.response?.status, error.message);
          setChartData([]);
        }
      };
      fetchHistory();
    }
  }, [selectedSymbol, modalOpen, activeTab]);

  const handleRowClick = (symbol) => {
    setSelectedSymbol(symbol);
    setModalOpen(true);
  };

  // Global KPI calc (2-dec USD; NaN safe)
  const globalAvgOI = metrics.length > 0 ? metrics.reduce((sum, m) => sum + safeFloat(m.oi_abs_usd), 0) / metrics.length : 0;
  const globalAvgLS = metrics.length > 0 ? metrics.reduce((sum, m) => {
    const lsKey = `Global_LS_${activeTab}`;
    return sum + safeFloat(m[lsKey]);
  }, 0) / metrics.length : 0;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">
      <Card className="mb-4 bg-gray-800">
        <Title className="text-2xl">Crypto Futures Dashboard</Title>
        <Text>Phase 1 MVP: Live table + WS refresh ({metrics.length} pairs, tf: {activeTab})</Text>
      </Card>

      <div className="flex gap-4">
        <aside className="w-64 bg-gray-800 p-4 rounded-lg">
          <nav>
            <ul className="space-y-2">
              <li><a href="#" className="text-blue-400 hover:text-blue-300">Metrics</a></li>
              <li><a href="#" className="text-gray-400">Alerts (P3)</a></li>
            </ul>
          </nav>
        </aside>

        <main className="flex-1">
          {/* P2 Tease: Tabs tf */}
          <TabGroup className="mb-4" value={activeTab} onChange={setActiveTab}>
            <TabList className="bg-gray-800">
              <Tab value="5m">5m</Tab>
              <Tab value="15m">15m</Tab>
              <Tab value="1h">1h</Tab>
            </TabList>
            <TabPanels>
              <TabPanel>
                {/* KPI globals in Card */}
                <Card className="mb-4 bg-gray-800">
                  <Title>Global Metrics</Title>
                  <div className="grid grid-cols-2 gap-4 mt-4">
                    <div>
                      <Metric>{globalAvgOI.toLocaleString('en-US', { maximumFractionDigits: 2 })}</Metric>
                      <Text className="text-sm text-gray-400">Avg OI (USD)</Text>
                    </div>
                    <div>
                      <Metric>{globalAvgLS.toFixed(4)}</Metric>
                      <Text className="text-sm text-gray-400">Avg L/S Ratio</Text>
                    </div>
                  </div>
                </Card>
                <div className="grid grid-cols-1 gap-4">
                  <Card className="bg-gray-800">
                    <Table>
                      <TableHead>
                        <TableRow>
                          <TableHeaderCell>Symbol</TableHeaderCell>
                          <TableHeaderCell>OI (USD)</TableHeaderCell>
                          <TableHeaderCell>Global L/S ({activeTab})</TableHeaderCell>
                          <TableHeaderCell>OI Δ%</TableHeaderCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {metrics.length > 0 ? (
                          metrics.map((metric) => {
                            const oiDelta = safeFloat(metric.oi_delta_pct);
                            const deltaType = oiDelta > 0 ? 'increase' : oiDelta < 0 ? 'decrease' : 'unchanged';
                            const lsKey = `Global_LS_${activeTab}`;
                            const lsValue = safeFloat(metric[lsKey]);
                            return (
                              <TableRow key={metric.symbol} onClick={() => handleRowClick(metric.symbol)} className="cursor-pointer hover:bg-gray-700">
                                <TableCell>{metric.symbol}</TableCell>
                                <TableCell>{metric.formatted_oi}</TableCell>
                                <TableCell>{isNaN(lsValue) ? 'N/A' : lsValue.toFixed(4)}</TableCell>
                                <TableCell>
                                  <BadgeDelta deltaType={deltaType}>
                                    {isNaN(oiDelta) ? 'N/A' : `${oiDelta.toFixed(2)}%`}
                                  </BadgeDelta>
                                </TableCell>
                              </TableRow>
                            );
                          })
                        ) : (
                          <TableRow>
                            <TableCell colSpan={4} className="text-center text-gray-400">No metrics loaded (check fetch)</TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </Card>
                  {/* P3 Tease: DonutChart LS distribution (data check) */}
                  <Card className="bg-gray-800">
                    <Title>LS Ratio Distribution</Title>
                    {metrics.length > 0 ? (
                      <>
                        <DonutChart
                          className="mt-6 h-80"
                          data={metrics.map(m => ({ name: m.symbol, value: safeFloat(m[`Global_LS_${activeTab}`]) || 0 }))}  // Dynamic tf
                          category="value"
                          index="name"
                          colors={['blue', 'green', 'orange']}
                          showAnimation={false}  // Suppress resize warns
                          minHeight={300}  // Explicit height px
                        />
                        <Legend
                          className="mt-4"
                          categories={metrics.map(m => m.symbol)}
                          colors={['blue', 'green', 'orange']}
                        />
                      </>
                    ) : (
                      <Text className="text-gray-400">No data</Text>
                    )}
                  </Card>
                </div>
              </TabPanel>
            </TabPanels>
          </TabGroup>

          {/* Modal for Chart */}
          <Dialog open={modalOpen} onClose={() => setModalOpen(false)} className="bg-gray-800">
            <Title>Chart for {selectedSymbol} ({activeTab})</Title>
            {/* P4 Tease: Slider zoom */}
            <div className="mt-4">
              <input type="range" min="0" max={Math.max(0, chartData.length - 1)} className="w-full" onChange={(e) => console.log('Slider:', e.target.value)} />
            </div>
            {/* LineChart (P1 lines: price/OI/LS) */}
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData} margin={{top: 5, right: 30, left: 20, bottom: 5}}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" angle={-45} textAnchor="end" height={70} />
                <YAxis yAxisId="left" orientation="left" />
                <YAxis yAxisId="right" orientation="right" domain={['dataMin * 0.95', 'dataMax * 1.05']} />
                <Tooltip />
                <Line type="monotone" dataKey="price" yAxisId="left" stroke="#8884d8" name="Price" />
                <Line type="monotone" dataKey="oi" yAxisId="right" stroke="#82ca9d" name="OI" />
                <Line type="monotone" dataKey="ls" stroke="#ffc658" name="L/S" />
              </LineChart>
            </ResponsiveContainer>
            {/* P2 Tease: Export button */}
            <button className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
              Export CSV (Papa P2)
            </button>
          </Dialog>
        </main>
      </div>
    </div>
  );
}

export default App;