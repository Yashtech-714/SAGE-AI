// SQL syntax highlighter — pure JSX, no dangerouslySetInnerHTML

const KW = new Set([
  'SELECT','FROM','WHERE','JOIN','ON','GROUP','BY','ORDER','LIMIT','HAVING',
  'AS','AND','OR','NOT','IN','LIKE','DISTINCT','INNER','LEFT','RIGHT','OUTER',
  'UNION','ALL','NULL','IS','BETWEEN','CASE','WHEN','THEN','ELSE','END',
  'ASC','DESC','WITH','OVER','PARTITION','ROWS','RANGE',
])

const FN = new Set([
  'COUNT','SUM','AVG','MAX','MIN','COALESCE','NULLIF','CAST','ROUND',
  'FLOOR','CEIL','ABS','LENGTH','UPPER','LOWER','TRIM','SUBSTR','REPLACE',
  'STRFTIME','JULIANDAY','DATE','DATETIME','IFNULL','IIF',
])

function tokenize(sql) {
  const tokens = []
  const re = /('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|(--[^\n]*)|([A-Za-z_]\w*)|([=<>!]+|[+\-*/])|([(),;.])|(\s+)|(.)>/g
  // simpler split approach:
  const parts = sql.split(/(\s+|'[^']*'|\b\d+(?:\.\d+)?\b|--[^\n]*|[(),;.]|[=<>!]+)/)
  for (const p of parts) {
    if (!p) continue
    const up = p.toUpperCase().trim()
    if (/^\s+$/.test(p)) { tokens.push({ t: 'ws', v: p }); continue }
    if (/^'/.test(p)) { tokens.push({ t: 'str', v: p }); continue }
    if (/^\d/.test(p)) { tokens.push({ t: 'num', v: p }); continue }
    if (/^--/.test(p)) { tokens.push({ t: 'cmt', v: p }); continue }
    if (KW.has(up)) { tokens.push({ t: 'kw', v: p }); continue }
    if (FN.has(up)) { tokens.push({ t: 'fn', v: p }); continue }
    if (/^[(),;.]$/.test(p)) { tokens.push({ t: 'punc', v: p }); continue }
    tokens.push({ t: 'id', v: p })
  }
  return tokens
}

const CLS = {
  kw:   'text-indigo-400 font-semibold',
  fn:   'text-violet-400',
  str:  'text-emerald-400',
  num:  'text-amber-400',
  cmt:  'text-slate-500 italic',
  punc: 'text-slate-500',
  id:   'text-slate-200',
  ws:   '',
}

export function SqlHighlight({ sql }) {
  if (!sql) return null
  const tokens = tokenize(sql)
  return (
    <code className="font-mono text-sm leading-relaxed">
      {tokens.map((tok, i) => (
        <span key={i} className={CLS[tok.t] || 'text-slate-300'}>{tok.v}</span>
      ))}
    </code>
  )
}
