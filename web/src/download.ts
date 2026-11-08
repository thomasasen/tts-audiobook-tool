/** Authenticated and memory-bounded M4B download without persistent URL tokens. */

export const FALLBACK_MAX_BYTES = 96 * 1024 * 1024

export type DownloadProgress = {
  received: number; total: number | null
  etaSeconds: number | null; measuredAt: number
}

type FileSink = {
  write(chunk: Uint8Array): Promise<void>
  close(): Promise<void>
  abort(): Promise<void>
}
type FileHandle = { createWritable(): Promise<FileSink> }
type FilePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string
    types: Array<{ description: string; accept: Record<string, string[]> }>
  }) => Promise<FileHandle>
}

export function supportsDirectSave(): boolean {
  return typeof (window as FilePickerWindow).showSaveFilePicker === 'function'
}

async function downloadAuthenticatedAudio(
  url: string,
  token: string,
  name: string,
  signal: AbortSignal,
  onProgress: (value: DownloadProgress) => void,
  format: 'm4b' | 'm4a' | 'zip',
): Promise<{ bytes: number; mode: 'stream' | 'fallback' }> {
  const label = format === 'm4b' ? 'M4B' : format === 'm4a' ? 'Kapitel-M4A' : 'Kapitel-ZIP'
  const picker = (window as FilePickerWindow).showSaveFilePicker
  // The picker must be the *first* awaited operation after a button click:
  // Chromium requires transient user activation. Never fetch before it.
  const sink = picker
    ? await (await picker({
      suggestedName: name,
      types: [{ description: format === 'm4b' ? 'M4B-Hörbuch' : format === 'm4a' ? 'M4A-Hörbuchkapitel' : 'Kapitelarchiv',
        accept: format === 'zip' ? { 'application/zip': ['.zip'] } :
          { 'audio/mp4': [format === 'm4b' ? '.m4b' : '.m4a'] } }],
    })).createWritable()
    : null

  let finished = false
  let response: Response | undefined
  try {
    if (signal.aborted) throw new DOMException('Download abgebrochen.', 'AbortError')
    response = await fetch(url, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
      signal,
    })
    if (!response.ok) throw new Error(`${label}-Download fehlgeschlagen (HTTP ${response.status}).`)
    const type = response.headers.get('content-type') ?? ''
    const correctType = format === 'zip'
      ? type.startsWith('application/zip') || type.startsWith('application/octet-stream')
      : type.startsWith('audio/mp4') || type.startsWith('application/octet-stream')
    if (!correctType) {
      throw new Error(`Der Server hat keine gültige ${label}-Datei geliefert.`)
    }
    const lengthHeader = response.headers.get('content-length')
    const length = lengthHeader === null ? null : Number(lengthHeader)
    if (length !== null && (!Number.isSafeInteger(length) || length < 0)) {
      throw new Error(`Ungültige ${label}-Dateigröße vom Server.`)
    }
    if (!sink && length !== null && length > FALLBACK_MAX_BYTES) {
      throw new Error(`Diese ${label}-Datei überschreitet 96 MB. Bitte Chrome oder Edge zum direkten Speichern verwenden.`)
    }
    if (!response.body) {
      throw new Error('Der Browser unterstützt keinen sicheren gestreamten Dateiempfang.')
    }
    const reader = response.body.getReader()
    let received = 0
    let firstByteAt: number | null = null
    let firstChunkLength = 0
    const chunks: Uint8Array[] = []
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (signal.aborted) throw new DOMException('Download abgebrochen.', 'AbortError')
        received += value.byteLength
        const transferNow = performance.now()
        if (firstByteAt === null) {
          firstByteAt = transferNow
          firstChunkLength = received
        }
        if (!sink && received > FALLBACK_MAX_BYTES) {
          throw new Error(`${label} überschreitet das Speicherlimit von 96 MB. Chrome oder Edge verwenden.`)
        }
        if (sink) {
          await sink.write(value)
        } else {
          chunks.push(value)
        }
        const seconds = (transferNow - firstByteAt) / 1000
        const rate = seconds >= 2 ? (received - firstChunkLength) / seconds : 0
        // A trustworthy transfer ETA requires a known Content-Length and
        // a sustained actual byte rate. Streaming ZIP has no such total.
        const etaSeconds = length !== null && length > received && rate > 0
          ? Math.max(1, Math.ceil((length - received) / rate)) : null
        onProgress({ received, total: length, etaSeconds, measuredAt: Date.now() })
      }
    } catch (error) {
      await reader.cancel().catch(() => undefined)
      throw error
    } finally {
      reader.releaseLock()
    }
    if (!received || (length !== null && received !== length)) {
      throw new Error(`Unvollständiger ${label}-Transfer: Datei wurde nicht gespeichert.`)
    }
    if (signal.aborted) throw new DOMException('Download abgebrochen.', 'AbortError')
    if (sink) {
      await sink.close()
    } else {
      // Only the deliberately bounded fallback allocates a browser Blob.
      const blob = new Blob(chunks, { type: format === 'zip' ? 'application/zip' : 'audio/mp4' })
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      try {
        link.href = objectUrl
        link.download = name
        link.click()
      } finally {
        // Do not revoke synchronously: the browser still needs the object URL
        // until the asynchronous save has been picked up by the download.
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
      }
    }
    finished = true
    return { bytes: received, mode: sink ? 'stream' : 'fallback' }
  } finally {
    if (!finished && sink) await sink.abort().catch(() => undefined)
    // Cancel any outstanding body after an error. The bearer token never
    // appears in a URL, download name, persistent browser store or exception.
    if (!finished && response?.body && !response.body.locked) {
      await response.body.cancel().catch(() => undefined)
    }
  }
}

/** Existing full-book download contract, including browser test semantics. */
export function downloadAuthenticatedM4b(
  url: string, token: string, name: string, signal: AbortSignal,
  onProgress: (value: DownloadProgress) => void,
) {
  return downloadAuthenticatedAudio(url, token, name, signal, onProgress, 'm4b')
}

/** Chapter download uses the same memory-bounded, authenticated streaming. */
export function downloadAuthenticatedChapter(
  url: string, token: string, name: string, signal: AbortSignal,
  onProgress: (value: DownloadProgress) => void,
) {
  return downloadAuthenticatedAudio(url, token, name, signal, onProgress, 'm4a')
}

/** Streams a named chapter ZIP without buffering multi-hour audio in memory. */
export function downloadAuthenticatedChapterZip(
  url: string, token: string, name: string, signal: AbortSignal,
  onProgress: (value: DownloadProgress) => void,
) {
  return downloadAuthenticatedAudio(url, token, name, signal, onProgress, 'zip')
}
