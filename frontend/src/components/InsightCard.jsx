export default function InsightCard({ insight }) {
  if (!insight) return null
  return (
    <div className="rounded-xl border border-indigo-500/20 bg-gradient-to-br from-indigo-950/30 to-surface-1 p-5 animate-slide-up">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-7 h-7 rounded-lg bg-indigo-500/15 flex items-center justify-center text-sm">✨</div>
        <span className="text-xs font-semibold text-indigo-300 uppercase tracking-wide">AI Business Insight</span>
      </div>
      <p className="text-slate-200 text-sm leading-relaxed">{insight}</p>
    </div>
  )
}
