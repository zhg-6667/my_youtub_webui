"use client"

import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { ChangeEvent, FormEvent, Suspense, useCallback, useEffect, useRef, useState } from "react"
import { ChevronRight, ExternalLink, Link2, Loader2, Play, Plus, Trash2, Upload } from "lucide-react"

import {
  ExecutionMode,
  LocalDirection,
  TaskSummary,
  VideoLinkRecord,
  createTask,
  createVideoLink,
  continueTask,
  deleteTask,
  deleteVideoLink,
  listVideoLinks,
  listTasks,
  uploadLocalTask,
} from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { BilibiliBatchSchedule, beijingTime } from "@/components/bilibili-batch-schedule"
import { statusBadgeClass } from "@/lib/status"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

function isActive(status: string) {
  return status === "queued" || status === "running"
}

function isAwaitingAction(status: string) {
  return status === "paused"
}

function formatTime(value: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function shortUrl(url: string) {
  return url.replace(/^https?:\/\/(www\.)?/, "")
}

function formatDuration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return ""
  const safe = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(safe / 3600)
  const minutes = Math.floor((safe % 3600) / 60)
  const secs = safe % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`
}

function activeCount(tasks: TaskSummary[]) {
  return tasks.filter((t) => isActive(t.status)).length
}

function createUrlFields(url: string) {
  if (!url) return { youtube: "", bilibili: "" }
  if (/bilibili\.com/i.test(url)) return { youtube: "", bilibili: url }
  return { youtube: url, bilibili: "" }
}

export function HomeContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { activeTasksText, stageLabel, statusLabel, t } = useI18n()
  const initialUrlFields = createUrlFields((searchParams.get("url") || "").trim())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const subtitleInputRef = useRef<HTMLInputElement>(null)
  const createFormRef = useRef<HTMLFormElement>(null)
  const [youtubeUrl, setYoutubeUrl] = useState(initialUrlFields.youtube)
  const [bilibiliUrl, setBilibiliUrl] = useState(initialUrlFields.bilibili)
  const [recordUrl, setRecordUrl] = useState("")
  const [localFile, setLocalFile] = useState<File | null>(null)
  const [localSubtitleFile, setLocalSubtitleFile] = useState<File | null>(null)
  const [localDirection, setLocalDirection] = useState<LocalDirection>("en-zh")
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("auto")
  const [autoBilibiliUpload, setAutoBilibiliUpload] = useState(false)
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [historyLimit, setHistoryLimit] = useState(100)
  const [videoLinks, setVideoLinks] = useState<VideoLinkRecord[]>([])
  const [error, setError] = useState("")
  const [recordError, setRecordError] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [recording, setRecording] = useState(false)
  const [deletingRecordId, setDeletingRecordId] = useState<string | null>(null)
  const [pendingVideoLinkRecordId, setPendingVideoLinkRecordId] = useState<string | null>(null)
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([])
  const [deletingTasks, setDeletingTasks] = useState(false)
  const [taskDeleteError, setTaskDeleteError] = useState("")
  const [continuingTaskId, setContinuingTaskId] = useState<string | null>(null)
  const [taskContinueError, setTaskContinueError] = useState("")

  function applyTaskList(list: TaskSummary[]) {
    const selectable = new Set(list.filter((task) => task.status !== "running").map((task) => task.id))
    setTasks(list)
    setSelectedTaskIds((selected) => {
      const next = selected.filter((id) => selectable.has(id))
      return next.length === selected.length ? selected : next
    })
  }

  async function refreshTasks() {
    const { tasks: list } = await listTasks(historyLimit)
    applyTaskList(list)
  }

  async function refreshVideoLinks() {
    const { video_links: list } = await listVideoLinks()
    setVideoLinks(list)
  }

  const fillCreateUrl = useCallback((url: string) => {
    setError("")
    setPendingVideoLinkRecordId(null)
    setLocalFile(null)
    setLocalSubtitleFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
    if (subtitleInputRef.current) {
      subtitleInputRef.current.value = ""
    }
    if (/bilibili\.com/i.test(url)) {
      setBilibiliUrl(url)
      setYoutubeUrl("")
    } else {
      setYoutubeUrl(url)
      setBilibiliUrl("")
    }
    window.setTimeout(() => createFormRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0)
  }, [])

  useEffect(() => {
    let cancelled = false

    const loadTasks = async () => {
      try {
        const { tasks: list } = await listTasks(historyLimit)
        if (!cancelled) applyTaskList(list)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : t.home.loadError)
      }
    }

    loadTasks()
    const interval = window.setInterval(loadTasks, 2000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [t.home.loadError, historyLimit])

  useEffect(() => {
    let cancelled = false

    const loadVideoLinks = async () => {
      try {
        const { video_links: list } = await listVideoLinks()
        if (!cancelled) setVideoLinks(list)
      } catch (err) {
        if (!cancelled) setRecordError(err instanceof Error ? err.message : t.home.videoLinkLoadError)
      }
    }

    loadVideoLinks()
    const interval = window.setInterval(loadVideoLinks, 5000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [t.home.videoLinkLoadError])

  function selectLocalFile(event: ChangeEvent<HTMLInputElement>) {
    setError("")
    setPendingVideoLinkRecordId(null)
    setLocalFile(event.target.files?.[0] || null)
  }

  function selectLocalSubtitleFile(event: ChangeEvent<HTMLInputElement>) {
    setError("")
    setLocalSubtitleFile(event.target.files?.[0] || null)
  }

  async function submitTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError("")
    const submittedUrl = youtubeUrl.trim() || bilibiliUrl.trim()
    if (!submittedUrl && !localFile) return
    setSubmitting(true)
    try {
      const created = localFile
        ? await uploadLocalTask(localFile, localDirection, localSubtitleFile, executionMode, autoBilibiliUpload)
        : await createTask(submittedUrl, executionMode, autoBilibiliUpload)
      setYoutubeUrl("")
      setBilibiliUrl("")
      setLocalFile(null)
      setLocalSubtitleFile(null)
      setAutoBilibiliUpload(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ""
      }
      if (subtitleInputRef.current) {
        subtitleInputRef.current.value = ""
      }
      if (pendingVideoLinkRecordId) {
        try {
          await deleteVideoLink(pendingVideoLinkRecordId)
          setPendingVideoLinkRecordId(null)
          await refreshVideoLinks()
        } catch (deleteErr) {
          setRecordError(deleteErr instanceof Error ? deleteErr.message : t.home.videoLinkDeleteError)
        }
      } else {
        refreshTasks().catch(() => undefined)
        router.push(`/tasks/${created.id}`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t.home.createError)
    } finally {
      setSubmitting(false)
    }
  }

  async function submitVideoLinkRecord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const url = recordUrl.trim()
    if (!url) return
    setRecording(true)
    setRecordError("")
    try {
      await createVideoLink(url)
      setRecordUrl("")
      await refreshVideoLinks()
    } catch (err) {
      setRecordError(err instanceof Error ? err.message : t.home.videoLinkCreateError)
    } finally {
      setRecording(false)
    }
  }

  async function removeVideoLinkRecord(recordId: string) {
    setDeletingRecordId(recordId)
    setRecordError("")
    try {
      await deleteVideoLink(recordId)
      await refreshVideoLinks()
    } catch (err) {
      setRecordError(err instanceof Error ? err.message : t.home.videoLinkDeleteError)
    } finally {
      setDeletingRecordId(null)
    }
  }

  function createFromVideoLink(record: VideoLinkRecord) {
    fillCreateUrl(record.url)
    setPendingVideoLinkRecordId(record.id)
  }

  const queued = activeCount(tasks)
  const hasUrl = Boolean(youtubeUrl.trim() || bilibiliUrl.trim())
  const hasLocalFile = Boolean(localFile)
  const canSubmit = Boolean((hasUrl || hasLocalFile) && !submitting)
  const selectableTaskIds = tasks.filter((task) => task.status !== "running").map((task) => task.id)
  const selectedTaskCount = selectedTaskIds.length
  const allSelectableTasksSelected =
    selectableTaskIds.length > 0 && selectableTaskIds.every((id) => selectedTaskIds.includes(id))

  function toggleTaskSelection(taskId: string, selected: boolean) {
    setTaskDeleteError("")
    setSelectedTaskIds((current) => {
      if (selected) {
        return current.includes(taskId) ? current : [...current, taskId]
      }
      return current.filter((id) => id !== taskId)
    })
  }

  function toggleAllTaskSelection(selected: boolean) {
    setTaskDeleteError("")
    setSelectedTaskIds(selected ? selectableTaskIds : [])
  }

  async function deleteSelectedTasks() {
    const deletable = new Set(selectableTaskIds)
    const ids = selectedTaskIds.filter((id) => deletable.has(id))
    if (ids.length === 0) return

    setDeletingTasks(true)
    setTaskDeleteError("")
    try {
      const results = await Promise.allSettled(ids.map((id) => deleteTask(id).then(() => id)))
      const deletedIds = results
        .filter((result): result is PromiseFulfilledResult<string> => result.status === "fulfilled")
        .map((result) => result.value)
      const failed = results.find((result): result is PromiseRejectedResult => result.status === "rejected")

      setSelectedTaskIds((current) => current.filter((id) => !deletedIds.includes(id)))
      await refreshTasks()

      if (failed) {
        const message = failed.reason instanceof Error ? failed.reason.message : t.home.taskBatchDeleteError
        setTaskDeleteError(`${t.home.taskBatchDeletePartialError} ${message}`)
      }
    } catch (err) {
      setTaskDeleteError(err instanceof Error ? err.message : t.home.taskBatchDeleteError)
    } finally {
      setDeletingTasks(false)
    }
  }

  async function continueRemainingStages(taskId: string) {
    setContinuingTaskId(taskId)
    setTaskContinueError("")
    try {
      await continueTask(taskId, "auto")
      await refreshTasks()
    } catch (err) {
      setTaskContinueError(err instanceof Error ? err.message : t.task.continueError)
    } finally {
      setContinuingTaskId(null)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6">
        <div className="grid gap-6 xl:grid-cols-[minmax(280px,0.5fr)_minmax(360px,1fr)_minmax(360px,1fr)] xl:items-start">
          <Card>
            <CardHeader>
              <CardTitle>{t.home.createTitle}</CardTitle>
            </CardHeader>
            <CardContent>
              <form ref={createFormRef} onSubmit={submitTask} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="youtube-url">{t.home.youtubeLabel}</Label>
                  <Input
                    id="youtube-url"
                    value={youtubeUrl}
                    onChange={(event) => {
                      setPendingVideoLinkRecordId(null)
                      setYoutubeUrl(event.target.value)
                    }}
                    placeholder="https://www.youtube.com/watch?v=..."
                    disabled={Boolean(bilibiliUrl.trim()) || hasLocalFile}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="bilibili-url">{t.home.bilibiliLabel}</Label>
                  <Input
                    id="bilibili-url"
                    value={bilibiliUrl}
                    onChange={(event) => {
                      setPendingVideoLinkRecordId(null)
                      setBilibiliUrl(event.target.value)
                    }}
                    placeholder="https://www.bilibili.com/video/BV..."
                    disabled={Boolean(youtubeUrl.trim()) || hasLocalFile}
                  />
                </div>
                <div className="grid gap-3 2xl:grid-cols-[1fr_180px]">
                  <div className="space-y-2">
                    <Label htmlFor="local-video">{t.home.localVideoLabel}</Label>
                    <Input
                      ref={fileInputRef}
                      id="local-video"
                      type="file"
                      accept="video/*,.mp4,.mov,.m4v,.mkv,.webm,.avi,.flv,.wmv"
                      onChange={selectLocalFile}
                      disabled={hasUrl}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="local-direction">{t.home.localDirectionLabel}</Label>
                    <Select
                      value={localDirection}
                      onValueChange={(value) => setLocalDirection(value as LocalDirection)}
                      disabled={hasUrl}
                    >
                      <SelectTrigger id="local-direction" className="h-10">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="en-zh">{t.home.localEnZh}</SelectItem>
                        <SelectItem value="zh-en">{t.home.localZhEn}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="local-subtitle">{t.home.localSubtitleLabel}</Label>
                  <Input
                    ref={subtitleInputRef}
                    id="local-subtitle"
                    type="file"
                    accept=".srt"
                    onChange={selectLocalSubtitleFile}
                    disabled={hasUrl || !hasLocalFile}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t.home.localSubtitleHelp}
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="execution-mode">{t.home.executionModeLabel}</Label>
                  <Select
                    value={executionMode}
                    onValueChange={(value) => setExecutionMode(value as ExecutionMode)}
                  >
                    <SelectTrigger id="execution-mode" className="h-10">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">{t.home.executionAuto}</SelectItem>
                      <SelectItem value="manual">{t.home.executionManual}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <label className="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">
                  <Input
                    type="checkbox"
                    checked={autoBilibiliUpload}
                    onChange={(event) => setAutoBilibiliUpload(event.target.checked)}
                    className="mt-0.5 size-4"
                  />
                  <span className="grid gap-1">
                    <span className="font-medium">{t.home.autoBilibiliUpload}</span>
                    <span className="text-xs text-muted-foreground">{t.home.autoBilibiliUploadHelp}</span>
                  </span>
                </label>
                <div className="flex items-center justify-between gap-3">
                  {queued > 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {activeTasksText(queued)}
                    </p>
                  ) : (
                    <span />
                  )}
                  <Button type="submit" disabled={!canSubmit}>
                    {hasLocalFile ? <Upload className="size-4" /> : <Play className="size-4" />}
                    {submitting ? t.home.submitting : t.home.createTask}
                  </Button>
                </div>
              </form>

              {error ? (
                <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <div className="contents">
            <Card className="min-w-0 xl:order-3">
              <CardHeader>
                <CardTitle>{t.home.videoLinkRecords} ({videoLinks.length})</CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={submitVideoLinkRecord} className="flex flex-col gap-3 sm:flex-row lg:flex-col xl:flex-row">
                  <div className="min-w-0 flex-1 space-y-2">
                    <Label htmlFor="record-url">{t.home.videoLinkLabel}</Label>
                    <Input
                      id="record-url"
                      value={recordUrl}
                      onChange={(event) => setRecordUrl(event.target.value)}
                      placeholder="https://www.youtube.com/watch?v=..."
                    />
                  </div>
                  <Button type="submit" className="self-end" disabled={!recordUrl.trim() || recording}>
                    <Plus className="size-4" />
                    {recording ? t.home.videoLinkRecording : t.home.videoLinkRecord}
                  </Button>
                </form>

                {recordError ? (
                  <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {recordError}
                  </div>
                ) : null}

                {videoLinks.length === 0 ? (
                  <div className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                    {t.home.videoLinkEmpty}
                  </div>
                ) : (
                  <ScrollArea className={videoLinks.length > 5 ? "mt-4 h-[23.75rem]" : "mt-4"}>
                    <ul className="flex flex-col divide-y divide-border/60">
                      {videoLinks.map((item) => (
                        <li key={item.id} className="flex min-h-[4.75rem] flex-col gap-3 py-3 xl:flex-row xl:items-center">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-zinc-900">{shortUrl(item.url)}</p>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <Badge className="border-sky-200 bg-sky-50 text-sky-700">
                                {item.source_name === "bilibili" ? "Bilibili" : "YouTube"}
                              </Badge>
                              {item.duration_seconds !== null ? <span>{formatDuration(item.duration_seconds)}</span> : null}
                              <span>{formatTime(item.updated_at)}</span>
                              {item.last_task_id ? <span>· {t.home.videoLinkTaskCreated}</span> : null}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <Button type="button" size="sm" onClick={() => createFromVideoLink(item)}>
                              <Link2 className="size-4" />
                              {t.home.videoLinkCreateTask}
                            </Button>
                            <Button
                              nativeButton={false}
                              render={
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  aria-label={t.home.videoLinkOpen}
                                  title={t.home.videoLinkOpen}
                                />
                              }
                              variant="outline"
                              size="icon-sm"
                            >
                              <ExternalLink className="size-4" />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t.home.videoLinkDelete}
                              disabled={deletingRecordId === item.id}
                              onClick={() => removeVideoLinkRecord(item.id)}
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </ScrollArea>
                )}
              </CardContent>
            </Card>

            <Card className="min-w-0 xl:order-2">
              <CardHeader className="flex flex-col gap-3">
                <CardTitle>{t.home.taskHistory} ({tasks.length})</CardTitle>
                <div className="flex flex-wrap items-center gap-2">
                  <BilibiliBatchSchedule
                    selectedTasks={tasks.filter((task) => selectedTaskIds.includes(task.id))}
                    onCreated={() => { setSelectedTaskIds([]); void refreshTasks() }}
                  />
                  {tasks.length > 0 ? <>
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Input
                        type="checkbox"
                        checked={allSelectableTasksSelected}
                        disabled={selectableTaskIds.length === 0 || deletingTasks}
                        onChange={(event) => toggleAllTaskSelection(event.target.checked)}
                        className="size-4"
                      />
                      <span>{t.home.taskSelectAll}</span>
                    </label>
                    <span className="text-xs text-muted-foreground">
                      {selectedTaskCount} {t.home.taskSelectedCount}
                    </span>
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      disabled={selectedTaskCount === 0 || deletingTasks}
                      onClick={deleteSelectedTasks}
                    >
                      <Trash2 className="size-4" />
                      {deletingTasks ? t.home.taskDeletingSelected : t.home.taskDeleteSelected}
                    </Button>
                  </> : null}
                </div>
              </CardHeader>
              <CardContent className="px-0">
                {taskDeleteError ? (
                  <div className="mx-6 mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {taskDeleteError}
                  </div>
                ) : null}
                {taskContinueError ? (
                  <div className="mx-6 mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {taskContinueError}
                  </div>
                ) : null}
                {tasks.length === 0 ? (
                  <div className="px-6 py-12 text-center text-sm text-muted-foreground">
                    {t.home.empty}
                  </div>
                ) : (
                  <ScrollArea className={tasks.length > 5 ? "h-[22.5rem]" : ""}>
                    <ul className="flex flex-col">
                      {tasks.map((item) => (
                        <li key={item.id} className="flex min-h-[4.5rem] border-b border-border/60 last:border-b-0">
                          <label className="flex items-center px-6 pr-0">
                            <Input
                              type="checkbox"
                              aria-label={`${t.home.taskSelectOne} ${item.title || shortUrl(item.url)}`}
                              checked={selectedTaskIds.includes(item.id)}
                              disabled={item.status === "running" || deletingTasks}
                              onChange={(event) => toggleTaskSelection(item.id, event.target.checked)}
                              className="size-4"
                            />
                          </label>
                          <Link
                            href={`/tasks/${item.id}`}
                            className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-muted/60"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-left font-medium text-zinc-900">
                                {item.title || shortUrl(item.url)}
                              </p>
                              <div className="mt-1 flex min-w-0 items-center gap-2 overflow-hidden whitespace-nowrap text-xs text-muted-foreground">
                                <Badge className={statusBadgeClass(item.status)}>{statusLabel(item.status)}</Badge>
                                <span className="shrink-0">{formatTime(item.created_at)}</span>
                                {isActive(item.status) && item.current_stage ? (
                                  <span className="min-w-0 truncate">· {stageLabel(item.current_stage)}</span>
                                ) : null}
                                {isAwaitingAction(item.status) ? (
                                  <span className="shrink-0">· {t.status.paused}</span>
                                ) : null}
                                {item.status === "running" ? (
                                  <span className="shrink-0">· {t.home.taskRunningLocked}</span>
                                ) : null}
                              </div>
                              {item.bilibili_batch_id && item.bilibili_scheduled_publish_at ? (
                                <p className="mt-1 truncate text-xs text-sky-700">
                                  {t.home.batchPublication}: {beijingTime(item.bilibili_scheduled_publish_at)} · {t.home[`batchStatus_${item.bilibili_upload_status}`] || item.bilibili_upload_status}
                                </p>
                              ) : null}
                            </div>
                            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                          </Link>
                          {isAwaitingAction(item.status) ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="my-auto mr-4 shrink-0"
                              aria-label={t.task.continueAutoTask}
                              title={t.task.continueAutoTask}
                              disabled={continuingTaskId !== null || deletingTasks}
                              onClick={() => continueRemainingStages(item.id)}
                            >
                              {continuingTaskId === item.id ? (
                                <Loader2 className="size-4 animate-spin" />
                              ) : (
                                <Play className="size-4" />
                              )}
                            </Button>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </ScrollArea>
                )}
                {tasks.length >= historyLimit ? <div className="px-6 pt-3">
                  <Button type="button" variant="outline" size="sm" onClick={() => setHistoryLimit((current) => current + 100)}>{t.home.loadMoreTasks}</Button>
                </div> : null}
              </CardContent>
            </Card>
          </div>
        </div>
    </div>
  )
}

export default function Home() {
  return (
    <Suspense fallback={null}>
      <HomeContent />
    </Suspense>
  )
}
