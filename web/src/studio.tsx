import { useRef, useState, type FormEvent, type ChangeEvent } from 'react'
import {
  ArrowLeft, BookOpen, Check, ChevronRight, CloudUpload, FileAudio,
  FolderOpen, Headphones, LockKeyhole, Moon, ShieldCheck, Sun,
  Volume2, AlertCircle, RotateCcw
} from 'lucide-react'

type Chapter = {
  index: number
  title: string
  preview: string
  characters: number
  is_toc: boolean
}
type BookProject = {
  id: string
  title: string
  filename: string
  status: string
  chapter_count: number
  selected_start: number
  excluded_indices: number[]
  updated_at: string
  chapters?: Chapter[]
  suggested_start?: number
  warnings?: string[]
}
type VoiceCandidate = {
  id: string
  name: string
  description: string
  status: string
  license: string
  source: string
}
type ApiError = { detail?: string }
type View = 'library' | 'chapters' | 'voices' | 'export'

async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...init.headers },
    cache: 'no-store',
  })
  if (!response.ok) {
    let message = 'Die Anfrage ist fehlgeschlagen.'
    try {
      const payload = await response.json() as ApiError
      if (typeof payload.detail === 'string') message = payload.detail
    } catch { /* network or unexpected response */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: 'short', year: 'numeric',
  }).format(date)
}

