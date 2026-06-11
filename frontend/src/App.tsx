import { useCallback, useEffect, useRef, useState } from 'react'
import { ScoreView } from './ScoreView'
import { TransportBar } from './TransportBar'
import { useStore } from './store'

function PoemPanel() {
  const stage = useStore((s) => s.stage)
  const arrangement = useStore((s) => s.arrangement)
  const composePoem = useStore((s) => s.composePoem)
  const [text, setText] = useState('')
  const busy = stage === 'arranging' || stage === 'analyzing'
  const composed = arrangement?.composition

  return (
    <div className="lyrics-panel">
      <h2>From a Poem</h2>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'No audio needed — paste a poem.\nLine breaks shape the phrases; blank lines split stanzas.'}
        rows={5}
      />
      <button
        className="rearrange"
        disabled={!text.trim() || busy}
        onClick={() => void composePoem(text)}
      >
        Compose
      </button>
      {composed && (
        <>
          <button
            className="rearrange recompose"
            disabled={!text.trim() || busy}
            onClick={() => void composePoem(text, true)}
          >
            Re-compose ↻
          </button>
          <dl className="fine-print">
            <dt>Mode</dt>
            <dd>{composed.mode}</dd>
            <dt>Foot</dt>
            <dd>{composed.foot}</dd>
            <dt>Rhyme</dt>
            <dd>{composed.schemes.join(' ')}</dd>
            <dt>Valence / arousal</dt>
            <dd>
              {composed.valence} / {composed.arousal}
            </dd>
          </dl>
        </>
      )}
    </div>
  )
}

