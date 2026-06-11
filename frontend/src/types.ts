// Mirrors backend/barbershop/score.py — the score JSON is the single
// source of truth; everything the UI shows is a projection of it.

export type VoiceName = 'tenor' | 'lead' | 'bari' | 'bass'

export interface Lyric {
  text: string
  syllabic: 'single' | 'begin' | 'middle' | 'end'
  extend: boolean
}

export interface Note {
  onset: number // ticks, 480 per quarter
  duration: number
  midi: number
  lyric: Lyric | null
}

export interface KeySig {
  fifths: number
  mode: 'major' | 'minor'
}

export interface TimeSig {
  beats: number
  beat_type: number
}

export interface ChordSpan {
  onset: number
  duration: number
  root_pc: number
  quality: string
}

export interface Score {
  title: string
  key: KeySig
  time: TimeSig
  tempo: number
  voices: Record<VoiceName, Note[]>
  chords: ChordSpan[]
}

export interface DemoInfo {
  id: string
  title: string
}

export interface FitEntry {
  phrase: number
  status: 'green' | 'yellow' | 'red'
  syllables: number
  notes: number
  detail: string
}

export interface Arrangement {
  input: unknown // opaque ArrangeInput JSON; lets re-arrange skip analysis
  score: Score
  musicxml: string
  violations: string[]
  metrics: {
    dom7_family_share: number
    bass_root_fifth_share: number
    final_chord_ring: boolean
    input_adherence?: number | null
  }
  lyrics?: { source: 'asr' | 'neutral' | 'lrclib' | 'none'; confidence: number }
  identity?: {
    title: string
    artist: string
    year: number | null
    recording_mbid: string
    match_score: number
  } | null
  chords?: { source: 'audio' | 'tab'; agreement: number | null; tab_url: string | null }
  composition?: {
    mode: 'major' | 'minor'
    tempo: number
    foot: string
    schemes: string[]
    valence: number
    arousal: number
    seed: number
  }
}