export default function Studio() {
  // Access token is intentionally memory-only. No URL, cookies, or localStorage.
  const [token, setToken] = useState('')
  const [tokenInput, setTokenInput] = useState('')
  const [projects, setProjects] = useState<BookProject[]>([])
  const [active, setActive] = useState<BookProject | null>(null)
  const [view, setView] = useState<View>('library')
  const [voices, setVoices] = useState<VoiceCandidate[]>([])
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dark, setDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches)
  const [start, setStart] = useState(0)
  const [excluded, setExcluded] = useState<number[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  async function signIn(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      await api('/api/session', tokenInput)
      const items = await api<{projects: BookProject[]}>('/api/projects', tokenInput)
      setProjects(items.projects)
      setToken(tokenInput); setTokenInput('')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  function signOut() {
    setToken(''); setTokenInput(''); setProjects([]); setActive(null)
    setNotice(''); setError(''); setView('library')
  }

  async function refreshProjects() {
    const items = await api<{projects: BookProject[]}>('/api/projects', token)
    setProjects(items.projects)
  }

  async function openProject(id: string, next: View = 'chapters') {
    setBusy(true); setError(''); setNotice('')
    try {
      const project = await api<BookProject>(`/api/projects/${id}`, token)
      setActive(project)
      setStart(project.selected_start)
      setExcluded(project.excluded_indices)
      setView(next)
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    if (fileRef.current) fileRef.current.value = ''
    setBusy(true); setError(''); setNotice('')
    try {
      const form = new FormData()
      form.append('file', file)
      const project = await api<BookProject>('/api/projects', token, { method: 'POST', body: form })
      await refreshProjects()
      setActive(project)
      setStart(project.suggested_start ?? project.selected_start)
      setExcluded(project.chapters?.filter(c => c.is_toc).map(c => c.index) ?? [])
      setView('chapters')
      setNotice('EPUB erfolgreich analysiert. Bitte Vorlesebeginn und Kapitel prüfen.')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function saveChapters() {
    if (!active) return
    setSaving(true); setError(''); setNotice('')
    try {
      const project = await api<BookProject>(`/api/projects/${active.id}/chapters`, token, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({start_index: start, excluded_indices: excluded}),
      })
      setActive(project)
      setStart(project.selected_start); setExcluded(project.excluded_indices)
      await refreshProjects()
      setNotice('Kapitelauswahl im bestehenden Hörbuch-Projektformat gespeichert.')
    } catch (err) { setError((err as Error).message) }
    finally { setSaving(false) }
  }

  async function showVoices() {
    if (!active) return
    setBusy(true); setError('')
    try {
      const catalog = await api<{ready: VoiceCandidate[], candidates: VoiceCandidate[]}>('/api/voices', token)
      setVoices([...catalog.ready, ...catalog.candidates])
      setView('voices')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  const allChapters = active?.chapters ?? []
  const readChapters = allChapters.filter(c => c.index >= start && !c.is_toc && !excluded.includes(c.index))
  const selectedChapter = allChapters.find(c => c.index === start)

  function toggleExcluded(index: number) {
    if (index === start) return
    setExcluded(previous =>
      previous.includes(index) ? previous.filter(i => i !== index) : [...previous, index]
    )
  }

  return (
    <div className={`shell ${dark ? 'dark' : 'light'}`}>
      <a className="skip-link" href="#content">Zum Inhalt springen</a>
      <header className="topbar">
        <div className="brand"><span className="brand-mark"><Headphones size={23}/></span><span>German <strong>Audiobook Studio</strong></span></div>
        <div className="top-actions">
          <span className="subtle-label">Deutschsprachige Fachbücher</span>
          <button className="icon-btn" title={dark ? 'Helles Design' : 'Dunkles Design'} aria-label={dark ? 'Helles Design' : 'Dunkles Design'} onClick={() => setDark(v => !v)}>{dark ? <Sun size={19}/> : <Moon size={19}/>}</button>
          {token && <button className="text-btn" onClick={signOut}>Abmelden</button>}
        </div>
      </header>

      {!token ? (
        <main id="content" className="login-layout">
          <div className="login-intro"><p className="eyebrow">GERMAN AUDIOBOOK STUDIO</p><h1>Aus guten Büchern werden gute Hörbücher.</h1><p>Deutsche Fachliteratur strukturiert vorbereiten. Ohne unnötige Regler, ohne amerikanische Standardstimmen und mit vollständiger Kontrolle über den Vorlesebeginn.</p></div>
          <form className="panel login-panel" onSubmit={signIn}>
            <div className="circle-icon"><LockKeyhole size={23}/></div>
            <h2>Geschützter Zugang</h2>
            <p>Gib den Zugangsschlüssel ein, den du beim Start des Studios festgelegt hast.</p>
            <label htmlFor="access-token">Zugangsschlüssel</label>
            <input id="access-token" required type="password" minLength={32} autoComplete="off" value={tokenInput} onChange={e => setTokenInput(e.target.value)} placeholder="Mindestens 32 Zeichen" />
            {error && <p className="error" role="alert"><AlertCircle size={16}/>{error}</p>}
            <button type="submit" className="primary-btn" disabled={busy}>{busy ? 'Prüfe Zugang …' : 'Studio öffnen'} <ChevronRight size={18}/></button>
            <p className="security"><ShieldCheck size={15}/> Der Schlüssel wird nicht im Browser gespeichert.</p>
          </form>
        </main>
      ) : (
        <div className="work-layout">
          <aside className="sidebar" aria-label="Navigation">
            <div className="sidebar-heading">ARBEITSBEREICH</div>
            <button className={`nav-item ${view === 'library' ? 'selected' : ''}`} onClick={() => {setView('library'); setError(''); setNotice('')}}><FolderOpen size={19}/> Bibliothek</button>
            <div className="sidebar-heading second-heading">AKTUELLES BUCH</div>
            <button disabled={!active} className={`nav-item ${view === 'chapters' ? 'selected' : ''}`} onClick={() => setView('chapters')}><BookOpen size={19}/> Text und Kapitel</button>
            <button disabled={!active} className={`nav-item ${view === 'voices' ? 'selected' : ''}`} onClick={showVoices}><Volume2 size={19}/> Sprecher</button>
            <button disabled={!active} className={`nav-item ${view === 'export' ? 'selected' : ''}`} onClick={() => setView('export')}><FileAudio size={19}/> Hörbuch und Export</button>
            <div className="sidebar-bottom"><ShieldCheck size={16}/> Privat auf deinem Server</div>
          </aside>
          <main id="content" className="content">
            {error && <div className="message error" role="alert"><AlertCircle size={18}/><span>{error}</span><button className="close-message" onClick={() => setError('')} aria-label="Fehlermeldung schließen">×</button></div>}
            {notice && <div className="message success" role="status"><Check size={18}/><span>{notice}</span><button className="close-message" onClick={() => setNotice('')} aria-label="Hinweis schließen">×</button></div>}
            {view === 'library' && <>
              <div className="page-heading"><div><p className="eyebrow">DEINE BIBLIOTHEK</p><h1>Meine Hörbücher</h1><p>Verwalte deine Projekte und bereite neue deutsche EPUB-Dateien vor.</p></div><button className="primary-btn" onClick={() => fileRef.current?.click()} disabled={busy}><CloudUpload size={18}/> {busy ? 'Analysiere EPUB …' : 'EPUB importieren'}</button></div>
              <input ref={fileRef} className="visually-hidden" type="file" accept=".epub,application/epub+zip" onChange={upload} aria-label="EPUB-Datei auswählen" />
              <div className="stat-row"><div className="stat"><span>Projekte</span><strong>{projects.length}</strong></div><div className="stat"><span>Für Vertonung vorbereitet</span><strong>{projects.filter(p => p.status === 'Vorbereitet').length}</strong></div><div className="stat"><span>Bedienung</span><strong>Deutsch</strong></div></div>
              <h2 className="section-heading">Projekte</h2>
              {projects.length === 0 ? (
                <div className="panel empty-state"><div className="circle-icon"><BookOpen size={25}/></div><h3>Deine Bibliothek ist noch leer</h3><p>Importiere eine EPUB-Datei. Das Studio erkennt die Kapitel und schlägt dir den passenden Einstieg vor.</p><button className="outline-btn" onClick={() => fileRef.current?.click()} disabled={busy}><CloudUpload size={17}/> Erste EPUB auswählen</button></div>
              ) : <div className="book-grid">{projects.map(p => <button key={p.id} className="book-card" onClick={() => openProject(p.id)} disabled={busy}>
                <div className="book-cover"><BookOpen size={32}/><span>EPUB</span></div>
                <span className="book-info"><strong>{p.title}</strong><small>{p.chapter_count} Abschnitte · {formatDate(p.updated_at)}</small><em className="status-chip">{p.status}</em></span><ChevronRight size={19}/>
              </button>)}</div>}
            </>}
            {view === 'chapters' && active && <>
              <div className="page-heading"><div><button className="back-link" onClick={() => setView('library')}><ArrowLeft size={16}/> Bibliothek</button><p className="eyebrow">KAPITEL VORBEREITEN</p><h1>{active.title}</h1><p>Lege fest, welcher Abschnitt als Erstes vorgelesen werden soll.</p></div></div>
              {active.warnings && active.warnings.length > 0 && <details className="warning-details"><summary><AlertCircle size={17}/> {active.warnings.length} Hinweise zur EPUB-Struktur</summary><ul>{active.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
              <div className="chapter-layout">
                <section className="panel chapter-panel"><div className="panel-head"><h2>Kapitelübersicht</h2><span className="count">{allChapters.length} Abschnitte</span></div>
                  <p className="helper">Wähle den Vorlesebeginn. Frühere Abschnitte und Inhaltsverzeichnisse werden nicht vorgelesen.</p>
                  <label htmlFor="start-chapter">Erster vorgelesener Abschnitt</label>
                  <select id="start-chapter" value={start} onChange={e => {const next = Number(e.target.value);setStart(next);setExcluded(v => v.filter(i => i !== next))}}>
                    {allChapters.filter(c => !c.is_toc).map(c => <option key={c.index} value={c.index}>{c.index + 1}. {c.title}{c.index === active.suggested_start ? ' (Empfehlung)' : ''}</option>)}
                  </select>
                  <div className="chapter-list" aria-label="Kapitel zur Vertonung">
                    {allChapters.map(c => {
                      const before = c.index < start
                      const checked = !before && !c.is_toc && !excluded.includes(c.index)
                      return <label key={c.index} className={`chapter-item ${checked ? 'included' : 'excluded'}`}>
                        <input type="checkbox" checked={checked} disabled={before || c.is_toc || c.index === start} onChange={() => toggleExcluded(c.index)} />
                        <span className="chapter-index">{String(c.index + 1).padStart(2, '0')}</span>
                        <span className="chapter-label"><strong>{c.title}</strong><small>{c.is_toc ? 'Inhaltsverzeichnis ausgeschlossen' : before ? 'Vor dem Startabschnitt' : c.characters.toLocaleString('de-DE') + ' Zeichen'}</small></span>
                        {c.index === start && <span className="recommend">Start</span>}
                      </label>
                    })}
                  </div>
                </section>
                <section className="panel preview-panel"><div className="panel-head"><h2>Textvorschau</h2><span className="preview-dot"/></div><p className="helper">Dies ist der Anfang des tatsächlichen Vorlesetextes, nicht die EPUB-Navigation.</p><h3>{selectedChapter?.title}</h3><div className="reading-preview">{selectedChapter?.preview || 'Kein lesbarer Text vorhanden.'}</div><div className="preview-footer"><span>{readChapters.length} ausgewählte Abschnitte</span><span>Ausgabesprache: Deutsch</span></div></section>
              </div>
              <div className="action-bar"><span><ShieldCheck size={16}/> Originaldatei bleibt privat auf dem Server.</span><div><button className="outline-btn" disabled={saving || busy} onClick={() => openProject(active.id)}><RotateCcw size={16}/> Zurücksetzen</button><button className="primary-btn" disabled={saving || readChapters.length === 0} onClick={saveChapters}>{saving ? 'Speichere …' : 'Auswahl speichern'}<Check size={18}/></button></div></div>
            </>}
            {view === 'voices' && active && <>
              <div className="page-heading"><div><p className="eyebrow">SPRECHERWAHL</p><h1>Die passende deutsche Stimme</h1><p>Nur rechtlich geprüfte, fertig konfigurierte Stimmen werden zur Auswahl freigeschaltet.</p></div></div>
              <div className="panel voice-notice"><LockKeyhole size={21}/><div><strong>Sprecher noch nicht freigegeben</strong><p>Der erste deutsche Referenzsprecher wird technisch und rechtlich geprüft. Ohne verifizierte Aufnahme, passendes Transkript und tatsächliche deutsche Hörprobe wird keine künstliche Ersatzstimme angeboten.</p></div></div>
              <h2 className="section-heading">Kandidaten in Prüfung</h2>
              <div className="voice-grid">{voices.map(voice => <div className="panel voice-card" key={voice.id}><div className="voice-avatar"><Volume2 size={26}/></div><div><span className="status-chip">Prüfung offen</span><h3>{voice.name}</h3><p>{voice.description}</p><small>Quelle: <a href={voice.source} target="_blank" rel="noopener noreferrer">{voice.license}</a></small></div></div>)}</div>
            </>}
            {view === 'export' && active && <>
              <div className="page-heading"><div><p className="eyebrow">HÖRBUCH ERSTELLEN</p><h1>Generieren und exportieren</h1><p>Projekt: {active.title}</p></div></div>
              <div className="panel empty-state"><div className="circle-icon"><FileAudio size={26}/></div><h2>Generierung noch nicht freigeschaltet</h2><p>Eine reale GPU-Worker-Integration und eine freigegebene deutsche Sprecherreferenz fehlen noch. Die Buchvorbereitung funktioniert bereits. Es wird keine Schein-Generierung gestartet.</p><button className="outline-btn" onClick={() => setView('chapters')}><BookOpen size={17}/> Kapitelauswahl bearbeiten</button></div>
            </>}
            <footer className="footer">German Audiobook Studio <span>·</span> Fachbücher. Deutsche Sprachausgabe. Geschützte Verarbeitung.</footer>
          </main>
        </div>
      )}
    </div>
  )
}
