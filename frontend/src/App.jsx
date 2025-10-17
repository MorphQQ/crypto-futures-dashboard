import { useState, useEffect } from 'react';
import io from 'socket.io-client';
import axios from 'axios';
import { 
  Card, Title, Text, Table, TableHead, TableRow, TableHeaderCell, TableBody, TableCell, BadgeDelta, 
  Modal, KPIList, Metric, Value, TabGroup, TabList, Tab, TabPanel, TabPanels, DonutChart, Legend 
} from '@tremor/react';
import './App.css';

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
        setMetrics(response.data);
        console.log(`Fetched ${activeTab} metrics:`, response.data.length, 'pairs');
      } catch (error) {
        console.error('Fetch error:', error);
      }
    };
    fetchMetrics();
  }, [activeTab]);

  // WS bind
  useEffect(() => {
    socket.on('connect', () => console.log('WS connected (EIO=4)'));
    socket.on('metrics_update', (update) => {
      const updatedMetrics = update.data || update;
      setMetrics(updatedMetrics);
      console.log(`WS ${activeTab} metrics_update received:`, updatedMetrics.length, 'pairs');
    });
    return () => {
      socket.off('connect');
      socket.off('metrics_update');
    };
  }, [activeTab]);

  // Modal history fetch
  useEffect(() => {
    if (selectedSymbol && modalOpen) {
      const fetchHistory = async () => {
        try {
          const response = await axios.get(`http://localhost:5000/api/metrics/${selectedSymbol}/history?tf=${activeTab}`);
          const mappedData = response.data.map(row => ({
            time: new Date(row.time * 1000).toLocaleString(),
            price: parseFloat(row.Price?.replace('$', '') || 0),
            oi: row.oi_abs_usd || 0,
            ls: row.global_ls_5m || 0
          }));
          setChartData(mappedData);
        } catch (error) {
          console.error('History fetch error:', error);
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

  // Global KPI calc (2-dec USD)
  const globalAvgOI = metrics.reduce((sum, m) => sum + (m.oi_abs_usd || 0), 0) / metrics.length;
  const globalAvgLS = metrics.reduce((sum, m) => sum + (m.global_ls_5m || 0), 0) / metrics.length;
  const kpiData = [
    { title: 'Avg OI (USD)', metric: globalAvgOI.toLocaleString('en-US', { maximumFractionDigits: 2 }), color: 'green' },
    { title: 'Avg L/S Ratio', metric: globalAvgLS.toFixed(4), color: 'blue' }
  ];

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
          <TabGroup className="mb-4" value={activeTab} onValueChange={setActiveTab}>
            <TabList className="bg-gray-800">
              <Tab value="5m">5m</Tab>
              <Tab value="15m">15m</Tab>
              <Tab value="1h">1h</Tab>
            </TabList>
            <TabPanels>
              <TabPanel>
                <KPIList data={kpiData} className="mb-4" />
                <Grid numCols={1} className="gap-4">  // Single col; no Sm/Md warning
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
                        {metrics.map((metric) => {
                          const oiDelta = metric.oi_delta_pct;
                          const deltaType = oiDelta > 0 ? 'increase' : 'decrease';
                          const lsKey = `Global_LS_${activeTab}`;
                          return (
                            <TableRow key={metric.symbol} onClick={() => handleRowClick(metric.symbol)} className="cursor-pointer hover:bg-gray-700">
                              <TableCell>{metric.symbol}</TableCell>
                              <TableCell>${(metric.oi_abs_usd || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}</TableCell>
                              <TableCell>{metric[lsKey]?.toFixed(4) ?? 'N/A'}</TableCell>
                              <TableCell>
                                <BadgeDelta deltaType={deltaType}>
                                  {oiDelta?.toFixed(2) ?? 'N/A'}%
                                </BadgeDelta>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </Card>
                  {/* P3 Tease: DonutChart LS distribution */}
                  <Card className="bg-gray-800">
                    <Title>LS Ratio Distribution</Title>
                    <DonutChart
                      className="mt-6 h-80"
                      data={metrics.map(m => ({ name: m.symbol, value: m.global_ls_5m || 0 }))}
                      category="value"
                      index="name"
                      colors={['blue', 'green', 'orange']}
                      showLegend={false}
                    />
                    <Legend
                      className="mt-4"
                      data={metrics.map(m => ({ name: m.symbol, color: 'blue' }))}
                    />
                  </Card>
                </Grid>
              </TabPanel>
            </TabPanels>
          </TabGroup>

          {/* Modal for Chart */}
          <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} className="bg-gray-800">
            <Title>Chart for {selectedSymbol} ({activeTab})</Title>
            {/* P4 Tease: Slider zoom */}
            <div className="mt-4">
              <input type="range" min="0" max={Math.max(0, chartData.length - 1)} className="w-full" onChange={(e) => console.log('Slider:', e.target.value)} />
            </div>
            {/* LineChart stub (P1; Recharts P4 full) */}
            <div className="h-96 mt-4 bg-gray-700 rounded flex items-center justify-center">
              {chartData.length > 0 ? (
                <Text>LineChart Stub: Price/OI/LS lines for {selectedSymbol} (zoom slider tease)</Text>
              ) : (
                <Text>No data (history fetch stub)</Text>
              )}
            </div>
            {/* P2 Tease: Export button */}
            <button className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
              Export CSV (Papa P2)
            </button>
          </Modal>
        </main>
      </div>
    </div>
  );
}

export default App;