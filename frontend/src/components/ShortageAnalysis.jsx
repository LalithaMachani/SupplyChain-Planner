import { useMemo, useState } from 'react'
import { formatQuantity, weekLabel } from '../lib/format.js'
import { shortageKey } from '../lib/shortage.js'
import { EmptyState, Panel, StatusPill } from './ui.jsx'

export default function ShortageAnalysis({ shortages, selectedShortage, onSelectShortage, onViewMitigations }) {
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return shortages.filter((item) => {
      const causeText = (item.causes ?? []).map((cause) => `${cause.cause_code} ${cause.explanation}`).join(' ')
      const matchesSearch = !query || [
        item.sku_id,
        item.location_id,
        item.week_start,
        causeText,
      ].join(' ').toLowerCase().includes(query)
      return matchesSearch && (severity === 'all' || item.severity === severity)
    })
  }, [search, severity, shortages])

  const severityTone = (value) => {
    if (value === 'critical' || value === 'high') return 'red'
    if (value === 'medium') return 'amber'
    return 'slate'
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Shortage analysis"
        description="Persisted shortage records for the selected planning run. Search by SKU, location, week, or cause."
        action={
          <div className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor="shortage-search">Search shortages</label>
            <input
              className="field-input w-48"
              id="shortage-search"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search…"
              value={search}
            />
            <label className="sr-only" htmlFor="severity-filter">Filter by severity</label>
            <select
              className="field-input w-36"
              id="severity-filter"
              onChange={(event) => setSeverity(event.target.value)}
              value={severity}
            >
              <option value="all">All severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        }
      >
        {shortages.length === 0 ? (
          <EmptyState
            title="No shortage records available"
            description="Calculate a planning run with accepted forecast and supply inputs to see shortage analysis."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No matching shortages"
            description="Adjust the search term or severity filter."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table min-w-[790px]">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>Location</th>
                  <th>Week</th>
                  <th>Shortage quantity</th>
                  <th>Severity</th>
                  <th>Primary cause</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => {
                  const key = shortageKey(item)
                  const selected = selectedShortage && shortageKey(selectedShortage) === key
                  const cause = item.causes?.[0]
                  return (
                    <tr className={selected ? 'bg-teal-50/50' : ''} key={key}>
                      <td className="font-semibold text-slate-800">{item.sku_id}</td>
                      <td>{item.location_id}</td>
                      <td>{weekLabel(item.week_start)}</td>
                      <td className="font-semibold text-rose-700">{formatQuantity(item.shortage_quantity_base)}</td>
                      <td><StatusPill tone={severityTone(item.severity)}>{item.severity}</StatusPill></td>
                      <td className="max-w-64 truncate" title={cause?.explanation ?? 'No cause recorded'}>
                        {cause?.cause_code ? (
                          <span>
                            <span className="font-medium text-slate-700">{cause.cause_code}</span>
                            <span className="mt-1 block truncate text-xs text-slate-500">{cause.explanation}</span>
                          </span>
                        ) : (
                          <span className="text-slate-400">No cause recorded</span>
                        )}
                      </td>
                      <td>
                        <button
                          className="text-xs font-semibold text-teal-700 hover:text-teal-900"
                          onClick={() => onSelectShortage(item)}
                        >
                          Details
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {selectedShortage && (
        <Panel
          title={`Shortage detail · SKU ${selectedShortage.sku_id} · LOC ${selectedShortage.location_id}`}
          description={weekLabel(selectedShortage.week_start)}
          action={
            <button className="btn-secondary" onClick={() => onViewMitigations(selectedShortage)}>
              View recommendations
            </button>
          }
        >
          <div className="grid gap-4 p-5 sm:grid-cols-3 sm:p-6">
            <Detail label="Shortage quantity" value={`${formatQuantity(selectedShortage.shortage_quantity_base)} base units`} />
            <Detail label="Severity" value={selectedShortage.severity} />
            <Detail label="Cause records" value={selectedShortage.causes?.length ?? 0} />
          </div>
          <div className="border-t border-slate-100 px-5 py-5 sm:px-6">
            <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500">Recorded causes</h3>
            {selectedShortage.causes?.length ? (
              <ul className="mt-3 space-y-3">
                {selectedShortage.causes.map((cause) => (
                  <li className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={cause.shortage_cause_id}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-slate-800">{cause.cause_code}</p>
                      <span className="text-xs font-medium text-slate-500">
                        {formatQuantity(cause.contribution_quantity_base)} base units
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-slate-600">{cause.explanation}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-slate-500">The backend did not record a cause for this shortage.</p>
            )}
          </div>
        </Panel>
      )}
    </div>
  )
}

function Detail({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold capitalize text-slate-800">{value}</p>
    </div>
  )
}
