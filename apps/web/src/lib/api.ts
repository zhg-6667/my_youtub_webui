function configuredApiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim()
  if (configured) return configured.replace(/\/$/, "")

  if (typeof window === "undefined") return "http://127.0.0.1:8000"
  return ""
}

export const API_BASE = configuredApiBase()

export type StageStatus = "pending" | "running" | "succeeded" | "failed"
export type TaskStatus = "queued" | "running" | "paused" | "succeeded" | "failed"
export type ExecutionMode = "auto" | "manual"
export type BilibiliPublishMode = "now" | "scheduled"
export type BilibiliUploadStatus = "queued" | "running" | "succeeded" | "failed"
export type WatermarkMaskMode = "patch" | "blur"
export type WatermarkMaskStatus = "queued" | "running" | "succeeded" | "failed"

export type VideoLinkRecord = {
  id: string
  url: string
  duration_seconds: number | null
  created_at: string
  updated_at: string
  last_task_id: string | null
  source_name?: string
}

export type YouTubeChannelMonitor = {
  id: string
  channel_url: string
  channel_id: string
  title: string | null
  enabled: boolean
  check_interval_minutes: number
  latest_count: number
  last_checked_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export type TaskStage = {
  task_id: string
  name: string
  label: string
  status: StageStatus
  progress: number | null
  started_at: string | null
  completed_at: string | null
  last_message: string | null
  error_message: string | null
}

export type Task = {
  id: string
  url: string
  title: string | null
  status: TaskStatus
  current_stage: string | null
  session_path: string | null
  final_video_path: string | null
  display_video_path: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  execution_mode: ExecutionMode
  auto_bilibili_upload: boolean
  source_name?: string
  stages: TaskStage[]
}

export type BilibiliUploadJob = {
  id: string
  task_id: string
  title: string
  publish_mode: BilibiliPublishMode
  dtime: string | null
  cover_path: string | null
  status: BilibiliUploadStatus
  log_path: string | null
  error_message: string | null
  return_code: number | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type WatermarkMaskJob = {
  id: string
  task_id: string
  mode: WatermarkMaskMode
  status: WatermarkMaskStatus
  input_video_path: string | null
  output_video_path: string | null
  region_json: string | null
  log_path: string | null
  error_message: string | null
  return_code: number | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type CookieInfo = {
  exists: boolean
  size: number
  updated_at: number | null
  content: string
}

export type OpenAISettings = {
  base_url: string
  api_key: string
  has_api_key: boolean
  model: string
  translate_concurrency: string
}

export type OpenAIModels = {
  models: string[]
}

export type YtdlpSettings = {
  proxy_port: string
}

export type BilibiliSettings = {
  author_config_json: string
  author_tags_json: string
  author_tids_json: string
}

export type MailSettings = {
  enabled: boolean
  smtp_host: string
  smtp_port: string
  smtp_username: string
  smtp_password: string
  has_smtp_password: boolean
  from_address: string
  to_addresses: string
  smtp_security: "none" | "tls" | "ssl"
  notify_on_success: boolean
  notify_on_failure: boolean
}

export type LocalDirection = "en-zh" | "zh-en"
export type CutInterval = { start: number; end: number }

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
    cache: "no-store",
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${response.status}`)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json()
}

export type TaskSummary = {
  id: string
  url: string
  title: string | null
  status: TaskStatus
  current_stage: string | null
  final_video_path: string | null
  display_video_path: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  execution_mode?: ExecutionMode
  auto_bilibili_upload?: boolean
  source_name?: string
}

export function getCurrentTask() {
  return request<Task | null>("/api/tasks/current")
}

export async function getTaskLog(taskId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/tasks/${taskId}/log`, { cache: "no-store" })
  if (!response.ok) {
    throw new Error(`Failed to load log: ${response.status}`)
  }
  return response.text()
}

export function listTasks(limit = 100) {
  return request<{ tasks: TaskSummary[] }>(`/api/tasks?limit=${limit}`)
}

export function listVideoLinks(limit = 100) {
  return request<{ video_links: VideoLinkRecord[] }>(`/api/video-links?limit=${limit}`)
}

export function createVideoLink(url: string) {
  return request<VideoLinkRecord>("/api/video-links", {
    method: "POST",
    body: JSON.stringify({ url }),
  })
}

export function deleteVideoLink(recordId: string) {
  return request<void>(`/api/video-links/${recordId}`, { method: "DELETE" })
}

export function listYouTubeChannelMonitors() {
  return request<{ monitors: YouTubeChannelMonitor[] }>("/api/youtube-channel-monitors")
}

export function createYouTubeChannelMonitor(
  channelUrl: string,
  checkIntervalMinutes = 30,
  latestCount = 2,
) {
  return request<YouTubeChannelMonitor>("/api/youtube-channel-monitors", {
    method: "POST",
    body: JSON.stringify({
      channel_url: channelUrl,
      check_interval_minutes: checkIntervalMinutes,
      latest_count: latestCount,
    }),
  })
}

export function updateYouTubeChannelMonitor(
  monitorId: string,
  payload: Partial<Pick<YouTubeChannelMonitor, "enabled" | "check_interval_minutes" | "latest_count">>,
) {
  return request<YouTubeChannelMonitor>(`/api/youtube-channel-monitors/${monitorId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export function deleteYouTubeChannelMonitor(monitorId: string) {
  return request<void>(`/api/youtube-channel-monitors/${monitorId}`, { method: "DELETE" })
}

export function checkYouTubeChannelMonitor(monitorId: string) {
  return request<{ monitor: YouTubeChannelMonitor; checked: number; added: number; seen: number }>(
    `/api/youtube-channel-monitors/${monitorId}/check`,
    { method: "POST" },
  )
}

export function getTask(taskId: string) {
  return request<Task>(`/api/tasks/${taskId}`)
}

export function deleteTask(taskId: string) {
  return request<void>(`/api/tasks/${taskId}`, { method: "DELETE" })
}

export function rerunTask(taskId: string) {
  return request<Task>(`/api/tasks/${taskId}/rerun`, { method: "POST" })
}

export function resumeTask(taskId: string) {
  return request<Task>(`/api/tasks/${taskId}/resume`, { method: "POST" })
}

export function continueTask(taskId: string, executionMode?: ExecutionMode) {
  return request<Task>(`/api/tasks/${taskId}/continue`, {
    method: "POST",
    body: JSON.stringify(executionMode ? { execution_mode: executionMode } : {}),
  })
}

export function redoStage(taskId: string, stageName: string) {
  return request<Task>(`/api/tasks/${taskId}/stages/${stageName}/redo`, { method: "POST" })
}

export function createTask(
  url: string,
  executionMode: ExecutionMode = "auto",
  autoBilibiliUpload = false,
) {
  return request<Task>("/api/tasks", {
    method: "POST",
    body: JSON.stringify({
      url,
      execution_mode: executionMode,
      auto_bilibili_upload: autoBilibiliUpload,
    }),
  })
}

export async function uploadLocalTask(
  file: File,
  direction: LocalDirection,
  subtitleFile: File | null = null,
  executionMode: ExecutionMode = "auto",
  autoBilibiliUpload = false,
) {
  const form = new FormData()
  form.append("direction", direction)
  form.append("file", file)
  if (subtitleFile) {
    form.append("subtitle_file", subtitleFile)
  }
  form.append("execution_mode", executionMode)
  form.append("auto_bilibili_upload", autoBilibiliUpload ? "true" : "false")

  const response = await fetch(`${API_BASE}/api/tasks/upload`, {
    method: "POST",
    body: form,
    cache: "no-store",
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<Task>
}

export function getCookieInfo() {
  return request<CookieInfo>("/api/cookies/youtube")
}

export function saveCookie(content: string) {
  return request<CookieInfo>("/api/cookies/youtube", {
    method: "POST",
    body: JSON.stringify({ content }),
  })
}

export function getOpenAISettings() {
  return request<OpenAISettings>("/api/settings/openai")
}

export function saveOpenAISettings(settings: {
  base_url: string
  api_key: string
  clear_api_key?: boolean
  model: string
  translate_concurrency: string
}) {
  return request<OpenAISettings>("/api/settings/openai", {
    method: "POST",
    body: JSON.stringify(settings),
  })
}

export function getOpenAIModels(settings: {
  base_url: string
  api_key: string
}) {
  return request<OpenAIModels>("/api/settings/openai/models", {
    method: "POST",
    body: JSON.stringify(settings),
  })
}

export function getYtdlpSettings() {
  return request<YtdlpSettings>("/api/settings/ytdlp")
}

export function saveYtdlpSettings(settings: YtdlpSettings) {
  return request<YtdlpSettings>("/api/settings/ytdlp", {
    method: "POST",
    body: JSON.stringify(settings),
  })
}

export function getBilibiliSettings() {
  return request<BilibiliSettings>("/api/settings/bilibili")
}

export function saveBilibiliSettings(settings: BilibiliSettings) {
  return request<BilibiliSettings>("/api/settings/bilibili", {
    method: "POST",
    body: JSON.stringify(settings),
  })
}

export function getMailSettings() {
  return request<MailSettings>("/api/settings/mail")
}

type MailSettingsSave = Omit<MailSettings, "has_smtp_password"> & {
  clear_smtp_password?: boolean
}

export function saveMailSettings(settings: MailSettingsSave) {
  return request<MailSettings>("/api/settings/mail", {
    method: "POST",
    body: JSON.stringify(settings),
  })
}

export function getCutIntervals(taskId: string) {
  return request<{ cut_intervals: CutInterval[] }>(`/api/tasks/${taskId}/cut-intervals`)
}

export function saveCutIntervals(taskId: string, cutIntervals: CutInterval[]) {
  return request<{ cut_intervals: CutInterval[] }>(`/api/tasks/${taskId}/cut-intervals`, {
    method: "PUT",
    body: JSON.stringify({ cut_intervals: cutIntervals }),
  })
}

export function trimVideo(taskId: string, cutIntervals?: CutInterval[]) {
  return request<Task>(`/api/tasks/${taskId}/trim`, {
    method: "POST",
    body: JSON.stringify(cutIntervals?.length ? { cut_intervals: cutIntervals } : {}),
  })
}

export function concatVideo(taskId: string) {
  return request<Task>(`/api/tasks/${taskId}/concat`, { method: "POST" })
}

export function createBilibiliUploadJob(
  taskId: string,
  payload: { title: string; publish_mode: BilibiliPublishMode; dtime?: string | null; cover_file?: File | null },
) {
  const form = new FormData()
  form.append("title", payload.title)
  form.append("publish_mode", payload.publish_mode)
  if (payload.dtime) {
    form.append("dtime", payload.dtime)
  }
  if (payload.cover_file) {
    form.append("cover_file", payload.cover_file)
  }

  return fetch(`${API_BASE}/api/tasks/${taskId}/bilibili-upload`, {
    method: "POST",
    body: form,
    cache: "no-store",
  }).then(async (response) => {
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || `Request failed: ${response.status}`)
    }
    return response.json() as Promise<BilibiliUploadJob>
  })
}

export function translateBilibiliTitle(taskId: string, title?: string) {
  return request<{ title: string }>(`/api/tasks/${taskId}/bilibili-title/translate`, {
    method: "POST",
    body: JSON.stringify({ title }),
  })
}

export function getBilibiliUploadJob(jobId: string) {
  return request<BilibiliUploadJob>(`/api/bilibili-upload-jobs/${jobId}`)
}

export async function getBilibiliUploadJobLog(jobId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/bilibili-upload-jobs/${jobId}/log`, { cache: "no-store" })
  if (!response.ok) {
    throw new Error(`Failed to load Bilibili upload log: ${response.status}`)
  }
  return response.text()
}

export function createWatermarkMaskJob(taskId: string, payload: { mode: WatermarkMaskMode }) {
  return request<WatermarkMaskJob>(`/api/tasks/${taskId}/watermark-mask`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function getWatermarkMaskJob(jobId: string) {
  return request<WatermarkMaskJob>(`/api/watermark-mask-jobs/${jobId}`)
}

export async function getWatermarkMaskJobLog(jobId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/watermark-mask-jobs/${jobId}/log`, { cache: "no-store" })
  if (!response.ok) {
    throw new Error(`Failed to load watermark mask log: ${response.status}`)
  }
  return response.text()
}

export function finalVideoUrl(taskId: string) {
  return `${API_BASE}/api/tasks/${taskId}/artifact/final-video`
}

export function finalVideoDownloadUrl(taskId: string) {
  return `${API_BASE}/api/tasks/${taskId}/artifact/final-video?download=1`
}
