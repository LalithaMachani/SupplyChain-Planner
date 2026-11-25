const apiBaseUrl = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function request(path, options = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, options)
  const contentType = response.headers.get('content-type') ?? ''
  const body = contentType.includes('application/json')
    ? await response.json()
    : await response.text()

  if (!response.ok) {
    const detail = typeof body?.detail === 'string'
      ? body.detail
      : body?.detail
        ? JSON.stringify(body.detail)
        : typeof body === 'string' && body
          ? body
          : response.statusText
    throw new Error(`Request failed (${response.status}): ${detail}`)
  }

  return body
}

function get(path) {
  return request(path)
}

function post(path, body) {
  if (body === undefined) {
    return request(path, { method: 'POST' })
  }
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function pageQuery(limit) {
  return `?limit=${limit}&offset=0`
}

export const api = {
  listRuns: () => get(`/api/v1/planning/runs${pageQuery(200)}`),
  getRun: (runId) => get(`/api/v1/planning/runs/${runId}`),
  createRun: (payload) => post('/api/v1/planning/runs', payload),
  calculateRun: (runId) => post(`/api/v1/planning/runs/${runId}/calculate`),
  listPab: (runId) => get(`/api/v1/planning/runs/${runId}/pab${pageQuery(5000)}`),
  listShortages: (runId) => get(`/api/v1/planning/runs/${runId}/shortages${pageQuery(5000)}`),
  listFindings: (runId) => get(`/api/v1/planning/runs/${runId}/findings${pageQuery(1000)}`),
  listMitigations: (runId) => get(`/api/v1/planning/runs/${runId}/mitigations${pageQuery(1000)}`),
  listShortageMitigations: (runId, shortage) =>
    get(
      `/api/v1/planning/runs/${runId}/shortages/${shortage.sku_id}/${shortage.location_id}/${shortage.week_start}/mitigations${pageQuery(1000)}`,
    ),
  listBatches: () => get(`/api/v1/ingestion/batches${pageQuery(200)}`),
  uploadCsv: (file, recordType, sourceName) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('record_type', recordType)
    formData.append('source_name', sourceName)
    return request('/api/v1/ingestion/upload', {
      method: 'POST',
      body: formData,
    })
  },
  validateBatch: (batchId) => post(`/api/v1/ingestion/batches/${batchId}/validate`),
  getBatchRecords: (batchId) =>
    get(`/api/v1/ingestion/batches/${batchId}/records${pageQuery(50)}`),
  getRecordFindings: (recordId) =>
    get(`/api/v1/ingestion/records/${recordId}/findings${pageQuery(200)}`),
}
