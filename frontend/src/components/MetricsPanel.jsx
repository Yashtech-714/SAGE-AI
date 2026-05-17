import { fmt } from '../utils/formatters'

function Stat({ label, value, sub, color = 'text-slate-100' }) {
  return (
    <div className="flex-1 min-w-0 rounded-lg bg-surface-2 border border-surface-4 px-4 py-3">
      <div className="text-xs text-slate-500 mb-1">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-xs text-slate-600 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function MetricsPanel({ result }) {
  if (!result) return null
  const { execution_time_ms, total_time_ms, attempts, total_tokens, context_tables, prompt_tokens, completion_tokens } = result
  return (
    <div className="animate-slide-up">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Execution Metrics</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Stat label="DB Execution" value={fmt.ms(execution_time_ms)} color="text-emerald-400" />
        <Stat label="Total Pipeline" value={fmt.ms(total_time_ms || 0)} color="text-slate-200" />
        <Stat label="LLM Attempts" value={attempts} color={attempts > 1 ? 'text-amber-400' : 'text-slate-200'} />
        <Stat label="Tokens Used" value={fmt.num(total_tokens)} sub={`${prompt_tokens}p + ${completion_tokens}c`} />
        <Stat label="Tables in Context" value={context_tables?.length || 0} sub={context_tables?.join(', ')} />
      </div>
    </div>
  )
}
