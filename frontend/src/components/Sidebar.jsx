import { Icon } from './ui.jsx'

const navigation = [
  { id: 'overview', label: 'Overview', icon: 'overview' },
  { id: 'runs', label: 'Planning runs', icon: 'runs' },
  { id: 'shortages', label: 'Shortage analysis', icon: 'shortage' },
  { id: 'pab', label: 'Inventory & PAB', icon: 'pab' },
  { id: 'mitigations', label: 'Mitigations', icon: 'mitigation' },
  { id: 'ingestion', label: 'Data ingestion', icon: 'ingestion' },
]

export default function Sidebar({ activePage, onNavigate, open, onClose }) {
  return (
    <>
      {open && (
        <button
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-slate-950/40 lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[260px] flex-col bg-[#102c3b] text-white transition-transform duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center gap-3 px-6 py-6">
          <div className="grid size-10 place-items-center rounded-xl bg-teal-400 text-sm font-black tracking-tight text-[#102c3b]">
            SC
          </div>
          <div>
            <p className="text-sm font-bold tracking-wide">SupplyChain</p>
            <p className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-slate-400">
              Planning workspace
            </p>
          </div>
          <button
            aria-label="Close navigation"
            className="ml-auto rounded-lg p-2 text-slate-300 hover:bg-white/10 lg:hidden"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>

        <div className="px-4 pt-3">
          <p className="px-3 pb-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            Workspace
          </p>
          <nav aria-label="Main navigation" className="space-y-1">
            {navigation.map((item) => {
              const selected = activePage === item.id
              return (
                <button
                  key={item.id}
                  aria-current={selected ? 'page' : undefined}
                  className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition ${
                    selected
                      ? 'bg-teal-400/15 text-teal-200 ring-1 ring-teal-300/20'
                      : 'text-slate-300 hover:bg-white/5 hover:text-white'
                  }`}
                  onClick={() => {
                    onNavigate(item.id)
                    onClose()
                  }}
                >
                  <Icon name={item.icon} className="size-[18px]" />
                  {item.label}
                </button>
              )
            })}
          </nav>
        </div>

        <div className="mt-auto border-t border-white/10 px-6 py-5">
          <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
            <span className="size-2 rounded-full bg-teal-400" />
            Read-only planning workspace
          </div>
          <p className="mt-2 text-[11px] leading-5 text-slate-500">
            Recommendations are read-only and require planner review.
          </p>
        </div>
      </aside>
    </>
  )
}
