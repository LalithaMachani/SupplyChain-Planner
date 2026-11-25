import { useMemo, useState } from 'react'
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDate, formatQuantity } from '../lib/format.js'
import { EmptyState, Panel, StatusPill } from './ui.jsx'

export default function PabAnalysis({ pab }) {
  const pairs = useMemo(() => {
    const map = new Map()
    pab.forEach((row) => {
      const key = `${row.sku_id}:${row.location_id}`
      if (!map.has(key)) map.set(key, { key, skuId: row.sku_id, locationId: row.location_id })
    })
    return [...map.values()].sort((a, b) => a.skuId - b.skuId || a.locationId - b.locationId)
  }, [pab])
  const [selectedKey, setSelectedKey] = useState('')
  const activeKey = pairs.some((pair) => pair.key === selectedKey)
    ? selectedKey
    : pairs[0]?.key ?? ''
  const selectedPair = pairs.find((pair) => pair.key === activeKey)
  const rows = useMemo(
    () =>
      pab
        .filter((row) => `${row.sku_id}:${row.location_id}` === activeKey)
        .sort((a, b) => a.week_start.localeCompare(b.week_start))
        .map((row) => ({
          ...row,
          firm_supply_base: Number(row.firm_inbound_base) + Number(row.firm_po_remainder_base),
          forecast_demand_base: Number(row.forecast_demand_base),
          ending_firm_base: Number(row.ending_firm_base),
          unconfirmed_supply_base: Number(row.unconfirmed_supply_base),
          shortage_quantity_base: Number(row.shortage_quantity_base),
        })),
    [pab, activeKey],
  )
  const negativeWeeks = rows.filter((row) => row.ending_firm_base < 0).length
  const firstWeek = rows[0]

  return (
    <div className="space-y-6">
      <Panel
        title="Projected available balance"
        description="Weekly PAB reconciles opening balance, firm shipment receipts, firm PO remainder, and forecast demand. Negative balances are preserved."
        action={
          pairs.length > 0 && (
            <label className="flex items-center gap-2 text-xs font-medium text-slate-500">
              Item
              <select
                className="field-input min-w-48"
                onChange={(event) => setSelectedKey(event.target.value)}
                value={activeKey}
              >
                {pairs.map((pair) => (
                  <option key={pair.key} value={pair.key}>
                    SKU {pair.skuId} · Location {pair.locationId}
                  </option>
                ))}
              </select>
            </label>
          )
        }
      >
        {pab.length === 0 ? (
          <EmptyState
            title="No PAB data available"
            description="Select a calculated run. Weekly PAB values are shown exactly from the planning API."
          />
        ) : (
          <>
            <div className="grid gap-3 border-b border-slate-100 px-5 py-4 sm:grid-cols-3 sm:px-6">
              <Summary label="Item-location" value={selectedPair ? `SKU ${selectedPair.skuId} · LOC ${selectedPair.locationId}` : '—'} />
              <Summary label="Weeks with negative PAB" value={negativeWeeks} />
              <Summary label="First-week opening" value={firstWeek ? `${formatQuantity(firstWeek.opening_base)} base units` : '—'} />
            </div>
            <div className="grid gap-6 p-4 sm:p-6 xl:grid-cols-[1.7fr_1fr]">
              <div>
                <h3 className="mb-1 text-sm font-semibold text-slate-800">Demand, firm supply & ending balance</h3>
                <p className="mb-4 text-xs text-slate-500">
                  Unconfirmed supply is shown separately and is not included in the firm ending balance.
                </p>
                <div className="h-[320px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={rows} margin={{ top: 8, right: 14, left: 0, bottom: 4 }}>
                      <CartesianGrid stroke="#e8eef2" strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        axisLine={false}
                        dataKey="week_start"
                        tickFormatter={(value) => formatDate(value, { month: 'short', day: 'numeric' })}
                        tickLine={false}
                        tick={{ fill: '#718096', fontSize: 10 }}
                        minTickGap={28}
                      />
                      <YAxis
                        axisLine={false}
                        tickLine={false}
                        tick={{ fill: '#718096', fontSize: 10 }}
                        width={52}
                      />
                      <Tooltip
                        labelFormatter={(value) => `Week of ${formatDate(value)}`}
                        formatter={(value, name) => [formatQuantity(value), name]}
                        contentStyle={{ borderRadius: 12, borderColor: '#e2e8f0', fontSize: 12 }}
                      />
                      <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
                      <Bar dataKey="firm_supply_base" name="Firm supply" fill="#42a797" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="forecast_demand_base" name="Forecast demand" fill="#f3ad58" radius={[4, 4, 0, 0]} />
                      <Line
                        dataKey="ending_firm_base"
                        name="Ending PAB"
                        stroke="#245f87"
                        strokeWidth={2.5}
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        dataKey="unconfirmed_supply_base"
                        name="Unconfirmed supply"
                        stroke="#94a3b8"
                        strokeDasharray="5 4"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <h3 className="mb-1 text-sm font-semibold text-slate-800">Shortage trend</h3>
                <p className="mb-4 text-xs text-slate-500">Persisted shortage quantity by week for this SKU-location pair.</p>
                <div className="h-[320px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 4 }}>
                      <CartesianGrid stroke="#e8eef2" strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        axisLine={false}
                        dataKey="week_start"
                        tickFormatter={(value) => formatDate(value, { month: 'short', day: 'numeric' })}
                        tickLine={false}
                        tick={{ fill: '#718096', fontSize: 10 }}
                        minTickGap={24}
                      />
                      <YAxis axisLine={false} tickLine={false} tick={{ fill: '#718096', fontSize: 10 }} width={52} />
                      <Tooltip
                        labelFormatter={(value) => `Week of ${formatDate(value)}`}
                        formatter={(value) => [`${formatQuantity(value)} base units`, 'Shortage']}
                        contentStyle={{ borderRadius: 12, borderColor: '#e2e8f0', fontSize: 12 }}
                      />
                      <Area dataKey="shortage_quantity_base" fill="#fce7e7" stroke="#dc6868" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
            <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-800">Weekly balance detail</h3>
                <span className="text-xs text-slate-500">{rows.length} weeks</span>
              </div>
              <div className="max-h-[390px] overflow-auto rounded-xl border border-slate-100">
                <table className="data-table min-w-[790px]">
                  <thead className="sticky top-0">
                    <tr>
                      <th>Week</th>
                      <th>Opening</th>
                      <th>Firm receipts</th>
                      <th>Firm PO</th>
                      <th>Demand</th>
                      <th>Ending PAB</th>
                      <th>Shortage</th>
                      <th>Unconfirmed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.week_start}>
                        <td>{formatDate(row.week_start)}</td>
                        <td>{formatQuantity(row.opening_base)}</td>
                        <td>{formatQuantity(row.firm_inbound_base)}</td>
                        <td>{formatQuantity(row.firm_po_remainder_base)}</td>
                        <td>{formatQuantity(row.forecast_demand_base)}</td>
                        <td className={row.ending_firm_base < 0 ? 'font-semibold text-rose-700' : 'font-medium text-slate-700'}>
                          {formatQuantity(row.ending_firm_base)}
                        </td>
                        <td>{row.shortage_quantity_base > 0 ? <StatusPill tone="red">{formatQuantity(row.shortage_quantity_base)}</StatusPill> : '—'}</td>
                        <td>{formatQuantity(row.unconfirmed_supply_base)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </Panel>
    </div>
  )
}

function Summary({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-800">{value}</p>
    </div>
  )
}
