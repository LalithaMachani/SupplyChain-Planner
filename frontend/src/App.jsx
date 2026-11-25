import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client.js'
import './App.css'
import Ingestion from './components/Ingestion.jsx'
import Mitigations from './components/Mitigations.jsx'
import Overview from './components/Overview.jsx'
import PabAnalysis from './components/PabAnalysis.jsx'
import PlanningRuns from './components/PlanningRuns.jsx'
import ShortageAnalysis from './components/ShortageAnalysis.jsx'
import Sidebar from './components/Sidebar.jsx'
import { Icon } from './components/ui.jsx'
import { formatDate } from './lib/format.js'

const emptyRunData = { pab: [], shortages: [], findings: [], mitigations: [] }

const pageTitles = {
  overview: ['Planning overview', 'Operational snapshot from your selected planning run.'],
  runs: ['Planning runs', 'Create, calculate, and inspect run-scoped planning scenarios.'],
  shortages: ['Shortage analysis', 'Search shortage records, review their causes, and inspect affected weeks.'],
  pab: ['Inventory & PAB', 'Review weekly demand, firm supply, and projected available balance.'],
  mitigations: ['Mitigation recommendations', 'Compare explainable options without executing supply changes.'],
  ingestion: ['Data ingestion', 'Stage CSV inputs and review backend validation findings.'],
}

