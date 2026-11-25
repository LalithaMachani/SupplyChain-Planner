// Runtime validation coverage for planning API requests and backend errors.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('planning API client', () => {
  it('sends calculation requests without a JSON body or content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'calculated' }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.calculateRun(17)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/planning/runs/17/calculate',
      { method: 'POST' },
    )
  })

  it('surfaces backend validation details to callers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Unknown SKU-location pair' }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))

    await expect(api.createRun({ items: [] })).rejects.toThrow(
      'Request failed (422): Unknown SKU-location pair',
    )
  })
})
