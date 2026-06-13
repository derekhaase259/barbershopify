import { create } from 'zustand'
import { api } from './api'
import { engine, VOICE_ORDER } from './playback/engine'
import type { Arrangement, DemoInfo, FitEntry, Score, VoiceName } from './types'

export interface Selection {
  voice: VoiceName
  onset: number
}

const MAX_UNDO = 100

const cloneScore = (s: Score): Score => structuredClone(s)

export type Stage = 'idle' | 'analyzing' | 'arranging' | 'rendering' | 'ready' | 'error'

export type Source = { kind: 'demo' | 'song'; id: string } | { kind: 'upload'; name: string }

export interface VoiceSettings {
  mute: boolean
  solo: boolean
  volume: number // dB
}

const defaultVoiceSettings = (): Record<VoiceName, VoiceSettings> =>
  Object.fromEntries(
    VOICE_ORDER.map((v) => [v, { mute: false, solo: false, volume: -8 }]),
  ) as Record<VoiceName, VoiceSettings>

interface AppState {
  demos: DemoInfo[]
  testSongs: DemoInfo[]
  source: Source | null
  spice: number
  duet: boolean
  stage: Stage
  error: string | null
  arrangement: Arrangement | null

  playing: boolean
  currentTick: number | null
  tempoBpm: number
  tuning: 'just' | 'equal'
  voiceSettings: Record<VoiceName, VoiceSettings>

  selected: Selection | null
  undoStack: Score[]
  redoStack: Score[]
  lyricsFit: FitEntry[] | null

  loadDemos: () => Promise<void>
  setSpice: (spice: number) => void
  setDuet: (duet: boolean) => void
  arrangeSource: (source: Source) => Promise<void>
  rearrange: () => Promise<void>
  uploadFile: (file: File) => Promise<void>
  setStage: (stage: Stage) => void

  play: () => Promise<void>
  pause: () => void
  stop: () => void
  setTempo: (bpm: number) => void
  setTuning: (tuning: 'just' | 'equal') => void
  toggleMute: (voice: VoiceName) => void
  toggleSolo: (voice: VoiceName) => void
  setVolume: (voice: VoiceName, db: number) => void

  composePoem: (text: string, newSeed?: boolean) => Promise<void>
  applyLyrics: (text: string) => Promise<void>
  editSelectedLyric: (text: string) => Promise<void>
  selectNote: (selection: Selection | null) => void
  nudgePitch: (semitones: number) => Promise<void>
  setDuration: (ticks: number) => Promise<void>
  deleteSelected: () => Promise<void>
  undo: () => Promise<void>
  redo: () => Promise<void>
  downloadMusicXml: () => void
  downloadMidi: () => Promise<void>
}

