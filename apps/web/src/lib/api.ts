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
export type BilibiliUploadStatus = "scheduled" | "queued" | "running" | "succeeded" | "failed" | "cancelled"
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

export type YouTubeAuthorVideo = {
  video_id: string
  title: string
  url: string
  duration_seconds: number | null
  thumbnail_url: string
  published_at: string | null
  published_date: string | null
  metadata_checked: boolean
  task_id: string | null
  task_status: TaskStatus | null
}

export type YouTubeVideoMetadata = Pick<YouTubeAuthorVideo, "video_id" | "published_at" | "published_date" | "metadata_checked">

export function fetchYouTubeVideoMetadata(items: { video_id: string; channel_id: string | null }[], signal?: AbortSignal) {
  return request<{ videos: YouTubeVideoMetadata[] }>("/api/youtube-videos/metadata", {
    method: "POST", body: JSON.stringify({ items }), signal,
  })
}

export type BatchTaskResult = {
  url: string
  video_id?: string
  task_id: string | null
  task_status: TaskStatus | null
  status: "created" | "existing" | "failed"
  error: string | null
}

export function createTasksBatch(urls: string[], executionMode: ExecutionMode, autoBilibiliUpload: boolean) {
  return request<{ results: BatchTaskResult[]; created: number; existing: number; failed: number }>("/api/tasks/batch", {
    method: "POST", body: JSON.stringify({ urls, execution_mode: executionMode, auto_bilibili_upload: autoBilibiliUpload }),
  })
}

export type YouTubeAuthorResult = {
  input_url: string
  channel_url: string
  channel_id: string | null
  title: string
  videos: YouTubeAuthorVideo[]
  shorts: YouTubeAuthorVideo[]
  errors: Partial<Record<"channel" | "videos" | "shorts", string>>
}

