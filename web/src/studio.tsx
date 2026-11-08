import { useEffect, useRef, useState, type FormEvent, type ChangeEvent } from 'react'
import {
  ArrowLeft, BookOpen, Check, ChevronRight, CloudUpload, FileAudio, LoaderCircle,
  FolderOpen, Headphones, LockKeyhole, Moon, ShieldCheck, Sun,
  Volume2, AlertCircle, RotateCcw, Trash2, PlayCircle, PauseCircle, StopCircle
} from 'lucide-react'
import { downloadAuthenticatedM4b, downloadAuthenticatedChapter, downloadAuthenticatedChapterZip, supportsDirectSave, type DownloadProgress } from './download'

type Chapter = {
  index: number
  title: string
  preview: string
  characters: number
  is_toc: boolean
}
type AuditResource = {
  index: number; href: string; status: string; characters?: number; preview?: string
  included_by_user?: boolean; override_reason?: string; mapped_segments?: number
}
type AuditSkipped = {
  index: number; href?: string; status: string; kind?: string; reason: string
  preview?: string; can_restore?: boolean; overridden?: boolean
}
type AuditReviewElement = {
  source_href: string; element_key: string; kind: string; preview: string
  source_text: string; alt_text: string; caption: string; handling: string
  can_edit: boolean; speaker_text: string; approved: boolean
}
type AuditSourceMapping = {
  chapter_index: number; chapter_title: string; source_href: string
  source_start: number | null; source_end: number | null
  output_start: number | null; output_end: number | null
  navigation_title: string; mapping_status: string
}
type SpeakerOverride = {source_href: string; element_key: string; speaker_text: string; approved: boolean}
type ContentAudit = {
  version: number
  status: string
  summary: Record<string, number>
  resources: AuditResource[]
  skipped: AuditSkipped[]
  source_mapping?: AuditSourceMapping[]
  chapter_coverage?: Record<string, {total_non_whitespace_characters: number; mapped_non_whitespace_characters: number; unmapped_non_whitespace_characters: number; coverage_percent: number}>
  restorable_source_hrefs?: string[]
  review_elements?: AuditReviewElement[]
  warnings: string[]
  review_notes: string[]
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
  content_audit?: ContentAudit
  included_source_hrefs?: string[]
  speaker_overrides?: SpeakerOverride[]
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
type CompletedChapter = {
  index: number
  title: string
  segment_count: number
  duration_seconds: number
  preview_seconds: number
  bytes: number
}
type GlossaryUpdate = {
  status: 'idle' | 'unchanged' | 'pending' | 'applied' | 'failed'
  affected?: number
  preserved?: number
  total?: number
  message?: string
}

type Job = {
  id: string
  project_id: string
  status: string
  done: number
  total: number
  created_at: string
  updated_at?: string
  chapter: string
  duration: number
  error: string
}
type ActivityEvent = {at: string; message: string}
type PreviewTask = {id: string | null; status: 'idle'|'running'|'ready'|'failed'; stage: string; events: ActivityEvent[]; error: string; duration_seconds?: number; speed?: number; custom_text?: boolean}
type VoiceSettings = {speed: number; preview_text: string}
const VERIFIED_SPEEDS = [0.85, 0.90, 0.95, 1, 1.05, 1.10, 1.15] as const
type JobActivity = {
  id: string; status: string; done: number; total: number; percent: number
  events: ActivityEvent[]; elapsed_seconds?: number; remaining_seconds?: number | null
  eta_sample_count?: number; calculated_at?: string; received_at?: number
  export?: {processed: number; total: number; percent: number; phase?: 'preparing'|'processing'|'finalizing'|'completed'; remaining_seconds?: number | null; eta_sample_count?: number} | null
}
type QualityIssue = {index: number; status: string; reason?: string; warnings: string[]; asr_similarity?: number | null; asr_wer?: number | null; asr_cer?: number | null; asr_number_mismatch?: boolean}
type QualityReport = {checked: number; unchecked?: number; total: number; issues: QualityIssue[]}
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
  voice_model_loaded?: boolean
  voice_warmup_state?: 'waiting' | 'loading' | 'ready' | 'failed' | 'disabled' | 'unavailable'
  voice_install_state?: string
  voice_approved: boolean
  reason: string
  asr?: {state: string; message: string; enabled: boolean; model: string | null}
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

function liveRemaining(remaining: number | null | undefined, receivedAt: number | undefined, now: number): number | null {
  if (remaining == null || !Number.isFinite(remaining) || remaining <= 0 || receivedAt === undefined) return null
  const age = Math.max(0, (now - receivedAt) / 1000)
  // Never keep counting down an estimate from an unresponsive / stale API.
  if (!Number.isFinite(age) || age > 15 || age >= remaining) return null
  return Math.max(1, Math.ceil(remaining - age))
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
  const [speakerDrafts, setSpeakerDrafts] = useState<Record<string, string>>({})
  const fileRef = useRef<HTMLInputElement>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [completedChapters, setCompletedChapters] = useState<CompletedChapter[]>([])
  const [chapterListError, setChapterListError] = useState('')
  const [chapterPreviewIndex, setChapterPreviewIndex] = useState<number | null>(null)
  const [chapterPreviewPending, setChapterPreviewPending] = useState<number | null>(null)
  const [chapterDownloadIndex, setChapterDownloadIndex] = useState<number | null>(null)
  const [quality, setQuality] = useState<QualityReport | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [glossary, setGlossary] = useState('')
  const [glossaryUpdate, setGlossaryUpdate] = useState<GlossaryUpdate>({status: 'idle'})
  const [audioUrl, setAudioUrl] = useState('')
  const [previewPlayed, setPreviewPlayed] = useState(false)
  const [previewDurationSeconds, setPreviewDurationSeconds] = useState<5|15|30|60>(5)
  const [voiceSpeed, setVoiceSpeed] = useState(1)
  const [voicePreviewText, setVoicePreviewText] = useState('')
  const [voiceSettingsDirty, setVoiceSettingsDirty] = useState(false)
  const [clockNow, setClockNow] = useState(() => Date.now())
  const [sampleIndex, setSampleIndex] = useState(0)
  const [previewTask, setPreviewTask] = useState<PreviewTask | null>(null)
  const [previewAudioValid, setPreviewAudioValid] = useState(false)
  const [previewAudioError, setPreviewAudioError] = useState('')
  const [jobActivity, setJobActivity] = useState<JobActivity | null>(null)
  const [exportPending, setExportPending] = useState(false)
  const [downloadPending, setDownloadPending] = useState(false)
  const [chapterZipPending, setChapterZipPending] = useState(false)
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null)
  const downloadAbort = useRef<AbortController | null>(null)
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
    downloadAbort.current?.abort()
    if (activeObjectUrl.current) URL.revokeObjectURL(activeObjectUrl.current)
    activeObjectUrl.current = null
  }, [])

  async function refreshActivity(projectId: string, jobId: string) {
    try {
      const activity = await api<JobActivity>(
        `/api/projects/${projectId}/jobs/${jobId}/activity`, token
      )
      setJobActivity(previous => {
        // Responses may arrive out of order when periodic polls overlap.
        // Keep the newest server snapshot for the same job.
        if (previous?.id === activity.id && previous.calculated_at && activity.calculated_at) {
          const older = Date.parse(activity.calculated_at) < Date.parse(previous.calculated_at)
          if (older) return previous
        }
        return {...activity, received_at: Date.now()}
      })
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
        body: JSON.stringify({
          duration_seconds: previewDurationSeconds, speed: voiceSpeed,
          preview_text: voicePreviewText, project_id: active?.id ?? null,
        }),
      })
      setPreviewTask(task)
    } catch (err) {
      setPreviewAudioError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function refreshCompletedChapters(projectId: string, jobId: string) {
    try {
      const response = await api<{chapters: CompletedChapter[]}>(
        `/api/projects/${projectId}/jobs/${jobId}/chapters`, token,
      )
      setCompletedChapters(response.chapters)
      setChapterListError('')
    } catch (error) {
      setChapterListError((error as Error).message)
    }
  }

  async function refreshJob(id: string, projectId: string) {
    const latest = await api<Job>(`/api/projects/${projectId}/jobs/${id}`, token)
    setJob(previous => {
      // Never render an older status after a newer poll completed first.
      if (previous?.id === latest.id && previous.updated_at && latest.updated_at) {
        if (Date.parse(latest.updated_at) < Date.parse(previous.updated_at)) return previous
      }
      return latest
    })
    void refreshCompletedChapters(projectId, id)
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
    setChapterPreviewIndex(null)
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
      const response = await api<{entries: Record<string, string>; update: GlossaryUpdate}>(`/api/projects/${active.id}/glossary`, token)
      setGlossary(Object.entries(response.entries).map(([key, value]) => `${key} = ${value}`).join('\n'))
      setGlossaryUpdate(response.update)
      setView('glossary')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  useEffect(() => {
    if (!token || !active || view !== 'glossary' || glossaryUpdate.status !== 'pending') return
    let cancelled = false
    const timer = window.setInterval(() => {
      void api<GlossaryUpdate>(`/api/projects/${active.id}/glossary/update`, token)
        .then(result => {
          if (cancelled) return
          setGlossaryUpdate(result)
          if (result.status === 'applied') {
            setNotice(result.message || 'Glossaränderung wurde übernommen.')
            void refreshProjects()
          } else if (result.status === 'failed') {
            setError(result.message || 'Glossaränderung konnte nicht übernommen werden.')
          }
        })
        .catch(error => { if (!cancelled) setError((error as Error).message) })
    }, 2000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [token, active?.id, view, glossaryUpdate.status])

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
      const response = await api<{entries: Record<string, string>; update: GlossaryUpdate}>(
        `/api/projects/${active.id}/glossary`, token,
        {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entries})},
      )
      setGlossaryUpdate(response.update)
      setNotice(response.update.status === 'pending'
        ? 'Änderungen angenommen. Die laufende Vertonung wird an der nächsten sicheren Segmentgrenze angepasst.'
        : response.update.message || 'Das Projektglossar wurde aktualisiert.')
      if (response.update.status === 'applied') await refreshProjects()
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
    setCompletedChapters([]); setChapterListError('')
    setChapterPreviewIndex(null); setOwnedAudioUrl('')
    if (selected) {
      void refreshActivity(projectId, selected.id)
      void refreshCompletedChapters(projectId, selected.id)
    }
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
        setCompletedChapters([]); setChapterListError('')
        setChapterPreviewIndex(null); setOwnedAudioUrl('')
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

  async function startM4bDownload() {
    if (!active || !job || job.status !== 'completed' || downloadPending) return
    const controller = new AbortController()
    downloadAbort.current = controller
    setDownloadPending(true)
    setDownloadProgress(null)
    setError('')
    setChapterDownloadIndex(null)
    const filename = (active.title || 'Hoerbuch').replace(/[\\/:*?"<>|]/g, '_').slice(0, 90) + '.m4b'
    try {
      const result = await downloadAuthenticatedM4b(
        `/api/projects/${active.id}/jobs/${job.id}/download`,
        token, filename, controller.signal, setDownloadProgress,
      )
      setNotice(`M4B übertragen (${(result.bytes / (1024 * 1024)).toFixed(1)} MB, ${result.mode === 'stream' ? 'direkt gespeichert' : 'begrenzter Browser-Download'}).`)
    } catch (error) {
      const exc = error as Error
      if (controller.signal.aborted || exc.name === 'AbortError') {
        setNotice('M4B-Download abgebrochen. Unvollständige Zieldatei wurde verworfen.')
      } else {
        setError(exc.message || 'M4B-Download fehlgeschlagen.')
      }
    } finally {
      if (downloadAbort.current === controller) downloadAbort.current = null
      setDownloadPending(false)
    }
  }

  async function startChapterZipDownload() {
    if (!active || !job || job.status !== 'completed' || !completedChapters.length || downloadPending) return
    const controller = new AbortController()
    downloadAbort.current = controller
    setDownloadPending(true); setChapterZipPending(true)
    setChapterDownloadIndex(null); setDownloadProgress(null); setError('')
    const base = (active.title || 'Hoerbuch').replace(/[\\/:*?"<>|]/g, '_').slice(0, 80)
    try {
      const result = await downloadAuthenticatedChapterZip(
        `/api/projects/${active.id}/jobs/${job.id}/chapters/archive`,
        token, `${base}-Kapitel.zip`, controller.signal, setDownloadProgress,
      )
      setNotice(`${completedChapters.length} Kapitel als ZIP gespeichert (${(result.bytes / (1024 * 1024)).toFixed(1)} MB).`)
    } catch (error) {
      const exc = error as Error
      if (controller.signal.aborted || exc.name === 'AbortError') {
        setNotice('Kapitel-ZIP-Download abgebrochen.')
      } else {
        setError(exc.message || 'Kapitel-ZIP-Download fehlgeschlagen.')
      }
    } finally {
      if (downloadAbort.current === controller) downloadAbort.current = null
      setDownloadPending(false); setChapterZipPending(false)
    }
  }

  async function previewChapter(chapter: CompletedChapter) {
    if (!active || !job) return
    const path = `/api/projects/${active.id}/jobs/${job.id}/chapters/${chapter.index}/audio`
    setBusy(true); setChapterPreviewPending(chapter.index); setError('')
    try {
      const response = await fetch(path, {
        headers: {Authorization: `Bearer ${token}`}, cache: 'no-store',
      })
      if (!response.ok) throw await apiResponseError(response, path)
      if (!(response.headers.get('content-type') ?? '').startsWith('audio/wav')) {
        throw new Error('Der Server hat keine browserkompatible WAV-Hörprobe zurückgegeben.')
      }
      const limit = 20 * 1024 * 1024
      const header = response.headers.get('content-length')
      if (header && Number(header) > limit) {
        throw new Error('Die WAV-Hörprobe ist ungewöhnlich groß. Bitte die M4A-Datei herunterladen.')
      }
      if (!response.body) throw new Error('Der Browser unterstützt keinen sicheren Audioempfang.')
      const reader = response.body.getReader()
      const chunks: Uint8Array[] = []
      let count = 0
      try {
        while (true) {
          const {done, value} = await reader.read()
          if (done) break
          count += value.byteLength
          if (count > limit) throw new Error('Die Hörprobe überschreitet 20 MB. Bitte das Kapitel herunterladen.')
          chunks.push(value)
        }
      } catch (error) {
        await reader.cancel().catch(() => undefined)
        throw error
      } finally {
        reader.releaseLock()
      }
      if (count < 1024 || (header && Number(header) !== count)) {
        throw new Error('Unvollständige Kapiteldatei empfangen.')
      }
      setOwnedAudioUrl(URL.createObjectURL(new Blob(chunks, {type: 'audio/wav'})))
      setChapterPreviewIndex(chapter.index)
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false); setChapterPreviewPending(null)
    }
  }

  async function startChapterDownload(chapter: CompletedChapter) {
    if (!active || !job || downloadPending) return
    const controller = new AbortController()
    downloadAbort.current = controller
    setDownloadPending(true); setChapterDownloadIndex(chapter.index)
    setDownloadProgress(null); setError('')
    const safe = chapter.title.replace(/[\u0000-\u001f\\/:*?"<>|]/g, '_').trim().slice(0, 78) || 'Kapitel'
    const filename = `${String(chapter.index + 1).padStart(2, '0')}_${safe}.m4a`
    try {
      const result = await downloadAuthenticatedChapter(
        `/api/projects/${active.id}/jobs/${job.id}/chapters/${chapter.index}/download`,
        token, filename, controller.signal, setDownloadProgress,
      )
      setNotice(`Kapitel ${chapter.index + 1} gespeichert (${(result.bytes / (1024 * 1024)).toFixed(1)} MB).`)
    } catch (error) {
      const exc = error as Error
      if (controller.signal.aborted || exc.name === 'AbortError') {
        setNotice('Kapitel-Download abgebrochen. Unvollständige Datei verworfen.')
      } else {
        setError(exc.message || 'Kapitel-Download fehlgeschlagen.')
      }
    } finally {
      if (downloadAbort.current === controller) downloadAbort.current = null
      setDownloadPending(false); setChapterDownloadIndex(null)
    }
  }

  function signOut() {
    downloadAbort.current?.abort()
    setChapterZipPending(false)
    setToken(''); setTokenInput(''); setProjects([]); setActive(null)
    setOwnedAudioUrl(''); setJob(null); setJobActivity(null); setQuality(null); setPreviewTask(null)
    setCompletedChapters([]); setChapterListError(''); setChapterPreviewIndex(null); setChapterDownloadIndex(null)
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
      setSpeakerDrafts({})
      setJob(null); setJobActivity(null); setCompletedChapters([])
      setChapterPreviewIndex(null); setChapterListError('')
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
      setSpeakerDrafts({})
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

  async function updateSourceOverride(href: string, include: boolean) {
    if (!active || !href || busy || saving) return
    setBusy(true); setError(''); setNotice('')
    try {
      const next = new Set(active.included_source_hrefs ?? [])
      if (include) next.add(href)
      else next.delete(href)
      const project = await api<BookProject>(`/api/projects/${active.id}/source-overrides`, token, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({include_hrefs: [...next]}),
      })
      setActive(project)
      setStart(project.suggested_start ?? project.selected_start)
      setExcluded(project.chapters?.filter(c => c.is_toc).map(c => c.index) ?? [])
      setSpeakerDrafts({})
      await refreshProjects()
      setNotice(include
        ? 'Quellabschnitt wurde zusätzlich aufgenommen. Bitte die neue Kapitel- und Textzuordnung prüfen.'
        : 'Manuelle Quellaufnahme wurde zurückgesetzt.')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function saveSpeakerOverride(element: AuditReviewElement) {
    if (!active || !element.can_edit || busy || saving) return
    const draftKey = `${element.source_href}::${element.element_key}`
    const speakerText = (speakerDrafts[draftKey] ?? element.speaker_text ?? '').trim()
    if (!speakerText) return
    setBusy(true); setError(''); setNotice('')
    try {
      const project = await api<BookProject>(`/api/projects/${active.id}/speaker-overrides`, token, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          source_href: element.source_href,
          element_key: element.element_key,
          speaker_text: speakerText,
        }),
      })
      setActive(project)
      setStart(project.suggested_start ?? project.selected_start)
      setExcluded(project.chapters?.filter(c => c.is_toc).map(c => c.index) ?? [])
      await refreshProjects()
      setNotice('Sprecherfassung wurde übernommen und in die Textvorschau eingearbeitet.')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function removeSpeakerOverride(element: AuditReviewElement) {
    if (!active || !element.approved || busy || saving) return
    setBusy(true); setError(''); setNotice('')
    try {
      const project = await api<BookProject>(`/api/projects/${active.id}/speaker-overrides`, token, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          source_href: element.source_href,
          element_key: element.element_key,
          speaker_text: '',
        }),
      })
      setActive(project)
      setStart(project.suggested_start ?? project.selected_start)
      setExcluded(project.chapters?.filter(c => c.is_toc).map(c => c.index) ?? [])
      const draftKey = `${element.source_href}::${element.element_key}`
      setSpeakerDrafts(previous => { const next = {...previous}; delete next[draftKey]; return next })
      await refreshProjects()
      setNotice('Sprecherfassung entfernt. Die ursprüngliche EPUB-Textbehandlung ist wieder aktiv.')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function showVoices() {
    if (!active) return
    setBusy(true); setError('')
    try {
      const [catalog, caps, task, settings] = await Promise.all([
        api<{ready: VoiceCandidate[], candidates: VoiceCandidate[]}>('/api/voices', token),
        api<Capabilities>('/api/capabilities', token),
        api<PreviewTask>('/api/voices/preview/status', token),
        api<VoiceSettings>(`/api/projects/${active.id}/voice-settings`, token),
      ])
      setVoices([...catalog.ready, ...catalog.candidates])
      setVoiceSpeed(settings.speed); setVoicePreviewText(settings.preview_text)
      setVoiceSettingsDirty(false)
      setOwnedAudioUrl(''); setPreviewPlayed(false); setPreviewAudioValid(false)
      setPreviewAudioError(''); handledPreview.current = null
      setCapabilities(caps); setPreviewTask(task)
      setView('voices')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function saveVoiceSettings() {
    if (!active) return
    setSaving(true); setError('')
    try {
      const saved = await api<VoiceSettings>(`/api/projects/${active.id}/voice-settings`, token, {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({speed:voiceSpeed, preview_text:voicePreviewText}),
      })
      setVoiceSpeed(saved.speed); setVoicePreviewText(saved.preview_text)
      setVoiceSettingsDirty(false)
      setNotice('Sprechtempo und Hörprobentext für dieses Buch gespeichert.')
    } catch (err) { setError((err as Error).message) }
    finally { setSaving(false) }
  }

  // Refresh installation state while the user watches this page: RunPod
  // brings up the web UI BEFORE the Thorsten model download is complete.
  // Keep existing preview audio and listening confirmation untouched.
  useEffect(() => {
    if (!token || view !== 'voices' || !active) return
    if (capabilities?.voice_installed && !['waiting', 'loading'].includes(capabilities.voice_warmup_state ?? 'disabled')) return
    let cancelled = false
    const interval = window.setInterval(() => {
      void api<Capabilities>('/api/capabilities', token)
        .then(next => { if (!cancelled) setCapabilities(next) })
        .catch(() => { /* manual page reload still available on transient failure */ })
    }, 5000)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [token, view, active?.id, capabilities?.voice_installed, capabilities?.voice_warmup_state])

  const allChapters = active?.chapters ?? []
  const readChapters = allChapters.filter(c => c.index >= start && !c.is_toc && !excluded.includes(c.index))
  const selectedChapter = allChapters.find(c => c.index === start)
  const matchingActivity = job && jobActivity?.id === job.id && jobActivity.status === job.status
    ? jobActivity : null
  const speechEta = matchingActivity && job?.status === 'running' && matchingActivity.done === job.done
    ? liveRemaining(matchingActivity.remaining_seconds, matchingActivity.received_at, clockNow) : null
  const exportActivity = job && ['exporting', 'completed'].includes(job.status) ? matchingActivity?.export : null
  // An unmeasured preparation step is not 0% of encoding. During a completed
  // export, the committed job status is authoritative even for legacy jobs
  // without a persisted export-progress file.
  const exportPercent = job?.status === 'completed' ? 100
    : exportActivity?.phase === 'preparing' ? null : exportActivity?.percent ?? null
  const exportProcessed = job?.status === 'completed' ? job.total : exportActivity?.processed
  const exportEta = exportActivity?.phase === 'processing'
    ? liveRemaining(exportActivity.remaining_seconds, matchingActivity?.received_at, clockNow) : null
  const downloadEta = liveRemaining(downloadProgress?.etaSeconds, downloadProgress?.measuredAt, clockNow)
  const transferTotal = downloadProgress?.total
  const transferReceived = downloadProgress?.received ?? 0
  const transferPercent = transferTotal != null && Number.isFinite(transferTotal) && transferTotal > 0
    ? Math.min(99, Math.max(0, Math.floor(100 * transferReceived / transferTotal))) : null
  const transferFinishing = transferTotal != null && transferTotal > 0 && transferReceived >= transferTotal
  const transferLabel = transferPercent === null ? 'Übertragung läuft (Gesamtgröße unbekannt)'
    : transferFinishing ? 'Übertragung vollständig, Datei wird gespeichert'
    : `${transferPercent} % übertragen`

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
          <nav className="sidebar" aria-label="Hauptnavigation">
            <div className="sidebar-heading">ARBEITSBEREICH</div>
            <button className={`nav-item ${view === 'library' ? 'selected' : ''}`} aria-current={view === 'library' ? 'page' : undefined} onClick={() => {setView('library'); setError(''); setNotice('')}}><FolderOpen size={19}/> Bibliothek</button>
            <div className="sidebar-heading second-heading">AKTUELLES BUCH</div>
            {active && <div className="sidebar-project" title={active.title}><span className="sidebar-project-icon"><BookOpen size={17}/></span><span className="sidebar-project-title">{active.title}</span></div>}
            <button disabled={!active} className={`nav-item ${view === 'chapters' ? 'selected' : ''}`} aria-current={view === 'chapters' ? 'page' : undefined} onClick={() => setView('chapters')}><BookOpen size={19}/> Text und Kapitel</button>
            <button disabled={!active} className={`nav-item ${view === 'voices' ? 'selected' : ''}`} aria-current={view === 'voices' ? 'page' : undefined} onClick={showVoices}><Volume2 size={19}/> Sprecher</button>
            <button disabled={!active} className={`nav-item ${view === 'glossary' ? 'selected' : ''}`} aria-current={view === 'glossary' ? 'page' : undefined} onClick={loadGlossary}><BookOpen size={19}/> Ausspracheglossar</button>
            <button disabled={!active} className={`nav-item ${view === 'export' ? 'selected' : ''}`} aria-current={view === 'export' ? 'page' : undefined} onClick={openProduction}><FileAudio size={19}/> Hörbuch und Export</button>
            <div className="sidebar-bottom"><ShieldCheck size={16}/> Privat auf deinem Server</div>
          </nav>
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
              {active.content_audit && <section className="panel audit-panel" aria-labelledby="content-audit-title">
                <div className="panel-head"><h2 id="content-audit-title">EPUB-Inhaltsprüfung</h2><span className={`audit-status ${active.content_audit.status === 'inventory-only' ? 'ok' : 'review'}`}>{active.content_audit.status === 'inventory-only' ? 'Inventur vollständig' : active.content_audit.status === 'audit-error' ? 'Prüfung fehlgeschlagen' : 'Prüfung erforderlich'}</span></div>
                <p className="helper">Quellinventur des EPUB-Spine im Vergleich zu den übernommenen Vorleseabschnitten. Die Prüfung ändert keine Inhalte und ersetzt keine redaktionelle Freigabe.</p>
                <div className="audit-grid">
                  <div><strong>{active.content_audit.summary.spine_items ?? 0}</strong><small>Spine-Einträge</small></div>
                  <div><strong>{active.content_audit.summary.retained_spine_items ?? 0}</strong><small>zugeordnet</small></div>
                  <div><strong>{active.content_audit.summary.skipped_spine_items ?? 0}</strong><small>nicht zugeordnet</small></div>
                  <div><strong>{active.content_audit.summary.mapping_coverage_percent ?? 0}%</strong><small>Text-Mapping-Abdeckung</small></div>
                  <div><strong>{(active.content_audit.summary.source_tables ?? 0) + (active.content_audit.summary.source_lists ?? 0)}</strong><small>Tabellen und Listen</small></div>
                  <div><strong>{active.content_audit.summary.source_images ?? 0}</strong><small>Abbildungen</small></div>
                  <div><strong>{active.content_audit.summary.source_mathml ?? 0}</strong><small>MathML-Elemente</small></div>
                  <div><strong>{(active.content_audit.summary.source_footnotes ?? 0) + (active.content_audit.summary.source_footnote_refs ?? 0)}</strong><small>Fußnoten und Verweise</small></div>
                </div>
                <p className="audit-note">Die Abdeckung zählt Nicht-Leerzeichen im extrahierten Text. Sie belegt die Zuordnung der übernommenen Textspannen, nicht die semantische Vollständigkeit jeder Abbildung oder Tabelle.</p>
                {active.content_audit.skipped.length > 0 && <details className="audit-skipped">
                  <summary>{active.content_audit.skipped.length} ausgelassene oder ungeklärte Quellressourcen</summary>
                  <ul>{active.content_audit.skipped.map((item, i) => <li key={`${item.href ?? item.index}-${i}`}>
                    <strong>{item.href ?? `Spine-Eintrag ${item.index + 1}`}</strong>
                    <span>{item.reason}</span>
                    {item.preview && <small>{item.preview}</small>}
                    {item.href && item.can_restore && <button className="audit-action" disabled={busy || saving || active.status !== 'Entwurf'} onClick={() => {void updateSourceOverride(item.href!, true)}}>Trotzdem in den Vorlesetext aufnehmen</button>}
                  </li>)}</ul>
                </details>}
                {active.content_audit.resources.filter(item => item.included_by_user).length > 0 && <details className="audit-skipped">
                  <summary>{active.content_audit.resources.filter(item => item.included_by_user).length} manuell wieder aufgenommene Quellen</summary>
                  <ul>{active.content_audit.resources.filter(item => item.included_by_user).map(item => <li key={item.href}>
                    <strong>{item.href}</strong><span>Wurde trotz der automatischen Auslassungsregel aufgenommen. {item.override_reason}</span>
                    <button className="audit-action" disabled={busy || saving || active.status !== 'Entwurf'} onClick={() => {void updateSourceOverride(item.href, false)}}>Automatische Auslassungsregel wieder anwenden</button>
                  </li>)}</ul>
                </details>}
                {(active.content_audit.review_elements ?? []).length > 0 && <details className="audit-elements">
                  <summary>{(active.content_audit.review_elements ?? []).length} komplexe Inhaltselemente zur menschlichen Prüfung</summary>
                  <p className="helper">Optional kannst du für Tabellen, Formeln, Abbildungen und Bilder eine eigene Sprecherfassung formulieren. Sie wird erst nach deiner ausdrücklichen Übernahme verwendet und ersetzt dann die bisherige Textbehandlung dieses Elements. Es wird kein Inhalt automatisch erfunden.</p>
                  {(active.content_audit.review_elements ?? []).map((element, i) => {
                    const draftKey = `${element.source_href}::${element.element_key}`
                    const value = speakerDrafts[draftKey] ?? element.speaker_text ?? ''
                    const inputId = `speaker-version-${i}`
                    return <div className="audit-element" key={draftKey}>
                      <div className="audit-element-heading"><strong>{element.kind}</strong><span>{element.source_href}</span>{element.approved && <em>Übernommen</em>}</div>
                      <p className="audit-element-source">{element.preview}</p>
                      {element.source_text && element.source_text !== element.preview && <small>Quelltextinhalt: {element.source_text}</small>}
                      {element.alt_text && element.alt_text !== element.preview && <small>Alternativtext: {element.alt_text}</small>}
                      <small>{element.handling}</small>
                      {element.can_edit ? <>
                        <label htmlFor={inputId}>Optionale Sprecherfassung</label>
                        <textarea id={inputId} rows={3} maxLength={1000} value={value} disabled={busy || saving || active.status !== 'Entwurf'} onChange={event => setSpeakerDrafts(previous => ({...previous, [draftKey]: event.target.value}))} placeholder="Nur den Inhalt formulieren, der tatsächlich vorgelesen werden soll."/>
                        <div className="audit-element-actions">
                          <button className="audit-action" disabled={busy || saving || active.status !== 'Entwurf' || !value.trim() || value.trim() === element.speaker_text.trim()} onClick={() => {void saveSpeakerOverride(element)}}>Sprecherfassung übernehmen</button>
                          {element.approved && <button className="audit-action secondary" disabled={busy || saving || active.status !== 'Entwurf'} onClick={() => {void removeSpeakerOverride(element)}}>Zur Originalfassung zurückkehren</button>}
                        </div>
                      </> : <small>Dieses Element ist aktuell keiner übernommenen Quellressource zugeordnet. Nimm den Quellabschnitt zuerst manuell auf, falls er vorgelesen werden soll.</small>}
                    </div>
                  })}
                </details>}
                {(active.content_audit.source_mapping ?? []).length > 0 && <details className="audit-mapping">
                  <summary>Quelltext-Zuordnung ({(active.content_audit.source_mapping ?? []).length} Textspannen)</summary>
                  <p className="helper">Offsets beziehen sich auf normalisierten extrahierten Text, nicht auf Bytepositionen im EPUB.</p>
                  <ol>{(active.content_audit.source_mapping ?? []).slice(0, 200).map((item, i) => <li key={`${item.chapter_index}-${item.source_href}-${item.output_start}-${i}`}>
                    <strong>{item.chapter_title || `Abschnitt ${item.chapter_index + 1}`}</strong>
                    <span>{item.source_href} · {item.mapping_status === 'mapped' ? `Quelle ${item.source_start}–${item.source_end}` : 'Quellposition nicht exakt auflösbar'}</span>
                  </li>)}</ol>
                  {(active.content_audit.source_mapping ?? []).length > 200 && <small>Es werden die ersten 200 von {(active.content_audit.source_mapping ?? []).length} Zuordnungen angezeigt.</small>}
                </details>}
                {active.content_audit.review_notes.map((note, i) => <p className="audit-note" key={i}>{note}</p>)}
                {active.content_audit.warnings.length > 0 && <details className="warning-details"><summary><AlertCircle size={16}/> {active.content_audit.warnings.length} Audit-Hinweise</summary><ul>{active.content_audit.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
              </section>}
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
                <p className="helper">Nur gezielt geprüfte Fachbegriffe eintragen. Der Buchtext bleibt unverändert. Bei einem laufenden Auftrag wird die Vertonung an einer sicheren Segmentgrenze angehalten. Nur Segmente mit geänderter tatsächlicher Aussprache werden neu erzeugt; gültige andere Audiodateien bleiben erhalten.</p>
                {glossaryUpdate.status === 'pending' && <p className="message" role="status" aria-live="polite"><LoaderCircle className="spinning" size={18}/>Glossaränderung wird sicher übernommen. Bereits laufende Sprachsegmente werden zuerst abgeschlossen.</p>}
                {glossaryUpdate.status === 'applied' && glossaryUpdate.total !== undefined && <p className="message success" role="status"><Check size={18}/>{glossaryUpdate.affected} von {glossaryUpdate.total} Segmenten betroffen; {glossaryUpdate.preserved} bereits erzeugte Audiodateien bleiben erhalten.</p>}
                {glossaryUpdate.status === 'failed' && <p className="message error" role="alert"><AlertCircle size={18}/>{glossaryUpdate.message || 'Glossaränderung konnte nicht sicher übernommen werden. Die Produktion bleibt pausiert.'}</p>}
                <button className="primary-btn" disabled={saving || glossaryUpdate.status === 'pending' || glossaryUpdate.status === 'failed'} onClick={saveGlossary}>{saving ? 'Speichere …' : glossaryUpdate.status === 'pending' ? 'Übernahme läuft …' : 'Glossaränderung übernehmen'}<Check size={18}/></button>
                <p className="helper">Während des M4B-Exports ist die Änderung aus Sicherheitsgründen gesperrt. Nach der Übernahme läuft eine zuvor aktive Produktion automatisch weiter; eine zuvor pausierte Produktion bleibt pausiert.</p>
              </div>
            </>}
            {view === 'voices' && active && <>
              <div className="page-heading"><div><p className="eyebrow">SPRECHERWAHL</p><h1>Deutsche Erzählerstimme</h1><p>Ausschließlich fertig trainierte Stimmen, keine hochgeladenen Referenzen oder Stimmklone.</p></div></div>
              {voices.every(voice => voice.status !== 'ready') && <div className="panel voice-notice" role="status"><LockKeyhole size={21}/><div><strong>{capabilities?.voice_installed ? 'Thorsten installiert – Hörfreigabe noch offen' : 'Thorsten noch nicht betriebsbereit'}</strong><p>{capabilities?.reason ?? 'Installationsstatus wird geprüft.'}</p></div></div>}
              <div className="voice-grid">{voices.map(voice => <section className="panel voice-card" key={voice.id} aria-label={voice.name}><div className="voice-avatar"><Volume2 size={26}/></div><div><span className="status-chip">{voice.status === 'ready' ? 'Freigegeben' : capabilities?.voice_installed ? capabilities?.voice_model_loaded ? 'Im GPU-Speicher · Freigabe offen' : capabilities?.voice_warmup_state === 'loading' ? 'Modell wird vorgeladen' : 'Installiert · Freigabe offen' : ['initializing', 'checking-cache', 'downloading', 'installing'].includes(capabilities?.voice_install_state ?? '') ? 'Installation läuft' : ['insufficient-space', 'cuda-unavailable', 'download-failed', 'installation-failed', 'voice-cuda-unavailable', 'bootstrap-failed'].includes(capabilities?.voice_install_state ?? '') ? 'Installation fehlgeschlagen' : 'Nicht installiert'}</span><h3>{voice.name}</h3><p>{voice.description}</p><small>Quelle und Lizenz: <a href={voice.source} target="_blank" rel="noopener noreferrer">{voice.license}</a></small>
                <div className="voice-sample-settings">
                  <label htmlFor="voice-sample-length">Länge der Hörprobe</label>
                  <select id="voice-sample-length" value={previewDurationSeconds} disabled={busy || previewTask?.status === 'running'} onChange={e => setPreviewDurationSeconds(Number(e.target.value) as 5|15|30|60)}>
                    <option value={5}>Ca. 5 Sekunden</option>
                    <option value={15}>Ca. 15 Sekunden</option>
                    <option value={30}>Ca. 30 Sekunden</option>
                    <option value={60}>Ca. 60 Sekunden</option>
                  </select>
                  <p className="helper">Bei der Standard-Hörprobe wird die Länge über ganze Sätze angenähert. Ein eigener Vorlese-Prompt hat seine natürliche Dauer.</p>
                </div>
                <div className="voice-actions"><button className="outline-btn" disabled={busy || !capabilities?.voice_preview_available || previewTask?.status === 'running'} onClick={() => {void startPreview()}}>{previewTask?.status === 'running' ? <LoaderCircle className="spinning" size={17}/> : <PlayCircle size={17}/>} {previewTask?.status === 'running' ? 'Hörprobe wird erzeugt …' : 'Echte Hörprobe erzeugen'}</button>
                <button className="primary-btn" disabled={busy || !previewPlayed || !previewAudioValid || previewTask?.status !== 'ready'} onClick={async () => {
                  try { await api('/api/voices/approve', token, {method:'POST'}); await showVoices(); setNotice('Stimme nach manueller Hörabnahme freigegeben.') }
                  catch (err) { setError((err as Error).message) }
                }}><Check size={17}/> Nach Anhören freigeben</button></div>
              </div></section>)}</div>
              <section className="panel voice-config-panel" aria-label="Thorsten Sprechkonfiguration">
                <div className="panel-head"><h2>Sprechtempo und Vorlese-Prompt</h2><span className="status-chip">Für dieses Buch</span></div>
                <p className="helper">Thorsten-CosyVoice3 unterstützt nach Herstellerangaben die Sprechgeschwindigkeit. Dieser Regler ändert das echte Modelltempo in Hörprobe und Buchproduktion.</p>
                <div className="voice-config-grid">
                  <div>
                    <label htmlFor="voice-speed">Sprechgeschwindigkeit</label>
                    <select id="voice-speed" value={voiceSpeed} disabled={busy || saving} onChange={event => {setVoiceSpeed(Number(event.target.value));setVoiceSettingsDirty(true)}}>
                      {VERIFIED_SPEEDS.map(value => <option key={value} value={value}>{value.toFixed(2)}× {value === 1 ? '· Originaltempo' : value < 1 ? '· ruhiger' : '· schneller'}</option>)}
                    </select>
                    <p className="helper">0,85× bis 1,15×. Kein Regler für Emotionen oder Betonung.</p>
                  </div>
                  <div>
                    <label htmlFor="voice-preview-text">Eigener Vorlese-Prompt (Hörprobentext)</label>
                    <textarea id="voice-preview-text" rows={4} maxLength={600} value={voicePreviewText} disabled={busy || saving} onChange={event => {setVoicePreviewText(event.target.value);setVoiceSettingsDirty(true)}} placeholder="Zum Beispiel einen Absatz aus deinem Fachbuch eingeben …"/>
                    <p className="helper">Wird tatsächlich gesprochen, nicht als Stilanweisung interpretiert. Leer lassen für die Standard-Hörprobe. Bei eigenem Text richtet sich die Dauer nach dessen Länge.</p>
                  </div>
                </div>
                <div className="voice-config-actions">
                  <button className="outline-btn" disabled={busy || saving || !voiceSettingsDirty} onClick={() => {void saveVoiceSettings()}}>{saving ? 'Speichere …' : 'Für das Buch speichern'}<Check size={17}/></button>
                  <span className="helper">{voiceSettingsDirty ? 'Die Hörprobe verwendet bereits diese Werte; die Buchproduktion erst nach dem Speichern.' : 'Die gespeicherte Geschwindigkeit gilt für alle neuen Buchsegmente.'}</span>
                </div>
                <p className="helper voice-prompt-notice">Freie Stil-Prompts wie „lebendiger sprechen“ werden für Thorstens SFT-Stimme nicht angeboten, weil eine verlässliche Wirkung dafür nicht belegt ist. Verwende denselben Text mit verschiedenen Geschwindigkeiten für einen hörbaren A/B-Vergleich.</p>
              </section>
              {previewTask && previewTask.status !== 'idle' && <section className="panel generation-progress" aria-label="Hörproben-Fortschritt">
                <div className="panel-head"><h2>{previewTask.status === 'ready' ? 'Hörprobe erzeugt' : previewTask.status === 'failed' ? 'Hörprobe fehlgeschlagen' : 'Hörprobe wird erzeugt'}</h2>
                  <span className="status-chip">{previewTask.status === 'running' ? 'In Bearbeitung' : previewTask.status === 'ready' ? 'Bereit zur Hörkontrolle' : 'Fehler'}</span>
                </div>
                {previewTask.speed !== undefined && <p className="helper">Getestetes Tempo: {previewTask.speed.toFixed(2)}× · {previewTask.custom_text ? 'Eigener Vorlesetext' : 'Standard-Hörprobe'}</p>}
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
              {audioUrl && previewTask?.status === 'ready' && !previewAudioError && <section className="panel audio-preview" aria-label="Hörprobe">
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
              </section>}
            </>}
            {view === 'export' && active && <>
              <div className="page-heading"><div><p className="eyebrow">PRODUKTION UND EXPORT</p><h1>Hörbuch erstellen</h1><p>{active.title}</p></div></div>
              <div className="panel production-panel">
                {capabilities?.asr && <p className="helper" role="status">ASR-Qualitätskontrolle: {capabilities.asr.message}</p>}
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
                      <strong>{formatDuration(Math.max(0, (
                        (['generated', 'completed', 'failed', 'cancelled'].includes(job.status)
                          ? Date.parse(job.updated_at || job.created_at) : clockNow)
                        - Date.parse(job.created_at)) / 1000))}</strong>
                      <small>Seit Auftragsbeginn, inklusive Pausen; bei Abschluss angehalten</small>
                    </div>
                    <div>
                      <span>Geschätzte Restzeit</span>
                      <strong>{job.status === 'paused' ? 'Pausiert'
                        : job.status === 'queued' ? 'Wartet auf GPU'
                        : job.status === 'failed' ? 'Fehlgeschlagen'
                        : job.status === 'cancelled' ? 'Abgebrochen'
                        : job.status === 'completed' ? 'Abgeschlossen'
                        : job.status === 'generated' ? 'Audio fertig'
                        : job.status === 'exporting' && exportActivity?.phase === 'finalizing' ? 'Datei wird abgeschlossen'
                        : job.status === 'exporting' ? (exportEta !== null ? 'ca. ' + formatDuration(exportEta) : 'Wird ermittelt …')
                        : job.status === 'running' && job.done === job.total ? 'Letzte Verarbeitungsschritte'
                        : job.status === 'running' ? (speechEta !== null ? 'ca. ' + formatDuration(speechEta) : 'Wird ermittelt …')
                        : 'Unbekannt'}</strong>
                      <small>{job.status === 'running' && speechEta !== null
                        ? 'Messung aus echten Segmentlaufzeiten; inklusive paralleler Worker'
                        : job.status === 'running' ? 'Nach mindestens 3 gültigen Segmentmessungen; bei Stillstand keine Prognose'
                        : job.status === 'exporting' && exportActivity?.phase === 'finalizing'
                          ? 'AAC-Container wird finalisiert; keine belastbare Restzeit'
                        : job.status === 'exporting' && exportEta !== null
                          ? 'Schätzung aus gemessener Exportgeschwindigkeit; Finale nicht enthalten'
                        : job.status === 'exporting' ? 'Nach mindestens 4 Export-Messpunkten; keine erfundene Restzeit'
                        : 'Keine aktive Restzeitprognose'}</small>
                    </div>
                  </div>
                  {(job.status === 'exporting' || exportPending || job.status === 'completed') && <div role="status" aria-live="polite">
                    <div className="progress-head"><strong>M4B-Export</strong><span>{exportPercent === null ? 'Vorbereitung …' : `${exportPercent} %`}</span></div>
                    <progress max={100} value={exportPercent ?? undefined} aria-label="Fortschritt M4B-Export"/>
                    <p>{job.status === 'completed' ? 'M4B-Datei vollständig erstellt und gespeichert.'
                      : exportActivity?.phase === 'finalizing' ? `${exportProcessed ?? job.total} von ${job.total} Segmenten verarbeitet · Datei wird abgeschlossen.`
                      : exportProcessed == null ? 'Export wird vorbereitet; noch keine bestätigten Segmente.'
                      : `${exportProcessed} von ${exportActivity?.total ?? job.total} Segmenten verarbeitet · Export läuft im Hintergrund.`}</p>
                  </div>}
                  <details className="activity-log" open={job.status === 'failed' ? true : undefined}>
                    <summary>Auftragsprotokoll ({jobActivity?.events.length ?? 0} Einträge)</summary>
                    <ol>{jobActivity?.events.map((entry, i) => <li key={i}><time dateTime={entry.at}>{new Date(entry.at).toLocaleTimeString('de-DE')}</time><span>{entry.message}</span></li>) ?? <li>Noch keine Einträge vorhanden.</li>}</ol>
                  </details>
                  {job.error && <p className="error" role="alert">{job.error}</p>}
                  {job.status === 'completed' && <p className="helper">Download-Modus: {supportsDirectSave() ? 'Direkt auf Datenträger speichern (streaming, ohne großen Browser-Blob)' : 'Browser-Fallback bis 96 MB. Für größere Bücher Chrome oder Edge verwenden.'}</p>}
                  {downloadPending && chapterDownloadIndex === null && !chapterZipPending && <div role="status" aria-live="polite" className="production-time-metrics">
                    <span>M4B-Download: {((downloadProgress?.received ?? 0) / (1024 * 1024)).toFixed(1)} MB empfangen{downloadProgress?.total ? ` von ${(downloadProgress.total / (1024 * 1024)).toFixed(1)} MB` : ''} · {downloadEta !== null ? `ca. ${formatDuration(downloadEta)} verbleibend` : 'Restzeit noch nicht bestimmbar'}</span>
                    <progress aria-label="M4B-Download" aria-valuetext={transferLabel} max={100} value={transferPercent ?? undefined}/>
                    <small>{transferLabel}</small>
                  </div>}
                  <div className="job-actions">
                    {['queued','running'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('pause')}><PauseCircle size={17}/> Pausieren</button>}
                    {['paused','failed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('resume')}><PlayCircle size={17}/> Fortsetzen</button>}
                    {job.status === 'cancelled' && <button className="primary-btn" disabled={busy || !capabilities?.tts_generation} onClick={() => productionAction('start')}><PlayCircle size={17}/> Neuen Versuch starten</button>}
                    {['queued','running','paused','failed'].includes(job.status) && <button className="outline-btn" disabled={busy} onClick={() => productionAction('cancel')}><StopCircle size={17}/> Abbrechen</button>}
                    {['generated','completed'].includes(job.status) && <button className="primary-btn" disabled={busy} onClick={() => productionAction('export')}><FileAudio size={17}/> M4B erstellen</button>}
                    {job.status === 'completed' && <p className="helper">Das komplette M4B wird zum Speicherziel gestreamt. Für einzelne Hörproben den Segmentplayer unten verwenden.</p>}
                    {job.status === 'completed' && <button className="primary-btn" disabled={busy || downloadPending} onClick={() => {void startM4bDownload()}}><FileAudio size={17}/> {downloadPending ? 'M4B wird übertragen …' : 'M4B herunterladen'}</button>}
                    {downloadPending && <button className="outline-btn" onClick={() => downloadAbort.current?.abort()}><StopCircle size={17}/> Download abbrechen</button>}
                  </div>
                  {job.done > 0 && <div className="segment-preview">
                    <label htmlFor="segment-index">Segment zum Anhören (0–{job.done-1})</label>
                    <input id="segment-index" type="number" min={0} max={job.done-1} value={Math.min(sampleIndex,job.done-1)} onChange={e => setSampleIndex(Number(e.target.value))}/>
                    <button className="outline-btn" disabled={busy} onClick={() => loadAudio(`/api/projects/${active.id}/jobs/${job.id}/segments/${Math.min(sampleIndex,job.done-1)}`)}><PlayCircle size={17}/> Segment anhören</button>
                  </div>}
                  {quality && <section className="quality-check" aria-label="Qualitätskontrolle">
                    <h3>Qualitätskontrolle</h3>
                    <p>{quality.checked} von {quality.total} Segmenten per ASR geprüft. {quality.total - quality.checked} nicht per ASR geprüft. Automatische Hinweise erfordern menschliche Kontrolle.</p>
                    {quality.issues.length === 0 ? <p>Keine gemeldeten Auffälligkeiten. Eine vollständige Hörabnahme ersetzt dies nicht.</p> : <ul>{quality.issues.map(issue =>
                      <li key={issue.index}><strong>Segment {issue.index + 1}</strong> · {issue.warnings.join(' ') || issue.reason || 'ASR-Prüfung nicht durchgeführt.'}
                        {issue.asr_wer != null && <small> ASR-Verdacht: WER {Math.round(issue.asr_wer * 100)} %, CER {Math.round((issue.asr_cer ?? 0) * 100)} %; kein Beweis für einen Sprachfehler.</small>}
                        <div className="job-actions">
                          <button className="outline-btn" disabled={busy} onClick={() => loadAudio('/api/projects/' + active.id + '/jobs/' + job.id + '/segments/' + issue.index)}><PlayCircle size={17}/> Anhören</button>
                          <button className="outline-btn" disabled={busy || !['generated','completed'].includes(job.status)} onClick={() => retrySegment(issue.index)}><RotateCcw size={17}/> Neu erzeugen</button>
                        </div>
                      </li>
                    )}</ul>}
                  </section>}
                  {audioUrl && chapterPreviewIndex === null && <audio src={audioUrl} controls className="chapter-player"/>}
                </>}
              </div>
              {job && <section className="panel completed-chapters" aria-label="Fertige Hörbuchkapitel">
                <div className="panel-head">
                  <h2>Fertige Kapitel</h2>
                  <span className="status-chip">{completedChapters.length} verfügbar</span>
                </div>
                <p className="helper">Jedes fertiggestellte Kapitel steht als vollständige M4A-Datei bereit. Alle Kapitel lassen sich mit lesbaren Dateinamen gesammelt als ZIP speichern. Zum Vorhören gibt es einen WAV-Ausschnitt.</p>
                {job.status === 'completed' && completedChapters.length > 0 && <div className="job-actions">
                  <button className="primary-btn" disabled={busy || downloadPending} onClick={() => {void startChapterZipDownload()}}><FileAudio size={17}/>{chapterZipPending ? 'ZIP wird übertragen …' : 'Alle Kapitel als ZIP herunterladen'}</button>
                </div>}
                {chapterZipPending && <div role="status" aria-live="polite" className="chapter-file-download">
                  <span>Kapitel-ZIP: {((downloadProgress?.received ?? 0) / (1024 * 1024)).toFixed(1)} MB empfangen · {downloadEta !== null ? `ca. ${formatDuration(downloadEta)} verbleibend` : 'Gesamtgröße nicht übermittelt; Restzeit offen'}</span>
                  <progress aria-label="Download aller Kapitel" aria-valuetext={transferLabel} max={100} value={transferPercent ?? undefined}/>
                  <small>{transferLabel}</small>
                  <button className="text-btn" onClick={() => downloadAbort.current?.abort()}>Abbrechen</button>
                </div>}
                {chapterListError && <p className="message error" role="alert">Kapiteldateien konnten nicht geladen werden: {chapterListError}</p>}
                {completedChapters.length === 0
                  ? <p className="chapter-empty">Noch kein Kapitel vollständig vertont und als Audiodatei bereitgestellt.</p>
                  : <ol className="chapter-files">
                    {completedChapters.map(chapter => <li className="chapter-file" key={chapter.index}>
                      <div className="chapter-file-header">
                        <span className="chapter-file-icon"><FileAudio size={21}/></span>
                        <div className="chapter-file-info">
                          <strong>{chapter.title}</strong>
                          <small>Kapitel {chapter.index + 1} · {formatDuration(chapter.duration_seconds)} · {(chapter.bytes / (1024 * 1024)).toFixed(1)} MB · M4A</small>
                        </div>
                        <span className="audit-status ok">Fertig</span>
                      </div>
                      <div className="chapter-file-actions">
                        <button className="outline-btn" disabled={busy || downloadPending} onClick={() => {void previewChapter(chapter)}}>
                          {chapterPreviewPending === chapter.index ? <LoaderCircle className="spinning" size={17}/> : <PlayCircle size={17}/>}
                          {chapterPreviewPending === chapter.index ? 'Lade Hörprobe …' : 'Vorhören'}
                        </button>
                        <button className="outline-btn" disabled={busy || downloadPending} onClick={() => {void startChapterDownload(chapter)}}>
                          <FileAudio size={17}/> {chapterDownloadIndex === chapter.index ? 'Wird gespeichert …' : 'M4A herunterladen'}
                        </button>
                      </div>
                      {audioUrl && chapterPreviewIndex === chapter.index && <div className="chapter-file-audition"><small className="chapter-audition-label">Hörprobe: {formatDuration(chapter.preview_seconds)} aus diesem Kapitel</small><audio className="chapter-file-player" src={audioUrl} controls preload="metadata" aria-label={`Kapitel ${chapter.index + 1} vorhören`} onError={() => setError('Der Browser konnte die WAV-Hörprobe nicht abspielen. Bitte die Datei prüfen.')}/></div>}
                      {downloadPending && chapterDownloadIndex === chapter.index && <div className="chapter-file-download" role="status">
                        <span>{((downloadProgress?.received ?? 0) / (1024 * 1024)).toFixed(1)} MB übertragen · {downloadEta !== null ? `ca. ${formatDuration(downloadEta)} verbleibend` : 'Restzeit noch nicht bestimmbar'}</span>
                        <progress aria-label={`Download Kapitel ${chapter.index + 1}`} aria-valuetext={transferLabel} max={100} value={transferPercent ?? undefined}/>
                        <small>{transferLabel}</small>
                        <button className="text-btn" onClick={() => downloadAbort.current?.abort()}>Abbrechen</button>
                      </div>}
                    </li>)}
                  </ol>}
              </section>}
            </>}
            <footer className="footer">German Audiobook Studio <span>·</span> Fachbücher. Deutsche Sprachausgabe. Geschützte Verarbeitung.</footer>
          </main>
        </div>
      )}
    </div>
  )
}