export const useStore = create<AppState>((set, get) => {
  engine.setCallbacks({
    onTick: (tick) => set({ currentTick: tick }),
    onStateChange: (playing) => set({ playing }),
  })

  const finish = (arrangement: Arrangement) => {
    engine.setScore(arrangement.score)
    set({
      arrangement,
      stage: 'rendering',
      tempoBpm: arrangement.score.tempo,
      selected: null,
      undoStack: [],
      redoStack: [],
      lyricsFit: null,
    })
  }

  const fail = (err: unknown) =>
    set({ stage: 'error', error: err instanceof Error ? err.message : String(err) })

  /** Apply a mutation to a fresh copy of the score, re-render, revalidate. */
  const applyScore = async (next: Score, pushUndo: boolean) => {
    const arrangement = get().arrangement
    if (!arrangement) return
    if (pushUndo) {
      set({
        undoStack: [...get().undoStack.slice(-MAX_UNDO + 1), arrangement.score],
        redoStack: [],
      })
    }
    const rendered = await api.render(next)
    engine.setScore(next)
    set({
      arrangement: { ...arrangement, score: next, ...rendered },
      stage: 'rendering',
    })
  }

  const selectedNote = (score: Score) => {
    const sel = get().selected
    if (!sel) return null
    const idx = score.voices[sel.voice].findIndex((n) => n.onset === sel.onset)
    return idx >= 0 ? { sel, idx } : null
  }

  return {
    demos: [],
    testSongs: [],
    source: null,
    spice: 3,
    duet: false,
    stage: 'idle',
    error: null,
    arrangement: null,

    playing: false,
    currentTick: null,
    tempoBpm: 100,
    tuning: 'just',
    voiceSettings: defaultVoiceSettings(),

    selected: null,
    undoStack: [],
    redoStack: [],
    lyricsFit: null,

    loadDemos: async () => {
      const [demos, testSongs] = await Promise.all([api.listDemos(), api.listTestSongs()])
      set({ demos, testSongs })
    },

    setSpice: (spice) => set({ spice }),
    setDuet: (duet) => set({ duet }),

    arrangeSource: async (source) => {
      if (source.kind === 'upload') return
      set({ source, stage: source.kind === 'song' ? 'analyzing' : 'arranging', error: null })
      try {
        const arrangement =
          source.kind === 'demo'
            ? await api.arrangeDemo(source.id, get().spice, get().duet)
            : await api.arrangeTestSong(source.id, get().spice, get().duet)
        finish(arrangement)
      } catch (err) {
        fail(err)
      }
    },

    rearrange: async () => {
      const input = get().arrangement?.input
      if (!input) return
      set({ stage: 'arranging', error: null })
      try {
        finish(await api.arrangeInput(input, get().spice, get().duet))
      } catch (err) {
        fail(err)
      }
    },

    uploadFile: async (file) => {
      set({ source: { kind: 'upload', name: file.name }, stage: 'analyzing', error: null })
      try {
        finish(await api.upload(file, get().spice, get().duet))
      } catch (err) {
        fail(err)
      }
    },

    setStage: (stage) => set({ stage }),

    play: () => engine.play(),
    pause: () => engine.pause(),
    stop: () => engine.stop(),

    setTempo: (bpm) => {
      engine.setTempo(bpm)
      set({ tempoBpm: bpm })
    },

    setTuning: (tuning) => {
      engine.tuning = tuning
      set({ tuning })
    },

    toggleMute: (voice) => {
      const settings = { ...get().voiceSettings }
      settings[voice] = { ...settings[voice], mute: !settings[voice].mute }
      engine.setMute(voice, settings[voice].mute)
      set({ voiceSettings: settings })
    },

    toggleSolo: (voice) => {
      const settings = { ...get().voiceSettings }
      settings[voice] = { ...settings[voice], solo: !settings[voice].solo }
      engine.setSolo(voice, settings[voice].solo)
      set({ voiceSettings: settings })
    },

    setVolume: (voice, db) => {
      const settings = { ...get().voiceSettings }
      settings[voice] = { ...settings[voice], volume: db }
      engine.setVolume(voice, db)
      set({ voiceSettings: settings })
    },

    composePoem: async (text, newSeed = false) => {
      if (!text.trim()) return
      const prev = get().arrangement?.composition?.seed ?? 0
      const seed = newSeed ? prev + 1 : prev
      set({ source: { kind: 'upload', name: 'poem' }, stage: 'arranging', error: null })
      try {
        finish(await api.compose(text, get().spice, seed))
      } catch (err) {
        fail(err)
      }
    },

    applyLyrics: async (text) => {
      const arrangement = get().arrangement
      if (!arrangement || !text.trim()) return
      set({ stage: 'arranging', error: null })
      try {
        const result = await api.setLyrics(arrangement.input, text, get().spice)
        finish(result)
        set({ lyricsFit: result.fit })
      } catch (err) {
        fail(err)
      }
    },

    editSelectedLyric: async (text) => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const next = cloneScore(arrangement.score)
      const found = selectedNote(next)
      if (!found) return
      const note = next.voices[found.sel.voice][found.idx]
      if (text.trim()) {
        note.lyric = note.lyric
          ? { ...note.lyric, text: text.trim() }
          : { text: text.trim(), syllabic: 'single', extend: false }
      } else {
        note.lyric = null
      }
      await applyScore(next, true)
    },

    selectNote: (selection) => set({ selected: selection }),

    nudgePitch: async (semitones) => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const next = cloneScore(arrangement.score)
      const found = selectedNote(next)
      if (!found) return
      const note = next.voices[found.sel.voice][found.idx]
      const midi = Math.max(24, Math.min(96, note.midi + semitones))
      if (midi === note.midi) return
      note.midi = midi
      void engine.preview(found.sel.voice, midi)
      await applyScore(next, true)
    },

    setDuration: async (ticks) => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const next = cloneScore(arrangement.score)
      const found = selectedNote(next)
      if (!found) return
      const notes = next.voices[found.sel.voice]
      const note = notes[found.idx]
      const following = notes[found.idx + 1]
      const max = following ? following.onset - note.onset : Number.MAX_SAFE_INTEGER
      note.duration = Math.max(120, Math.min(ticks, max))
      await applyScore(next, true)
    },

    deleteSelected: async () => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const next = cloneScore(arrangement.score)
      const found = selectedNote(next)
      if (!found) return
      next.voices[found.sel.voice].splice(found.idx, 1)
      set({ selected: null })
      await applyScore(next, true)
    },

    undo: async () => {
      const stack = get().undoStack
      const arrangement = get().arrangement
      if (!stack.length || !arrangement) return
      const prev = stack[stack.length - 1]
      set({
        undoStack: stack.slice(0, -1),
        redoStack: [...get().redoStack, arrangement.score],
        selected: null,
      })
      await applyScore(prev, false)
    },

    redo: async () => {
      const stack = get().redoStack
      const arrangement = get().arrangement
      if (!stack.length || !arrangement) return
      const next = stack[stack.length - 1]
      set({
        redoStack: stack.slice(0, -1),
        undoStack: [...get().undoStack, arrangement.score],
        selected: null,
      })
      await applyScore(next, false)
    },

    downloadMusicXml: () => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const blob = new Blob([arrangement.musicxml], { type: 'application/vnd.recordare.musicxml+xml' })
      triggerDownload(blob, `${arrangement.score.title || 'arrangement'}.musicxml`)
    },

    downloadMidi: async () => {
      const arrangement = get().arrangement
      if (!arrangement) return
      const blob = await api.exportMidi(arrangement.score)
      triggerDownload(blob, `${arrangement.score.title || 'arrangement'}.mid`)
    },
  }
})

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
