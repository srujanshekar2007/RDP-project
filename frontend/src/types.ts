export type Role = 'citizen' | 'authority' | 'ngo' | 'admin'

export interface User {
  id: number
  full_name: string
  email: string
  role: Role
  created_at: string
}

export interface Analysis {
  id: number
  status: string
  result: Record<string, unknown> | null
  error_message: string | null
  annotated_url: string | null
  created_at: string
}

export interface Complaint {
  id: string
  reporter_id: number | null
  reporter_name: string
  road_name: string
  description: string
  latitude: number | null
  longitude: number | null
  status: string
  severity: string
  priority_score: number
  duplicate_of: number | null
  rejection_reason: string | null
  assigned_authority_id: number | null
  created_at: string
  updated_at: string
  resolved_at: string | null
  media: { id: number; name: string; type: string; url: string; size: number }[]
  analyses: Analysis[]
  ai_report: Record<string, unknown> | null
  updates: { id: number; author_id: number; note: string; evidence_url: string | null; created_at: string }[]
  timeline: { action: string; from: string | null; to: string | null; remarks: string; timestamp: string }[]
  interventions: { id: number; proposal: string; status: string; created_at: string }[]
}

export interface NotificationItem {
  id: number
  complaint_id: string | null
  message: string
  read: boolean
  created_at: string
}

export interface DashboardSummary {
  total: number
  by_status: { name: string; value: number }[]
  by_severity: { name: string; value: number }[]
  over_time: { month: string; count: number }[]
  average_resolution_hours: number | null
}

export async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init?.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${import.meta.env.VITE_API_BASE ?? ''}${path}`, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function shortStatus(status: string) {
  return status.replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase())
}
