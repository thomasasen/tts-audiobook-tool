import { useEffect, useRef, useState, type FormEvent, type ChangeEvent } from 'react'
import {
  ArrowLeft, BookOpen, Check, ChevronRight, CloudUpload, FileAudio, LoaderCircle,
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
  created_at: string
  chapter: string
  duration: number
  error: string
}
type ActivityEvent = {at: string; message: string}
type PreviewTask = {id: string | null; status: 'idle'|'running'|'ready'|'failed'; stage: string; events: ActivityEvent[]; error: string; duration_seconds?: number}
type JobActivity = {id: string; status: string; done: number; total: number; percent: number; events: ActivityEvent[]; elapsed_seconds?: number; remaining_seconds?: number | null; eta_sample_count?: number; calculated_at?: string}
type QualityIssue = {index: number; status: string; warnings: string[]; asr_similarity?: number | null}
type QualityReport = {checked: number; total: number; issues: QualityIssue[]}
const STATUS_LABELS: Record<string, string> = {
  queued: 'Warteschlange', running: 'Wird vertont', paused: 'Pausiert',
  failed: 'Fehlgeschlagen', generated: 'Audio erzeugt',
  completed: 'Export abgeschlossen', cancelled: 'Abgebrochen',
  exporting: 'M4B wird erstellt',
}
type Capabilities = {
  epub_import: boolean
  chapter_selection: boolean
  tts_generation: boolean
  audio_export: boolean
  voice_preview_available: boolean
  voice_installed: boolean
  voice_approved: boolean
  reason: string
}

async function apiResponseError(response: Response, path: string): Promise<Error> {
  // Reverse proxies and unhandled FastAPI exceptions can return plain text
  // ("Internal Server Error") or HTML, never a JSON {detail} object.
  // Do not attempt to parse these bodies or expose internal response content.
  const type = response.headers.get('content-type') ?? ''
  if (type.includes('json')) {
    try {
      const payload = await response.json() as ApiError | null
      if (payload && typeof payload.detail === 'string' && payload.detail.trim()) {
        return new Error(payload.detail)
      }
    } catch { /* malformed error body: fall back to status and endpoint */ }
  }
  const message = response.status >= 500 ? 'Serverfehler' : 'Anfrage fehlgeschlagen'
  return new Error(`${message} (HTTP ${response.status}) bei ${path}. Bitte das RunPod-Webserver-Log prüfen.`)
}

async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...init.headers },
    cache: 'no-store',
  })
  if (!response.ok) throw await apiResponseError(response, path)
  // Successful deletion is intentionally bodyless (HTTP 204/205).
  if (response.status === 204 || response.status === 205) return undefined as T

  const type = response.headers.get('content-type') ?? ''
  if (!type.includes('json')) {
    throw new Error(`Unerwartete Serverantwort (HTTP ${response.status}) bei ${path}: JSON erwartet. Bitte das RunPod-Webserver-Log prüfen.`)
  }
  try {
    return await response.json() as T
  } catch {
    throw new Error(`Ungültige JSON-Antwort (HTTP ${response.status}) bei ${path}. Bitte das RunPod-Webserver-Log prüfen.`)
  }
}