function LyricsPanel() {
  const arrangement = useStore((s) => s.arrangement)
  const lyricsFit = useStore((s) => s.lyricsFit)
  const stage = useStore((s) => s.stage)
  const applyLyrics = useStore((s) => s.applyLyrics)
  const [text, setText] = useState('')

  if (!arrangement) return null
  return (
    <div className="lyrics-panel">
      <h2>Lyrics</h2>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'Paste lyrics here — one line per musical phrase.\nLine breaks matter: they are how phrases are matched.'}
        rows={5}
      />
      <button
        className="rearrange"
        disabled={!text.trim() || stage !== 'ready'}
        onClick={() => void applyLyrics(text)}
      >
        Set lyrics
      </button>
      {lyricsFit && (
        <ul className="fit-list">
          {lyricsFit.map((f) => (
            <li key={f.phrase} className={`fit ${f.status}`} title={f.detail}>
              phrase {f.phrase}: {f.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SelectedLyricEditor() {
  const selected = useStore((s) => s.selected)
  const arrangement = useStore((s) => s.arrangement)
  const editSelectedLyric = useStore((s) => s.editSelectedLyric)
  const note =
    selected && arrangement
      ? arrangement.score.voices[selected.voice].find((n) => n.onset === selected.onset)
      : null
  const [draft, setDraft] = useState<string | null>(null)

  useEffect(() => setDraft(null), [selected])
  if (!selected || !note || selected.voice !== 'lead') return null
  const value = draft ?? note.lyric?.text ?? ''
  return (
    <label className="syllable-editor">
      Syllable under selected note
      <input
        value={value}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            void editSelectedLyric(value)
            setDraft(null)
          }
        }}
        onBlur={() => {
          if (draft !== null && draft !== (note.lyric?.text ?? '')) {
            void editSelectedLyric(draft)
          }
          setDraft(null)
        }}
        placeholder="(no syllable)"
      />
    </label>
  )
}

const SPICE_LABELS: Record<number, string> = {
  1: 'faithful',
  2: 'gentle',
  3: 'classic',
  4: 'adventurous',
  5: 'contest showpiece',
}

const STAGE_COPY: Record<string, string> = {
  idle: 'Pick a number from the programme, drop a record on the Victrola, or bring your own.',
  analyzing:
    'Listening closely — finding the beat, the key, the tune… (~30s; the very first analysis also downloads a ~150 MB speech model, which can take a few minutes)',
  arranging: 'Arranging four parts…',
  rendering: 'Engraving the score…',
  ready: '',
  error: 'Something went wrong.',
}

function UploadSlot() {
  const uploadFile = useStore((s) => s.uploadFile)
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0]
      if (file) void uploadFile(file)
    },
    [uploadFile],
  )

  return (
    <div
      className={over ? 'upload-slot over' : 'upload-slot'}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        onFiles(e.dataTransfer.files)
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".mp3,.m4a,.wav,audio/*"
        hidden
        onChange={(e) => onFiles(e.target.files)}
      />
      Drop a song here
      <span className="upload-hint">.mp3 / .m4a / .wav — or click to browse</span>
    </div>
  )
}

export default function App() {
  const { demos, testSongs, source, spice, stage, error, arrangement } = useStore()
  const { loadDemos, setSpice, arrangeSource, rearrange, downloadMusicXml, downloadMidi } =
    useStore()

  useEffect(() => {
    void loadDemos()
  }, [loadDemos])

  const isSelected = (kind: string, id: string) =>
    source !== null && 'id' in source && source.kind === kind && source.id === id

  return (
    <div className="hall">
      <header className="marquee">
        <h1>Barbershopify</h1>
        <p className="tagline">Four-part harmony, freshly lathered</p>
        <div className="pole-stripe" aria-hidden="true" />
      </header>

      <div className="floor">
        <aside className="programme">
          <h2>Programme</h2>
          <ul className="bill">
            {demos.map((d) => (
              <li key={d.id}>
                <button
                  className={isSelected('demo', d.id) ? 'act selected' : 'act'}
                  onClick={() => void arrangeSource({ kind: 'demo', id: d.id })}
                >
                  {d.title}
                </button>
              </li>
            ))}
          </ul>

          <h2>Victrola</h2>
          <ul className="bill">
            {testSongs.map((s) => (
              <li key={s.id}>
                <button
                  className={isSelected('song', s.id) ? 'act selected' : 'act'}
                  onClick={() => void arrangeSource({ kind: 'song', id: s.id })}
                >
                  {s.title}
                </button>
              </li>
            ))}
          </ul>
          <UploadSlot />

          <PoemPanel />

          <h2>Spice</h2>
          <div className="spice-control">
            <input
              type="range"
              min={1}
              max={5}
              step={1}
              value={spice}
              onChange={(e) => setSpice(Number(e.target.value))}
              aria-label="Spice level"
            />
            <div className="spice-readout">
              <span className="spice-number">{spice}</span>
              <span className="spice-label">{SPICE_LABELS[spice]}</span>
            </div>
          </div>
          <button
            className="rearrange"
            disabled={!arrangement || stage === 'arranging' || stage === 'analyzing'}
            onClick={() => void rearrange()}
          >
            Re-arrange
          </button>

          <LyricsPanel />
          <SelectedLyricEditor />

          {arrangement && stage === 'ready' && (
            <div className="exports">
              <button className="export-btn" onClick={downloadMusicXml}>
                ⤓ MusicXML
              </button>
              <button className="export-btn" onClick={() => void downloadMidi()}>
                ⤓ MIDI
              </button>
            </div>
          )}

          {arrangement && stage === 'ready' && (
            <dl className="fine-print">
              {arrangement.identity && (
                <>
                  <dt>Identified</dt>
                  <dd>
                    ♪ {arrangement.identity.title} — {arrangement.identity.artist}
                    {arrangement.identity.year ? ` (${arrangement.identity.year})` : ''}
                  </dd>
                </>
              )}
              <dt>Dominant 7th share</dt>
              <dd>{Math.round(arrangement.metrics.dom7_family_share * 100)}%</dd>
              <dt>Bass on root/5th</dt>
              <dd>{Math.round(arrangement.metrics.bass_root_fifth_share * 100)}%</dd>
              <dt>Final chord rings</dt>
              <dd>{arrangement.metrics.final_chord_ring ? 'yes' : 'no'}</dd>
              <dt>Legality</dt>
              <dd>
                {arrangement.violations.length === 0
                  ? 'clean'
                  : `${arrangement.violations.length} violations`}
              </dd>
              {arrangement.lyrics && arrangement.lyrics.source !== 'none' && (
                <>
                  <dt>Lyrics</dt>
                  <dd>
                    {arrangement.lyrics.source === 'lrclib'
                      ? 'looked up (LRCLIB)'
                      : arrangement.lyrics.source === 'asr'
                        ? `heard (${Math.round(arrangement.lyrics.confidence * 100)}% sure)`
                        : 'doo/dah (transcription unclear)'}
                  </dd>
                </>
              )}
            </dl>
          )}
        </aside>

        <main className="stage">
          {stage !== 'ready' && (
            <p className={stage === 'error' ? 'status error' : 'status'}>
              {stage === 'error' && error ? `${STAGE_COPY.error} ${error}` : STAGE_COPY[stage]}
            </p>
          )}
          <TransportBar />
          <ScoreView />
        </main>
      </div>
    </div>
  )
}
