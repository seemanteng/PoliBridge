// Thin fetch wrapper. There is no sign-in: PoliBridge is operated by the Risk & Compliance team, who can see and edit
// everything, so the backend acts as that team and records owner actions as entered on the owner's behalf.
import { useCallback, useEffect, useState } from 'react'

export type J = any // eslint-disable-line @typescript-eslint/no-explicit-any


export class ApiError extends Error {}

async function call(method: string, path: string, body?: unknown): Promise<J> {
  const r = await fetch('/api' + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new ApiError(typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? r.statusText))
  return data
}

async function upload(path: string, file: File): Promise<J> {
  const r = await fetch('/api' + path, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new ApiError(typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? r.statusText))
  return data
}

export const api = {
  upload,
  get: (p: string) => call('GET', p),
  post: (p: string, b?: unknown) => call('POST', p, b ?? {}),
  patch: (p: string, b?: unknown) => call('PATCH', p, b ?? {}),
  del: (p: string) => call('DELETE', p),
}

// Bumped after every mutation or clock change so all mounted views refetch.
let version = 0
const listeners = new Set<() => void>()
export const refreshAll = () => {
  version++
  listeners.forEach((l) => l())
}

export function useApi(path: string | null) {
  const [data, setData] = useState<J>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(version)
  useEffect(() => {
    const l = () => setTick(version)
    listeners.add(l)
    return () => { listeners.delete(l) }
  }, [])
  const load = useCallback(() => {
    if (!path) return
    api.get(path).then((d) => { setData(d); setError(null) }).catch((e) => setError(e.message))
  }, [path])
  useEffect(load, [load, tick])
  return { data, error, reload: load }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const p2 = (n: number) => String(n).padStart(2, '0')
export function fmt(iso?: string | null, withYear = false) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${withYear ? ' ' + d.getFullYear() : ''} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}
export function fmtDay(iso?: string | null, withYear = false) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${p2(d.getDate())} ${MONTHS[d.getMonth()]}${withYear ? ' ' + d.getFullYear() : ''}`
}
