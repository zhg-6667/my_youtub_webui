"use client"

import { useEffect, useState } from "react"
import { CalendarClock, ListChecks, Loader2, Plus, X } from "lucide-react"

import {
  API_BASE, BilibiliPublishBatch, BilibiliPublishBatchItem, TaskSummary, bilibiliCoverUrl,
  cancelPendingBilibiliBatch, confirmBilibiliBatch, listBilibiliBatches,
  previewBilibiliBatch, retryBilibiliBatchJob,
} from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const copy = {
  zh: {
    create: "批量排期发布", manage: "发布排期", help: "所有时间均为北京时间（UTC+8），指视频在 B 站公开的目标时间。系统提前上传并设置定时发布，按任务创建时间从早到晚安排。",
    start: "开始日期", startTime: "开始时间", startFrom: "开始排期", startHelp: "从选择的日期和时间开始，按每天的发布时间点依次排期。", times: "每天的发布时间", add: "添加时间点", remove: "移除时间点", preview: "生成完整排期预览", confirm: "确认创建发布排期",
    invalid: "请仅勾选处理成功且有成品的视频；已有投稿或发布安排的任务不可重复加入。以下任务暂不可排期：",
    selected: "个视频已选择", empty: "暂无发布批次。选择任务历史中的成品视频后创建排期。",
    loading: "加载中", error: "操作失败，请重试。", previewTitle: "完整排期预览", sequence: "顺序", title: "视频发布标题", created: "任务创建时间", publish: "公开发布时间（北京时间）",
    details: "查看发布信息", description: "简介", tags: "标签", category: "分区 ID", topic: "话题", season: "合集 / 小节 ID", cover: "查看封面", noCover: "自动截帧封面", edit: "打开任务详情修改",
    skipped: "以下时刻已跳过", past: "时刻已过", lead: "提前量不足 30 分钟", occupied: "已有发布安排", before_start: "早于开始时间",
    timing: "确认提交后立即进入上传队列，多视频按顺序依次上传；每项任务结束后间隔 1 分钟再执行下一项。按预览时间设置定时公开，发布时间至少需预留 30 分钟。", refresh: "刷新", cancel: "取消未上传项", retry: "重试此项",
    manageHelp: "可取消尚未开始上传的项目。已经上传的定时稿件，请前往 B 站管理。稿件提交成功表示 B 站已接收投稿，实际公开状态以 B 站为准。后台需要保持运行以执行尚未上传的排期。",
    confirmed: "排期已创建", changed: "任务选择或排期设置已变化，请重新生成预览。", startMismatch: "后端未使用所选的开始日期和时间，请重启后端服务后重新生成预览。", back: "返回设置", close: "关闭", status: "状态",
    previewStatus: "待确认", scheduled: "等待上传", queued: "等待上传", running: "上传投稿中", succeeded: "定时稿件已提交", failed: "失败", cancelled: "已取消", removed: "原任务记录已删除",
    noTags: "未设置", scheduleAt: "批次创建于", noChange: "修改日期或时间后，请重新生成预览。",
  },
  en: {
    create: "Schedule selected", manage: "Publication schedules", help: "All times are Beijing time (UTC+8) and mean the target public release time on Bilibili. Videos upload in advance and are ordered by task creation time, oldest first.",
    start: "Start date", startTime: "Start time", startFrom: "Schedule from", startHelp: "Assign videos to daily publication slots on or after the selected start date and time.", times: "Daily publication times", add: "Add time", remove: "Remove time", preview: "Preview full schedule", confirm: "Confirm publication schedule",
    invalid: "Select only successful tasks with finished videos. Tasks already submitted or scheduled cannot be added again. Unavailable tasks:",
    selected: "videos selected", empty: "No publication batches. Select finished videos in task history to create a schedule.",
    loading: "Loading", error: "Request failed. Please retry.", previewTitle: "Full schedule preview", sequence: "Order", title: "Video publication title", created: "Task created", publish: "Public release (Beijing time)",
    details: "Publishing details", description: "Description", tags: "Tags", category: "Category ID", topic: "Topic", season: "Collection / section ID", cover: "View cover", noCover: "Automatic video frame", edit: "Edit in task details",
    skipped: "Skipped slots", past: "Already passed", lead: "Less than 30 minutes ahead", occupied: "Already scheduled", before_start: "Before selected start time",
    timing: "Confirmation immediately queues all selected videos for upload, one at a time, with a 1-minute interval after each task finishes. Public release follows the preview and must be at least 30 minutes ahead.", refresh: "Refresh", cancel: "Cancel pending uploads", retry: "Retry item",
    manageHelp: "Cancel items whose upload has not started. Manage uploaded scheduled submissions on Bilibili. Submitted means Bilibili accepted the submission; verify public visibility on Bilibili. Keep the backend running for pending uploads.",
    confirmed: "Schedule created", changed: "The selection or schedule settings changed. Generate a new preview.", startMismatch: "The backend did not apply the selected start date and time. Restart the backend and generate a new preview.", back: "Back to settings", close: "Close", status: "Status",
    previewStatus: "Unconfirmed", scheduled: "Upload queued", queued: "Upload queued", running: "Uploading", succeeded: "Scheduled submission accepted", failed: "Failed", cancelled: "Cancelled", removed: "Original task record deleted",
    noTags: "Not set", scheduleAt: "Batch created", noChange: "After changing the date or times, generate a new preview.",
  },
}