export function lookupYouTubeAuthors(channelUrls: string[], latestCount: number) {
  return request<{ channels: YouTubeAuthorResult[] }>("/api/youtube-authors/lookup", {
    method: "POST",
    body: JSON.stringify({ channel_urls: channelUrls, latest_count: latestCount }),
  })
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

export type BilibiliPublishInfo = {
  source_title: string | null
  source_description: string | null
  source_url: string | null
  source_upload_date: string | null
  author: string | null
  author_id: string | null
  author_url: string | null
  duration_seconds: number | null
  view_count: number | null
  like_count: number | null
  comment_count: number | null
  source_categories: string[]
  source_tags: string[]
  publish_description: string
  publish_tags: string[]
  publish_tid: number
  cover_available: boolean
}

export type BilibiliCoverProject = {
  text: string
  font_family: string
  font_size: number
  color: string
  x: number
  y: number
}

export type BilibiliCoverState = {
  available: boolean
  source: "edited" | "manual" | "source" | null
  manual_available: boolean
  edited_available: boolean
  project: BilibiliCoverProject | null
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
  bilibili_upload_title: string | null
  bilibili_upload_description: string | null
  bilibili_upload_tags: string | null
  bilibili_upload_tid: number | null
  bilibili_topic_name: string | null
  bilibili_topic_id: number | null
  bilibili_topic_mission_id: number | null
  bilibili_season_id: number | null
  bilibili_section_id: number | null
  bilibili_publish_mode: BilibiliPublishMode
  bilibili_dtime: string | null
  bilibili_upload_after: string | null
  bilibili_publish_info?: BilibiliPublishInfo | null
  source_name?: string
  stages: TaskStage[]
}

export type BilibiliUploadJob = {
  id: string
  task_id: string
  title: string
  description: string | null
  tags: string | null
  tid: number | null
  topic_name: string | null
  topic_id: number | null
  topic_mission_id: number | null
  season_id: number | null
  section_id: number | null
  aid: number | null
  bvid: string | null
  publish_mode: BilibiliPublishMode
  dtime: string | null
  upload_after: string | null
  cover_path: string | null
  status: BilibiliUploadStatus
  log_path: string | null
  error_message: string | null
  return_code: number | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type BilibiliTopic = {
  topic_id: number
  topic_name: string
  mission_id: number | null
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

export type BilibiliSeason = {
  season_id: number
  season_title: string
  section_id: number
  section_title: string
}

export type BilibiliSettings = {
  author_config_json: string
  author_tags_json: string
  author_tids_json: string
  pokemon_topic_config_json: string
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
  bilibili_upload_title?: string | null
  bilibili_publish_mode?: BilibiliPublishMode
  bilibili_dtime?: string | null
  bilibili_upload_after?: string | null
  source_name?: string
  bilibili_upload_status?: BilibiliUploadStatus
  bilibili_scheduled_publish_at?: string | null
  bilibili_batch_id?: string | null
  bilibili_upload_blocked?: boolean | number
}

export type BilibiliPublishBatchItem = {
  position: number
  task_id: string
  task_created_at: string
  publish_at: string
  title: string
  description: string
  tags: string
  tid: number
  topic_name: string | null
  season_id: number | null
  section_id: number | null
  has_cover: boolean
  job_id: string | null
  status: BilibiliUploadStatus | "preview" | "removed"
  error_message: string | null
  bvid: string | null
  can_retry: boolean
}

export type BilibiliPublishBatch = {
  id: string
  status: "draft" | "confirmed"
  start_date: string
  start_time: string
  daily_times: string[]
  timezone: "Asia/Shanghai"
  min_lead_hours: number
  skipped: { publish_at: string; reason: "past" | "lead" | "occupied" | "before_start" }[]
  items: BilibiliPublishBatchItem[]
  created_at: string
  confirmed_at: string | null
}

export function previewBilibiliBatch(payload: { task_ids: string[]; start_date: string; start_time?: string; daily_times: string[] }) {
  return request<BilibiliPublishBatch>("/api/bilibili-publish-batches/preview", { method: "POST", body: JSON.stringify(payload) })
}

export function confirmBilibiliBatch(id: string) {
  return request<BilibiliPublishBatch>(`/api/bilibili-publish-batches/${id}/confirm`, { method: "POST" })
}

export function listBilibiliBatches() {
  return request<{ batches: BilibiliPublishBatch[] }>("/api/bilibili-publish-batches")
}

export function cancelPendingBilibiliBatch(id: string) {
  return request<BilibiliPublishBatch>(`/api/bilibili-publish-batches/${id}/cancel-pending`, { method: "POST" })
}

export function retryBilibiliBatchJob(batchId: string, jobId: string) {
  return request<BilibiliPublishBatch>(`/api/bilibili-publish-batches/${batchId}/jobs/${jobId}/retry`, { method: "POST" })
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

export function openTaskDirectory(taskId: string) {
  return request<{ path: string }>(`/api/tasks/${taskId}/open-folder`, { method: "POST" })
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

export function updateBilibiliUploadSettings(
  taskId: string,
  payload: {
    auto_bilibili_upload: boolean
    title?: string | null
    description?: string | null
    tags?: string | null
    tid?: number | null
    topic_name?: string | null
    topic_id?: number | null
    topic_mission_id?: number | null
    season_id?: number | null
    section_id?: number | null
    publish_mode: BilibiliPublishMode
    dtime?: string | null
    upload_after?: string | null
  },
) {
  return request<Task>(`/api/tasks/${taskId}/bilibili-upload-settings`, {
    method: "PUT",
    body: JSON.stringify(payload),
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

export function trimVideo(taskId: string, cutIntervals?: CutInterval[], sourceVideoFile?: File | null) {
  if (sourceVideoFile) {
    const form = new FormData()
    form.append("cut_intervals", JSON.stringify(cutIntervals || []))
    form.append("source_video_file", sourceVideoFile)
    return fetch(`${API_BASE}/api/tasks/${taskId}/trim`, {
      method: "POST",
      body: form,
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `Request failed: ${response.status}`)
      }
      return response.json() as Promise<Task>
    })
  }

  const body: { cut_intervals?: CutInterval[] } = {}
  if (cutIntervals?.length) body.cut_intervals = cutIntervals
  return request<Task>(`/api/tasks/${taskId}/trim`, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function fastTrimVideo(taskId: string, cutIntervals?: CutInterval[]) {
  return request<Task>(`/api/tasks/${taskId}/trim/fast`, {
    method: "POST",
    body: JSON.stringify(cutIntervals?.length ? { cut_intervals: cutIntervals } : {}),
  })
}

export function fastTrimReplaceSourceVideo(taskId: string, cutIntervals?: CutInterval[]) {
  return request<Task>(`/api/tasks/${taskId}/trim/fast-replace-source`, {
    method: "POST",
    body: JSON.stringify(cutIntervals?.length ? { cut_intervals: cutIntervals } : {}),
  })
}

export function concatVideo(taskId: string) {
  return request<Task>(`/api/tasks/${taskId}/concat`, { method: "POST" })
}

export function createBilibiliUploadJob(
  taskId: string,
  payload: {
    title: string
    description?: string | null
    tags?: string | null
    tid?: number | null
    topic_name?: string | null
    topic_id?: number | null
    topic_mission_id?: number | null
    season_id?: number | null
    section_id?: number | null
    publish_mode: BilibiliPublishMode
    dtime?: string | null
    upload_after?: string | null
    cover_file?: File | null
  },
) {
  const form = new FormData()
  form.append("title", payload.title)
  if (payload.description !== undefined && payload.description !== null) {
    form.append("description", payload.description)
  }
  if (payload.tags !== undefined && payload.tags !== null) {
    form.append("tags", payload.tags)
  }
  if (payload.tid !== undefined && payload.tid !== null) {
    form.append("tid", String(payload.tid))
  }
  if (payload.topic_name !== undefined && payload.topic_name !== null) {
    form.append("topic_name", payload.topic_name)
  }
  if (payload.topic_id !== undefined && payload.topic_id !== null) {
    form.append("topic_id", String(payload.topic_id))
  }
  if (payload.topic_mission_id !== undefined && payload.topic_mission_id !== null) {
    form.append("topic_mission_id", String(payload.topic_mission_id))
  }
  if (payload.season_id !== undefined && payload.season_id !== null) {
    form.append("season_id", String(payload.season_id))
  }
  if (payload.section_id !== undefined && payload.section_id !== null) {
    form.append("section_id", String(payload.section_id))
  }
  form.append("publish_mode", payload.publish_mode)
  if (payload.dtime) {
    form.append("dtime", payload.dtime)
  }
  if (payload.upload_after) {
    form.append("upload_after", payload.upload_after)
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

export function getBilibiliSeasons() {
  return request<{ seasons: BilibiliSeason[] }>("/api/bilibili/seasons")
}

export function resolveBilibiliTopic(name: string) {
  return request<BilibiliTopic>(`/api/bilibili/topics/resolve?name=${encodeURIComponent(name)}`)
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

export function sourceCoverUrl(taskId: string) {
  return `${API_BASE}/api/tasks/${taskId}/artifact/source-cover`
}

export function bilibiliCoverUrl(taskId: string) {
  return `${API_BASE}/api/tasks/${taskId}/artifact/bilibili-cover`
}

export function bilibiliCoverBackgroundUrl(taskId: string) {
  return `${API_BASE}/api/tasks/${taskId}/artifact/bilibili-cover?background=true`
}

export function getBilibiliCover(taskId: string) {
  return request<BilibiliCoverState>(`/api/tasks/${taskId}/bilibili-cover`)
}

async function coverFormRequest(path: string, method: "PUT" | "DELETE", form?: FormData) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    body: form,
    cache: "no-store",
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<BilibiliCoverState>
}

export function saveBilibiliManualCover(taskId: string, file: File) {
  const form = new FormData()
  form.append("file", file)
  return coverFormRequest(`/api/tasks/${taskId}/bilibili-cover/manual`, "PUT", form)
}

export function deleteBilibiliManualCover(taskId: string) {
  return coverFormRequest(`/api/tasks/${taskId}/bilibili-cover/manual`, "DELETE")
}

export function saveBilibiliEditedCover(taskId: string, file: File, project: BilibiliCoverProject) {
  const form = new FormData()
  form.append("file", file)
  form.append("project_json", JSON.stringify(project))
  return coverFormRequest(`/api/tasks/${taskId}/bilibili-cover/edited`, "PUT", form)
}

export function deleteBilibiliEditedCover(taskId: string) {
  return coverFormRequest(`/api/tasks/${taskId}/bilibili-cover/edited`, "DELETE")
}
