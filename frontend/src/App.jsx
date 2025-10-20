import { useState, useEffect } from 'react';
import io from 'socket.io-client';
import axios from 'axios';
import { 
  Card, Title, Text, Table, TableHead, TableRow, TableHeaderCell, TableBody, TableCell, BadgeDelta, 
  Dialog, TabGroup, TabList, Tab, TabPanels, TabPanel, DonutChart, Legend, Metric 
} from '@tremor/react';
import localforage from 'localforage';
import Papa from 'papaparse';
import jsPDF from 'jspdf';
import 'jspdf-autotable';
import './App.css';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const safeFloat = (val) => {
  if (typeof val === 'string') {
    const cleaned = val.replace(/[^\d.-]/g, '');
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
  const [activeTab, setActiveTab] = useState('5m');

  // Fetch metrics
  useEffect(() => {
    const fetchMetrics = async () => {
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
      } catch (error) {
        console.error('Fetch error:', error.response?.status, error.message);
        localforage.getItem(`metrics_${activeTab}`).then(cached => {
          if (cached) setMetrics(cached);
        });
      }
    };
    fetchMetrics();
  }, [activeTab]);

  // Poll fallback if metrics are low
  useEffect(() => {
    if (metrics.length < 5) {
      const interval = setInterval(async () => {
        if (metrics.length < 5) {
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
          } catch (err) {
            console.error('Poll fetch error:', err.message);
          }
        }
      }, 10000);
      return () => clearInterval(interval);
    }
  }, [activeTab, metrics.length]);

  // WebSocket updates
  useEffect(() => {
    socket.on('connect', () => console.log('WS connected (EIO=4)'));
    socket.on('metrics_update', (update) => {
      const rawMetrics = update.data || update;
      const tfMetrics = rawMetrics
        .filter(m => m.timeframe === activeTab || m[`Global_LS_${activeTab}`])
        .map(m => ({
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
      if (tfMetrics.length > 0) {
        setMetrics(tfMetrics);
        localforage.setItem(`metrics_${activeTab}`, tfMetrics);
      }
    });
    return () => {
      socket.off('connect');
      socket.off('metrics_update');
    };
  }, [activeTab]);

  // Fetch symbol history for modal chart
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
            ls: safeFloat(row[lsKey]) || 0
          }));
          setChartData(mappedData);
        } catch (error) {
          console.error('History fetch error:', error.message);
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

  // Global metrics
  const globalAvgOI = metrics.length > 0 ? metrics.reduce((sum, m) => sum + safeFloat(m.oi_abs_usd), 0) / metrics.length : 0;
  const globalAvgLS = metrics.length > 0 ? metrics.reduce((sum, m) => sum + safeFloat(m[`Global_LS_${activeTab}`]), 0) / metrics.length : 0;

  const exportCSV = () => {
    const exportData = metrics.map(m => ({
      Symbol: m.symbol,
      Price: `$${safeFloat(m.price).toFixed(2)}`,
      'Market Cap': m.Market_Cap || 'N/A',
      'OI USD': m.formatted_oi,
      'Top L/S': safeFloat(m.top_ls).toFixed(2),
      [`Global LS ${activeTab}`]: safeFloat(m[`Global_LS_${activeTab}`]).toFixed(4),
      RSI: Math.round(safeFloat(m.rsi)),
      CVD: safeFloat(m.cvd),
      Z: safeFloat(m.z_ls),
      Imbalance: `${safeFloat(m.imbalance).toFixed(2)}%`,
      Funding: `${(safeFloat(m.funding) * 100).toFixed(2)}%`,
      'OI Δ %': `${safeFloat(m.oi_delta_pct).toFixed(2)}%`,
      'L/S Δ %': `${safeFloat(m.ls_delta_pct).toFixed(2)}%`
    }));
    const csv = Papa.unparse(exportData);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `metrics_${activeTab}.csv`;
    link.click();
  };

  const exportPDF = () => {
    const doc = new jsPDF();
    doc.text(`Metrics Export (${activeTab})`, 10, 10);
    doc.autoTable({
      head: [['Symbol', 'Price', 'Market Cap', 'OI USD', 'Top L/S', `Global LS ${activeTab}`, 'RSI', 'CVD', 'Z', 'Imbalance', 'Funding', 'OI Δ %', 'L/S Δ %']],
      body: metrics.map(m => [
        m.symbol,
        `$${safeFloat(m.price).toFixed(2)}`,
        m.Market_Cap || 'N/A',
        m.formatted_oi,
        safeFloat(m.top_ls).toFixed(2),
        safeFloat(m[`Global_LS_${activeTab}`]).toFixed(4),
        Math.round(safeFloat(m.rsi)),
        safeFloat(m.cvd),
        safeFloat(m.z_ls),
        `${safeFloat(m.imbalance).toFixed(2)}%`,
        `${(safeFloat(m.funding) * 100).toFixed(2)}%`,
        `${safeFloat(m.oi_delta_pct).toFixed(2)}%`,
        `${safeFloat(m.ls_delta_pct).toFixed(2)}%`
      ])
    });
    doc.save(`metrics_${activeTab}.pdf`);
  };

  return (
    <div className="p-4">
      <Title>Crypto Metrics Dashboard ({activeTab})</Title>
      <TabGroup index={['5m', '15m', '30m', '1h'].indexOf(activeTab)} onIndexChange={idx => setActiveTab(['5m', '15m', '30m', '1h'][idx])}>
        <TabList>
          <Tab>5m</Tab>
          <Tab>15m</Tab>
          <Tab>30m</Tab>
          <Tab>1h</Tab>
        </TabList>
      </TabGroup>

      <Card className="mt-4">
        <Text>Global Avg OI: ${globalAvgOI.toLocaleString(undefined, { maximumFractionDigits: 2 })}</Text>
        <Text>Global Avg L/S: {globalAvgLS.toFixed(4)}</Text>
      </Card>

      <Card className="mt-4">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Symbol</TableHeaderCell>
              <TableHeaderCell>Price</TableHeaderCell>
              <TableHeaderCell>Market Cap</TableHeaderCell>
              <TableHeaderCell>OI USD</TableHeaderCell>
              <TableHeaderCell>Top L/S</TableHeaderCell>
              <TableHeaderCell>Global L/S</TableHeaderCell>
              <TableHeaderCell>RSI</TableHeaderCell>
              <TableHeaderCell>CVD</TableHeaderCell>
              <TableHeaderCell>Z</TableHeaderCell>
              <TableHeaderCell>Imbalance</TableHeaderCell>
              <TableHeaderCell>Funding</TableHeaderCell>
              <TableHeaderCell>OI Δ %</TableHeaderCell>
              <TableHeaderCell>L/S Δ %</TableHeaderCell>
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
                const mcapVal = safeFloat(metric.Market_Cap);

                return (
                  <TableRow
                    key={metric.symbol}
                    onClick={() => handleRowClick(metric.symbol)}
                    className="cursor-pointer hover:bg-gray-700"
                  >
                    <TableCell>{metric.symbol}</TableCell>
                    <TableCell>${priceVal.toFixed(2)}</TableCell>
                    <TableCell>{metric.Market_Cap || (isNaN(mcapVal) ? 'N/A' : `$${mcapVal.toLocaleString()}`)}</TableCell>
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
                <TableCell colSpan={13} className="text-center text-gray-400">
                  No metrics loaded (check fetch)
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Modal with Chart */}
      {modalOpen && selectedSymbol && (
        <Dialog open={modalOpen} onClose={() => setModalOpen(false)}>
          <Title>{selectedSymbol} History ({activeTab})</Title>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="time" />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="price" stroke="#8884d8" />
              <Line type="monotone" dataKey="ls" stroke="#82ca9d" />
              <Line type="monotone" dataKey="oi" stroke="#ff7300" />
            </LineChart>
          </ResponsiveContainer>
        </Dialog>
      )}

      <div className="mt-4 flex gap-2">
        <button className="btn" onClick={exportCSV}>Export CSV</button>
        <button className="btn" onClick={exportPDF}>Export PDF</button>
      </div>
    </div>
  );
}

export default App;