function todayInBeijing(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(now)
}

function currentTimeInBeijing(now = new Date()) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now)
}

export function beijingTime(value: string) {
  const date = /^\d+$/.test(value) ? new Date(Number(value) * 1000) : new Date(value)
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date)
}

export function BilibiliBatchSchedule({ selectedTasks, onCreated }: {
  selectedTasks: TaskSummary[]
  onCreated: () => void
}) {
  const { language } = useI18n()
  const c = copy[language]
  const [mode, setMode] = useState<"create" | "manage" | null>(null)
  const [startDate, setStartDate] = useState(todayInBeijing)
  const [startTime, setStartTime] = useState(currentTimeInBeijing)
  const [times, setTimes] = useState(["11:00", "19:00"])
  const [preview, setPreview] = useState<BilibiliPublishBatch | null>(null)
  const [previewSelection, setPreviewSelection] = useState("")
  const [batches, setBatches] = useState<BilibiliPublishBatch[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const selectionKey = selectedTasks.map((task) => task.id).sort().join(",")
  const invalid = selectedTasks.filter((task) => task.status !== "succeeded" || !task.display_video_path || /[/\\]video_source\.mp4$/.test(task.display_video_path) || Boolean(task.bilibili_upload_blocked))
  const validTimes = times.length > 0 && times.every((time) => /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time))
  const validStartTime = /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(startTime)
  const hasPreview = preview && previewSelection === selectionKey
    && preview.start_date === startDate && preview.start_time === startTime

  useEffect(() => {
    if (mode !== "manage") return
    let cancelled = false
    async function load() {
      try {
        const result = await listBilibiliBatches()
        if (!cancelled) setBatches(result.batches)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : copy[language].error)
      }
    }
    void load()
    const interval = window.setInterval(load, 5000)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [mode, language])

  function invalidate() { setPreview(null); setError(""); setNotice("") }

  async function generatePreview() {
    setBusy(true)
    setError("")
    setPreview(null)
    try {
      const result = await previewBilibiliBatch({ task_ids: selectedTasks.map((task) => task.id), start_date: startDate, start_time: startTime, daily_times: times })
      if (result.start_date !== startDate || result.start_time !== startTime) {
        throw new Error(c.startMismatch)
      }
      setPreview(result)
      setPreviewSelection(selectionKey)
    } catch (err) {
      setError(err instanceof Error ? err.message : c.error)
    } finally { setBusy(false) }
  }

  async function confirm() {
    if (!hasPreview) return
    setBusy(true)
    setError("")
    try {
      const result = await confirmBilibiliBatch(preview.id)
      setBatches((current) => [result, ...current.filter((batch) => batch.id !== result.id)])
      setNotice(c.confirmed)
      setMode("manage")
      setPreview(null)
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? err.message : c.error)
    } finally { setBusy(false) }
  }

  async function changeBatch(batch: BilibiliPublishBatch, item?: BilibiliPublishBatchItem) {
    setBusy(true)
    setError("")
    try {
      const result = item?.job_id
        ? await retryBilibiliBatchJob(batch.id, item.job_id)
        : await cancelPendingBilibiliBatch(batch.id)
      setBatches((current) => current.map((entry) => entry.id === result.id ? result : entry))
    } catch (err) { setError(err instanceof Error ? err.message : c.error) }
    finally { setBusy(false) }
  }

  function statusLabel(status: BilibiliPublishBatchItem["status"]) {
    return status === "preview" ? c.previewStatus : c[status]
  }

  function itemsTable(batch: BilibiliPublishBatch, isPreview: boolean) {
    return (
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[560px] text-left text-xs">
          <thead className="bg-muted"><tr>
            <th className="p-3">{c.sequence}</th><th className="p-3">{c.title}</th>
            <th className="p-3">{c.publish}</th>{!isPreview ? <th className="p-3">{c.status}</th> : null}
          </tr></thead>
          <tbody>{batch.items.map((item) => (
            <tr key={item.position} className="border-t align-top">
              <td className="p-3 tabular-nums">{item.position}</td>
              <td className="max-w-sm space-y-2 p-3">
                <a href={`/tasks/${item.task_id}`} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2">{item.title}</a>
                <p className="text-muted-foreground">{c.created}: {beijingTime(item.task_created_at)}</p>
                <details><summary className="cursor-pointer text-muted-foreground">{c.details}</summary>
                  <dl className="mt-2 space-y-1 break-words">
                    <dt className="font-medium">{c.description}</dt><dd className="whitespace-pre-wrap">{item.description || "—"}</dd>
                    <dt className="font-medium">{c.tags}</dt><dd>{item.tags || c.noTags}</dd>
                    <dt>{c.category}: {item.tid}</dt>
                    {item.topic_name ? <dt>{c.topic}: {item.topic_name}</dt> : null}
                    {item.season_id ? <dt>{c.season}: {item.season_id} / {item.section_id}</dt> : null}
                    <dd>{item.has_cover
                      ? <a href={item.job_id ? `${API_BASE}/api/bilibili-upload-jobs/${item.job_id}/cover` : bilibiliCoverUrl(item.task_id)} target="_blank" rel="noreferrer" className="underline">{c.cover}</a>
                      : c.noCover}</dd>
                    {isPreview ? <dd><a href={`/tasks/${item.task_id}`} target="_blank" rel="noreferrer" className="underline">{c.edit}</a></dd> : null}
                  </dl>
                </details>
              </td>
              <td className="whitespace-nowrap p-3 font-medium tabular-nums">{beijingTime(item.publish_at)}</td>
              {!isPreview ? <td className="max-w-xs space-y-2 p-3">
                <Badge className={item.status === "failed" ? "border-red-200 bg-red-50 text-red-700" : ""}>{statusLabel(item.status)}</Badge>
                {item.error_message ? <p className="whitespace-pre-wrap break-words text-red-700">{item.error_message}</p> : null}
                {item.bvid ? <a className="block underline" href={`https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}`} target="_blank" rel="noreferrer">{item.bvid}</a> : null}
                {item.can_retry ? <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => changeBatch(batch, item)}>{c.retry}</Button> : null}
              </td> : null}
            </tr>
          ))}</tbody>
        </table>
      </div>
    )
  }

  return <>
    <Button type="button" size="sm" disabled={selectedTasks.length === 0} onClick={() => {
      const now = new Date()
      setMode("create"); setStartDate(todayInBeijing(now)); setStartTime(currentTimeInBeijing(now)); invalidate()
    }}><CalendarClock className="size-4" />{c.create}</Button>
    <Button type="button" size="sm" variant="outline" onClick={() => { setMode("manage"); setError(""); setNotice("") }}><ListChecks className="size-4" />{c.manage}</Button>
    <Dialog open={mode !== null} onOpenChange={(open) => { if (!open && !busy) setMode(null) }}>
      <DialogContent className="top-4 max-h-[calc(100dvh-2rem)] max-w-4xl overflow-y-auto sm:top-4" showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{mode === "create" ? c.create : c.manage}</DialogTitle>
          <DialogDescription>{mode === "create" ? c.help : c.manageHelp}</DialogDescription>
        </DialogHeader>
        {error ? <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
        {notice ? <p role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p> : null}
        {mode === "create" ? <>
          <p className="text-sm">{selectedTasks.length} {c.selected}</p>
          {invalid.length ? <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <p>{c.invalid}</p><ul className="mt-2 list-inside list-disc">{invalid.map((task) => <li key={task.id}>{task.title || task.id}</li>)}</ul>
          </div> : null}
          <div className="grid gap-4 sm:grid-cols-[180px_130px_minmax(0,1fr)]">
            <div className="space-y-2"><Label htmlFor="batch-start-date">{c.start}</Label>
              <Input id="batch-start-date" type="date" min={todayInBeijing()} value={startDate} disabled={busy} onChange={(event) => { setStartDate(event.target.value); invalidate() }} />
            </div>
            <div className="space-y-2"><Label htmlFor="batch-start-time">{c.startTime}</Label>
              <Input id="batch-start-time" type="time" step="60" value={startTime} disabled={busy} onChange={(event) => { setStartTime(event.target.value); invalidate() }} />
            </div>
            <div className="space-y-2"><Label>{c.times}</Label><div className="flex flex-wrap gap-2">
              {times.map((time, index) => <div key={index} className="flex items-center gap-1">
                <Input type="time" step="60" aria-label={`${c.times} ${index + 1}`} className="w-32" value={time} disabled={busy} onChange={(event) => {
                  setTimes((current) => current.map((value, i) => i === index ? event.target.value : value)); invalidate()
                }} />
                <Button type="button" size="icon-sm" variant="ghost" aria-label={`${c.remove} ${index + 1}`} disabled={busy || times.length === 1} onClick={() => { setTimes((current) => current.filter((_, i) => i !== index)); invalidate() }}><X className="size-4" /></Button>
              </div>)}
              <Button type="button" variant="outline" size="sm" disabled={busy || times.length >= 24} onClick={() => { setTimes((current) => [...current, ""]); invalidate() }}><Plus className="size-4" />{c.add}</Button>
            </div></div>
          </div>
          <p className="text-xs text-muted-foreground">{c.startHelp}</p>
          <p className="text-xs text-muted-foreground">{c.timing}</p>
          {hasPreview ? <>
            <h3 className="font-medium">{c.previewTitle} · {preview.items.length}</h3>
            <p className="text-xs text-muted-foreground">{c.startFrom}: {preview.start_date} {preview.start_time} · UTC+8</p>
            {preview.skipped.length ? <details className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900" open>
              <summary className="cursor-pointer">{c.skipped} ({preview.skipped.length})</summary>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">{preview.skipped.map((slot) => <li key={slot.publish_at}>{beijingTime(slot.publish_at)} · {c[slot.reason]}</li>)}</ul>
            </details> : null}
            {itemsTable(preview, true)}
          </> : preview ? <p className="text-amber-700">{c.changed}</p> : null}
        </> : <div className="space-y-5">
          {batches.length === 0 ? <p className="py-8 text-center text-muted-foreground">{c.empty}</p> : batches.map((batch, index) => <details key={batch.id} open={index === 0} className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">{c.scheduleAt} {beijingTime(batch.created_at)} · {batch.items.length}</summary>
            <div className="mt-3 space-y-3">
              <p className="text-xs text-muted-foreground">{c.startFrom}: {batch.start_date} {batch.start_time} · {batch.daily_times.join(" / ")} · UTC+8</p>
              {batch.items.some((item) => item.status === "scheduled" || item.status === "queued") ? <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => changeBatch(batch)}>{c.cancel}</Button> : null}
              {itemsTable(batch, false)}
            </div>
          </details>)}
        </div>}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={() => setMode(null)}>{c.close}</Button>
          {mode === "create" ? <>
            <Button type="button" variant={hasPreview ? "outline" : "default"} disabled={busy || !selectedTasks.length || invalid.length > 0 || !startDate || !validStartTime || !validTimes} onClick={generatePreview}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}{c.preview}
            </Button>
            {hasPreview ? <Button type="button" disabled={busy || invalid.length > 0} onClick={confirm}>{busy ? <Loader2 className="size-4 animate-spin" /> : null}{c.confirm}</Button> : null}
          </> : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </>
}
