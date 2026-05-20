import { useQuery } from '@tanstack/react-query'
import { getExamples } from '../services/api'
import QueryHistory from './QueryHistory'
import SchemaViewer from './SchemaViewer'

const TABS = [
  { id: 'history', label: 'History', icon: '🕐' },
  { id: 'examples', label: 'Examples', icon: '💡' },
  { id: 'schema', label: 'Schema', icon: '🗂' },
]

function ExamplesPanel({ onSelect }) {
  const { data } = useQuery({ queryKey: ['examples'], queryFn: getExamples, staleTime: Infinity })
  const questions = data?.all_questions || []
  return (
    <div className="space-y-0.5 px-2">
      {questions.map((q, i) => (
        <button
          key={i}
          onClick={() => onSelect(q)}
          className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-surface-3 transition-colors group"
        >
          <div className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors leading-relaxed">{q}</div>
        </button>
      ))}
    </div>
  )
}

export default function Sidebar({ activeTab, onTabChange, history, onSelect, onClear }) {
  return (
    <div className="w-72 flex-shrink-0 h-full flex flex-col border-r border-surface-4 bg-surface-1">
      {/* brand */}
      <div className="px-5 py-4 border-b border-surface-4">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-accent/20 flex items-center justify-center text-sm">⚡</div>
          <div>
            <div className="text-sm font-semibold text-slate-100">SAGE AI</div>
            <div className="text-xs text-slate-600">SQL Analytics Generation Engine</div>
          </div>
        </div>
      </div>

      {/* tabs */}
      <div className="flex border-b border-surface-4">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => onTabChange(t.id)}
            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
              activeTab === t.id
                ? 'text-accent-light border-b-2 border-accent bg-accent/5'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* panel content */}
      <div className="flex-1 overflow-y-auto py-2">
        {activeTab === 'history' && (
          <QueryHistory history={history} onSelect={onSelect} onClear={onClear} />
        )}
        {activeTab === 'examples' && (
          <ExamplesPanel onSelect={onSelect} />
        )}
        {activeTab === 'schema' && (
          <SchemaViewer />
        )}
      </div>

      {/* footer */}
      <div className="px-4 py-3 border-t border-surface-4">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs text-slate-600">Groq · Llama-3.3-70b</span>
        </div>
      </div>
    </div>
  )
}
