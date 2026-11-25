import { useEffect, useState } from 'react'
import { api } from '../api/client.js'
import { formatDate, formatStatus } from '../lib/format.js'
import { EmptyState, Panel, StatusPill } from './ui.jsx'

async function fetchBatchDetails(batchId) {
  const recordPage = await api.getBatchRecords(batchId)
  const records = recordPage.items ?? []
  const findingPages = await Promise.all(
    records
      .filter((record) => record.finding_count > 0)
      .map(async (record) => {
        const page = await api.getRecordFindings(record.staging_record_id)
        return (page.items ?? []).map((finding) => ({
          ...finding,
          source_row_number: record.source_row_number,
        }))
      }),
  )
  return { records, findings: findingPages.flat() }
}

export default function Ingestion({ batches, onRefreshBatches, onNotice }) {
  const [file, setFile] = useState(null)
  const [recordType, setRecordType] = useState('')
  const [sourceName, setSourceName] = useState('')
  const [selectedBatchIdState, setSelectedBatchId] = useState('')
  const [batchDetails, setBatchDetails] = useState(null)
  const [refreshingDetailsId, setRefreshingDetailsId] = useState(null)
  const [validationState, setValidationState] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selectedBatchId = selectedBatchIdState
    || String(batches[0]?.ingestion_batch_id ?? '')
  const detailsMatch = batchDetails?.batchId === selectedBatchId
  const records = detailsMatch ? batchDetails.records : []
  const findings = detailsMatch ? batchDetails.findings : []
  const detailsError = detailsMatch ? batchDetails.error : ''
  const loadingDetails = Boolean(
    selectedBatchId && (!detailsMatch || refreshingDetailsId === selectedBatchId),
  )
  const visibleError = error || detailsError
  const validationResult = validationState?.batchId === selectedBatchId
    ? validationState.result
    : null

  const selectBatch = (batchId) => {
    const nextBatchId = String(batchId)
    if (nextBatchId !== selectedBatchId) {
      setValidationState(null)
      setError('')
    }
    setSelectedBatchId(nextBatchId)
  }

  useEffect(() => {
    if (!selectedBatchId) return
    let active = true
    const batchId = selectedBatchId
    fetchBatchDetails(batchId).then((details) => {
      if (active) setBatchDetails({ batchId, ...details, error: '' })
    }).catch((requestError) => {
      if (active) {
        setBatchDetails({
          batchId,
          records: [],
          findings: [],
          error: requestError.message,
        })
      }
    })
    return () => {
      active = false
    }
  }, [selectedBatchId])

  const refreshBatches = async () => {
    setError('')
    try {
      await onRefreshBatches()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const upload = async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    setError('')
    if (!file) return setError('Choose a CSV file to upload.')
    setBusy(true)
    try {
      const result = await api.uploadCsv(file, recordType.trim(), sourceName.trim())
      setFile(null)
      form.reset()
      setValidationState(null)
      selectBatch(result.ingestion_batch_id)
      onNotice(`Uploaded ${result.source_file_name} as batch ${result.batch_identifier}.`)
      try {
        await onRefreshBatches()
      } catch (requestError) {
        setError(`Upload succeeded, but refreshing the batch list failed: ${requestError.message}`)
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const validate = async () => {
    if (!selectedBatchId) return
    setBusy(true)
    setError('')
    try {
      const result = await api.validateBatch(selectedBatchId)
      setValidationState({ batchId: selectedBatchId, result })
      let refreshError = ''
      try {
        await onRefreshBatches()
      } catch (requestError) {
        refreshError = requestError.message
      }
      setRefreshingDetailsId(selectedBatchId)
      try {
        const details = await fetchBatchDetails(selectedBatchId)
        setBatchDetails({
          batchId: selectedBatchId,
          ...details,
          error: '',
        })
      } catch (requestError) {
        setBatchDetails({
          batchId: selectedBatchId,
          records: [],
          findings: [],
          error: requestError.message,
        })
      } finally {
        setRefreshingDetailsId(null)
      }
      onNotice(`Validation finished for batch ${selectedBatchId}.`)
      if (refreshError) {
        setError(`Validation succeeded, but refreshing the batch list failed: ${refreshError}`)
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const selectedBatch = batches.find(
    (batch) => String(batch.ingestion_batch_id) === selectedBatchId,
  )

  return (
    <div className="space-y-6">
      {visibleError && <div className="alert-error" role="alert">{visibleError}</div>}
      <div className="grid items-start gap-6 xl:grid-cols-[0.8fr_1.2fr]">
        <Panel title="Upload CSV" description="Create an ingestion batch from a CSV file using the backend upload contract.">
          <form className="space-y-4 p-5 sm:p-6" onSubmit={upload}>
            <label className="field-label">
              CSV file
              <input
                accept=".csv,text/csv"
                className="field-input file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1 file:text-xs file:font-semibold file:text-slate-700"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                required
                type="file"
              />
            </label>
            <label className="field-label">
              Record type
              <input
                className="field-input"
                maxLength={64}
                onChange={(event) => setRecordType(event.target.value)}
                placeholder="Use a record type supported by backend validation"
                required
                value={recordType}
              />
              <span className="mt-1 block text-[11px] font-normal text-slate-500">
                Validation rules depend on the selected record type.
              </span>
            </label>
            <label className="field-label">
              Source name
              <input
                className="field-input"
                maxLength={120}
                onChange={(event) => setSourceName(event.target.value)}
                placeholder="Name of the source system or file feed"
                required
                value={sourceName}
              />
            </label>
            <p className="rounded-lg bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">
              Uploading stages the file. It does not import accepted rows into planning or run validation automatically.
            </p>
            <button className="btn-primary w-full" disabled={busy} type="submit">
              {busy ? 'Working…' : 'Upload CSV'}
            </button>
          </form>
        </Panel>

        <Panel
          title="Ingestion batches"
          description="Batch status and validation results returned by the ingestion API."
          action={
            <button className="btn-secondary" disabled={busy} onClick={() => void refreshBatches()}>
              Refresh
            </button>
          }
        >
          {batches.length === 0 ? (
            <EmptyState title="No ingestion batches" description="Uploaded CSV files will appear here after the backend accepts them." />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table min-w-[640px]">
                <thead>
                  <tr>
                    <th>Batch</th>
                    <th>Source file</th>
                    <th>Status</th>
                    <th>Records</th>
                    <th>Findings</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {batches.map((batch) => (
                    <tr className={selectedBatchId === String(batch.ingestion_batch_id) ? 'bg-teal-50/50' : ''} key={batch.ingestion_batch_id}>
                      <td className="font-semibold text-slate-800">#{batch.ingestion_batch_id}</td>
                      <td>
                        <span className="block max-w-48 truncate">{batch.source_file_name}</span>
                        <span className="mt-1 block text-[11px] text-slate-500">{formatDate(batch.received_at)}</span>
                      </td>
                      <td><StatusPill tone={batch.processing_status === 'validated' ? 'teal' : 'slate'}>{formatStatus(batch.processing_status)}</StatusPill></td>
                      <td>{batch.record_count}</td>
                      <td>{batch.finding_count}</td>
                      <td>
                        <button className="text-xs font-semibold text-teal-700 hover:text-teal-900" onClick={() => selectBatch(batch.ingestion_batch_id)}>
                          Inspect
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title={selectedBatch ? `Batch #${selectedBatch.ingestion_batch_id} · ${selectedBatch.source_file_name}` : 'Batch validation'}
        description={selectedBatch ? `Received ${formatDate(selectedBatch.received_at)} · ${selectedBatch.record_count} records` : 'Select a batch to review validation results.'}
        action={
          selectedBatch && (
            <button className="btn-primary" disabled={busy} onClick={validate}>
              {busy ? 'Validating…' : 'Run validation'}
            </button>
          )
        }
      >
        {!selectedBatch ? (
          <EmptyState title="No batch selected" description="Upload a file or select an existing batch to view its records and findings." />
        ) : (
          <div className="space-y-5 p-5 sm:p-6">
            {validationResult && (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <ResultCard label="Processing status" value={formatStatus(validationResult.processing_status)} />
                <ResultCard label="Accepted records" value={validationResult.accepted_count} />
                <ResultCard label="Rejected records" value={validationResult.rejected_count} />
                <ResultCard label="Validation findings" value={validationResult.finding_count} />
              </div>
            )}
            {validationResult?.unsupported_rules?.length > 0 && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                Unsupported validation rules: {validationResult.unsupported_rules.join(', ')}
              </p>
            )}
            {loadingDetails ? (
              <p className="text-sm text-slate-500">Loading records and validation findings…</p>
            ) : visibleError ? (
              <p className="text-sm text-rose-700">Batch details are unavailable: {visibleError}</p>
            ) : (
              <>
                <div>
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-800">Validation findings</h3>
                    <span className="text-xs text-slate-500">{findings.length} loaded</span>
                  </div>
                  {findings.length ? (
                    <div className="space-y-2">
                      {findings.map((finding) => (
                        <div className="rounded-xl border border-slate-200 p-4" key={finding.validation_finding_id}>
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-sm font-semibold text-slate-800">{finding.rule_code}</p>
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-slate-500">Row {finding.source_row_number}</span>
                              <StatusPill tone={finding.severity === 'error' || finding.severity === 'critical' ? 'red' : 'amber'}>
                                {finding.severity}
                              </StatusPill>
                            </div>
                          </div>
                          <p className="mt-2 text-sm leading-6 text-slate-600">{finding.details}</p>
                          {finding.field_path && <p className="mt-1 text-xs text-slate-500">Field: {finding.field_path}</p>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="rounded-xl bg-slate-50 px-4 py-4 text-sm text-slate-500">
                      {selectedBatch.finding_count > 0
                        ? 'Findings are reported by the batch, but no detailed findings were returned for the first 50 records.'
                        : 'No validation findings have been returned for this batch.'}
                    </p>
                  )}
                </div>
                <div>
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-800">Staged records</h3>
                    <span className="text-xs text-slate-500">Showing {records.length} of {selectedBatch.record_count}</span>
                  </div>
                  {records.length ? (
                    <div className="overflow-x-auto rounded-xl border border-slate-100">
                      <table className="data-table min-w-[620px]">
                        <thead>
                          <tr><th>Row</th><th>Type</th><th>Disposition</th><th>Findings</th></tr>
                        </thead>
                        <tbody>
                          {records.map((record) => (
                            <tr key={record.staging_record_id}>
                              <td>{record.source_row_number}</td>
                              <td>{record.record_type}</td>
                              <td><StatusPill tone={record.disposition === 'accepted' ? 'teal' : record.disposition === 'rejected' ? 'red' : 'slate'}>{formatStatus(record.disposition)}</StatusPill></td>
                              <td>{record.finding_count}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">The API returned no staged records for this batch.</p>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}

function ResultCard({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold capitalize text-slate-900">{value}</p>
    </div>
  )
}
