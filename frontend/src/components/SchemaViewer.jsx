import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSchema } from '../services/api'

export default function SchemaViewer() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['schema'], queryFn: getSchema, staleTime: 300000 })
  const [open, setOpen] = useState({})
  const [search, setSearch] = useState('')

  const toggle = t => setOpen(o => ({ ...o, [t]: !o[t] }))

  if (isLoading) return <div className="px-4 py-4 text-xs text-slate-600 animate-pulse">Loading schema…</div>
  if (isError) return <div className="px-4 py-4 text-xs text-red-500">Failed to load schema</div>

  const tables = data?.tables?.filter(t =>
    !search || t.table.toLowerCase().includes(search.toLowerCase())
  ) || []

  return (
    <div>
      <div className="px-3 pb-2">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search tables…"
          className="w-full bg-surface-2 border border-surface-4 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder:text-slate-600 outline-none focus:border-accent/40"
        />
      </div>
      <div className="space-y-0.5 px-2">
        {tables.map(t => (
          <div key={t.table}>
            <button
              onClick={() => toggle(t.table)}
              className="w-full text-left flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-3 transition-colors group"
            >
              <span className="text-slate-600 text-xs">{open[t.table] ? '▼' : '▶'}</span>
              <span className="text-xs font-medium text-slate-300 group-hover:text-slate-100 font-mono">{t.table}</span>
              <span className="ml-auto text-xs text-slate-700">{t.columns.length}</span>
            </button>
            {open[t.table] && (
              <div className="ml-5 mb-1 space-y-0.5">
                {t.columns.map(col => (
                  <div key={col.name} className="flex items-center gap-2 px-3 py-1 text-xs">
                    {col.primary_key && <span title="PK" className="text-amber-500 text-xs">🔑</span>}
                    {col.foreign_key && <span title="FK" className="text-sky-500 text-xs">🔗</span>}
                    <span className="text-slate-400 font-mono">{col.name}</span>
                    <span className="ml-auto text-slate-700">{col.type}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
