import { formatDate, formatQuantity, weekLabel } from '../lib/format.js'
import { shortageKey } from '../lib/shortage.js'
import { EmptyState, Panel, StatusPill } from './ui.jsx'

const labels = {
  expedite_inbound: 'Expedite inbound shipment',
  inventory_transfer: 'Inventory transfer',
  approved_substitute: 'Approved substitute',
  alternative_supplier: 'Alternative supplier',
}

export default function Mitigations({
  run,
  shortages,
  selectedShortage,
  onSelectShortage,
  recommendations,
  loading,
}) {
  const options = [
    { value: 'all', label: 'All run shortages' },
    ...shortages.map((shortage) => ({
      value: shortageKey(shortage),
      label: `SKU ${shortage.sku_id} · LOC ${shortage.location_id} · ${shortage.week_start}`,
    })),
  ]
  const selectedValue = selectedShortage ? shortageKey(selectedShortage) : 'all'

  return (
    <Panel
      title="Mitigation recommendations"
      description="Read-only options based on run-frozen supply, inventory, and current eligibility evidence. Review before taking any action."
      action={
        <label className="flex items-center gap-2 text-xs font-medium text-slate-500">
          Shortage
          <select
            className="field-input min-w-56"
            disabled={shortages.length === 0}
            onChange={(event) => {
              const next = shortages.find((item) => shortageKey(item) === event.target.value)
              onSelectShortage(next ?? null)
            }}
            value={selectedValue}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
      }
    >
      {!run ? (
        <EmptyState title="Select a planning run" description="Mitigation recommendations are tied to calculated run shortages." />
      ) : shortages.length === 0 ? (
        <EmptyState title="No shortages to mitigate" description="This run has no shortage records returned by the planning API." />
      ) : loading ? (
        <EmptyState title="Loading recommendations" description="Fetching read-only recommendations from the backend." />
      ) : recommendations.length === 0 ? (
        <EmptyState
          title="No recommendations available"
          description="The backend returned no supported mitigation options for this run or selected shortage."
        />
      ) : (
        <div className="divide-y divide-slate-100">
          {recommendations.map((item) => {
            const feasible = item.feasibility === 'feasible'
            const arrival = item.expected_arrival_date
              ? `Estimated arrival ${formatDate(item.expected_arrival_date)}`
              : item.current_expected_arrival_date
                ? `Current ETA ${formatDate(item.current_expected_arrival_date)}; expedited arrival is unconfirmed`
                : 'Arrival timing not supported by available evidence'
            return (
              <article className="p-5 sm:p-6" key={item.recommendation_key}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-bold text-slate-900">
                        {labels[item.type] ?? item.type}
                      </h3>
                      <StatusPill tone={feasible ? 'teal' : 'amber'}>
                        {feasible ? 'Evidence supports option' : 'Insufficient evidence'}
                      </StatusPill>
                      {item.is_alternative_option && (
                        <StatusPill tone="blue">Alternative option</StatusPill>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      SKU {item.sku_id} · Location {item.location_id} · {weekLabel(item.shortage_week)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Proposed quantity</p>
                    <p className="mt-1 text-sm font-bold text-slate-900">
                      {item.proposed_quantity_base === null
                        ? 'Not quantified'
                        : `${formatQuantity(item.proposed_quantity_base)} base units`}
                    </p>
                  </div>
                </div>

                <p className="mt-4 max-w-4xl text-sm leading-6 text-slate-600">{item.rationale}</p>

                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <EvidenceValue
                    label="Shortage quantity addressed"
                    value={`${formatQuantity(item.affected_shortage_quantity_base)} base units`}
                  />
                  <EvidenceValue label="Arrival" value={arrival} />
                  <EvidenceValue
                    label="Cost evidence"
                    value={
                      item.estimated_cost === null
                        ? 'Not provided'
                        : `${item.cost_currency} ${formatQuantity(item.estimated_cost, 4)} (recorded offer estimate)`
                    }
                  />
                  <EvidenceValue label="Source" value={sourceLabel(item)} />
                </div>

                {item.evidence?.length > 0 && (
                  <details className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                    <summary className="cursor-pointer text-xs font-semibold text-slate-700">
                      Evidence ({item.evidence.length})
                    </summary>
                    <ul className="mt-3 list-disc space-y-1 pl-5 text-xs leading-5 text-slate-600">
                      {item.evidence.map((evidence, index) => <li key={`${item.recommendation_key}-${index}`}>{evidence}</li>)}
                    </ul>
                  </details>
                )}
              </article>
            )
          })}
        </div>
      )}
      {run && recommendations.length > 0 && (
        <div className="border-t border-slate-100 bg-slate-50 px-5 py-3 text-xs leading-5 text-slate-500 sm:px-6">
          Recommendations are options for planner review only. The dashboard does not place orders, expedite shipments, or create transfers.
        </div>
      )}
    </Panel>
  )
}

function sourceLabel(item) {
  if (item.inbound_shipment_line_id) return `Shipment line ${item.inbound_shipment_line_id}`
  if (item.source_location_id) return `Location ${item.source_location_id}`
  if (item.substitution_id) return `Substitution ${item.substitution_id} · SKU ${item.source_sku_id}`
  if (item.supplier_id) return `Supplier ${item.supplier_id}`
  return 'Planning evidence'
}

function EvidenceValue({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-1 text-xs font-medium leading-5 text-slate-700">{value}</p>
    </div>
  )
}
