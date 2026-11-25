import { useState } from 'react'
import { formatDate, formatStatus, nextMonday } from '../lib/format.js'
import { EmptyState, Panel, StatusPill } from './ui.jsx'

const blankPair = () => ({ skuId: '', locationId: '' })

export default function PlanningRuns({
  runs,
  run,
  loading,
  calculating,
  onSelectRun,
  onCreateRun,
  onCalculateRun,
}) {
  const [scenarioKey, setScenarioKey] = useState('')
  const [horizonStart, setHorizonStart] = useState(nextMonday)
  const [pairs, setPairs] = useState([blankPair()])
  const [snapshotId, setSnapshotId] = useState('')
  const [thresholds, setThresholds] = useState({ medium: '', high: '', critical: '' })
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const updatePair = (index, key, value) => {
    setPairs((current) => current.map((pair, pairIndex) =>
      pairIndex === index ? { ...pair, [key]: value } : pair,
    ))
  }

  const submit = async (event) => {
    event.preventDefault()
    setFormError('')
    const items = pairs
      .filter((pair) => pair.skuId.trim() && pair.locationId.trim())
      .map((pair) => ({
        sku_id: Number(pair.skuId),
        location_id: Number(pair.locationId),
      }))
    const weekDay = horizonStart ? new Date(`${horizonStart}T00:00:00`).getDay() : -1
    if (!scenarioKey.trim()) return setFormError('Enter a scenario key.')
    if (weekDay !== 1) return setFormError('The horizon start must be a Monday.')
    if (!items.length || items.some((item) => !Number.isInteger(item.sku_id) || !Number.isInteger(item.location_id) || item.sku_id <= 0 || item.location_id <= 0)) {
      return setFormError('Add at least one valid positive SKU ID and location ID pair.')
    }

    const severityThresholds = Object.fromEntries(
      Object.entries(thresholds).filter(([, value]) => value.trim()),
    )
    const payload = {
      scenario_key: scenarioKey.trim(),
      horizon_start_week: horizonStart,
      items,
      ...(snapshotId.trim() ? { inventory_snapshot_id: Number(snapshotId) } : {}),
      ...(Object.keys(severityThresholds).length ? { severity_thresholds: severityThresholds } : {}),
    }
    if (payload.inventory_snapshot_id && (!Number.isInteger(payload.inventory_snapshot_id) || payload.inventory_snapshot_id <= 0)) {
      return setFormError('Inventory snapshot ID must be a positive integer.')
    }
    setSubmitting(true)
    try {
      await onCreateRun(payload)
      setScenarioKey('')
      setPairs([blankPair()])
      setSnapshotId('')
      setThresholds({ medium: '', high: '', critical: '' })
    } catch (error) {
      setFormError(error.message)
    } finally {
      setSubmitting(false)
    }
  }

  const tone = (status) => {
    if (status === 'completed' || status === 'published') return 'teal'
    if (status === 'failed') return 'red'
    if (status === 'validating' || status === 'calculating') return 'amber'
    return 'slate'
  }

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(320px,0.82fr)_minmax(0,1.4fr)]">
      <Panel
        title="Create planning run"
        description="Define the sparse SKU-location scope to freeze forecast and supply inputs."
      >
        <form className="space-y-5 p-5 sm:p-6" onSubmit={submit}>
          <label className="field-label">
            Scenario key
            <input
              className="field-input"
              maxLength={64}
              onChange={(event) => setScenarioKey(event.target.value)}
              placeholder="e.g. Q4-replenishment"
              required
              value={scenarioKey}
            />
          </label>

          <label className="field-label">
            Horizon start week (Monday)
            <input
              className="field-input"
              onChange={(event) => setHorizonStart(event.target.value)}
              required
              type="date"
              value={horizonStart}
            />
          </label>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="field-label mb-0">SKU-location pairs</span>
              <button
                className="inline-flex items-center gap-1 text-xs font-semibold text-teal-700 hover:text-teal-900"
                onClick={() => setPairs((current) => [...current, blankPair()])}
                type="button"
              >
                <span className="text-base leading-none">+</span> Add pair
              </button>
            </div>
            <div className="space-y-2">
              {pairs.map((pair, index) => (
                <div className="grid grid-cols-[1fr_1fr_auto] gap-2" key={index}>
                  <input
                    aria-label={`SKU ID ${index + 1}`}
                    className="field-input"
                    min="1"
                    onChange={(event) => updatePair(index, 'skuId', event.target.value)}
                    placeholder="SKU ID"
                    type="number"
                    value={pair.skuId}
                  />
                  <input
                    aria-label={`Location ID ${index + 1}`}
                    className="field-input"
                    min="1"
                    onChange={(event) => updatePair(index, 'locationId', event.target.value)}
                    placeholder="Location ID"
                    type="number"
                    value={pair.locationId}
                  />
                  <button
                    aria-label={`Remove pair ${index + 1}`}
                    className="rounded-lg px-3 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
                    disabled={pairs.length === 1}
                    onClick={() => setPairs((current) => current.filter((_, pairIndex) => pairIndex !== index))}
                    type="button"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              Use IDs from your catalog. The backend validates every selected pair.
            </p>
          </div>

          <details className="rounded-xl border border-slate-200 px-4 py-3">
            <summary className="cursor-pointer text-xs font-semibold text-slate-700">
              Optional planning settings
            </summary>
            <div className="mt-4 space-y-4">
              <label className="field-label">
                Inventory snapshot ID
                <input
                  className="field-input"
                  min="1"
                  onChange={(event) => setSnapshotId(event.target.value)}
                  placeholder="Use the backend's current validated snapshot"
                  type="number"
                  value={snapshotId}
                />
              </label>
              <div>
                <p className="field-label mb-2">Severity thresholds (base units)</p>
                <div className="grid grid-cols-3 gap-2">
                  {Object.entries(thresholds).map(([level, value]) => (
                    <label className="text-[11px] font-medium capitalize text-slate-500" key={level}>
                      {level}
                      <input
                        className="field-input mt-1"
                        min="0"
                        onChange={(event) =>
                          setThresholds((current) => ({ ...current, [level]: event.target.value }))
                        }
                        placeholder="Optional"
                        step="any"
                        type="number"
                        value={value}
                      />
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </details>

          {formError && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{formError}</p>}
          <button className="btn-primary w-full" disabled={submitting} type="submit">
            {submitting ? 'Creating run…' : 'Create planning run'}
          </button>
        </form>
      </Panel>

      <div className="space-y-6">
        <Panel title="Planning runs" description="Runs and lifecycle status returned by the planning API.">
          {runs.length === 0 ? (
            <EmptyState
              title={loading ? 'Loading planning runs' : 'No planning runs found'}
              description={
                loading
                  ? 'Waiting for the backend response.'
                  : 'Create a run with at least one SKU-location pair to start planning.'
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table min-w-[680px]">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Scenario</th>
                    <th>Horizon</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((item) => (
                    <tr className={run?.planning_run_id === item.planning_run_id ? 'bg-teal-50/50' : ''} key={item.planning_run_id}>
                      <td className="font-semibold text-slate-800">#{item.planning_run_id}</td>
                      <td>{item.scenario_key}</td>
                      <td>{formatDate(item.horizon_start_week)}</td>
                      <td><StatusPill tone={tone(item.run_status)}>{formatStatus(item.run_status)}</StatusPill></td>
                      <td>{formatDate(item.created_at)}</td>
                      <td>
                        <button className="text-xs font-semibold text-teal-700 hover:text-teal-900" onClick={() => onSelectRun(item.planning_run_id)}>
                          Open
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          title={run ? `Run #${run.planning_run_id} details` : 'Run details'}
          description="Frozen scope and lifecycle information."
          action={
            run && (
              <button
                className="btn-secondary"
                disabled={calculating || run.run_status === 'calculating'}
                onClick={() => onCalculateRun(run.planning_run_id)}
              >
                {calculating ? 'Calculating…' : 'Calculate run'}
              </button>
            )
          }
        >
          {!run ? (
            <EmptyState title="Select a planning run" description="Run details and calculation controls will appear here." />
          ) : (
            <div className="p-5 sm:p-6">
              <div className="grid gap-3 sm:grid-cols-2">
                <Detail label="Scenario" value={run.scenario_key} />
                <Detail label="Status" value={formatStatus(run.run_status)} />
                <Detail label="Horizon begins" value={formatDate(run.horizon_start_week)} />
                <Detail label="Weeks" value={run.horizon_weeks} />
                <Detail label="Captured" value={formatDate(run.captured_at)} />
                <Detail label="Inventory snapshot" value={`#${run.inventory_snapshot_id}`} />
              </div>
              <div className="mt-5">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Scope · {run.items?.length ?? 0} SKU-location pairs
                </p>
                <div className="flex flex-wrap gap-2">
                  {(run.items ?? []).map((item) => (
                    <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700" key={`${item.sku_id}-${item.location_id}`}>
                      SKU {item.sku_id} · LOC {item.location_id}
                    </span>
                  ))}
                  {(!run.items || run.items.length === 0) && (
                    <span className="text-sm text-slate-500">The API returned no scope items.</span>
                  )}
                </div>
              </div>
              {loading && <p className="mt-4 text-xs text-slate-500">Refreshing run data…</p>}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

function Detail({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-800">{value}</p>
    </div>
  )
}
