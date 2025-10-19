import { useState, useEffect } from 'react';
import io from 'socket.io-client';
import axios from 'axios';
import { 
  Card, Title, Text, Table, TableHead, TableRow, TableHeaderCell, TableBody, TableCell, BadgeDelta, 
  Dialog, TabGroup, TabList, Tab, TabPanels, TabPanel, DonutChart, Legend, Metric 
} from '@tremor/react';
import localforage from 'localforage';  // Cache per-tf
import Papa from 'papaparse';  // CSV
import jsPDF from 'jspdf';
import 'jspdf-autotable';  // Tables
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
        console.log('Fetch response:', response.status, response.data.length, 'pairs', response.data.slice(0,1));  // Sample
        const parsedMetrics = response.data.map(m => ({
          ...m,
          oi_abs_usd: safeFloat(m.oi_abs_usd),
          formatted_oi: safeFloat(m.oi_abs_usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
          oi_delta_pct: safeFloat(m.oi_delta_pct),
          ls_delta_pct: safeFloat(m.ls_delta_pct),  // New
          z_ls: safeFloat(m.z_ls),  // New
          cvd: safeFloat(m.cvd),  // New
          imbalance: safeFloat(m.imbalance),  // New
          funding: safeFloat(m.funding),  // New
          rsi: safeFloat(m.rsi) || 50,  // Stub if missing
          price: safeFloat(m.price || m.Price),  // Fallback
          top_ls: safeFloat(m.Top_LS),  // Case match JSON
          [`Global_LS_${activeTab}`]: safeFloat(m[`Global_LS_${activeTab}`]),
          Global_LS_5m: safeFloat(m.Global_LS_5m),
          Global_LS_15m: safeFloat(m.Global_LS_15m),
          Global_LS_30m: safeFloat(m.Global_LS_30m),
          Global_LS_1h: safeFloat(m.Global_LS_1h),
          Market_Cap: m.Market_Cap || `$${safeFloat(m.Market_Cap || 0).toLocaleString()}`  // New: Fmt $1.2T
        }));
        setMetrics(parsedMetrics);  // Used
        // Cache per-tf
        localforage.setItem(`metrics_${activeTab}`, parsedMetrics).then(() => console.log('Cached tf:', activeTab));
      } catch (error) {
        console.error('Fetch error:', error.response?.status, error.response?.data || error.message);
        // Offline fallback
        localforage.getItem(`metrics_${activeTab}`).then(cached => {
          if (cached) {
            console.log('Loaded cached tf:', activeTab);
            setMetrics(cached);
          }
        });
      }
    };
    fetchMetrics();
  }, [activeTab]);  // Dep tf switch

  // Poll fallback for fast initial pop if empty (10s axios if length<5; fix deps no loop)
  useEffect(() => {
    if (metrics.length < 5) {  // Low? Poll on mount/switch
      const interval = setInterval(async () => {
        if (metrics.length < 5) {  // Re-check inside
          try {
            const response = await axios.get(`http://localhost:5000/api/metrics?tf=${activeTab}`);
            const parsedMetrics = response.data.map(m => ({
              ...m,
              oi_abs_usd: safeFloat(m.oi_abs_usd),
              formatted_oi: safeFloat(m.oi_abs_usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
              oi_delta_pct: safeFloat(m.oi_delta_pct),
              ls_delta_pct: safeFloat(m.ls_delta_pct),
              z_ls: safeFloat(m.z_ls),
              cvd: safeFloat(m.cvd),
              imbalance: safeFloat(m.imbalance),
              funding: safeFloat(m.funding),
              rsi: safeFloat(m.rsi) || 50,
              price: safeFloat(m.price || m.Price),
              top_ls: safeFloat(m.Top_LS),
              [`Global_LS_${activeTab}`]: safeFloat(m[`Global_LS_${activeTab}`]),
              Global_LS_5m: safeFloat(m.Global_LS_5m),
              Global_LS_15m: safeFloat(m.Global_LS_15m),
              Global_LS_30m: safeFloat(m.Global_LS_30m),
              Global_LS_1h: safeFloat(m.Global_LS_1h),
              Market_Cap: m.Market_Cap || `$${safeFloat(m.Market_Cap || 0).toLocaleString()}`
            }));
            setMetrics(parsedMetrics);
            localforage.setItem(`metrics_${activeTab}`, parsedMetrics);
            console.log('Poll fallback hit:', parsedMetrics.length, 'pairs tf:', activeTab);
          } catch (err) {
            console.error('Poll fetch err:', err.response?.status || err.message);
          }
        }
      }, 10000);  // 10s poll if low
      return () => clearInterval(interval);
    }
  }, [activeTab]);  // Fix: Only tf; no length→no re-poll loop

  // WS bind
  useEffect(() => {
    socket.on('connect', () => console.log('WS connected (EIO=4)'));
    socket.on('metrics_update', (update) => {
      const rawMetrics = update.data || update;
      console.log('WS update:', rawMetrics.length, 'pairs sample:', rawMetrics.slice(0,1));
      // Filter by activeTab (timeframe or LS key presence fallback)
      const tfMetrics = rawMetrics.filter(m => m.timeframe === activeTab || m[`Global_LS_${activeTab}`]).map(m => ({  // Fallback LS key if no timeframe bind
        ...m,
        // Parse as fetch
        oi_abs_usd: safeFloat(m.oi_abs_usd),
        formatted_oi: safeFloat(m.oi_abs_usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
        oi_delta_pct: safeFloat(m.oi_delta_pct),
        ls_delta_pct: safeFloat(m.ls_delta_pct),
        z_ls: safeFloat(m.z_ls),
        cvd: safeFloat(m.cvd),
        imbalance: safeFloat(m.imbalance),
        funding: safeFloat(m.funding),
        rsi: safeFloat(m.rsi) || 50,
        price: safeFloat(m.price || m.Price),
        top_ls: safeFloat(m.Top_LS),
        [`Global_LS_${activeTab}`]: safeFloat(m[`Global_LS_${activeTab}`]),
        Global_LS_5m: safeFloat(m.Global_LS_5m),
        Global_LS_15m: safeFloat(m.Global_LS_15m),
        Global_LS_30m: safeFloat(m.Global_LS_30m),
        Global_LS_1h: safeFloat(m.Global_LS_1h),
        Market_Cap: m.Market_Cap || `$${safeFloat(m.Market_Cap || 0).toLocaleString()}`
      }));
      console.log('Filtered tfMetrics:', tfMetrics.length, 'pairs');  // Debug len
      if (tfMetrics.length > 0) {  // Always set if >0 (no len<5 crash)
        setMetrics(tfMetrics);
        localforage.setItem(`metrics_${activeTab}`, tfMetrics);
      }
    });
    return () => {
      socket.off('connect');
      socket.off('metrics_update');
    };
  }, [activeTab]);  // Dep tf for filter

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

  const exportCSV = () => {
    const exportData = metrics.map(m => ({
      Symbol: m.symbol,
      Price: `$${safeFloat(m.price).toFixed(2)}`,
      'Market Cap': m.Market_Cap || 'N/A',  // New: Add col
      'OI USD': m.formatted_oi,
      'Top L/S': safeFloat(m.top_ls).toFixed(2),
      [`Global LS ${activeTab}`]: safeFloat(m[`Global_LS_${activeTab}`]).toFixed(4),
      RSI: Math.round(safeFloat(m.rsi)),
      CVD: safeFloat(m.cvd).toLocaleString(),
      'Z-Score': safeFloat(m.z_ls).toFixed(2),
      Imbalance: `${safeFloat(m.imbalance).toFixed(2)}%`,
      Funding: `${(safeFloat(m.funding) * 100).toFixed(2)}%`,
      'OI Δ%': `${safeFloat(m.oi_delta_pct).toFixed(2)}%`,
      'LS Δ%': `${safeFloat(m.ls_delta_pct).toFixed(2)}%`
    }));
    const csv = Papa.unparse(exportData);
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `futures-${activeTab}.csv`; a.click();
  };

  const exportPDF = () => {
  if (metrics.length === 0) { 
    console.warn('PDF: No data tf=', activeTab, '- check fetch/WS'); 
    return;  // Or alert('No metrics to export')
  }
  try {
    const doc = new jsPDF();
    const head = [['Symbol', 'Price', 'Market Cap', 'OI USD', 'Top L/S', `Global LS ${activeTab}`, 'RSI', 'CVD', 'Z-Score', 'Imbalance', 'Funding', 'OI Δ%', 'LS Δ%']];  // New: +Market Cap
    const body = metrics.map(m => {
      const safe = (v, def=0) => isNaN(Number(v)) || !isFinite(Number(v)) ? def : Number(v);  // + isFinite for inf/NaN
      const lsKey = `Global_LS_${activeTab}`;
      return [
        m.symbol,
        `$${safe(m.price).toFixed(2)}`,
        m.Market_Cap || `$${safe(m.Market_Cap || 0).toLocaleString()}`,  // New: +col
        m.formatted_oi || `$${safe(m.oi_abs_usd).toLocaleString(undefined, {maximumFractionDigits: 2})}B`,
        safe(m.top_ls).toFixed(2),
        safe(m[lsKey]).toFixed(4),
        Math.round(safe(m.rsi)),
        safe(m.cvd).toLocaleString(),
        safe(m.z_ls).toFixed(2),
        `${safe(m.imbalance).toFixed(2)}%`,
        `${safe(m.funding * 100).toFixed(2)}%`,
        `${safe(m.oi_delta_pct).toFixed(2)}%`,
        `${safe(m.ls_delta_pct).toFixed(2)}%`
      ];
    });
    doc.autoTable({ head, body, styles: { fontSize: 8, cellPadding: 3, halign: 'right' }, headStyles: { fillColor: [41, 41, 41], textColor: [255,255,255] } });  // Dark + right align $
    doc.save(`futures-metrics-${activeTab}-${new Date().toISOString().split('T')[0]}.pdf`);
    console.log('PDF exported:', metrics.length, 'rows tf=', activeTab);
  } catch (e) {
    console.error('PDF export error:', e.message, '- metrics sample:', metrics.slice(0,1));  // Debug NaN/empty
  }
};

  // Content Component (to avoid duplication; tf-dep)
  const MetricsContent = () => (
    <div className="grid grid-cols-1 gap-4">
      <Card className="mb-4 bg-gray-800">
        <Title>Global Metrics (tf: {activeTab})</Title>
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
      <div className="flex gap-4 mb-4">
        <button onClick={exportCSV} className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600">Export CSV</button>
        <button onClick={exportPDF} className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">Export PDF</button>
      </div>
      <Card className="bg-gray-800">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Symbol</TableHeaderCell>
              <TableHeaderCell>Price</TableHeaderCell>
              <TableHeaderCell>Market Cap</TableHeaderCell>  {/* New: Add col after Price */}
              <TableHeaderCell>OI (USD)</TableHeaderCell>
              <TableHeaderCell>Top L/S</TableHeaderCell>
              <TableHeaderCell>Global L/S ({activeTab})</TableHeaderCell>
              <TableHeaderCell>RSI</TableHeaderCell>
              <TableHeaderCell>CVD</TableHeaderCell>
              <TableHeaderCell>Z-Score</TableHeaderCell>
              <TableHeaderCell>Imbalance</TableHeaderCell>
              <TableHeaderCell>Funding</TableHeaderCell>
              <TableHeaderCell>OI Δ%</TableHeaderCell>
              <TableHeaderCell>LS Δ%</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {metrics.length > 0 ? (
              metrics.map((metric) => {
                const oiDelta = safeFloat(metric.oi_delta_pct);
                const lsDelta = safeFloat(metric.ls_delta_pct);
                const oiDeltaType = oiDelta > 0 ? 'increase' : oiDelta < 0 ? 'decrease' : 'unchanged';
                const lsDeltaType = lsDelta > 0 ? 'increase' : lsDelta < 0 ? 'decrease' : 'unchanged';
                const lsKey = `Global_LS_${activeTab}`;
                const lsValue = safeFloat(metric[lsKey]);
                const priceVal = safeFloat(metric.price);
                const topLsVal = safeFloat(metric.top_ls);
                const rsiVal = safeFloat(metric.rsi);
                const cvdVal = safeFloat(metric.cvd);
                const zVal = safeFloat(metric.z_ls);
                const imbVal = safeFloat(metric.imbalance);
                const fundVal = safeFloat(metric.funding);
                const mcapVal = safeFloat(metric.Market_Cap);  // New
                return (
                  <TableRow key={metric.symbol} onClick={() => handleRowClick(metric.symbol)} className="cursor-pointer hover:bg-gray-700">
                    <TableCell>{metric.symbol}</TableCell>
                    <TableCell>${priceVal.toFixed(2)}</TableCell>
                    <TableCell>{metric.Market_Cap || (isNaN(mcapVal) ? 'N/A' : `$${mcapVal.toLocaleString()}`)}</TableCell>  {/* New: Add cell after Price */}
                    <TableCell>{metric.formatted_oi}</TableCell>
                    <TableCell>{isNaN(topLsVal) ? 'N/A' : topLsVal.toFixed(2)}</TableCell>
                    <TableCell>{isNaN(lsValue) ? 'N/A' : lsValue.toFixed(4)}</TableCell>
                    <TableCell>{isNaN(rsiVal) ? 'N/A' : Math.round(rsiVal)}</TableCell>
                    <TableCell>{isNaN(cvdVal) ? 'N/A' : cvdVal.toLocaleString()}</TableCell>
                    <TableCell>{isNaN(zVal) ? 'N/A' : zVal.toFixed(2)}</TableCell>
                    <TableCell>{isNaN(imbVal) ? 'N/A' : `${imbVal.toFixed(2)}%`}</TableCell>
                    <TableCell>{isNaN(fundVal) ? 'N/A' : `${(fundVal * 100).toFixed(2)}%`}</TableCell>
                    <TableCell>
                      <BadgeDelta deltaType={oiDeltaType}>
                        {isNaN(oiDelta) ? 'N/A' : `${oiDelta.toFixed(2)}%`}
                      </BadgeDelta>
                    </TableCell>
                    <TableCell>
                      <BadgeDelta deltaType={lsDeltaType}>
                        {isNaN(lsDelta) ? 'N/A' : `${lsDelta.toFixed(2)}%`}
                      </BadgeDelta>
                    </TableCell>
                  </TableRow>
                );
              })
            ) : (
              <TableRow>
                <TableCell colSpan={13} className="text-center text-gray-400">No metrics loaded (check fetch)</TableCell>  {/* Fix: colSpan 13 w/ MCap */}
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
  );

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
          {/* P2 Tease: Tabs tf - Native Tremor w/ Multiple TabPanel */}
          <TabGroup className="mb-4" value={activeTab} onChange={(value) => { console.log('Tab switch to:', value); setActiveTab(value); }}>
            <TabList className="bg-gray-800">
              <Tab value="5m">5m</Tab>
              <Tab value="15m">15m</Tab>
              <Tab value="30m">30m</Tab>
              <Tab value="1h">1h</Tab>
            </TabList>
            <TabPanels>
              <TabPanel>
                <MetricsContent />
              </TabPanel>
              <TabPanel>
                <MetricsContent />
              </TabPanel>
              <TabPanel>
                <MetricsContent />
              </TabPanel>
              <TabPanel>
                <MetricsContent />
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
            {/* P2 Export button */}
            <button onClick={exportCSV} className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
              Export CSV (Papa P2)
            </button>
          </Dialog>
        </main>
      </div>
    </div>
  );
}

export default App;