export default function App() {
  const [activePage, setActivePage] = useState('overview')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [runs, setRuns] = useState([])
  const [runCount, setRunCount] = useState(0)
  const [batches, setBatches] = useState([])
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [activeRun, setActiveRun] = useState(null)
  const [runData, setRunData] = useState(emptyRunData)
  const [runLoading, setRunLoading] = useState(false)
  const [runDataErrorState, setRunDataErrorState] = useState(null)
  const [backendConnected, setBackendConnected] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)
  const [pageError, setPageError] = useState('')
  const [notice, setNotice] = useState('')
  const [shortageSelection, setShortageSelection] = useState(null)
  const [mitigationSelection, setMitigationSelection] = useState(null)
  const [mitigationLoading, setMitigationLoading] = useState(false)
  const [calculatingRunId, setCalculatingRunId] = useState(null)

  const refreshRuns = useCallback(async (preferredId = null) => {
    const page = await api.listRuns()
    const items = page.items ?? []
    setRuns(items)
    setRunCount(page.total ?? items.length)
    setBackendConnected(true)
    setSelectedRunId((current) => {
      const desired = preferredId ?? current
      return items.some((item) => item.planning_run_id === desired)
        ? desired
        : items[0]?.planning_run_id ?? null
    })
    return page
  }, [])

  const refreshBatches = useCallback(async () => {
    const page = await api.listBatches()
    setBatches(page.items ?? [])
    return page
  }, [])

  const loadRun = useCallback(async (runId) => {
    if (!runId) {
      setActiveRun(null)
      setRunData(emptyRunData)
      setRunDataErrorState(null)
      setRunLoading(false)
      return
    }
    setRunLoading(true)
    setActiveRun(null)
    setRunData(emptyRunData)
    setRunDataErrorState(null)
    try {
      const [run, shortagesPage, pabPage, findingsPage, mitigationsPage] = await Promise.all([
        api.getRun(runId),
        api.listShortages(runId),
        api.listPab(runId),
        api.listFindings(runId),
        api.listMitigations(runId),
      ])
      setActiveRun(run)
      setRunData({
        shortages: shortagesPage.items ?? [],
        pab: pabPage.items ?? [],
        findings: findingsPage.items ?? [],
        mitigations: mitigationsPage.items ?? [],
      })
      setMitigationSelection(null)
    } catch (error) {
      setActiveRun(null)
      setRunData(emptyRunData)
      setRunDataErrorState({ runId, message: error.message })
    } finally {
      setRunLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    Promise.allSettled([api.listRuns(), api.listBatches()]).then((results) => {
      if (!active) return
      const [runsResult, batchesResult] = results
      const failed = []
      if (runsResult.status === 'fulfilled') {
        const items = runsResult.value.items ?? []
        setRuns(items)
        setRunCount(runsResult.value.total ?? items.length)
        setBackendConnected(true)
        setSelectedRunId((current) =>
          items.some((item) => item.planning_run_id === current)
            ? current
            : items[0]?.planning_run_id ?? null,
        )
      } else {
        failed.push(runsResult.reason?.message ?? 'Backend request failed.')
      }
      if (batchesResult.status === 'fulfilled') {
        setBatches(batchesResult.value.items ?? [])
      } else {
        failed.push(batchesResult.reason?.message ?? 'Backend request failed.')
      }
      setPageError(failed.join(' · '))
      setInitialLoading(false)
    })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!selectedRunId) return
    let active = true
    Promise.all([
      api.getRun(selectedRunId),
      api.listShortages(selectedRunId),
      api.listPab(selectedRunId),
      api.listFindings(selectedRunId),
      api.listMitigations(selectedRunId),
    ]).then(([run, shortagesPage, pabPage, findingsPage, mitigationsPage]) => {
      if (!active) return
      setActiveRun(run)
      setRunData({
        shortages: shortagesPage.items ?? [],
        pab: pabPage.items ?? [],
        findings: findingsPage.items ?? [],
        mitigations: mitigationsPage.items ?? [],
      })
      setRunDataErrorState(null)
      setMitigationSelection(null)
    }).catch((error) => {
      if (!active) return
      setActiveRun(null)
      setRunData(emptyRunData)
      setRunDataErrorState({ runId: selectedRunId, message: error.message })
    }).finally(() => {
      if (active) setRunLoading(false)
    })
    return () => {
      active = false
    }
  }, [selectedRunId])

  const run = activeRun?.planning_run_id === selectedRunId ? activeRun : null
  const currentRunData = run ? runData : emptyRunData
  const runDataLoaded = run !== null
  const runDataError = runDataErrorState?.runId === selectedRunId
    ? runDataErrorState.message
    : ''
  const runIsLoading = runLoading || Boolean(
    selectedRunId && !run && runDataErrorState?.runId !== selectedRunId,
  )
  const selectedShortage = shortageSelection?.runId === selectedRunId
    ? shortageSelection.shortage
    : null
  const specificMitigations = mitigationSelection?.runId === selectedRunId
    ? mitigationSelection.items
    : null
  const currentTitle = pageTitles[activePage] ?? pageTitles.overview

  const showError = (error) => {
    setNotice('')
    setPageError(error.message ?? String(error))
  }

  const handleRefresh = async () => {
    setPageError('')
    try {
      await Promise.all([refreshRuns(), refreshBatches()])
      if (selectedRunId) await loadRun(selectedRunId)
      setNotice('Dashboard data refreshed from the backend.')
    } catch (error) {
      showError(error)
    }
  }

  const handleCreateRun = async (payload) => {
    setPageError('')
    const created = await api.createRun(payload)
    await refreshRuns(created.planning_run_id)
    setActivePage('runs')
    setNotice(`Planning run #${created.planning_run_id} was created.`)
  }

  const handleCalculateRun = async (runId) => {
    setPageError('')
    setCalculatingRunId(runId)
    try {
      await api.calculateRun(runId)
      await refreshRuns(runId)
      await loadRun(runId)
      setNotice(`Planning run #${runId} calculation completed.`)
    } catch (error) {
      showError(error)
    } finally {
      setCalculatingRunId(null)
    }
  }

  const handleShortageSelect = (shortage) => {
    setShortageSelection({ runId: selectedRunId, shortage })
    setMitigationSelection(null)
  }

  const handleRunSelect = (runId) => {
    if (runId !== selectedRunId) {
      setShortageSelection(null)
      setMitigationSelection(null)
    }
    setSelectedRunId(runId)
    setActivePage('runs')
  }

  const handleShortageMitigations = async (shortage) => {
    setShortageSelection({ runId: selectedRunId, shortage })
    setActivePage('mitigations')
    setMitigationSelection(null)
    setMitigationLoading(true)
    setPageError('')
    try {
      const page = await api.listShortageMitigations(selectedRunId, shortage)
      setMitigationSelection({ runId: selectedRunId, items: page.items ?? [] })
    } catch (error) {
      showError(error)
    } finally {
      setMitigationLoading(false)
    }
  }

  const handleMitigationFilter = async (shortage) => {
    setShortageSelection(shortage ? { runId: selectedRunId, shortage } : null)
    if (!shortage) {
      setMitigationSelection(null)
      return
    }
    await handleShortageMitigations(shortage)
  }

  const handleNotice = (message) => {
    setPageError('')
    setNotice(message)
  }

  const mitigationItems = selectedShortage
    ? specificMitigations ?? []
    : currentRunData.mitigations

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="flex min-h-screen">
        <Sidebar
          activePage={activePage}
          onClose={() => setSidebarOpen(false)}
          onNavigate={(page) => {
            setActivePage(page)
            setPageError('')
            setNotice('')
          }}
          open={sidebarOpen}
        />
        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
            <div className="flex min-h-[76px] items-center justify-between gap-4 px-4 sm:px-7 lg:px-9">
              <div className="flex min-w-0 items-center gap-3">
                <button
                  aria-label="Open navigation"
                  className="rounded-lg border border-slate-200 p-2 text-slate-600 lg:hidden"
                  onClick={() => setSidebarOpen(true)}
                >
                  <Icon name="menu" />
                </button>
                <div className="min-w-0">
                  <h1 className="truncate text-lg font-bold tracking-tight text-slate-900 sm:text-xl">
                    {currentTitle[0]}
                  </h1>
                  <p className="hidden truncate text-xs text-slate-500 sm:block">{currentTitle[1]}</p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2 sm:gap-4">
                <div className="hidden text-right md:block">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Selected run</p>
                  <p className="mt-0.5 max-w-36 truncate text-xs font-semibold text-slate-700">
                    {run ? `${run.scenario_key} · #${run.planning_run_id}` : 'None'}
                  </p>
                </div>
                <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[11px] font-semibold ${
                  backendConnected ? 'bg-teal-50 text-teal-700' : 'bg-slate-100 text-slate-500'
                }`}>
                  <span className={`size-1.5 rounded-full ${backendConnected ? 'bg-teal-500' : 'bg-slate-400'}`} />
                  {backendConnected ? 'API connected' : initialLoading ? 'Connecting' : 'API unavailable'}
                </span>
                <button
                  aria-label="Refresh dashboard data"
                  className="rounded-lg border border-slate-200 p-2 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                  disabled={initialLoading || runIsLoading}
                  onClick={handleRefresh}
                  title="Refresh data"
                >
                  <Icon className={`size-[18px] ${initialLoading || runIsLoading ? 'animate-spin' : ''}`} name="refresh" />
                </button>
              </div>
            </div>
          </header>

          <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 sm:px-7 lg:px-9 lg:py-8">
            {pageError && (
              <div className="alert-error flex items-start justify-between gap-4" role="alert">
                <span>{pageError}</span>
                <button aria-label="Dismiss error" className="font-bold" onClick={() => setPageError('')}>×</button>
              </div>
            )}
            {runDataError && (
              <div className="alert-error" role="alert">
                {selectedRunId ? `Run #${selectedRunId} data is unavailable: ` : 'Run data is unavailable: '}
                {runDataError}
              </div>
            )}
            {notice && (
              <div className="alert-success flex items-start justify-between gap-4" role="status">
                <span>{notice}</span>
                <button aria-label="Dismiss message" className="font-bold" onClick={() => setNotice('')}>×</button>
              </div>
            )}

            {activePage === 'overview' && (
              <Overview
                data={currentRunData}
                loading={runIsLoading || (run !== null && !runDataLoaded)}
                onNavigate={setActivePage}
                onSelectRun={handleRunSelect}
                run={run}
                runCount={runCount}
                runs={runs}
              />
            )}
            {activePage === 'runs' && (
              <PlanningRuns
                calculating={calculatingRunId === selectedRunId}
                loading={runIsLoading || initialLoading}
                onCalculateRun={handleCalculateRun}
                onCreateRun={handleCreateRun}
                onSelectRun={handleRunSelect}
                run={run}
                runs={runs}
              />
            )}
            {activePage === 'shortages' && (
              <ShortageAnalysis
                onSelectShortage={handleShortageSelect}
                onViewMitigations={handleShortageMitigations}
                selectedShortage={selectedShortage}
                shortages={currentRunData.shortages}
              />
            )}
            {activePage === 'pab' && <PabAnalysis pab={currentRunData.pab} />}
            {activePage === 'mitigations' && (
              <Mitigations
                loading={runIsLoading || mitigationLoading}
                onSelectShortage={handleMitigationFilter}
                recommendations={mitigationItems}
                run={run}
                selectedShortage={selectedShortage}
                shortages={currentRunData.shortages}
              />
            )}
            {activePage === 'ingestion' && (
              <Ingestion
                batches={batches}
                onNotice={handleNotice}
                onRefreshBatches={refreshBatches}
              />
            )}

            <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-4 text-[11px] text-slate-400">
              <span>SupplyChain Planner · Data from the configured backend API</span>
              <span>
                {run ? `Run #${run.planning_run_id} · Captured ${formatDate(run.captured_at)}` : 'No planning run selected'}
              </span>
            </footer>
          </main>
        </div>
      </div>
    </div>
  )
}
