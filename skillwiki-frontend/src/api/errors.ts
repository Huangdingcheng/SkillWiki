import axios from 'axios'

type ErrorPayload = {
  detail?: unknown
  error?: unknown
  message?: unknown
}

function stringifyDetail(detail: unknown): string | null {
  if (!detail) return null
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .filter(Boolean)
      .join('; ')
  }
  if (typeof detail === 'object') return JSON.stringify(detail)
  return String(detail)
}

export function getApiErrorMessage(error: unknown, fallback = 'Request failed'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as ErrorPayload | string | undefined
    if (typeof data === 'string' && data.trim()) return data
    if (data && typeof data === 'object') {
      return (
        stringifyDetail(data.detail) ||
        stringifyDetail(data.error) ||
        stringifyDetail(data.message) ||
        error.message ||
        fallback
      )
    }
    return error.message || fallback
  }

  if (error instanceof Error) return error.message || fallback
  return fallback
}