function formatDuration(value: number): string {
  const seconds = Math.max(0, Math.floor(Number.isFinite(value) ? value : 0))
  const hh = Math.floor(seconds / 3600)
  const mm = Math.floor((seconds % 3600) / 60)
  const ss = seconds % 60
  return hh > 0
    ? `${hh} Std. ${String(mm).padStart(2, '0')} Min. ${String(ss).padStart(2, '0')} Sek.`
    : `${mm} Min. ${String(ss).padStart(2, '0')} Sek.`
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
  const [quality, setQuality] = useState<QualityReport | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [glossary, setGlossary] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [previewPlayed, setPreviewPlayed] = useState(false)
  const [previewDurationSeconds, setPreviewDurationSeconds] = useState<5|15|30|60>(5)
  const [clockNow, setClockNow] = useState(() => Date.now())
  const [sampleIndex, setSampleIndex] = useState(0)
  const [previewTask, setPreviewTask] = useState<PreviewTask | null>(null)
  const [previewAudioValid, setPreviewAudioValid] = useState(false)
  const [previewAudioError, setPreviewAudioError] = useState('')
  const [jobActivity, setJobActivity] = useState<JobActivity | null>(null)
  const [exportPending, setExportPending] = useState(false)
  const handledPreview = useRef<string | null>(null)
  const activeObjectUrl = useRef<string | null>(null)

  // URL.createObjectURL resources are browser-owned: never revoke them
  // inside a React state updater (React may execute updaters more than once).
  function setOwnedAudioUrl(next: string) {
    if (activeObjectUrl.current && activeObjectUrl.current !== next) {
      URL.revokeObjectURL(activeObjectUrl.current)
    }
    activeObjectUrl.current = next || null
    setAudioUrl(next)
  }

  useEffect(() => () => {
    if (activeObjectUrl.current) URL.revokeObjectURL(activeObjectUrl.current)
    activeObjectUrl.current = null
  }, [])

  async function refreshActivity(projectId: string, jobId: string) {
    try {
      const activity = await api<JobActivity>(
        `/api/projects/${projectId}/jobs/${jobId}/activity`, token
      )
      setJobActivity(activity)
    } catch {
      // Older pre-update jobs or a transient activity error must not stop
      // polling the canonical persisted job state.
    }
  }

  async function loadPreviewAudio(taskId: string) {
    const path = `/api/voices/preview/audio/${taskId}`
    try {
      const response = await fetch(path, {
        headers: { Authorization: `Bearer ${token}` }, cache: 'no-store',
      })
      if (!response.ok) throw await apiResponseError(response, path)
      const type = response.headers.get('content-type') ?? ''
      if (!type.startsWith('audio/wav') && !type.startsWith('audio/x-wav')) {
        throw new Error('Der Server hat keine WAV-Hörprobe zurückgegeben.')
      }
      const blob = await response.blob()
      if (blob.size < 100) throw new Error('Die erzeugte Hörprobe ist leer oder unvollständig.')
      const url = URL.createObjectURL(blob)
      setOwnedAudioUrl(url)
      setPreviewAudioValid(false)
      setPreviewPlayed(false)
    } catch (err) {
      setPreviewAudioError((err as Error).message)
      setPreviewAudioValid(false)
    }
  }

  async function startPreview() {
    setBusy(true); setError(''); setPreviewAudioError('')
    setPreviewAudioValid(false); setPreviewPlayed(false)
    handledPreview.current = null
    setOwnedAudioUrl('')
    try {
      const task = await api<PreviewTask>('/api/voices/preview/start', token, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({duration_seconds: previewDurationSeconds}),
      })
      setPreviewTask(task)
    } catch (err) {
      setPreviewAudioError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function refreshJob(id: string, projectId: string) {
    const latest = await api<Job>(`/api/projects/${projectId}/jobs/${id}`, token)
    setJob(latest)
    void refreshActivity(projectId, id)
    if (['generated','completed'].includes(latest.status)) {
      const report = await api<QualityReport>('/api/projects/' + projectId + '/jobs/' + id + '/quality', token)
      setQuality(report)
    }
    if (['generated', 'completed', 'failed', 'cancelled'].includes(latest.status)) {
      await refreshProjects()
    }
  }

  // The on-screen clock ticks once per second while job metrics are refreshed
  // from the server every three seconds. This is a projection, not a promise.
  useEffect(() => {
    if (!token || view !== 'export' || !job) return
    setClockNow(Date.now())
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [token, view, job?.id])

  useEffect(() => {
    if (!token || !active || !job || (!['queued', 'running', 'exporting'].includes(job.status) && !exportPending)) return
    const interval = window.setInterval(() => {
      void refreshJob(job.id, active.id).catch(err => setError((err as Error).message))
    }, 3000)
    return () => window.clearInterval(interval)
  }, [token, active?.id, job?.id, job?.status, exportPending])

  useEffect(() => {
    if (!token || view !== 'voices' || !previewTask?.id) return
    if (previewTask.status !== 'running') {
      if (previewTask.status === 'ready' && handledPreview.current !== previewTask.id) {
        handledPreview.current = previewTask.id
        void loadPreviewAudio(previewTask.id)
      }
      return
    }
    let cancelled = false
    const interval = window.setInterval(() => {
      void api<PreviewTask>('/api/voices/preview/status', token)
        .then(task => { if (!cancelled && task.id === previewTask.id) setPreviewTask(task) })
        .catch(err => {
          if (!cancelled) setPreviewAudioError((err as Error).message)
        })
    }, 1500)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [token, view, previewTask?.id, previewTask?.status])

  async function loadAudio(path: string, method = 'GET') {
    setBusy(true); setError('')
    try {
      const response = await fetch(path, {method, headers: {Authorization: `Bearer ${token}`}, cache: 'no-store'})
      if (!response.ok) throw await apiResponseError(response, path)
      const type = response.headers.get('content-type') ?? ''
      if (!type.startsWith('audio/') && type !== 'application/octet-stream') {
        throw new Error(`Unerwartete Audio-Antwort (HTTP ${response.status}) bei ${path}. Bitte das RunPod-Webserver-Log prüfen.`)
      }
      const url = URL.createObjectURL(await response.blob())
      setOwnedAudioUrl(url)
      setPreviewPlayed(false)
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
    const selected = list.jobs[0] ?? null
    setJob(selected); setCapabilities(caps)
    setJobActivity(null)
    if (selected) void refreshActivity(projectId, selected.id)
    if (selected && ['generated','completed'].includes(selected.status)) {
      setQuality(await api<QualityReport>('/api/projects/' + projectId + '/jobs/' + selected.id + '/quality', token))
    } else setQuality(null)
  }

  async function productionAction(action: 'start'|'pause'|'resume'|'cancel'|'export') {
    if (!active) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (action === 'start') {
        const next = await api<Job>(`/api/projects/${active.id}/jobs`, token, {method: 'POST'})
        setJob(next)
      } else if (action === 'export' && job) {
        setExportPending(true)
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
    finally { setBusy(false); setExportPending(false) }
  }

  async function retrySegment(index: number) {
    if (!active || !job) return
    if (!window.confirm('Dieses Segment mit der aktuellen Stimme neu erzeugen? Ein vorhandener M4B-Export wird ungültig.')) return
    setBusy(true); setError('')
    try {
      const next = await api<Job>('/api/projects/' + active.id + '/jobs/' + job.id + '/segments/' + index + '/retry', token, {method:'POST'})
      setJob(next); setQuality(null); setNotice('Segment zur erneuten Erzeugung vorgemerkt.')
    } catch (err) {setError((err as Error).message)}
    finally {setBusy(false)}
  }

  async function deleteProject(id: string) {
    if (!window.confirm('Dieses Buch und alle erzeugten Dateien endgültig löschen?')) return
    setBusy(true); setError('')
    try {
      await api(`/api/projects/${id}`, token, {method: 'DELETE'})
      if (active?.id === id) {setActive(null); setJob(null);setJobActivity(null);setView('library')}
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
    setOwnedAudioUrl(''); setJob(null); setJobActivity(null); setQuality(null); setPreviewTask(null)
    setPreviewAudioError(''); setPreviewAudioValid(false); setPreviewPlayed(false); setExportPending(false)
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
      setJob(null); setJobActivity(null)
      if (next === 'export') await refreshProduction(id)
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
      setOwnedAudioUrl(''); setPreviewPlayed(false); setPreviewAudioValid(false)
      setPreviewAudioError(''); handledPreview.current = null
      const [caps, task] = await Promise.all([
        api<Capabilities>('/api/capabilities', token),
        api<PreviewTask>('/api/voices/preview/status', token),
      ])
      setCapabilities(caps); setPreviewTask(task)
      setView('voices')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  // Refresh installation state while the user watches this page: RunPod
  // brings up the web UI BEFORE the Thorsten model download is complete.
  // Keep existing preview audio and listening confirmation untouched.
  useEffect(() => {
    if (!token || view !== 'voices' || !active || capabilities?.voice_installed) return
    let cancelled = false
    const interval = window.setInterval(() => {
      void api<Capabilities>('/api/capabilities', token)
        .then(next => { if (!cancelled) setCapabilities(next) })
        .catch(() => { /* manual page reload still available on transient failure */ })
    }, 5000)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [token, view, active?.id, capabilities?.voice_installed])

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
              {voices.every(voice => voice.status !== 'ready') && <div className="panel voice-notice"><LockKeyhole size={21}/><div><strong>{capabilities?.voice_installed ? 'Thorsten installiert – Hörfreigabe noch offen' : 'Thorsten noch nicht betriebsbereit'}</strong><p>{capabilities?.reason ?? 'Installationsstatus wird geprüft.'}</p></div></div>}
              <div className="voice-grid">{voices.map(voice => <div className="panel voice-card" key={voice.id}><div className="voice-avatar"><Volume2 size={26}/></div><div><span className="status-chip">{voice.status === 'ready' ? 'Freigegeben' : capabilities?.voice_installed ? 'Installiert · Freigabe offen' : 'Nicht installiert'}</span><h3>{voice.name}</h3><p>{voice.description}</p><small>Quelle und Lizenz: <a href={voice.source} target="_blank" rel="noopener noreferrer">{voice.license}</a></small>
                <div className="voice-sample-settings">
                  <label htmlFor="voice-sample-length">Länge der Hörprobe</label>
                  <select id="voice-sample-length" value={previewDurationSeconds} disabled={busy || previewTask?.status === 'running'} onChange={e => setPreviewDurationSeconds(Number(e.target.value) as 5|15|30|60)}>
                    <option value={5}>Ca. 5 Sekunden</option>
                    <option value={15}>Ca. 15 Sekunden</option>
                    <option value={30}>Ca. 30 Sekunden</option>
                    <option value={60}>Ca. 60 Sekunden</option>
                  </select>
                  <p className="helper">Ungefähre Dauer, da das Sprechtempo variieren kann. Keine Sätze werden abgeschnitten.</p>
                </div>
                <div className="voice-actions"><button className="outline-btn" disabled={busy || !capabilities?.voice_preview_available || previewTask?.status === 'running'} onClick={() => {void startPreview()}}>{previewTask?.status === 'running' ? <LoaderCircle className="spinning" size={17}/> : <PlayCircle size={17}/>} {previewTask?.status === 'running' ? 'Hörprobe wird erzeugt …' : 'Echte Hörprobe erzeugen'}</button>
                <button className="primary-btn" disabled={busy || !previewPlayed || !previewAudioValid || previewTask?.status !== 'ready'} onClick={async () => {
                  try { await api('/api/voices/approve', token, {method:'POST'}); await showVoices(); setNotice('Stimme nach manueller Hörabnahme freigegeben.') }
                  catch (err) { setError((err as Error).message) }
                }}><Check size={17}/> Nach Anhören freigeben</button></div>
              </div></div>)}</div>
              {previewTask && previewTask.status !== 'idle' && <section className="panel generation-progress" aria-label="Hörproben-Fortschritt">
                <div className="panel-head"><h2>{previewTask.status === 'ready' ? 'Hörprobe erzeugt' : previewTask.status === 'failed' ? 'Hörprobe fehlgeschlagen' : 'Hörprobe wird erzeugt'}</h2>
                  <span className="status-chip">{previewTask.status === 'running' ? 'In Bearbeitung' : previewTask.status === 'ready' ? 'Bereit zur Hörkontrolle' : 'Fehler'}</span>
                </div>
                {previewTask.status === 'failed'
                  ? <div className="message error" role="alert"><AlertCircle size={18}/><span>{previewTask.error || previewTask.stage}</span></div>
                  : <p role="status" aria-live="polite">{previewTask.stage}</p>}
                {previewTask.status === 'running' && <progress aria-label="Hörprobe wird verarbeitet (Fortschritt der Modellberechnung nicht messbar)"/>}
                <p className="helper">Die Modellinitialisierung und GPU-Synthese liefern keine belastbaren Prozentwerte. Die Arbeitsschritte werden live angezeigt.</p>
                <details className="activity-log">
                  <summary>Protokoll der Hörprobe ({previewTask.events.length} Einträge)</summary>
                  <ol>{previewTask.events.map((entry, i) => <li key={i}><time dateTime={entry.at}>{new Date(entry.at).toLocaleTimeString('de-DE')}</time><span>{entry.message}</span></li>)}</ol>
                </details>
              </section>}
              {previewAudioError && <div className="message error" role="alert"><AlertCircle size={18}/><span>{previewAudioError}{audioUrl && <> <a href={audioUrl} download="thorsten-hoerprobe.wav">WAV zur Prüfung herunterladen</a></>}</span></div>}
              {audioUrl && previewTask?.status === 'ready' && !previewAudioError && <div className="panel audio-preview">
                <h3>Hörprobe</h3>
                <audio src={audioUrl} controls preload="metadata" onLoadedMetadata={event => {
                  const duration = event.currentTarget.duration
                  if (Number.isFinite(duration) && duration >= 0.25) {
                    setPreviewAudioValid(true)
                  } else {
                    setPreviewAudioValid(false)
                    setPreviewPlayed(false)
                    setPreviewAudioError('Die Hörprobe enthält keine abspielbare Audiodauer. Bitte das Thorsten-Worker-Log prüfen.')
                  }
                }} onError={event => {
                  setPreviewAudioValid(false)
                  setPreviewPlayed(false)
                  const code = event.currentTarget.error?.code
                  const detail = code === 4 ? 'Die Audioquelle oder Kodierung wird nicht unterstützt.'
                    : code === 3 ? 'Der Browser konnte die Audiodaten nicht decodieren.'
                    : code === 2 ? 'Der Browser konnte die Audiodaten nicht laden.'
                    : 'Die WAV-Hörprobe konnte nicht abgespielt werden.'
                  setPreviewAudioError(`Hörprobe nicht abspielbar (Medienfehler ${code ?? 'unbekannt'}): ${detail}`)
                }} onEnded={() => { if (previewAudioValid) setPreviewPlayed(true) }}/>
                <p className="helper">Prüfe Akzent, Stimmstabilität, Betonung und Verständlichkeit. Die Freigabe ist eine manuelle Qualitätsentscheidung.</p>
              </div>}
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
                  <div className="panel-head"><h2>Auftrag</h2><span className="status-chip">{STATUS_LABELS[job.status] ?? job.status}</span></div>
                  <p>Aktuelles Kapitel: {job.chapter || 'Noch kein Kapitel begonnen'}</p>
                  <div className="progress-head"><strong>Audioerzeugung</strong><span>{job.total > 0 ? Math.round(100 * job.done / job.total) : 0} %</span></div>
                  <progress max={Math.max(1,job.total)} value={job.done} aria-label="Fertiggestellte Audiosegmente"/>
                  <p>{job.done} von {job.total} Segmenten tatsächlich fertig · {Math.round(job.duration)} Sekunden Audio</p>
                  <div className="production-time-metrics" aria-label="Produktionsdauer und Restzeit">
                    <div>
                      <span>Vergangene Zeit</span>
                      <strong>{formatDuration(Math.max(0, (clockNow - Date.parse(job.created_at)) / 1000))}</strong>
                      <small>Seit Auftragsbeginn, inklusive Pausen</small>
                    </div>
                    <div>
                      <span>Geschätzte Restzeit</span>
                      <strong>{job.status === 'paused' ? 'Pausiert'
                        : job.status === 'queued' ? 'Wartet auf GPU'
                        : job.status === 'failed' ? 'Fehlgeschlagen'
                        : job.status === 'cancelled' ? 'Abgebrochen'
                        : ['generated','completed'].includes(job.status) ? '0 Min. 00 Sek.'
                        : job.status === 'exporting' ? 'Export läuft'
                        : job.status === 'running' && jobActivity?.id === job.id && jobActivity.remaining_seconds != null && jobActivity.calculated_at
                          ? formatDuration(Math.max(0, jobActivity.remaining_seconds - (clockNow - Date.parse(jobActivity.calculated_at)) / 1000))
                          : 'Wird ermittelt …'}</strong>
                      <small>{job.status === 'running' && jobActivity?.remaining_seconds != null
                        ? 'Schätzung aus echten Segmentlaufzeiten; alle 3 Sekunden neu berechnet'
                        : job.status === 'running' ? 'Nach mindestens 3 vollständig gemessenen Segmenten'
                        : job.status === 'exporting' ? 'Exportzeit nicht zuverlässig vorhersagbar'
                        : 'Keine aktive Restzeitprognose'}</small>
                    </div>
                  </div>
                  {(job.status === 'exporting' || exportPending) && <p role="status">M4B-Export läuft. Fortschritt ist im Protokoll sichtbar; keine Schätzung der Restzeit.</p>}
                  <details className="activity-log" open={job.status === 'failed' ? true : undefined}>
                    <summary>Auftragsprotokoll ({jobActivity?.events.length ?? 0} Einträge)</summary>
                    <ol>{jobActivity?.events.map((entry, i) => <li key={i}><time dateTime={entry.at}>{new Date(entry.at).toLocaleTimeString('de-DE')}</time><span>{entry.message}</span></li>) ?? <li>Noch keine Einträge vorhanden.</li>}</ol>
                  </details>
                  {job.error && <p className="error" role="alert">{job.error}</p>}
                  <div className="job-actions">
                    {['queued','running'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('pause')}><PauseCircle size={17}/> Pausieren</button>}
                    {['paused','failed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('resume')}><PlayCircle size={17}/> Fortsetzen</button>}
                    {job.status === 'cancelled' && <button className="primary-btn" disabled={busy || !capabilities?.tts_generation} onClick={() => productionAction('start')}><PlayCircle size={17}/> Neuen Versuch starten</button>}
                    {['queued','running','paused','failed'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('cancel')}><StopCircle size={17}/> Abbrechen</button>}
                    {['generated','completed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('export')}><FileAudio size={17}/> M4B erstellen</button>}
                    {job.status === 'completed' && <button className="outline-btn" disabled={busy} onClick={() => loadAudio(`/api/projects/${active.id}/jobs/${job.id}/download`)}><Headphones size={17}/> Fertiges Hörbuch anhören</button>}
                    {job.status === 'completed' && <button className="primary-btn" disabled={busy} onClick={async () => {
                      const response = await fetch(`/api/projects/${active.id}/jobs/${job.id}/download`, {headers:{Authorization:`Bearer ${token}`}})
                      if(!response.ok) {setError('Download nicht verfügbar.');return}
                      const url=URL.createObjectURL(await response.blob())
                      const link=document.createElement('a');link.href=url;link.download='audiobook.m4b';link.click()
                      window.setTimeout(()=>URL.revokeObjectURL(url),5000)
                    }}>M4B herunterladen</button>}
                  </div>
                  {job.done > 0 && <div className="segment-preview">
                    <label htmlFor="segment-index">Segment zum Anhören (0–{job.done-1})</label>
                    <input id="segment-index" type="number" min={0} max={job.done-1} value={Math.min(sampleIndex,job.done-1)} onChange={e => setSampleIndex(Number(e.target.value))}/>
                    <button className="outline-btn" disabled={busy} onClick={() => loadAudio(`/api/projects/${active.id}/jobs/${job.id}/segments/${Math.min(sampleIndex,job.done-1)}`)}><PlayCircle size={17}/> Segment anhören</button>
                  </div>}
                  {quality && <section className="quality-check" aria-label="Qualitätskontrolle">
                    <h3>Qualitätskontrolle</h3>
                    <p>{quality.checked} von {quality.total} Segmenten per ASR geprüft. Automatische Hinweise erfordern menschliche Kontrolle.</p>
                    {quality.issues.length === 0 ? <p>Keine gemeldeten Auffälligkeiten. Eine vollständige Hörabnahme ersetzt dies nicht.</p> : <ul>{quality.issues.map(issue =>
                      <li key={issue.index}><strong>Segment {issue.index + 1}</strong> · {issue.warnings.join(' ') || 'Spracherkennung nicht verfügbar.'}
                        <div className="job-actions">
                          <button className="outline-btn" disabled={busy} onClick={() => loadAudio('/api/projects/' + active.id + '/jobs/' + job.id + '/segments/' + issue.index)}><PlayCircle size={17}/> Anhören</button>
                          <button className="outline-btn" disabled={busy || !['generated','completed'].includes(job.status)} onClick={() => retrySegment(issue.index)}><RotateCcw size={17}/> Neu erzeugen</button>
                        </div>
                      </li>
                    )}</ul>}
                  </section>}
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
