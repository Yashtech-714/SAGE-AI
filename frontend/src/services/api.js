import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const client = axios.create({
  baseURL: BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 90000,
})

client.interceptors.response.use(
  r => r,
  err => {
    const msg = err.response?.data?.error || err.message || 'Request failed'
    return Promise.reject(new Error(msg))
  }
)

export const submitQuery = (question, maxRows = 50, includeInsight = true) =>
  client.post('/query', { question, max_rows: maxRows, include_insight: includeInsight }).then(r => r.data)

export const getHealth  = () => client.get('/health').then(r => r.data)
export const getSchema  = () => client.get('/schema').then(r => r.data)
export const getMetrics = () => client.get('/metrics').then(r => r.data)
export const getExamples= () => client.get('/examples').then(r => r.data)
