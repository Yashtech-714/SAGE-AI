export default function RetryInfo({ attempts, error }) {
  if (!attempts || attempts <= 1) return null
  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-950/20 px-4 py-3 flex items-start gap-3 animate-slide-up">
      <span className="text-amber-400 text-base mt-0.5">⚠</span>
      <div>
        <p className="text-sm font-medium text-amber-300">SQL Self-Corrected</p>
        <p className="text-xs text-amber-500/80 mt-0.5">
          The AI generated {attempts} attempt{attempts > 1 ? 's' : ''} before producing valid SQL.
          {error && !error.includes('Failed') ? ` Last error: ${error.slice(0, 100)}` : ''}
        </p>
      </div>
    </div>
  )
}
