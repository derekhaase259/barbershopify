import type { Arrangement, DemoInfo, FitEntry, Score } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}: ${detail}`)
  }
  return res.json() as Promise<T>
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  listDemos: () => request<DemoInfo[]>('/api/demos'),
  listTestSongs: () => request<DemoInfo[]>('/api/test-songs'),
  arrangeDemo: (id: string, spice: number, duet: boolean) =>
    request<Arrangement>(`/api/demos/${id}/arrange`, post({ spice, duet })),
  arrangeTestSong: (id: string, spice: number, duet: boolean) =>
    request<Arrangement>(`/api/test-songs/${id}/arrange`, post({ spice, duet })),
  arrangeInput: (input: unknown, spice: number, duet: boolean) =>
    request<Arrangement>('/api/arrange', post({ input, spice, duet })),
  upload: (file: File, spice: number, duet: boolean) => {
    const form = new FormData()
    form.append('file', file)
    return request<Arrangement>(`/api/upload?spice=${spice}&duet=${duet}`, {
      method: 'POST',
      body: form,
    })
  },
  compose: (text: string, spice: number, seed: number) =>
    request<Arrangement>('/api/compose', post({ text, spice, seed })),
  setLyrics: (input: unknown, text: string, spice: number) =>
    request<Arrangement & { fit: FitEntry[] }>('/api/lyrics/set', post({ input, text, spice })),
  render: (score: Score) =>
    request<Pick<Arrangement, 'musicxml' | 'violations' | 'metrics'>>(
      '/api/render',
      post({ score }),
    ),
  exportMidi: async (score: Score): Promise<Blob> => {
    const res = await fetch('/api/export/midi', post({ score }))
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.blob()
  },
}
