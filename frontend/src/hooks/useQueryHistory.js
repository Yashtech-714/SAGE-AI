import { useState, useEffect } from 'react'
const KEY = 'nl2sql_history'
const MAX = 20

export function useQueryHistory() {
  const [history, setHistory] = useState(() => {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]') }
    catch { return [] }
  })

  const add = entry => setHistory(prev => {
    const next = [{ ...entry, id: Date.now(), ts: new Date().toISOString() }, ...prev].slice(0, MAX)
    localStorage.setItem(KEY, JSON.stringify(next))
    return next
  })

  const clear = () => { setHistory([]); localStorage.removeItem(KEY) }

  return { history, add, clear }
}
