import { fmt } from '../utils/formatters'

export default function QueryHistory({ history, onSelect, onClear }) {
  if (!history.length) return (
    <div className="px-4 py-8 text-center">
      <div className="text-2xl mb-2">🕐</div>
      <p className="text-xs text-slate-600">No queries yet</p>
    </div>
  )

  return (
    <div>
      <div className="flex items-center justify-between px-4 py-2">
        <span className="text-xs text-slate-500 font-medium">Recent ({history.length})</span>
        <button onClick={onClear} className="text-xs text-slate-700 hover:text-slate-400 transition-colors">Clear</button>
      </div>
      <div className="space-y-0.5 px-2">
        {history.map(h => (
          <button
            key={h.id}
            onClick={() => onSelect(h.question)}
            className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-surface-3 transition-colors group"
          >
            <div className="text-xs text-slate-300 group-hover:text-slate-100 transition-colors truncate mb-1">
              {h.question}
            </div>
            <div className="flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${h.success ? 'bg-emerald-500' : 'bg-red-500'}`} />
              <span className="text-xs text-slate-600">{fmt.relTime(h.ts)}</span>
              {h.execution_time_ms && (
                <span className="text-xs text-slate-700">{fmt.ms(h.execution_time_ms)}</span>
              )}
              {h.attempts > 1 && (
                <span className="text-xs text-amber-600">{h.attempts} tries</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
