import { useState, useMemo } from 'react'
import { fmt } from '../utils/formatters'

const PAGE_SIZE = 15

export default function ResultsTable({ rows = [], columns = [], rowCount }) {
  const [sort, setSort] = useState({ col: null, dir: 'asc' })
  const [page, setPage] = useState(0)

  const sorted = useMemo(() => {
    if (!sort.col) return rows
    return [...rows].sort((a, b) => {
      const av = a[sort.col], bv = b[sort.col]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv))
      return sort.dir === 'asc' ? cmp : -cmp
    })
  }, [rows, sort])

  const pages = Math.ceil(sorted.length / PAGE_SIZE)
  const visible = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const toggleSort = col => {
    setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'asc' })
    setPage(0)
  }

  const exportCSV = () => {
    const header = columns.join(',')
    const body = rows.map(r => columns.map(c => JSON.stringify(r[c] ?? '')).join(',')).join('\n')
    const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'results.csv' })
    a.click()
  }

  if (!rows.length) return (
    <div className="rounded-xl border border-surface-4 bg-surface-1 px-6 py-12 text-center animate-slide-up">
      <div className="text-3xl mb-3">📭</div>
      <p className="text-slate-400 text-sm">No results returned</p>
    </div>
  )

  return (
    <div className="rounded-xl border border-surface-4 bg-surface-1 overflow-hidden animate-slide-up">
      {/* header bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-4 bg-surface-2">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-indigo-400" />
          <span className="text-xs font-semibold text-slate-300 uppercase tracking-wide">Results</span>
          <span className="text-xs text-slate-500">
            {fmt.num(rowCount || rows.length)} rows {rowCount > rows.length ? `(showing ${rows.length})` : ''}
          </span>
        </div>
        <button onClick={exportCSV} className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1 rounded hover:bg-surface-3 transition-colors">
          ↓ CSV
        </button>
      </div>

      {/* table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-surface-4">
              {columns.map(col => (
                <th
                  key={col}
                  onClick={() => toggleSort(col)}
                  className="text-left px-4 py-2.5 text-xs font-semibold text-slate-400 uppercase tracking-wider cursor-pointer hover:text-slate-200 select-none whitespace-nowrap"
                >
                  {col}
                  {sort.col === col && <span className="ml-1 text-accent-light">{sort.dir === 'asc' ? '↑' : '↓'}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr key={i} className="border-b border-surface-4/50 hover:bg-surface-3/50 transition-colors">
                {columns.map(col => (
                  <td key={col} className="px-4 py-2.5 text-slate-300 font-mono text-xs whitespace-nowrap max-w-xs truncate">
                    {row[col] === null || row[col] === undefined ? (
                      <span className="text-slate-600 italic">null</span>
                    ) : typeof row[col] === 'number' ? (
                      <span className="text-amber-400">{fmt.num(row[col])}</span>
                    ) : String(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-surface-4 bg-surface-2">
          <span className="text-xs text-slate-500">
            Page {page + 1} of {pages}
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-xs rounded border border-surface-4 text-slate-400 hover:text-slate-200 hover:border-surface-4 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >← Prev</button>
            <button
              onClick={() => setPage(p => Math.min(pages - 1, p + 1))}
              disabled={page === pages - 1}
              className="px-3 py-1 text-xs rounded border border-surface-4 text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >Next →</button>
          </div>
        </div>
      )}
    </div>
  )
}
