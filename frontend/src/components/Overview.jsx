import { formatDate, formatQuantity, formatStatus } from '../lib/format.js'
import { countShortagesBySeverity } from '../lib/shortage.js'
import { EmptyState, MetricCard, Panel, StatusPill } from './ui.jsx'

export default function Overview({
  runs,
  runCount,
  run,
  data,
  loading,
  onSelectRun,
  onNavigate,
}) {
  const severityCounts = countShortagesBySeverity(data.shortages)
  const criticalCount = severityCounts.critical
  const affectedSkus = new Set(data.shortages.map((shortage) => shortage.sku_id)).size
  const firstWeekRows = data.pab
    .filter((row) => row.week_start === run?.horizon_start_week)
    .sort((left, right) => left.sku_id - right.sku_id || left.location_id - right.location_id)
  const statusTone = (status) => {
    if (status === 'completed' || status === 'published') return 'teal'
    if (status === 'failed') return 'red'
    if (status === 'calculating' || status === 'validating') return 'amber'
    return 'slate'
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Planning runs"
          value={runCount}
          note={`Latest ${runs.length} returned by the backend`}
          icon="runs"
          tone="blue"
        />
        <MetricCard
          label="Shortage records"
          value={loading ? '…' : run ? data.shortages.length : '—'}
          note={run ? `Selected run · ${run.scenario_key}` : 'Select a calculated run to analyze'}
          icon="shortage"
          tone={data.shortages.length ? 'amber' : 'teal'}
        />
        <MetricCard
          label="Critical shortages"
          value={loading ? '…' : run ? criticalCount : '—'}
          note="Based on the selected run's persisted severity"
          icon="shortage"
          tone={criticalCount ? 'red' : 'teal'}
        />
        <MetricCard
          label="Affected SKUs"
          value={loading ? '…' : run ? affectedSkus : '—'}
          note="Distinct SKUs with at least one shortage"
          icon="overview"
          tone="teal"
        />
      </div>

      <Panel
        title="Shortage severity distribution"
        description="Counts use persisted shortage records for the selected planning run."
      >
        {!run && !loading ? (
          <EmptyState
            title="No planning run selected"
            description="Select a run to see its shortage severity distribution."
          />
        ) : (
          <div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-4 sm:p-6">
            {[
              ['critical', 'Critical', 'bg-rose-50 text-rose-700'],
              ['high', 'High', 'bg-red-50 text-red-700'],
              ['medium', 'Medium', 'bg-amber-50 text-amber-700'],
              ['low', 'Low', 'bg-slate-100 text-slate-700'],
            ].map(([severity, label, tone]) => (
              <div className={`rounded-xl px-4 py-3 ${tone}`} key={severity}>
                <p className="text-xl font-semibold">{loading ? '…' : run ? severityCounts[severity] : '—'}</p>
                <p className="mt-1 text-xs font-medium">{label}</p>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Panel
          title="Supply & inventory overview"
          description={
            run
              ? `First-week base-unit balances for ${run.horizon_start_week}. Quantities remain scoped to each SKU and location.`
              : 'Inventory and supply figures appear after selecting a planning run.'
          }
          action={
            run && (
              <button
                className="text-xs font-semibold text-teal-700 hover:text-teal-900"
                onClick={() => onNavigate('pab')}
              >
                View PAB
              </button>
            )
          }
        >
          {!run && !loading ? (
            <EmptyState
              title="No planning run selected"
              description="Create a run or choose an existing run to see its inventory and supply snapshot."
              action={
                <button className="btn-primary" onClick={() => onNavigate('runs')}>
                  Open planning runs
                </button>
              }
            />
          ) : firstWeekRows.length === 0 ? (
            <EmptyState
              title={loading ? 'Loading run data' : 'No PAB data available'}
              description={
                loading
                  ? 'The selected run data is being retrieved from the backend.'
                  : 'This run has no calculated weekly PAB records yet.'
              }
            />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 border-b border-slate-100 px-5 py-4 sm:grid-cols-3 sm:px-6">
                <SummaryCount
                  label="Inventory positions"
                  value={firstWeekRows.length}
                />
                <SummaryCount
                  label="Positions with firm supply"
                  value={firstWeekRows.filter(
                    (row) => Number(row.firm_inbound_base) + Number(row.firm_po_remainder_base) > 0,
                  ).length}
                />
                <SummaryCount
                  label="Positions with negative PAB"
                  value={firstWeekRows.filter((row) => Number(row.ending_firm_base) < 0).length}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="data-table min-w-[690px]">
                  <thead>
                    <tr>
                      <th>SKU / location</th>
                      <th>Opening</th>
                      <th>Firm supply</th>
                      <th>Demand</th>
                      <th>Ending PAB</th>
                    </tr>
                  </thead>
                  <tbody>
                    {firstWeekRows.slice(0, 8).map((row) => (
                      <tr key={`${row.sku_id}-${row.location_id}`}>
                        <td>
                          <span className="font-semibold text-slate-800">SKU {row.sku_id}</span>
                          <span className="mt-1 block text-xs text-slate-500">Location {row.location_id}</span>
                        </td>
                        <td>{formatQuantity(row.opening_base)}</td>
                        <td>{formatQuantity(Number(row.firm_inbound_base) + Number(row.firm_po_remainder_base))}</td>
                        <td>{formatQuantity(row.forecast_demand_base)}</td>
                        <td className={Number(row.ending_firm_base) < 0 ? 'font-semibold text-rose-700' : 'font-semibold text-slate-800'}>
                          {formatQuantity(row.ending_firm_base)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {firstWeekRows.length > 8 && (
                <p className="px-6 py-3 text-xs text-slate-500">
                  Showing 8 of {firstWeekRows.length} item-location positions.
                </p>
              )}
            </>
          )}
        </Panel>

        <Panel
          title="Recent planning runs"
          description="Open a run to inspect its scope, status, and calculated results."
          action={
            <button
              className="text-xs font-semibold text-teal-700 hover:text-teal-900"
              onClick={() => onNavigate('runs')}
            >
              All runs
            </button>
          }
        >
          {runs.length === 0 ? (
            <EmptyState
              title="No planning runs yet"
              description="Create a planning run with the SKU-location pairs you want to analyze."
              action={
                <button className="btn-primary" onClick={() => onNavigate('runs')}>
                  Create a run
                </button>
              }
            />
          ) : (
            <div className="divide-y divide-slate-100">
              {runs.slice(0, 6).map((recentRun) => (
                <button
                  key={recentRun.planning_run_id}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left transition hover:bg-slate-50 sm:px-6"
                  onClick={() => onSelectRun(recentRun.planning_run_id)}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold text-slate-800">
                      {recentRun.scenario_key}
                    </span>
                    <span className="mt-1 block text-xs text-slate-500">
                      Run #{recentRun.planning_run_id} · {formatDate(recentRun.created_at)}
                    </span>
                  </span>
                  <StatusPill tone={statusTone(recentRun.run_status)}>
                    {formatStatus(recentRun.run_status)}
                  </StatusPill>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

function SummaryCount({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-3">
      <p className="text-lg font-semibold text-slate-900">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{label}</p>
    </div>
  )
}
