import { useEffect, useRef, useState, type FormEvent, type ChangeEvent } from 'react'
import {
  ArrowLeft, BookOpen, Check, ChevronRight, CloudUpload, FileAudio,
  FolderOpen, Headphones, LockKeyhole, Moon, ShieldCheck, Sun,
  Volume2, AlertCircle, RotateCcw, Trash2, PlayCircle, PauseCircle, StopCircle
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
type View = 'library' | 'chapters' | 'voices' | 'export' | 'glossary'
type Job = {
  id: string
  project_id: string
  status: string
  done: number
  total: number
  chapter: string
  duration: number
  error: string
}
type Capabilities = {
  epub_import: boolean
  chapter_selection: boolean
  tts_generation: boolean
  audio_export: boolean
  voice_preview_available: boolean
  reason: string
}

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
  const [job, setJob] = useState<Job | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [glossary, setGlossary] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [previewPlayed, setPreviewPlayed] = useState(false)
  const [sampleIndex, setSampleIndex] = useState(0)

  async function refreshJob(id: string, projectId: string) {
    const latest = await api<Job>(`/api/projects/${projectId}/jobs/${id}`, token)
    setJob(latest)
    if (['generated', 'completed', 'failed', 'cancelled'].includes(latest.status)) {
      await refreshProjects()
    }
  }

  useEffect(() => {
    if (!token || !active || !job || !['queued', 'running', 'exporting'].includes(job.status)) return
    const interval = window.setInterval(() => {
      void refreshJob(job.id, active.id).catch(err => setError((err as Error).message))
    }, 3000)
    return () => window.clearInterval(interval)
  }, [token, active?.id, job?.id, job?.status])

  async function loadAudio(path: string, method = 'GET') {
    setBusy(true); setError('')
    try {
      const response = await fetch(path, {method, headers: {Authorization: `Bearer ${token}`}, cache: 'no-store'})
      if (!response.ok) {
        const payload = await response.json() as ApiError
        throw new Error(payload.detail ?? 'Die Audiodatei ist nicht verfügbar.')
      }
      const url = URL.createObjectURL(await response.blob())
      setAudioUrl(previous => { if (previous) URL.revokeObjectURL(previous); return url })
      setPreviewPlayed(true)
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function loadGlossary() {
    if (!active) return
    setBusy(true); setError('')
    try {
      const response = await api<{entries: Record<string, string>}>(`/api/projects/${active.id}/glossary`, token)
      setGlossary(Object.entries(response.entries).map(([key, value]) => `${key} = ${value}`).join('\n'))
      setView('glossary')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function saveGlossary() {
    if (!active) return
    const entries: Record<string, string> = {}
    for (const line of glossary.split('\n').map(s => s.trim()).filter(Boolean)) {
      const separator = line.indexOf('=')
      if (separator < 1 || separator === line.length - 1) {
        setError('Bitte das Format Begriff = Sprechweise verwenden.')
        return
      }
      entries[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
    }
    setSaving(true); setError('')
    try {
      await api(`/api/projects/${active.id}/glossary`, token, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entries})})
      setNotice('Das bestehende Projekt-Ausspracheglossar wurde aktualisiert.')
    } catch (err) { setError((err as Error).message) }
    finally { setSaving(false) }
  }

  async function refreshProduction(projectId: string) {
    const [list, caps] = await Promise.all([
      api<{jobs: Job[]}>(`/api/projects/${projectId}/jobs`, token),
      api<Capabilities>('/api/capabilities', token),
    ])
    setJob(list.jobs[0] ?? null); setCapabilities(caps)
  }

  async function productionAction(action: 'start'|'pause'|'resume'|'cancel'|'export') {
    if (!active) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (action === 'start') {
        const next = await api<Job>(`/api/projects/${active.id}/jobs`, token, {method: 'POST'})
        setJob(next)
      } else if (action === 'export' && job) {
        await api(`/api/projects/${active.id}/jobs/${job.id}/export`, token, {method: 'POST'})
        await refreshJob(job.id, active.id)
      } else if (job) {
        const updated = await api<Job>(`/api/projects/${active.id}/jobs/${job.id}/control`, token, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action}),
        })
        setJob(updated)
      }
      await refreshProjects()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function deleteProject(id: string) {
    if (!window.confirm('Dieses Buch und alle erzeugten Dateien endgültig löschen?')) return
    setBusy(true); setError('')
    try {
      await api(`/api/projects/${id}`, token, {method: 'DELETE'})
      if (active?.id === id) {setActive(null); setJob(null);setView('library')}
      await refreshProjects()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function openProduction() {
    if (!active) return
    setBusy(true); setError('')
    try { await refreshProduction(active.id); setView('export') }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

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
    if (audioUrl) URL.revokeObjectURL(audioUrl)
    setAudioUrl(''); setJob(null); setNotice(''); setError(''); setView('library')
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
      if (next === 'export') await refreshProduction(id)
      setJob(null)
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
      setPreviewPlayed(false)
      setCapabilities(await api<Capabilities>('/api/capabilities', token))
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
            <button disabled={!active} className={`nav-item ${view === 'glossary' ? 'selected' : ''}`} onClick={loadGlossary}><BookOpen size={19}/> Ausspracheglossar</button>
            <button disabled={!active} className={`nav-item ${view === 'export' ? 'selected' : ''}`} onClick={openProduction}><FileAudio size={19}/> Hörbuch und Export</button>
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
              ) : <div className="book-grid">{projects.map(p => <div key={p.id} className="book-card"><button className="book-open" onClick={() => openProject(p.id)} disabled={busy}>
                <div className="book-cover"><BookOpen size={32}/><span>EPUB</span></div>
                <span className="book-info"><strong>{p.title}</strong><small>{p.chapter_count} Abschnitte · {formatDate(p.updated_at)}</small><em className="status-chip">{p.status}</em></span><ChevronRight size={19}/>
              </button><button className="icon-btn delete-project" title="Projekt löschen" aria-label={`Projekt ${p.title} löschen`} onClick={() => deleteProject(p.id)} disabled={busy}><Trash2 size={18}/></button></div>)}</div>}
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
            {view === 'glossary' && active && <>
              <div className="page-heading"><div><p className="eyebrow">SPRACHEINSTELLUNGEN</p><h1>Ausspracheglossar</h1><p>Pro Begriff eine Zeile. Gesprochene Form definieren, ohne den Originaltext zu verändern.</p></div></div>
              <div className="panel glossary-panel">
                <label htmlFor="glossary">Begriff = gesprochene Form</label>
                <textarea id="glossary" rows={12} value={glossary} onChange={e => setGlossary(e.target.value)} placeholder={'MEDDPICC = M E D D P I C C\nSaaS = Säss'} spellCheck={false}/>
                <p className="helper">Nur gezielt geprüfte Fachbegriffe eintragen. Die Originalfassung des Buchs bleibt unverändert. Dieses Glossar nutzt die vorhandenen Wortersetzungen des Projektmodells.</p>
                <button className="primary-btn" disabled={saving || !['Vorbereitet','Fertig','Audio bereit','Entwurf'].includes(active.status)} onClick={saveGlossary}>{saving ? 'Speichere …' : 'Glossar speichern'}<Check size={18}/></button>
              </div>
            </>}
            {view === 'voices' && active && <>
              <div className="page-heading"><div><p className="eyebrow">SPRECHERWAHL</p><h1>Deutsche Erzählerstimme</h1><p>Ausschließlich fertig trainierte Stimmen, keine hochgeladenen Referenzen oder Stimmklone.</p></div></div>
              {voices.every(voice => voice.status !== 'ready') && <div className="panel voice-notice"><LockKeyhole size={21}/><div><strong>Sprecherfreigabe erforderlich</strong><p>Thorsten-CosyVoice3 muss lokal installiert werden. Erzeuge die echte Hörprobe, höre sie vollständig an und bestätige erst danach die Freigabe. Ohne installierte Engine ist die Produktion gesperrt.</p></div></div>}
              <div className="voice-grid">{voices.map(voice => <div className="panel voice-card" key={voice.id}><div className="voice-avatar"><Volume2 size={26}/></div><div><span className="status-chip">{voice.status === 'ready' ? 'Freigegeben' : 'Freigabe offen'}</span><h3>{voice.name}</h3><p>{voice.description}</p><small>Quelle und Lizenz: <a href={voice.source} target="_blank" rel="noopener noreferrer">{voice.license}</a></small>
                <div className="voice-actions"><button className="outline-btn" disabled={busy || !capabilities?.voice_preview_available} onClick={() => {setPreviewPlayed(false);void loadAudio('/api/voices/preview','POST')}}><PlayCircle size={17}/> Echte Hörprobe erzeugen</button>
                <button className="primary-btn" disabled={busy || !previewPlayed} onClick={async () => {
                  try { await api('/api/voices/approve', token, {method:'POST'}); await showVoices(); setNotice('Stimme nach manueller Hörabnahme freigegeben.') }
                  catch (err) { setError((err as Error).message) }
                }}><Check size={17}/> Nach Anhören freigeben</button></div>
              </div></div>)}</div>
              {audioUrl && <div className="panel audio-preview"><h3>Hörprobe</h3><audio src={audioUrl} controls onPlay={() => setPreviewPlayed(true)}/><p className="helper">Prüfe Akzent, Stimmstabilität, Betonung und Verständlichkeit. Die Freigabe ist eine manuelle Qualitätsentscheidung.</p></div>}
            </>}
            {view === 'export' && active && <>
              <div className="page-heading"><div><p className="eyebrow">PRODUKTION UND EXPORT</p><h1>Hörbuch erstellen</h1><p>{active.title}</p></div></div>
              <div className="panel production-panel">
                {!job ? <>
                  <h2>Vertonung vorbereiten</h2>
                  <p>{capabilities?.reason ?? 'Prüfe die installierten Stimmen.'}</p>
                  <button className="primary-btn" disabled={busy || !capabilities?.tts_generation || active.status !== 'Vorbereitet'} onClick={() => productionAction('start')}><PlayCircle size={18}/> Vertonung starten</button>
                  {!capabilities?.tts_generation && <button className="outline-btn" onClick={showVoices}><Volume2 size={17}/> Sprecher prüfen</button>}
                </> : <>
                  <div className="panel-head"><h2>Auftrag</h2><span className="status-chip">{job.status}</span></div>
                  <p>Aktuelles Kapitel: {job.chapter || 'Noch kein Kapitel begonnen'}</p>
                  <progress max={Math.max(1,job.total)} value={job.done} aria-label="Fortschritt"/><p>{job.done} von {job.total} Segmenten · {Math.round(job.duration)} Sekunden Audio</p>
                  {job.error && <p className="error" role="alert">{job.error}</p>}
                  <div className="job-actions">
                    {['queued','running'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('pause')}><PauseCircle size={17}/> Pausieren</button>}
                    {['paused','failed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('resume')}><PlayCircle size={17}/> Fortsetzen</button>}
                    {['queued','running','paused','failed'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('cancel')}><StopCircle size={17}/> Abbrechen</button>}
                    {['generated','completed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('export')}><FileAudio size={17}/> M4B erstellen</button>}
                    {job.status === 'completed' && <button className="outline-btn" disabled={busy} onClick={() => loadAudio(\`/api/projects/\${active.id}/jobs/\${job.id}/download\`)}><Headphones size={17}/> Fertiges Hörbuch anhören</button>}
                    {job.status === 'completed' && <button className="primary-btn" disabled={busy} onClick={async () => {
                      const response = await fetch(\`/api/projects/\${active.id}/jobs/\${job.id}/download\`, {headers:{Authorization:\`Bearer \${token}\`}})
                      if(!response.ok) {setError('Download nicht verfügbar.');return}
                      const url=URL.createObjectURL(await response.blob())
                      const link=document.createElement('a');link.href=url;link.download='audiobook.m4b';link.click()
                      window.setTimeout(()=>URL.revokeObjectURL(url),5000)
                    }}>M4B herunterladen</button>}
                  </div>
                  {job.done > 0 && <div className="segment-preview">
                    <label htmlFor="segment-index">Segment zum Anhören (0–{job.done-1})</label>
                    <input id="segment-index" type="number" min={0} max={job.done-1} value={Math.min(sampleIndex,job.done-1)} onChange={e => setSampleIndex(Number(e.target.value))}/>
                    <button className="outline-btn" disabled={busy} onClick={() => loadAudio(\`/api/projects/\${active.id}/jobs/\${job.id}/segments/\${Math.min(sampleIndex,job.done-1)}\`)}><PlayCircle size={17}/> Segment anhören</button>
                  </div>}
                  {audioUrl && <audio src={audioUrl} controls className="chapter-player"/>}
                </>}
              </div>
            </>}
            <footer className="footer">German Audiobook Studio <span>·</span> Fachbücher. Deutsche Sprachausgabe. Geschützte Verarbeitung.</footer>
          </main>
        </div>
      )}
    </div>
  )
}
