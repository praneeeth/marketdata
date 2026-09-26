import { useState, useEffect } from 'react'
import { fetchAPI } from '@candlewise/api'

const EVENT = 'candlewise:avatar-changed'

// In-memory cache for the SPA session only (avoids repeat requests within a session).
// The real persistence is in the backend DB (ui_avatar in data/candlewise.db); it is fetched again after a refresh.
let cache: string | null = null
let inflight: Promise<string> | null = null

function load(): Promise<string> {
  if (cache !== null) return Promise.resolve(cache)
  if (!inflight) {
    inflight = fetchAPI<{ value: string }>('/settings/avatar')
      .then(r => {
        cache = r?.value || ''
        return cache as string
      })
      .catch(() => {
        cache = ''
        return ''
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

/**
 * Save the avatar (an empty string clears it): the backend writes the image to data/avatars and the DB stores only the file name;
 * a local broadcast updates it at once. Note the cache holds a data URL (GET also returns a data URL).
 */
export async function saveAvatar(value: string): Promise<void> {
  await fetchAPI('/settings/avatar', { method: 'PUT', body: JSON.stringify({ value }) })
  cache = value
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: value }))
}

/** The current avatar (a data URL or image address). Comes from the backend DB; synced across components at once. */
export function useAvatar(): string {
  const [avatar, setAvatar] = useState<string>(cache ?? '')
  useEffect(() => {
    let alive = true
    load().then(v => {
      if (alive) setAvatar(v)
    })
    const onChange = (e: Event) => setAvatar((e as CustomEvent<string>).detail ?? '')
    window.addEventListener(EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(EVENT, onChange)
    }
  }, [])
  return avatar
}

/**
 * Compress an uploaded image into a size×size square JPEG data URL (centre-cropped),
 * keeping it small (about 10-20KB) so large base64 doesn't bloat DB storage.
 */
export function fileToAvatarDataUrl(file: File, size = 128): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Failed to read the file'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('Failed to decode the image'))
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          reject(new Error('canvas unavailable'))
          return
        }
        const scale = Math.max(size / img.width, size / img.height)
        const w = img.width * scale
        const h = img.height * scale
        ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result as string
    }
    reader.readAsDataURL(file)
  })
}
