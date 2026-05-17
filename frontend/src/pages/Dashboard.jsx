import { useState, useEffect, useCallback } from 'react'
import { submitQuery } from '../services/api'
import { useQueryHistory } from '../hooks/useQueryHistory'
import Sidebar from '../components/Sidebar'
import QueryInput from '../components/QueryInput'
import SqlViewer from '../components/SqlViewer'
import ResultsTable from '../components/ResultsTable'
import InsightCard from '../components/InsightCard'
import MetricsPanel from '../components/MetricsPanel'
import RetryInfo from '../components/RetryInfo'
import LoadingState from '../components/LoadingState'

const STEP_DELAYS = [0, 700, 2000, 3200]

export default function Dashboard() {
  const [question, setQuestion] = useState('')
  const [maxRows, setMaxRows] = useState(50)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [loadingStep, setLoadingStep] = useState(0)
  const [activeTab, setActiveTab] = useState('history')
  const { history, add, clear } = useQueryHistory()

  const runQuery = useCallback(async (q) => {
    const text = (q || question).trim()
    if (!text || isLoading) return
    if (q) setQuestion(q)

    setIsLoading(true)
    setError(null)
    setResult(null)
    setLoadingStep(0)

    // cycle loading steps
    const timers = STEP_DELAYS.slice(1).map((delay, i) =>
      setTimeout(() => setLoadingStep(i + 1), delay)
    )

    try {
      const data = await submitQuery(text, maxRows)
      setResult(data)
      add({
        question: text,
        success: data.success,
        attempts: data.attempts,
        row_count: data.row_count,
        execution_time_ms: data.execution_time_ms,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      timers.forEach(clearTimeout)
      setIsLoading(false)
    }
  }, [question, maxRows, isLoading, add])

  // keyboard shortcut
  useEffect(() => {
    const handler = e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') runQuery()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [runQuery])

  const selectFromSidebar = q => {
    setQuestion(q)
    setActiveTab('history')
    runQuery(q)
  }

  const showResults = !isLoading && result

  return (
    <div className="flex h-screen overflow-hidden bg-surface-0 text-slate-200">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        history={history}
        onSelect={selectFromSidebar}
        onClear={clear}
      />

      {/* main workspace */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* top bar */}
        <div className="flex-shrink-0 px-6 py-3.5 border-b border-surface-4 bg-surface-1 flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-slate-100">Analytics Workspace</h1>
            <p className="text-xs text-slate-600 mt-0.5">Ask any business question in plain English</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <span className="px-2 py-1 rounded border border-surface-4 bg-surface-2">Olist E-Commerce</span>
            <span className="px-2 py-1 rounded border border-surface-4 bg-surface-2">9 tables · 99k orders</span>
          </div>
        </div>

        {/* scrollable content */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {/* query input */}
          <QueryInput
            value={question}
            onChange={setQuestion}
            onSubmit={() => runQuery()}
            isLoading={isLoading}
            maxRows={maxRows}
            onMaxRowsChange={setMaxRows}
          />

          {/* loading state */}
          {isLoading && <LoadingState step={loadingStep} />}

          {/* error */}
          {error && !isLoading && (
            <div className="rounded-xl border border-red-500/25 bg-red-950/20 px-5 py-4 animate-slide-up">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-red-400">✕</span>
                <span className="text-sm font-medium text-red-300">Query Failed</span>
              </div>
              <p className="text-xs text-red-400/80">{error}</p>
            </div>
          )}

          {/* results */}
          {showResults && !result.success && (
            <div className="rounded-xl border border-red-500/25 bg-red-950/20 px-5 py-4 animate-slide-up">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-red-400">✕</span>
                <span className="text-sm font-medium text-red-300">AI Pipeline Failed</span>
              </div>
              <p className="text-xs text-red-400/80">{result.error}</p>
              <p className="text-xs text-red-600 mt-1">Attempts: {result.attempts}</p>
            </div>
          )}

          {showResults && result.success && (
            <>
              <RetryInfo attempts={result.attempts} error={result.error} />
              <SqlViewer sql={result.sql} attempts={result.attempts} />
              <ResultsTable
                rows={result.rows}
                columns={result.columns}
                rowCount={result.row_count}
              />
              {result.insight && <InsightCard insight={result.insight} />}
              <MetricsPanel result={result} />
            </>
          )}

          {/* empty welcome state */}
          {!isLoading && !result && !error && (
            <div className="flex flex-col items-center justify-center py-20 text-center animate-fade-in">
              <div className="text-5xl mb-4">🔎</div>
              <h2 className="text-base font-semibold text-slate-300 mb-2">Ask a business question</h2>
              <p className="text-sm text-slate-600 max-w-sm">
                Type any analytics question above or pick one from the Examples tab.
                The AI will generate optimized SQL, execute it, and explain the results.
              </p>
              <div className="mt-6 grid grid-cols-2 gap-2 max-w-md w-full">
                {['Top sellers by revenue', 'Monthly orders 2018', 'Review scores by category', 'Orders by state'].map(q => (
                  <button
                    key={q}
                    onClick={() => selectFromSidebar(q)}
                    className="text-left px-3 py-2.5 rounded-lg border border-surface-4 bg-surface-2 hover:bg-surface-3 hover:border-accent/30 transition-all text-xs text-slate-400 hover:text-slate-200"
                  >
                    {q} →
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
