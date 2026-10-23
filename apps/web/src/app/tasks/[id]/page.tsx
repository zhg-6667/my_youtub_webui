"use client"

import { useRouter } from "next/navigation"
import { use, useEffect, useMemo, useRef, useState, FormEvent, ChangeEvent } from "react"
import {
  CheckCircle2,
  Circle,
  Download,
  ExternalLink,
  Eye,
  FileText,
  FolderOpen,
  Heart,
  ImageIcon,
  Languages,
  Loader2,
  MessageCircle,
  Plus,
  Play,
  RotateCw,
  Save,
  Scissors,
  Search,
  Send,
  Tags,
  Trash2,
  UserRound,
  XCircle,
} from "lucide-react"

import {
  BilibiliPublishMode,
  BilibiliCoverProject,
  BilibiliCoverState,
  BilibiliSeason,
  BilibiliUploadJob,
  CutInterval,
  ExecutionMode,
  StageStatus,
  Task,
  WatermarkMaskJob,
  WatermarkMaskMode,
  concatVideo,
  bilibiliCoverBackgroundUrl,
  bilibiliCoverUrl,
  continueTask,
  createBilibiliUploadJob,
  createWatermarkMaskJob,
  deleteBilibiliEditedCover,
  deleteBilibiliManualCover,
  deleteTask,
  finalVideoDownloadUrl,
  finalVideoUrl,
  fastTrimReplaceSourceVideo,
  fastTrimVideo,
  getBilibiliUploadJob,
  getBilibiliCover,
  getBilibiliSeasons,
  getBilibiliSettings,
  getCutIntervals,
  getTask,
  getTaskLog,
  getWatermarkMaskJob,
  openTaskDirectory,
  redoStage,
  rerunTask,
  resumeTask,
  resolveBilibiliTopic,
  saveCutIntervals,
  saveBilibiliEditedCover,
  saveBilibiliManualCover,
  sourceCoverUrl,
  translateBilibiliTitle,
  trimVideo,
  updateBilibiliUploadSettings,
} from "@/lib/api"
import { BilibiliCoverEditor } from "@/components/bilibili-cover-editor"
import { BILIBILI_CATEGORIES } from "@/lib/bilibili-categories"
import { useI18n } from "@/lib/i18n"
import { statusBadgeClass } from "@/lib/status"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

function stageIcon(status: StageStatus) {
  if (status === "succeeded") return <CheckCircle2 className="size-5 text-[#00aeec]" />
  if (status === "failed") return <XCircle className="size-5 text-[#ff0033]" />
  if (status === "running") return <Loader2 className="size-5 animate-spin text-[#fb7299]" />
  return <Circle className="size-5 text-muted-foreground" />
}

function formatTime(value: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function durationOf(start: string | null, end: string | null) {
  if (!start) return ""
  const startMs = new Date(start).getTime()
  const endMs = end ? new Date(end).getTime() : Date.now()
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return ""
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  return `${minutes}m${rem.toString().padStart(2, "0")}s`
}

function formatSourceDate(value: string | null | undefined) {
  if (!value) return ""
  if (/^\d{8}$/.test(value)) {
    return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  }
  return formatTime(value)
}

function formatVideoDuration(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return ""
  const seconds = Math.max(0, Math.round(value))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`
}

function formatMetric(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—"
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

function normalizeProgress(value: number | null | undefined) {
  if (typeof value !== "number") return null
  return Math.max(0, Math.min(100, Math.round(value)))
}

function localDatePart(date = new Date()) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function toDatetimeLocalValue(date: Date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function datetimeLocalValue(value: string | null | undefined) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 16)
  return toDatetimeLocalValue(date)
}

const BILIBILI_PUBLISH_HOURS = Array.from({ length: 13 }, (_, index) => index + 10)

type TrimRangeInput = {
  id: string
  startMin: string
  startSec: string
  endMin: string
  endSec: string
}

type BilibiliTopicOption = {
  name: string
  topicId: number
}

function parseBilibiliTopicOptions(value: string): BilibiliTopicOption[] {
  const raw = value.trim()
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return []
    return Object.entries(parsed)
      .map(([name, topicId]) => ({
        name: name.trim(),
        topicId,
      }))
      .filter(
        (item): item is BilibiliTopicOption =>
          Boolean(item.name)
          && typeof item.topicId === "number"
          && Number.isInteger(item.topicId)
          && item.topicId > 0,
      )
  } catch {
    return []
  }
}

function emptyTrimRange(): TrimRangeInput {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    startMin: "",
    startSec: "",
    endMin: "",
    endSec: "",
  }
}

function emptyTrimRangeWithVideoEnd(durationSeconds: number | null | undefined): TrimRangeInput {
  const range = emptyTrimRange()
  if (typeof durationSeconds !== "number" || !Number.isFinite(durationSeconds) || durationSeconds < 0) {
    return range
  }
  const end = Math.floor(durationSeconds) + 1
  return {
    ...range,
    endMin: String(Math.floor(end / 60)),
    endSec: String(end % 60),
  }
}

function trimRangeHasValues(range: TrimRangeInput): boolean {
  return Boolean(range.startMin || range.startSec || range.endMin || range.endSec)
}

function secondsToRange(interval: CutInterval): TrimRangeInput {
  const start = Math.max(0, Math.floor(interval.start))
  const end = Math.max(0, Math.floor(interval.end))
  return {
    id: emptyTrimRange().id,
    startMin: String(Math.floor(start / 60)),
    startSec: String(start % 60),
    endMin: String(Math.floor(end / 60)),
    endSec: String(end % 60),
  }
}

export function TaskDetailContent({ id }: { id: string }) {
  const router = useRouter()
  const { stageLabel, statusLabel, t } = useI18n()
  const [task, setTask] = useState<Task | null>(null)
  const [log, setLog] = useState("")
  const [error, setError] = useState("")
  const [openingTaskDirectory, setOpeningTaskDirectory] = useState(false)
  const [openTaskDirectoryError, setOpenTaskDirectoryError] = useState("")
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState("")
  const [rerunOpen, setRerunOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [rerunError, setRerunError] = useState("")
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState("")
  const [continuing, setContinuing] = useState(false)
  const [continueError, setContinueError] = useState("")
  const [redoingStage, setRedoingStage] = useState<string | null>(null)
  const [redoConfirmStage, setRedoConfirmStage] = useState<string | null>(null)
  const [redoError, setRedoError] = useState("")
  const [concatenating, setConcatenating] = useState(false)
  const [concatError, setConcatError] = useState("")
  const [trimming, setTrimming] = useState(false)
  const [fastTrimming, setFastTrimming] = useState(false)
  const [fastTrimReplacingSource, setFastTrimReplacingSource] = useState(false)
  const [videoRefreshKey, setVideoRefreshKey] = useState(0)
  const [trimError, setTrimError] = useState("")
  const [savingTrimIntervals, setSavingTrimIntervals] = useState(false)
  const [trimSavedMessage, setTrimSavedMessage] = useState("")
  const [trimRanges, setTrimRanges] = useState<TrimRangeInput[]>([emptyTrimRange()])
  const [trimSourceFile, setTrimSourceFile] = useState<File | null>(null)
  const trimSourceInputRef = useRef<HTMLInputElement | null>(null)
  const trimRangesDirtyRef = useRef(false)
  const trimIntervalsSavedRef = useRef(false)
  const [bilibiliOpen, setBilibiliOpen] = useState(false)
  const [bilibiliAutoUpload, setBilibiliAutoUpload] = useState(false)
  const [bilibiliTitle, setBilibiliTitle] = useState("")
  const [bilibiliDescription, setBilibiliDescription] = useState("")
  const [bilibiliTags, setBilibiliTags] = useState("")
  const [bilibiliTid, setBilibiliTid] = useState("17")
  const [bilibiliTopicName, setBilibiliTopicName] = useState("")
  const [bilibiliTopicId, setBilibiliTopicId] = useState<number | null>(null)
  const [bilibiliTopicMissionId, setBilibiliTopicMissionId] = useState<number | null>(null)
  const [bilibiliTopicOptions, setBilibiliTopicOptions] = useState<BilibiliTopicOption[]>([])
  const [selectedBilibiliTopicOption, setSelectedBilibiliTopicOption] = useState("manual")
  const [resolvingBilibiliTopic, setResolvingBilibiliTopic] = useState(false)
  const [bilibiliSeasonValue, setBilibiliSeasonValue] = useState("none")
  const [bilibiliSeasons, setBilibiliSeasons] = useState<BilibiliSeason[]>([])
  const [bilibiliSeasonsLoading, setBilibiliSeasonsLoading] = useState(false)
  const [bilibiliSeasonsError, setBilibiliSeasonsError] = useState("")
  const [bilibiliCoverFile, setBilibiliCoverFile] = useState<File | null>(null)
  const [bilibiliCoverInputKey, setBilibiliCoverInputKey] = useState(0)
  const [bilibiliCover, setBilibiliCover] = useState<BilibiliCoverState | null>(null)
  const [bilibiliCoverRefreshKey, setBilibiliCoverRefreshKey] = useState(0)
  const [savingBilibiliCover, setSavingBilibiliCover] = useState(false)
  const [bilibiliUploadTiming, setBilibiliUploadTiming] = useState<"now" | "scheduled">("now")
  const [bilibiliUploadAfter, setBilibiliUploadAfter] = useState("")
  const [bilibiliPublishMode, setBilibiliPublishMode] = useState<BilibiliPublishMode>("now")
  const [bilibiliDtime, setBilibiliDtime] = useState("")
  const [creatingBilibiliJob, setCreatingBilibiliJob] = useState(false)
  const [savingBilibiliSettings, setSavingBilibiliSettings] = useState(false)
  const [bilibiliSettingsSavedMessage, setBilibiliSettingsSavedMessage] = useState("")
  const [translatingBilibiliTitle, setTranslatingBilibiliTitle] = useState(false)
  const [bilibiliJob, setBilibiliJob] = useState<BilibiliUploadJob | null>(null)
  const [bilibiliError, setBilibiliError] = useState("")
  const bilibiliSettingsInitializedTaskRef = useRef<string | null>(null)
  const bilibiliTitleDirtyRef = useRef(false)
  const bilibiliDescriptionDirtyRef = useRef(false)
  const bilibiliTagsDirtyRef = useRef(false)
  const bilibiliTidDirtyRef = useRef(false)
  const bilibiliTopicDirtyRef = useRef(false)
  const bilibiliSeasonDirtyRef = useRef(false)
  const [watermarkMode, setWatermarkMode] = useState<WatermarkMaskMode>("patch")
  const [creatingWatermarkJob, setCreatingWatermarkJob] = useState(false)
  const [watermarkJob, setWatermarkJob] = useState<WatermarkMaskJob | null>(null)
  const [watermarkError, setWatermarkError] = useState("")

  const applyBilibiliSettingsFromTask = (next: Task) => {
    if (bilibiliSettingsInitializedTaskRef.current !== next.id) {
      bilibiliSettingsInitializedTaskRef.current = next.id
      bilibiliTitleDirtyRef.current = false
      bilibiliDescriptionDirtyRef.current = false
      bilibiliTagsDirtyRef.current = false
      bilibiliTidDirtyRef.current = false
      bilibiliTopicDirtyRef.current = false
      bilibiliSeasonDirtyRef.current = false
      setBilibiliAutoUpload(next.auto_bilibili_upload)
      setBilibiliTitle(next.bilibili_upload_title || next.title || "")
      setBilibiliDescription(
        next.bilibili_upload_description
          ?? next.bilibili_publish_info?.publish_description
          ?? "",
      )
      setBilibiliTags(
        next.bilibili_upload_tags
          ?? next.bilibili_publish_info?.publish_tags.join(",")
          ?? "",
      )
      setBilibiliTid(String(next.bilibili_upload_tid ?? next.bilibili_publish_info?.publish_tid ?? 17))
      setBilibiliTopicName(next.bilibili_topic_name ?? "")
      setBilibiliTopicId(next.bilibili_topic_id)
      setBilibiliTopicMissionId(next.bilibili_topic_mission_id)
      setSelectedBilibiliTopicOption("manual")
      setBilibiliSeasonValue(
        next.bilibili_season_id && next.bilibili_section_id
          ? `${next.bilibili_season_id}:${next.bilibili_section_id}`
          : "none",
      )
      setBilibiliUploadTiming(next.bilibili_upload_after ? "scheduled" : "now")
      setBilibiliUploadAfter(datetimeLocalValue(next.bilibili_upload_after))
      setBilibiliPublishMode(next.bilibili_publish_mode || "now")
      setBilibiliDtime(next.bilibili_dtime ? next.bilibili_dtime.slice(0, 16) : "")
      return
    }
    if (!bilibiliTitleDirtyRef.current && !next.bilibili_upload_title && next.title) {
      setBilibiliTitle(next.title)
    }
    if (
      !bilibiliDescriptionDirtyRef.current
      && next.bilibili_upload_description === null
      && next.bilibili_publish_info
    ) {
      setBilibiliDescription(next.bilibili_publish_info.publish_description)
    }
    if (
      !bilibiliTagsDirtyRef.current
      && next.bilibili_upload_tags === null
      && next.bilibili_publish_info
    ) {
      setBilibiliTags(next.bilibili_publish_info.publish_tags.join(","))
    }
    if (
      !bilibiliTidDirtyRef.current
      && next.bilibili_upload_tid === null
      && next.bilibili_publish_info
    ) {
      setBilibiliTid(String(next.bilibili_publish_info.publish_tid))
    }
    if (!bilibiliTopicDirtyRef.current) {
      setBilibiliTopicName(next.bilibili_topic_name ?? "")
      setBilibiliTopicId(next.bilibili_topic_id)
      setBilibiliTopicMissionId(next.bilibili_topic_mission_id)
    }
    if (!bilibiliSeasonDirtyRef.current) {
      setBilibiliSeasonValue(
        next.bilibili_season_id && next.bilibili_section_id
          ? `${next.bilibili_season_id}:${next.bilibili_section_id}`
          : "none",
      )
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError("")
    try {
      await deleteTask(id)
      const closeEvent = new CustomEvent("youdub:close-tab", {
        cancelable: true,
        detail: { key: `task:${id}` },
      })
      const handled = !window.dispatchEvent(closeEvent)
      if (!handled) router.replace("/")
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : t.task.deleteError)
      setDeleting(false)
    }
  }

  const handleRerun = async () => {
    setRerunning(true)
    setRerunError("")
    try {
      const next = await rerunTask(id)
      setRerunOpen(false)
      setTask(next)
      setLog("")
    } catch (err) {
      setRerunError(err instanceof Error ? err.message : t.task.rerunError)
    } finally {
      setRerunning(false)
    }
  }

  const handleResume = async () => {
    setResuming(true)
    setResumeError("")
    try {
      const next = await resumeTask(id)
      setTask(next)
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : t.task.resumeError)
    } finally {
      setResuming(false)
    }
  }

  const handleContinue = async (executionMode?: ExecutionMode) => {
    setContinuing(true)
    setContinueError("")
    try {
      const next = await continueTask(id, executionMode)
      setTask(next)
    } catch (err) {
      setContinueError(err instanceof Error ? err.message : t.task.continueError)
    } finally {
      setContinuing(false)
    }
  }

  const handleRedoStage = async (stageName: string) => {
    setRedoingStage(stageName)
    setRedoError("")
    try {
      const next = await redoStage(id, stageName)
      setTask(next)
      return true
    } catch (err) {
      setRedoError(err instanceof Error ? err.message : t.task.redoStageError)
      return false
    } finally {
      setRedoingStage(null)
    }
  }

  const handleConfirmRedoStage = async () => {
    if (!redoConfirmStage) return
    const succeeded = await handleRedoStage(redoConfirmStage)
    if (succeeded) setRedoConfirmStage(null)
  }

  const parseTrimRanges = () => {
    const intervals: CutInterval[] = []
    for (const range of trimRanges) {
      const values = [range.startMin, range.startSec, range.endMin, range.endSec].map((value) => value.trim())
      if (values.every((value) => !value)) continue
      if (values.some((value) => value === "" || !/^\d+$/.test(value))) {
        throw new Error(t.task.trimIntervalInvalid)
      }
      const [startMin, startSec, endMin, endSec] = values.map(Number)
      if (startSec > 59 || endSec > 59) {
        throw new Error(t.task.trimSecondInvalid)
      }
      const start = startMin * 60 + startSec
      const end = endMin * 60 + endSec
      if (end <= start) {
        throw new Error(t.task.trimEndInvalid)
      }
      intervals.push({ start, end })
    }
    return intervals
  }

  const handleTrimRangeChange = (rangeId: string, field: keyof Omit<TrimRangeInput, "id">, value: string) => {
    const digits = value.replace(/\D/g, "")
    trimRangesDirtyRef.current = true
    setTrimRanges((current) =>
      current.map((range) => (range.id === rangeId ? { ...range, [field]: digits } : range)),
    )
    setTrimSavedMessage("")
  }

  const handleSaveTrimIntervals = async () => {
    setSavingTrimIntervals(true)
    setTrimError("")
    setTrimSavedMessage("")
    try {
      const intervals = parseTrimRanges()
      const saved = await saveCutIntervals(id, intervals)
      trimIntervalsSavedRef.current = true
      setTrimRanges(saved.cut_intervals.length ? saved.cut_intervals.map(secondsToRange) : [emptyTrimRange()])
      setTrimSavedMessage(t.task.trimIntervalsSaved)
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : t.task.trimStageError)
    } finally {
      setSavingTrimIntervals(false)
    }
  }

  const handleOpenTaskDirectory = async () => {
    if (!task?.session_path) return
    setOpeningTaskDirectory(true)
    setOpenTaskDirectoryError("")
    try {
      await openTaskDirectory(id)
    } catch (err) {
      setOpenTaskDirectoryError(err instanceof Error ? err.message : t.task.openTaskDirectoryError)
    } finally {
      setOpeningTaskDirectory(false)
    }
  }

  const handleTrim = async () => {
    setTrimming(true)
    setTrimError("")
    setTrimSavedMessage("")
    try {
      const intervals = parseTrimRanges()
      if (!intervals.length) {
        throw new Error(t.task.trimIntervalRequired)
      }
      const next = await trimVideo(id, intervals, trimSourceFile)
      setTask(next)
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : t.task.trimStageError)
    } finally {
      setTrimming(false)
    }
  }

  const handleFastTrim = async () => {
    setFastTrimming(true)
    setTrimError("")
    setTrimSavedMessage("")
    try {
      const intervals = parseTrimRanges()
      if (!intervals.length) {
        throw new Error(t.task.trimIntervalRequired)
      }
      const next = await fastTrimVideo(id, intervals)
      setTask(next)
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : t.task.fastTrimStageError)
    } finally {
      setFastTrimming(false)
    }
  }

  const handleFastTrimReplaceSource = async () => {
    setFastTrimReplacingSource(true)
    setTrimError("")
    setTrimSavedMessage("")
    try {
      const intervals = parseTrimRanges()
      if (!intervals.length) {
        throw new Error(t.task.trimIntervalRequired)
      }
      const next = await fastTrimReplaceSourceVideo(id, intervals)
      setTask(next)
      setVideoRefreshKey(Date.now())
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : t.task.fastTrimReplaceSourceError)
    } finally {
      setFastTrimReplacingSource(false)
    }
  }

  const handleTrimSourceFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setTrimSourceFile(event.target.files?.[0] || null)
  }

  const handleResolveBilibiliTopic = async () => {
    const topicName = bilibiliTopicName.trim()
    if (!topicName) {
      setBilibiliTopicId(null)
      setBilibiliTopicMissionId(null)
      return
    }
    setResolvingBilibiliTopic(true)
    setBilibiliError("")
    try {
      const topic = await resolveBilibiliTopic(topicName)
      setBilibiliTopicName(topic.topic_name)
      setBilibiliTopicId(topic.topic_id)
      setBilibiliTopicMissionId(topic.mission_id)
      setBilibiliSettingsSavedMessage("")
    } catch (err) {
      setBilibiliTopicId(null)
      setBilibiliTopicMissionId(null)
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliTopicResolveError)
    } finally {
      setResolvingBilibiliTopic(false)
    }
  }

  const handleBilibiliTopicOptionChange = (value: string | null) => {
    setSelectedBilibiliTopicOption(value || "manual")
    if (!value || value === "manual") return
    const option = bilibiliTopicOptions.find((item) => item.topicId === Number(value))
    if (!option) return
    bilibiliTopicDirtyRef.current = true
    setBilibiliTopicName(option.name)
    setBilibiliTopicId(option.topicId)
    setBilibiliTopicMissionId(null)
    setBilibiliSettingsSavedMessage("")
    setBilibiliError("")
  }

  const buildBilibiliPayload = (requireTitle: boolean) => {
    setBilibiliError("")
    const title = bilibiliTitle.trim()
    if (requireTitle && !title) {
      setBilibiliError(t.task.bilibiliTitleRequired)
      return null
    }
    if (bilibiliPublishMode === "scheduled" && !bilibiliDtime) {
      setBilibiliError(t.task.bilibiliDtimeRequired)
      return null
    }
    if (bilibiliUploadTiming === "scheduled" && !bilibiliUploadAfter) {
      setBilibiliError(t.task.bilibiliUploadAfterRequired)
      return null
    }
    const tid = Number(bilibiliTid)
    if (!Number.isInteger(tid) || tid <= 0) {
      setBilibiliError(t.task.bilibiliTidInvalid)
      return null
    }
    let seasonId: number | null = null
    let sectionId: number | null = null
    if (bilibiliSeasonValue !== "none") {
      const [rawSeasonId, rawSectionId] = bilibiliSeasonValue.split(":").map(Number)
      if (!Number.isInteger(rawSeasonId) || rawSeasonId <= 0 || !Number.isInteger(rawSectionId) || rawSectionId <= 0) {
        setBilibiliError(t.task.bilibiliSeasonInvalid)
        return null
      }
      seasonId = rawSeasonId
      sectionId = rawSectionId
    }
    return {
      title,
      description: bilibiliDescription.trim(),
      tags: bilibiliTags.trim(),
      tid,
      topic_name: bilibiliTopicName.trim() || null,
      topic_id: bilibiliTopicId,
      topic_mission_id: bilibiliTopicMissionId,
      season_id: seasonId,
      section_id: sectionId,
      publish_mode: bilibiliPublishMode,
      dtime: bilibiliPublishMode === "scheduled" ? bilibiliDtime : null,
      upload_after: bilibiliUploadTiming === "scheduled" ? bilibiliUploadAfter : null,
    }
  }

  const handleSaveBilibiliSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBilibiliSettingsSavedMessage("")
    const payload = buildBilibiliPayload(bilibiliAutoUpload)
    if (!payload) return
    setSavingBilibiliSettings(true)
    try {
      const next = await updateBilibiliUploadSettings(id, {
        auto_bilibili_upload: bilibiliAutoUpload,
        title: payload.title || null,
        description: payload.description,
        tags: payload.tags,
        tid: payload.tid,
        topic_name: payload.topic_name,
        topic_id: payload.topic_id,
        topic_mission_id: payload.topic_mission_id,
        season_id: payload.season_id,
        section_id: payload.section_id,
        publish_mode: payload.publish_mode,
        dtime: payload.dtime,
        upload_after: payload.upload_after,
      })
      setTask(next)
      setBilibiliSettingsSavedMessage(t.task.bilibiliSettingsSaved)
    } catch (err) {
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliSettingsSaveError)
    } finally {
      setSavingBilibiliSettings(false)
    }
  }

  const applyBilibiliCover = (next: BilibiliCoverState) => {
    setBilibiliCover(next)
    setBilibiliCoverRefreshKey((current) => current + 1)
  }

  const handleBilibiliManualCoverChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setBilibiliCoverFile(file)
    setSavingBilibiliCover(true)
    setBilibiliError("")
    try {
      applyBilibiliCover(await saveBilibiliManualCover(id, file))
      setBilibiliSettingsSavedMessage(t.task.bilibiliManualCoverSaved)
    } catch (err) {
      setBilibiliCoverFile(null)
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliManualCoverSaveError)
    } finally {
      setBilibiliCoverInputKey((current) => current + 1)
      setSavingBilibiliCover(false)
    }
  }

  const handleDeleteBilibiliManualCover = async () => {
    setSavingBilibiliCover(true)
    setBilibiliError("")
    try {
      applyBilibiliCover(await deleteBilibiliManualCover(id))
      setBilibiliCoverFile(null)
      setBilibiliCoverInputKey((current) => current + 1)
    } catch (err) {
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliManualCoverSaveError)
    } finally {
      setSavingBilibiliCover(false)
    }
  }

  const handleSaveBilibiliEditedCover = async (file: File, project: BilibiliCoverProject) => {
    setBilibiliError("")
    applyBilibiliCover(await saveBilibiliEditedCover(id, file, project))
    setBilibiliSettingsSavedMessage(t.task.bilibiliCoverSaved)
  }

  const handleDeleteBilibiliEditedCover = async () => {
    setBilibiliError("")
    applyBilibiliCover(await deleteBilibiliEditedCover(id))
  }

  const handleCreateBilibiliUpload = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    const payload = buildBilibiliPayload(true)
    if (!payload) return
    setCreatingBilibiliJob(true)
    try {
      const job = await createBilibiliUploadJob(id, {
        title: payload.title,
        description: payload.description,
        tags: payload.tags,
        tid: payload.tid,
        topic_name: payload.topic_name,
        topic_id: payload.topic_id,
        topic_mission_id: payload.topic_mission_id,
        season_id: payload.season_id,
        section_id: payload.section_id,
        publish_mode: payload.publish_mode,
        dtime: payload.dtime,
        upload_after: payload.upload_after,
      })
      setBilibiliJob(job)
      setBilibiliCoverFile(null)
      setBilibiliCoverInputKey((current) => current + 1)
    } catch (err) {
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliUploadError)
    } finally {
      setCreatingBilibiliJob(false)
    }
  }

  const handleConcat = async () => {
    setConcatenating(true)
    setConcatError("")
    try {
      const next = await concatVideo(id)
      setTask(next)
    } catch (err) {
      setConcatError(err instanceof Error ? err.message : t.task.concatStageError)
    } finally {
      setConcatenating(false)
    }
  }

  const handleTranslateBilibiliTitle = async () => {
    const sourceTitle = bilibiliTitle.trim() || task?.title || ""
    if (!sourceTitle.trim()) {
      setBilibiliError(t.task.bilibiliTitleRequired)
      return
    }
    setTranslatingBilibiliTitle(true)
    setBilibiliError("")
    try {
      const result = await translateBilibiliTitle(id, sourceTitle)
      bilibiliTitleDirtyRef.current = true
      setBilibiliTitle(result.title)
    } catch (err) {
      setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliTitleTranslateError)
    } finally {
      setTranslatingBilibiliTitle(false)
    }
  }

  const handleQuickBilibiliDtime = (time: string) => {
    const datePart = bilibiliDtime.includes("T") ? bilibiliDtime.split("T")[0] : localDatePart()
    setBilibiliDtime(`${datePart}T${time}`)
    setBilibiliSettingsSavedMessage("")
  }

  const handleQuickBilibiliUploadAfter = (time: string) => {
    const datePart = bilibiliUploadAfter.includes("T") ? bilibiliUploadAfter.split("T")[0] : localDatePart()
    setBilibiliUploadAfter(`${datePart}T${time}`)
    setBilibiliSettingsSavedMessage("")
  }

  const handleQuickBilibiliUploadAfterTenMinutes = () => {
    setBilibiliUploadAfter(toDatetimeLocalValue(new Date(Date.now() + 10 * 60_000)))
    setBilibiliSettingsSavedMessage("")
  }

  const handleCreateWatermarkMask = async () => {
    setWatermarkError("")
    setCreatingWatermarkJob(true)
    try {
      const job = await createWatermarkMaskJob(id, { mode: watermarkMode })
      setWatermarkJob(job)
    } catch (err) {
      setWatermarkError(err instanceof Error ? err.message : t.task.watermarkMaskError)
    } finally {
      setCreatingWatermarkJob(false)
    }
  }

  const isRunning = task?.status === "running"
  const isQueued = task?.status === "queued"
  const isFailed = task?.status === "failed"
  const isPaused = task?.status === "paused"
  const isManual = task?.execution_mode === "manual"
  const isBilibiliTask = task?.source_name === "bilibili"
  const displayVideoPath = task?.display_video_path || task?.final_video_path || null
  const bilibiliPublishInfo = task?.bilibili_publish_info || null
  const trimDefaultDurationSeconds = bilibiliPublishInfo?.duration_seconds
  const bilibiliResolvedCoverUrl = `${bilibiliCoverUrl(id)}?v=${bilibiliCoverRefreshKey}`
  const bilibiliCoverEditorBackgroundUrl = `${bilibiliCoverBackgroundUrl(id)}&v=${bilibiliCoverRefreshKey}`
  const parsedBilibiliTid = Number(bilibiliTid)
  const effectiveBilibiliTid = Number.isInteger(parsedBilibiliTid) && parsedBilibiliTid > 0
    ? parsedBilibiliTid
    : bilibiliPublishInfo?.publish_tid || 17
  const bilibiliCategory = BILIBILI_CATEGORIES.find((category) => category.tid === effectiveBilibiliTid)
  const selectedBilibiliSeason = bilibiliSeasons.find(
    (season) => `${season.season_id}:${season.section_id}` === bilibiliSeasonValue,
  )
  const selectedBilibiliSeasonLabel = selectedBilibiliSeason
    ? `${selectedBilibiliSeason.season_title} / ${selectedBilibiliSeason.section_title}`
    : t.task.bilibiliSeasonNone
  const effectiveBilibiliTags = bilibiliTags.split(",").map((tag) => tag.trim()).filter(Boolean)
  const canRedoStage = isManual && !isRunning && !isQueued
  const canTrim = !isRunning && !isQueued
  const canConcat = !isRunning && !isQueued
  const trimActionRunning = trimming || fastTrimming || fastTrimReplacingSource
  const canEditTrimIntervals = Boolean(task) && !(task?.status === "running" && task.current_stage === "trim_video")
  const redoConfirmStageInfo = task?.stages.find((stage) => stage.name === redoConfirmStage)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getTask(id)
        if (cancelled) return
        setTask(next)
        applyBilibiliSettingsFromTask(next)
        const logText = await getTaskLog(id)
        if (cancelled) return
        setLog(logText)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : t.task.loadError)
      }
    }
    load()
    const interval = window.setInterval(load, 2000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [id, t.task.loadError])

  useEffect(() => {
    if (!task?.session_path) return
    let cancelled = false
    const loadCover = async () => {
      try {
        const next = await getBilibiliCover(id)
        if (!cancelled) setBilibiliCover(next)
      } catch {
        if (!cancelled) setBilibiliCover(null)
      }
    }
    loadCover()
    return () => {
      cancelled = true
    }
  }, [id, task?.session_path])

  useEffect(() => {
    let cancelled = false
    const loadSeasons = async () => {
      setBilibiliSeasonsLoading(true)
      setBilibiliSeasonsError("")
      try {
        const result = await getBilibiliSeasons()
        if (!cancelled) setBilibiliSeasons(result.seasons)
      } catch (err) {
        if (!cancelled) setBilibiliSeasonsError(err instanceof Error ? err.message : t.task.bilibiliSeasonLoadError)
      } finally {
        if (!cancelled) setBilibiliSeasonsLoading(false)
      }
    }
    loadSeasons()
    return () => {
      cancelled = true
    }
  }, [t.task.bilibiliSeasonLoadError])

  useEffect(() => {
    let cancelled = false
    const loadBilibiliSettings = async () => {
      try {
        const settings = await getBilibiliSettings()
        if (!cancelled) setBilibiliTopicOptions(parseBilibiliTopicOptions(settings.pokemon_topic_config_json))
      } catch {
        if (!cancelled) setBilibiliTopicOptions([])
      }
    }
    loadBilibiliSettings()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    trimRangesDirtyRef.current = false
    trimIntervalsSavedRef.current = false
    const loadIntervals = async () => {
      try {
        const result = await getCutIntervals(id)
        if (cancelled) return
        trimIntervalsSavedRef.current = result.cut_intervals.length > 0
        setTrimRanges((current) => {
          if (result.cut_intervals.length) return result.cut_intervals.map(secondsToRange)
          return current.some(trimRangeHasValues) ? current : [emptyTrimRange()]
        })
      } catch {
        if (!cancelled) {
          setTrimRanges((current) => (current.some(trimRangeHasValues) ? current : [emptyTrimRange()]))
        }
      }
    }
    loadIntervals()
    return () => {
      cancelled = true
    }
  }, [id])

  useEffect(() => {
    if (typeof trimDefaultDurationSeconds !== "number" || !Number.isFinite(trimDefaultDurationSeconds)) return
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled || trimIntervalsSavedRef.current || trimRangesDirtyRef.current) return
      setTrimRanges((current) => {
        if (current.length !== 1 || trimRangeHasValues(current[0])) return current
        return [emptyTrimRangeWithVideoEnd(trimDefaultDurationSeconds)]
      })
    })
    return () => {
      cancelled = true
    }
  }, [trimDefaultDurationSeconds])

  useEffect(() => {
    if (!bilibiliJob || !["scheduled", "queued", "running"].includes(bilibiliJob.status)) return
    let cancelled = false
    const refresh = async () => {
      try {
        const next = await getBilibiliUploadJob(bilibiliJob.id)
        if (!cancelled) setBilibiliJob(next)
      } catch (err) {
        if (!cancelled) setBilibiliError(err instanceof Error ? err.message : t.task.bilibiliUploadError)
      }
    }
    const interval = window.setInterval(refresh, 2000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [bilibiliJob, t.task.bilibiliUploadError])

  useEffect(() => {
    if (!watermarkJob || !["queued", "running"].includes(watermarkJob.status)) return
    let cancelled = false
    const refresh = async () => {
      try {
        const next = await getWatermarkMaskJob(watermarkJob.id)
        if (cancelled) return
        setWatermarkJob(next)
        if (next.status === "succeeded") {
          const updatedTask = await getTask(id)
          if (!cancelled) setTask(updatedTask)
        }
      } catch (err) {
        if (!cancelled) setWatermarkError(err instanceof Error ? err.message : t.task.watermarkMaskError)
      }
    }
    const interval = window.setInterval(refresh, 2000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [id, watermarkJob, t.task.watermarkMaskError])

  const progress = useMemo(() => {
    if (!task?.stages?.length) return 0
    const completed = task.stages.filter((stage) => stage.status === "succeeded").length
    return Math.round((completed / task.stages.length) * 100)
  }, [task])

  if (error && !task) {
    return (
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <Card>
          <CardContent className="px-6 py-10 text-sm text-red-600">{error}</CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto grid w-full max-w-[1440px] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_460px] xl:grid-cols-[minmax(0,1fr)_500px]">
        <Card className="lg:col-start-1">
          <CardHeader className="gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle>{t.task.overview}</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="bg-background"
                  onClick={handleOpenTaskDirectory}
                  disabled={!task?.session_path || openingTaskDirectory}
                  title={task?.session_path ? task.session_path : t.task.openTaskDirectoryUnavailable}
                >
                  {openingTaskDirectory ? <Loader2 className="size-3 animate-spin" /> : <FolderOpen className="size-3" />}
                  {openingTaskDirectory ? t.task.openingTaskDirectory : t.task.openTaskDirectory}
                </Button>
                <Badge className={statusBadgeClass(task?.status)}>{statusLabel(task?.status)}</Badge>
              </div>
            </div>
            <Progress value={progress} />
          </CardHeader>
          <CardContent>
            {task ? (
              <div className="space-y-2 text-sm">
                {task.title ? (
                  <dl className="grid gap-x-6 sm:grid-cols-[120px_1fr]">
                    <dt className="text-muted-foreground">{t.task.title}</dt>
                    <dd className="break-words font-medium">{task.title}</dd>
                  </dl>
                ) : null}
                <dl className="grid gap-x-6 sm:grid-cols-[120px_1fr]">
                  <dt className="text-muted-foreground">URL</dt>
                  <dd className="break-all">
                    <a href={task.url} target="_blank" rel="noreferrer" className="text-[#00aeec] hover:underline">
                      {task.url}
                    </a>
                  </dd>
                </dl>
                <dl className="grid min-w-0 gap-2 sm:grid-cols-3">
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.taskId}</dt>
                    <dd className="min-w-0 truncate font-mono text-xs" title={task.id}>{task.id}</dd>
                  </div>
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.executionMode}</dt>
                    <dd>{task.execution_mode === "manual" ? t.task.executionManual : t.task.executionAuto}</dd>
                  </div>
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.autoBilibiliUpload}</dt>
                    <dd>{task.auto_bilibili_upload ? t.task.enabled : t.task.disabled}</dd>
                  </div>
                </dl>
                <dl className="grid min-w-0 gap-2 sm:grid-cols-3">
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.created}</dt>
                    <dd className="text-xs xl:text-sm">{formatTime(task.created_at)}</dd>
                  </div>
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.started}</dt>
                    <dd className="text-xs xl:text-sm">{formatTime(task.started_at)}</dd>
                  </div>
                  <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
                    <dt className="shrink-0 text-muted-foreground">{t.task.completed}</dt>
                    <dd className="text-xs xl:text-sm">{formatTime(task.completed_at) || "—"}</dd>
                  </div>
                </dl>
                {task.session_path ? (
                  <dl className="grid gap-x-6 sm:grid-cols-[120px_1fr]">
                    <dt className="text-muted-foreground">{t.task.session}</dt>
                    <dd className="break-all text-xs text-muted-foreground">{task.session_path}</dd>
                  </dl>
                ) : null}
                {openTaskDirectoryError ? (
                  <dl className="grid gap-x-6 sm:grid-cols-[120px_1fr]">
                    <dt className="sr-only">{t.task.openTaskDirectory}</dt>
                    <dd className="text-xs text-red-600">{openTaskDirectoryError}</dd>
                  </dl>
                ) : null}
              </div>
            ) : (
              <div className="py-6 text-center text-sm text-muted-foreground">{t.task.loading}</div>
            )}
          </CardContent>
        </Card>

        {task ? (
          <Card className="lg:col-start-2 lg:row-start-1 lg:row-span-5">
            <CardHeader className="gap-2">
              <div className="flex items-center justify-between gap-3">
                <CardTitle>{t.task.bilibiliPublishInfoTitle}</CardTitle>
                <Badge className="border-[#00aeec]/25 bg-[#00aeec]/10 text-[#0086b3]">Bilibili</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              {bilibiliPublishInfo ? (
                <section className="space-y-4">
                  {bilibiliCover?.available || bilibiliPublishInfo.cover_available ? (
                    <div
                      role="img"
                      aria-label={bilibiliPublishInfo.source_title || t.task.bilibiliCoverPreview}
                      className="relative aspect-video w-full overflow-hidden rounded-md border border-border bg-muted bg-cover bg-center"
                      style={{ backgroundImage: `url("${bilibiliCover?.available ? bilibiliResolvedCoverUrl : sourceCoverUrl(task.id)}")` }}
                    >
                      {bilibiliPublishInfo.duration_seconds !== null ? (
                        <span className="absolute right-2 bottom-2 rounded bg-black/75 px-1.5 py-0.5 text-xs tabular-nums text-white">
                          {formatVideoDuration(bilibiliPublishInfo.duration_seconds)}
                        </span>
                      ) : null}
                    </div>
                  ) : null}

                  {bilibiliCover?.available || bilibiliPublishInfo.cover_available ? (
                    <div className="flex justify-start">
                      <BilibiliCoverEditor
                        backgroundUrl={bilibiliCoverEditorBackgroundUrl}
                        initialProject={bilibiliCover?.project || null}
                        editedAvailable={Boolean(bilibiliCover?.edited_available)}
                        onSave={handleSaveBilibiliEditedCover}
                        onReset={handleDeleteBilibiliEditedCover}
                      />
                    </div>
                  ) : null}

                  <div className="grid grid-cols-3 divide-x rounded-md border border-border bg-muted/25 py-2">
                    <div className="flex items-center justify-center gap-1.5 px-2" title={t.task.sourceViews}>
                      <Eye className="size-4 text-[#00aeec]" />
                      <span className="text-xs tabular-nums">{formatMetric(bilibiliPublishInfo.view_count)}</span>
                    </div>
                    <div className="flex items-center justify-center gap-1.5 px-2" title={t.task.sourceLikes}>
                      <Heart className="size-4 text-[#fb7299]" />
                      <span className="text-xs tabular-nums">{formatMetric(bilibiliPublishInfo.like_count)}</span>
                    </div>
                    <div className="flex items-center justify-center gap-1.5 px-2" title={t.task.sourceComments}>
                      <MessageCircle className="size-4 text-emerald-600" />
                      <span className="text-xs tabular-nums">{formatMetric(bilibiliPublishInfo.comment_count)}</span>
                    </div>
                  </div>

                  <dl className="grid gap-3 text-sm">
                    <div className="grid grid-cols-[1.1rem_minmax(0,1fr)] gap-2">
                      <UserRound className="mt-0.5 size-4 text-muted-foreground" />
                      <div className="flex min-w-0 items-center gap-2 whitespace-nowrap">
                        <dt className="shrink-0 text-xs text-muted-foreground">{t.task.sourceAuthor}</dt>
                        <dd className="min-w-0 overflow-hidden font-medium">
                          {bilibiliPublishInfo.author_url ? (
                            <a
                              href={bilibiliPublishInfo.author_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex max-w-full items-center gap-1 text-[#0086b3] hover:underline"
                            >
                              <span className="truncate">{bilibiliPublishInfo.author || "—"}</span>
                              <ExternalLink className="size-3 shrink-0" />
                            </a>
                          ) : (
                            <span className="block truncate">{bilibiliPublishInfo.author || "—"}</span>
                          )}
                        </dd>
                        {bilibiliPublishInfo.author_id ? (
                          <dd className="min-w-0 truncate font-mono text-xs text-muted-foreground" title={bilibiliPublishInfo.author_id}>
                            {bilibiliPublishInfo.author_id}
                          </dd>
                        ) : null}
                        <dd className="shrink-0 text-xs text-muted-foreground">
                          {formatSourceDate(bilibiliPublishInfo.source_upload_date) || "—"}
                        </dd>
                      </div>
                    </div>
                    <div className="grid grid-cols-[1.1rem_minmax(0,1fr)] gap-2">
                      <ExternalLink className="mt-0.5 size-4 text-muted-foreground" />
                      <div className="min-w-0">
                        <dt className="text-xs text-muted-foreground">{t.task.sourceVideo}</dt>
                        <dd>
                          {bilibiliPublishInfo.source_url ? (
                            <a
                              href={bilibiliPublishInfo.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="break-all text-[#0086b3] hover:underline"
                            >
                              {bilibiliPublishInfo.source_title || bilibiliPublishInfo.source_url}
                            </a>
                          ) : (
                            "—"
                          )}
                        </dd>
                      </div>
                    </div>
                  </dl>

                  <div className="space-y-2">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Tags className="size-3.5" />
                      {t.task.bilibiliTagsAndCategory}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="outline" className="border-[#00aeec]/30 text-[#0086b3]">
                        {bilibiliCategory
                          ? `${bilibiliCategory.group} / ${bilibiliCategory.name} (${effectiveBilibiliTid})`
                          : `tid ${effectiveBilibiliTid}`}
                      </Badge>
                      {effectiveBilibiliTags.map((tag) => (
                        <Badge key={tag} variant="secondary">{tag}</Badge>
                      ))}
                      {bilibiliTopicName.trim() ? (
                        <Badge variant="outline" className="border-[#00aeec]/30 text-[#0086b3]">
                          {bilibiliTopicId
                            ? `${bilibiliTopicName.trim()} (#${bilibiliTopicId})`
                            : bilibiliTopicName.trim()}
                        </Badge>
                      ) : null}
                    </div>
                  </div>

                </section>
              ) : (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
                  {t.task.bilibiliMetadataPending}
                </div>
              )}

              <section className="space-y-4 border-t border-border pt-5">
                <h3 className="text-sm font-semibold">{t.task.bilibiliSettingsTitle}</h3>
                <form onSubmit={handleSaveBilibiliSettings} className="space-y-4">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Label htmlFor="bilibili-settings-title">{t.task.bilibiliTitleLabel}</Label>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleTranslateBilibiliTitle}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    >
                      {translatingBilibiliTitle ? <Loader2 className="size-4 animate-spin" /> : <Languages className="size-4" />}
                      {translatingBilibiliTitle ? t.task.bilibiliTitleTranslating : t.task.bilibiliTitleTranslate}
                    </Button>
                  </div>
                  <Input
                    id="bilibili-settings-title"
                    value={bilibiliTitle}
                    onChange={(event) => {
                      bilibiliTitleDirtyRef.current = true
                      setBilibiliTitle(event.target.value)
                      setBilibiliSettingsSavedMessage("")
                    }}
                    placeholder={task.title || t.task.bilibiliTitlePlaceholder}
                    disabled={savingBilibiliSettings || translatingBilibiliTitle}
                  />
                  <p className="text-xs text-muted-foreground">{t.task.bilibiliTitleHelp}</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="bilibili-settings-description">{t.task.bilibiliDescriptionLabel}</Label>
                  <Textarea
                    id="bilibili-settings-description"
                    className="h-[88px]"
                    value={bilibiliDescription}
                    onChange={(event) => {
                      bilibiliDescriptionDirtyRef.current = true
                      setBilibiliDescription(event.target.value)
                      setBilibiliSettingsSavedMessage("")
                    }}
                    rows={6}
                    disabled={savingBilibiliSettings || translatingBilibiliTitle}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_7rem]">
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-tags">{t.task.bilibiliTagsLabel}</Label>
                    <Textarea
                      id="bilibili-settings-tags"
                      className="h-[58px] min-h-[58px]"
                      value={bilibiliTags}
                      onChange={(event) => {
                        bilibiliTagsDirtyRef.current = true
                        setBilibiliTags(event.target.value)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      placeholder={t.task.bilibiliTagsPlaceholder}
                      rows={4}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    />
                    <p className="text-xs text-muted-foreground">{t.task.bilibiliTagsHelp}</p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-tid">{t.task.bilibiliTidLabel}</Label>
                    <Input
                      id="bilibili-settings-tid"
                      type="number"
                      min="1"
                      step="1"
                      value={bilibiliTid}
                      onChange={(event) => {
                        bilibiliTidDirtyRef.current = true
                        setBilibiliTid(event.target.value)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    />
                    <p className="text-xs text-muted-foreground">
                      {bilibiliCategory ? `${bilibiliCategory.group} / ${bilibiliCategory.name}` : t.task.bilibiliTidHelp}
                    </p>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="bilibili-settings-topic">{t.task.bilibiliTopicLabel}</Label>
                  <div className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_auto]">
                    <Select
                      value={selectedBilibiliTopicOption}
                      onValueChange={handleBilibiliTopicOptionChange}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle || resolvingBilibiliTopic}
                    >
                      <SelectTrigger id="bilibili-settings-topic-config" className="h-10">
                        <SelectValue placeholder={t.task.bilibiliTopicConfigSelect} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="manual">{t.task.bilibiliTopicConfigNone}</SelectItem>
                        {bilibiliTopicOptions.length ? (
                          bilibiliTopicOptions.map((option) => (
                            <SelectItem key={`${option.name}:${option.topicId}`} value={String(option.topicId)}>
                              {option.name}
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="no-config" disabled>
                            {t.task.bilibiliTopicConfigEmpty}
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    <Input
                      id="bilibili-settings-topic"
                      value={bilibiliTopicName}
                      onChange={(event) => {
                        bilibiliTopicDirtyRef.current = true
                        setSelectedBilibiliTopicOption("manual")
                        setBilibiliTopicName(event.target.value)
                        setBilibiliTopicId(null)
                        setBilibiliTopicMissionId(null)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      placeholder={t.task.bilibiliTopicPlaceholder}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle || resolvingBilibiliTopic}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={handleResolveBilibiliTopic}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle || resolvingBilibiliTopic}
                    >
                      {resolvingBilibiliTopic ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                      {resolvingBilibiliTopic ? t.task.bilibiliTopicResolving : t.task.bilibiliTopicResolve}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {bilibiliTopicId
                      ? `${t.task.bilibiliTopicResolved}: ${bilibiliTopicId}`
                      : t.task.bilibiliTopicHelp}
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-season">{t.task.bilibiliSeasonLabel}</Label>
                    <Select
                      value={bilibiliSeasonValue}
                      onValueChange={(value) => {
                        bilibiliSeasonDirtyRef.current = true
                        setBilibiliSeasonValue(value || "none")
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || creatingBilibiliJob || bilibiliSeasonsLoading}
                    >
                      <SelectTrigger id="bilibili-settings-season" className="h-10">
                        <span className="min-w-0 truncate text-left">{selectedBilibiliSeasonLabel}</span>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">{t.task.bilibiliSeasonNone}</SelectItem>
                        {bilibiliSeasons.map((season) => (
                          <SelectItem
                            key={`${season.season_id}:${season.section_id}`}
                            value={`${season.season_id}:${season.section_id}`}
                          >
                            {season.season_title} / {season.section_title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {bilibiliSeasonsLoading || bilibiliSeasonsError ? (
                      <p className="text-xs text-muted-foreground">
                        {bilibiliSeasonsLoading ? t.task.bilibiliSeasonLoading : bilibiliSeasonsError}
                      </p>
                    ) : null}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-cover">{t.task.bilibiliCoverLabel}</Label>
                    <Input
                      key={bilibiliCoverInputKey}
                      id="bilibili-settings-cover"
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      onChange={handleBilibiliManualCoverChange}
                      disabled={savingBilibiliSettings || savingBilibiliCover || creatingBilibiliJob || translatingBilibiliTitle}
                      className="h-10"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      {bilibiliCoverFile?.name || bilibiliCover?.manual_available ? (
                        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
                          {bilibiliCoverFile?.name || t.task.bilibiliManualCoverSaved}
                        </p>
                      ) : null}
                      {bilibiliCover?.manual_available ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={handleDeleteBilibiliManualCover}
                          disabled={savingBilibiliSettings || savingBilibiliCover || creatingBilibiliJob || translatingBilibiliTitle}
                        >
                          <ImageIcon className="size-3" />
                          {t.task.bilibiliCoverClear}
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-upload-timing">{t.task.bilibiliUploadTiming}</Label>
                    <Select
                      value={bilibiliUploadTiming}
                      onValueChange={(value) => {
                        setBilibiliUploadTiming(value as "now" | "scheduled")
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    >
                      <SelectTrigger id="bilibili-settings-upload-timing" className="h-10">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="now">{t.task.uploadNow}</SelectItem>
                        <SelectItem value="scheduled">{t.task.uploadScheduled}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-publish-mode">{t.task.bilibiliPublishMode}</Label>
                    <Select
                      value={bilibiliPublishMode}
                      onValueChange={(value) => {
                        setBilibiliPublishMode(value as BilibiliPublishMode)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    >
                      <SelectTrigger id="bilibili-settings-publish-mode" className="h-10">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="now">{t.task.publishNow}</SelectItem>
                        <SelectItem value="scheduled">{t.task.publishScheduled}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                {bilibiliUploadTiming === "scheduled" ? (
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-upload-after">{t.task.scheduledUploadTime}</Label>
                    <Input
                      id="bilibili-settings-upload-after"
                      type="datetime-local"
                      value={bilibiliUploadAfter}
                      onChange={(event) => {
                        setBilibiliUploadAfter(event.target.value)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" variant="outline" size="sm" onClick={handleQuickBilibiliUploadAfterTenMinutes} disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                        {t.task.bilibiliQuickUploadInTenMinutes}
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={() => handleQuickBilibiliUploadAfter("09:00")} disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                        {t.task.bilibiliQuickUploadMorning}
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={() => handleQuickBilibiliUploadAfter("15:00")} disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                        {t.task.bilibiliQuickUploadAfternoon}
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={() => handleQuickBilibiliUploadAfter("20:00")} disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                        {t.task.bilibiliQuickUploadEvening}
                      </Button>
                    </div>
                  </div>
                ) : null}
                {bilibiliPublishMode === "scheduled" ? (
                  <div className="space-y-2">
                    <Label htmlFor="bilibili-settings-dtime">{t.task.scheduledPublishTime}</Label>
                    <Input
                      id="bilibili-settings-dtime"
                      type="datetime-local"
                      value={bilibiliDtime}
                      onChange={(event) => {
                        setBilibiliDtime(event.target.value)
                        setBilibiliSettingsSavedMessage("")
                      }}
                      disabled={savingBilibiliSettings || translatingBilibiliTitle}
                    />
                    <div className="flex flex-wrap gap-2">
                      {BILIBILI_PUBLISH_HOURS.map((hour) => {
                        const time = `${String(hour).padStart(2, "0")}:00`
                        return (
                          <Button key={time} type="button" variant="outline" size="sm" onClick={() => handleQuickBilibiliDtime(time)} disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                            {time}
                          </Button>
                        )
                      })}
                    </div>
                  </div>
                ) : null}
                {bilibiliError ? (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {bilibiliError}
                  </div>
                ) : null}
                {bilibiliSettingsSavedMessage ? (
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                    {bilibiliSettingsSavedMessage}
                  </div>
                ) : null}
                <div className="flex justify-end">
                  <Button type="submit" disabled={savingBilibiliSettings || translatingBilibiliTitle}>
                    {savingBilibiliSettings ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                    {savingBilibiliSettings ? t.task.savingBilibiliSettings : t.task.saveBilibiliSettings}
                  </Button>
                </div>
                </form>
                <Button
                  type="button"
                  className="w-full"
                  onClick={() => {
                    void handleCreateBilibiliUpload()
                  }}
                  disabled={!displayVideoPath || creatingBilibiliJob || savingBilibiliSettings || savingBilibiliCover || translatingBilibiliTitle}
                >
                  {creatingBilibiliJob ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                  {creatingBilibiliJob ? t.task.creatingBilibiliJob : t.task.publishBilibili}
                </Button>
                {bilibiliJob ? (
                  <div
                    className={`rounded-md border px-3 py-2 text-sm ${
                      bilibiliJob.status === "failed"
                        ? "border-red-200 bg-red-50 text-red-700"
                        : bilibiliJob.status === "succeeded"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                          : "border-sky-200 bg-sky-50 text-sky-900"
                    }`}
                  >
                    {bilibiliJob.status === "scheduled"
                      ? t.task.bilibiliUploadScheduled
                      : bilibiliJob.status === "queued"
                        ? t.task.bilibiliUploadQueued
                        : bilibiliJob.status === "running"
                          ? t.task.bilibiliUploadRunning
                          : bilibiliJob.status === "succeeded"
                            ? t.task.bilibiliUploadSucceeded
                            : bilibiliJob.status === "cancelled"
                              ? t.task.bilibiliUploadCancelled
                            : `${t.task.bilibiliUploadFailed}${bilibiliJob.error_message ? `：${bilibiliJob.error_message}` : ""}`}
                  </div>
                ) : null}
              </section>
            </CardContent>
          </Card>
        ) : null}

        {task?.stages.some((s) => s.name === "concat_video" || s.name === "trim_video") ? (
          <Card className="lg:col-start-1">
            <CardContent className="space-y-3">
              {task?.stages.some((s) => s.name === "concat_video") ? (
                <div className="flex flex-col gap-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-emerald-950">{t.task.concatStageHelp}</p>
                  <Button
                    type="button"
                    size="sm"
                    className="self-start"
                    onClick={handleConcat}
                    disabled={!canConcat || concatenating}
                  >
                    {concatenating ? <Loader2 className="size-3 animate-spin" /> : <Plus className="size-3" />}
                    {concatenating ? t.task.concatenatingStage : t.task.concatStage}
                  </Button>
                </div>
              ) : null}
              {task?.stages.some((s) => s.name === "trim_video") ? (
                <div className="space-y-3 rounded-lg border border-sky-100 bg-sky-50 px-3 py-3">
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-sky-950">{t.task.trimStageHelp}</p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="self-start bg-background"
                      onClick={() => setTrimRanges((current) => [...current, emptyTrimRange()])}
                      disabled={!canEditTrimIntervals}
                    >
                      <Plus className="size-3" />
                      {t.task.addTrimInterval}
                    </Button>
                  </div>
                  <div className="grid gap-2">
                    {trimRanges.map((range, index) => (
                      <div
                        key={range.id}
                        className="grid grid-cols-[1fr_auto_1fr_auto] items-end gap-2 rounded-md border border-sky-100 bg-background p-2 sm:grid-cols-[auto_72px_72px_auto_72px_72px_auto]"
                      >
                        <span className="col-span-4 text-xs font-medium text-muted-foreground sm:col-span-1">
                          {t.task.trimIntervalLabel} {index + 1}
                        </span>
                        <Input
                          aria-label={`${t.task.trimStartMinute} ${index + 1}`}
                          inputMode="numeric"
                          value={range.startMin}
                          onChange={(event) => handleTrimRangeChange(range.id, "startMin", event.target.value)}
                          placeholder={t.task.trimMinutePlaceholder}
                          disabled={!canEditTrimIntervals}
                          className="h-9"
                        />
                        <Input
                          aria-label={`${t.task.trimStartSecond} ${index + 1}`}
                          inputMode="numeric"
                          value={range.startSec}
                          onChange={(event) => handleTrimRangeChange(range.id, "startSec", event.target.value)}
                          placeholder={t.task.trimSecondPlaceholder}
                          disabled={!canEditTrimIntervals}
                          className="h-9"
                        />
                        <span className="pb-2 text-center text-xs text-muted-foreground">{t.task.trimTo}</span>
                        <Input
                          aria-label={`${t.task.trimEndMinute} ${index + 1}`}
                          inputMode="numeric"
                          value={range.endMin}
                          onChange={(event) => handleTrimRangeChange(range.id, "endMin", event.target.value)}
                          placeholder={t.task.trimMinutePlaceholder}
                          disabled={!canEditTrimIntervals}
                          className="h-9"
                        />
                        <Input
                          aria-label={`${t.task.trimEndSecond} ${index + 1}`}
                          inputMode="numeric"
                          value={range.endSec}
                          onChange={(event) => handleTrimRangeChange(range.id, "endSec", event.target.value)}
                          placeholder={t.task.trimSecondPlaceholder}
                          disabled={!canEditTrimIntervals}
                          className="h-9"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          className="h-9 w-9"
                          onClick={() =>
                            setTrimRanges((current) =>
                              current.length > 1 ? current.filter((item) => item.id !== range.id) : [emptyTrimRange()],
                            )
                          }
                          disabled={!canEditTrimIntervals}
                          aria-label={t.task.removeTrimInterval}
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="bg-background"
                      onClick={handleSaveTrimIntervals}
                      disabled={!canEditTrimIntervals || savingTrimIntervals}
                    >
                      {savingTrimIntervals ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
                      {savingTrimIntervals ? t.task.savingTrimIntervals : t.task.saveTrimIntervals}
                    </Button>
                    {canTrim ? (
                      <Button
                        type="button"
                        size="sm"
                        onClick={handleTrim}
                        disabled={trimActionRunning}
                      >
                        {trimming ? <Loader2 className="size-3 animate-spin" /> : <RotateCw className="size-3" />}
                        {trimming ? t.task.trimmingStage : t.task.trimStage}
                      </Button>
                    ) : null}
                    {canTrim ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="bg-background"
                        onClick={handleFastTrim}
                        disabled={trimActionRunning}
                      >
                        {fastTrimming ? <Loader2 className="size-3 animate-spin" /> : <Scissors className="size-3" />}
                        {fastTrimming ? t.task.fastTrimmingStage : t.task.fastTrimStage}
                      </Button>
                    ) : null}
                    {canTrim ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="bg-background"
                        onClick={handleFastTrimReplaceSource}
                        disabled={trimActionRunning}
                      >
                        {fastTrimReplacingSource ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : (
                          <RotateCw className="size-3" />
                        )}
                        {fastTrimReplacingSource ? t.task.fastTrimReplacingSource : t.task.fastTrimReplaceSource}
                      </Button>
                    ) : null}
                    <input
                      ref={trimSourceInputRef}
                      type="file"
                      accept="video/*,.mp4,.mov,.m4v,.mkv,.webm,.avi,.flv,.wmv"
                      className="hidden"
                      onChange={handleTrimSourceFileChange}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="bg-background"
                      onClick={() => trimSourceInputRef.current?.click()}
                      disabled={!canEditTrimIntervals || trimActionRunning}
                    >
                      <FolderOpen className="size-3" />
                      {t.task.chooseTrimSourceVideo}
                    </Button>
                    {trimSourceFile ? (
                      <>
                        <span className="max-w-full truncate text-xs text-sky-900">
                          {t.task.selectedTrimSourceVideo}: {trimSourceFile.name}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setTrimSourceFile(null)
                            if (trimSourceInputRef.current) trimSourceInputRef.current.value = ""
                          }}
                          disabled={trimActionRunning}
                        >
                          {t.task.clearTrimSourceVideo}
                        </Button>
                      </>
                    ) : null}
                    {trimSavedMessage ? <span className="text-xs text-emerald-700">{trimSavedMessage}</span> : null}
                  </div>
                </div>
              ) : null}
              {concatError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {concatError}
                </div>
              ) : null}
              {trimError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {trimError}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        {task && displayVideoPath ? (
          <Card className="lg:col-start-1">
            <CardHeader>
              <CardTitle>{t.task.finalVideo}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <video
                key={`${task.id}-${videoRefreshKey}`}
                src={`${finalVideoUrl(task.id)}?v=${videoRefreshKey}`}
                controls
                preload="metadata"
                className="w-full rounded-md border border-emerald-200 bg-black"
              />
              <p className="break-all text-xs text-muted-foreground">{displayVideoPath}</p>
              <div className="flex flex-wrap gap-2">
                <Button nativeButton={false} render={<a href={finalVideoDownloadUrl(task.id)} />}>
                  <Download className="size-4" />
                  {t.task.download}
                </Button>
                <Dialog open={bilibiliOpen} onOpenChange={setBilibiliOpen}>
                  <DialogContent className="max-h-[calc(100dvh-5rem)] overflow-y-auto">
                    <form onSubmit={handleCreateBilibiliUpload} className="space-y-4">
                      <DialogHeader>
                        <DialogTitle>{t.task.publishBilibiliTitle}</DialogTitle>
                        <DialogDescription>{t.task.publishBilibiliDescription}</DialogDescription>
                      </DialogHeader>
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <Label htmlFor="bilibili-title">{t.task.bilibiliTitleLabel}</Label>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={handleTranslateBilibiliTitle}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle}
                          >
                            {translatingBilibiliTitle ? <Loader2 className="size-4 animate-spin" /> : <Languages className="size-4" />}
                            {translatingBilibiliTitle ? t.task.bilibiliTitleTranslating : t.task.bilibiliTitleTranslate}
                          </Button>
                        </div>
                        <Input
                          id="bilibili-title"
                          value={bilibiliTitle}
                          onChange={(event) => {
                            bilibiliTitleDirtyRef.current = true
                            setBilibiliTitle(event.target.value)
                          }}
                          placeholder={task.title || t.task.bilibiliTitlePlaceholder}
                          disabled={creatingBilibiliJob || translatingBilibiliTitle}
                        />
                        <p className="text-xs text-muted-foreground">{t.task.bilibiliTitleHelp}</p>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="bilibili-description">{t.task.bilibiliDescriptionLabel}</Label>
                        <Textarea
                          id="bilibili-description"
                          value={bilibiliDescription}
                          onChange={(event) => {
                            bilibiliDescriptionDirtyRef.current = true
                            setBilibiliDescription(event.target.value)
                          }}
                          rows={5}
                          disabled={creatingBilibiliJob || translatingBilibiliTitle}
                        />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_7rem]">
                        <div className="space-y-2">
                          <Label htmlFor="bilibili-tags">{t.task.bilibiliTagsLabel}</Label>
                          <Textarea
                            id="bilibili-tags"
                            value={bilibiliTags}
                            onChange={(event) => {
                              bilibiliTagsDirtyRef.current = true
                              setBilibiliTags(event.target.value)
                            }}
                            placeholder={t.task.bilibiliTagsPlaceholder}
                            rows={4}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="bilibili-tid">{t.task.bilibiliTidLabel}</Label>
                          <Input
                            id="bilibili-tid"
                            type="number"
                            min="1"
                            step="1"
                            value={bilibiliTid}
                            onChange={(event) => {
                              bilibiliTidDirtyRef.current = true
                              setBilibiliTid(event.target.value)
                            }}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle}
                          />
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="bilibili-topic">{t.task.bilibiliTopicLabel}</Label>
                        <div className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_auto]">
                          <Select
                            value={selectedBilibiliTopicOption}
                            onValueChange={handleBilibiliTopicOptionChange}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle || resolvingBilibiliTopic}
                          >
                            <SelectTrigger id="bilibili-topic-config" className="h-10">
                              <SelectValue placeholder={t.task.bilibiliTopicConfigSelect} />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="manual">{t.task.bilibiliTopicConfigNone}</SelectItem>
                              {bilibiliTopicOptions.length ? (
                                bilibiliTopicOptions.map((option) => (
                                  <SelectItem key={`${option.name}:${option.topicId}`} value={String(option.topicId)}>
                                    {option.name}
                                  </SelectItem>
                                ))
                              ) : (
                                <SelectItem value="no-config" disabled>
                                  {t.task.bilibiliTopicConfigEmpty}
                                </SelectItem>
                              )}
                            </SelectContent>
                          </Select>
                          <Input
                            id="bilibili-topic"
                            value={bilibiliTopicName}
                            onChange={(event) => {
                              bilibiliTopicDirtyRef.current = true
                              setSelectedBilibiliTopicOption("manual")
                              setBilibiliTopicName(event.target.value)
                              setBilibiliTopicId(null)
                              setBilibiliTopicMissionId(null)
                            }}
                            placeholder={t.task.bilibiliTopicPlaceholder}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle || resolvingBilibiliTopic}
                          />
                          <Button
                            type="button"
                            variant="outline"
                            onClick={handleResolveBilibiliTopic}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle || resolvingBilibiliTopic}
                          >
                            {resolvingBilibiliTopic ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                            {resolvingBilibiliTopic ? t.task.bilibiliTopicResolving : t.task.bilibiliTopicResolve}
                          </Button>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {bilibiliTopicId
                            ? `${t.task.bilibiliTopicResolved}: ${bilibiliTopicId}`
                            : t.task.bilibiliTopicHelp}
                        </p>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="bilibili-cover">{t.task.bilibiliCoverLabel}</Label>
                        <Input
                          key={bilibiliCoverInputKey}
                          id="bilibili-cover"
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          onChange={handleBilibiliManualCoverChange}
                          disabled={savingBilibiliCover || creatingBilibiliJob || translatingBilibiliTitle}
                          className="h-10"
                        />
                        <div className="flex flex-wrap items-center gap-2">
                          {bilibiliCoverFile?.name || bilibiliCover?.manual_available ? (
                            <p className="min-w-0 flex-1 text-xs text-muted-foreground">
                              {bilibiliCoverFile?.name || t.task.bilibiliManualCoverSaved}
                            </p>
                          ) : null}
                          {bilibiliCover?.manual_available ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={handleDeleteBilibiliManualCover}
                              disabled={savingBilibiliCover || creatingBilibiliJob || translatingBilibiliTitle}
                            >
                              <ImageIcon className="size-3" />
                              {t.task.bilibiliCoverClear}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="bilibili-upload-timing">{t.task.bilibiliUploadTiming}</Label>
                        <Select
                          value={bilibiliUploadTiming}
                          onValueChange={(value) => setBilibiliUploadTiming(value as "now" | "scheduled")}
                          disabled={creatingBilibiliJob || translatingBilibiliTitle}
                        >
                          <SelectTrigger id="bilibili-upload-timing" className="h-10">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="now">{t.task.uploadNow}</SelectItem>
                            <SelectItem value="scheduled">{t.task.uploadScheduled}</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      {bilibiliUploadTiming === "scheduled" ? (
                        <div className="space-y-2">
                          <Label htmlFor="bilibili-upload-after">{t.task.scheduledUploadTime}</Label>
                          <Input
                            id="bilibili-upload-after"
                            type="datetime-local"
                            value={bilibiliUploadAfter}
                            onChange={(event) => setBilibiliUploadAfter(event.target.value)}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle}
                          />
                          <div className="flex flex-wrap gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={handleQuickBilibiliUploadAfterTenMinutes}
                              disabled={creatingBilibiliJob || translatingBilibiliTitle}
                            >
                              {t.task.bilibiliQuickUploadInTenMinutes}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => handleQuickBilibiliUploadAfter("09:00")}
                              disabled={creatingBilibiliJob || translatingBilibiliTitle}
                            >
                              {t.task.bilibiliQuickUploadMorning}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => handleQuickBilibiliUploadAfter("15:00")}
                              disabled={creatingBilibiliJob || translatingBilibiliTitle}
                            >
                              {t.task.bilibiliQuickUploadAfternoon}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => handleQuickBilibiliUploadAfter("20:00")}
                              disabled={creatingBilibiliJob || translatingBilibiliTitle}
                            >
                              {t.task.bilibiliQuickUploadEvening}
                            </Button>
                          </div>
                        </div>
                      ) : null}
                      <div className="space-y-2">
                        <Label htmlFor="bilibili-publish-mode">{t.task.bilibiliPublishMode}</Label>
                        <Select
                          value={bilibiliPublishMode}
                          onValueChange={(value) => setBilibiliPublishMode(value as BilibiliPublishMode)}
                          disabled={creatingBilibiliJob || translatingBilibiliTitle}
                        >
                          <SelectTrigger id="bilibili-publish-mode" className="h-10">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="now">{t.task.publishNow}</SelectItem>
                            <SelectItem value="scheduled">{t.task.publishScheduled}</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      {bilibiliPublishMode === "scheduled" ? (
                        <div className="space-y-2">
                          <Label htmlFor="bilibili-dtime">{t.task.scheduledPublishTime}</Label>
                          <Input
                            id="bilibili-dtime"
                            type="datetime-local"
                            value={bilibiliDtime}
                            onChange={(event) => setBilibiliDtime(event.target.value)}
                            disabled={creatingBilibiliJob || translatingBilibiliTitle}
                          />
                          <div className="flex flex-wrap gap-2">
                            {BILIBILI_PUBLISH_HOURS.map((hour) => {
                              const time = `${String(hour).padStart(2, "0")}:00`
                              return (
                                <Button key={time} type="button" variant="outline" size="sm" onClick={() => handleQuickBilibiliDtime(time)} disabled={creatingBilibiliJob || translatingBilibiliTitle}>
                                  {time}
                                </Button>
                              )
                            })}
                          </div>
                        </div>
                      ) : null}
                      {bilibiliError ? (
                        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                          {bilibiliError}
                        </div>
                      ) : null}
                      <DialogFooter>
                        <DialogClose render={<Button type="button" variant="outline" disabled={creatingBilibiliJob || translatingBilibiliTitle} />}>
                          {t.common.cancel}
                        </DialogClose>
                        <Button type="submit" disabled={creatingBilibiliJob || savingBilibiliCover || translatingBilibiliTitle}>
                          {creatingBilibiliJob ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                          {creatingBilibiliJob ? t.task.creatingBilibiliJob : t.task.startBilibiliUpload}
                        </Button>
                      </DialogFooter>
                    </form>
                  </DialogContent>
                </Dialog>
                {isBilibiliTask ? (
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-100 bg-sky-50 px-2 py-2">
                    <Select
                      value={watermarkMode}
                      onValueChange={(value) => setWatermarkMode(value as WatermarkMaskMode)}
                      disabled={creatingWatermarkJob || watermarkJob?.status === "queued" || watermarkJob?.status === "running"}
                    >
                      <SelectTrigger className="h-9 min-w-40 bg-background">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="patch">{t.task.watermarkMaskPatch}</SelectItem>
                        <SelectItem value="blur">{t.task.watermarkMaskBlur}</SelectItem>
                      </SelectContent>
                    </Select>
                    <Button
                      variant="outline"
                      onClick={handleCreateWatermarkMask}
                      disabled={creatingWatermarkJob || watermarkJob?.status === "queued" || watermarkJob?.status === "running"}
                    >
                      {creatingWatermarkJob || watermarkJob?.status === "queued" || watermarkJob?.status === "running" ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <RotateCw className="size-4" />
                      )}
                      {creatingWatermarkJob ? t.task.creatingWatermarkMask : t.task.maskWatermark}
                    </Button>
                  </div>
                ) : null}
              </div>
              {watermarkError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {watermarkError}
                </div>
              ) : null}
              {watermarkJob ? (
                <div
                  className={`rounded-lg border px-3 py-2 text-sm ${
                    watermarkJob.status === "failed"
                      ? "border-red-200 bg-red-50 text-red-700"
                      : watermarkJob.status === "succeeded"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border-sky-200 bg-sky-50 text-sky-900"
                  }`}
                >
                  {watermarkJob.status === "queued"
                    ? t.task.watermarkMaskQueued
                    : watermarkJob.status === "running"
                      ? t.task.watermarkMaskRunning
                      : watermarkJob.status === "succeeded"
                        ? t.task.watermarkMaskSucceeded
                        : `${t.task.watermarkMaskFailed}${watermarkJob.error_message ? `：${watermarkJob.error_message}` : ""}`}
                </div>
              ) : null}
              {isBilibiliTask ? <p className="text-xs text-muted-foreground">{t.task.watermarkMaskHelp}</p> : null}
            </CardContent>
          </Card>
        ) : null}

        <Card className="lg:col-start-1">
          <CardHeader>
            <CardTitle>{t.task.stages}</CardTitle>
          </CardHeader>
          <CardContent>
            {isManual && canRedoStage ? (
              <p className="mb-3 text-sm text-muted-foreground">{t.task.redoStageHelp}</p>
            ) : null}
            {redoError ? (
              <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {redoError}
              </div>
            ) : null}
            {task ? (
              <ol className="grid gap-3">
                {task.stages.map((stage, index) => {
                  const stageProgress = normalizeProgress(stage.progress)
                  const showRedo =
                    canRedoStage && (stage.status === "succeeded" || stage.status === "failed")
                  return (
                    <li
                      key={stage.name}
                      className="flex items-start gap-3 rounded-lg border border-border bg-background px-4 py-3"
                    >
                      <div className="mt-0.5">{stageIcon(stage.status)}</div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs text-muted-foreground">#{index + 1}</span>
                          <p className="font-medium">{stageLabel(stage.name, stage.label)}</p>
                          <Badge className={statusBadgeClass(stage.status)}>{statusLabel(stage.status)}</Badge>
                          {stage.started_at ? (
                            <span className="text-xs text-muted-foreground">
                              {durationOf(stage.started_at, stage.completed_at)}
                            </span>
                          ) : null}
                          {showRedo ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="ml-auto h-7 px-2 text-xs"
                              disabled={redoingStage !== null}
                              onClick={() => setRedoConfirmStage(stage.name)}
                            >
                              {redoingStage === stage.name ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <RotateCw className="size-3" />
                              )}
                              {redoingStage === stage.name ? t.task.redoingStage : t.task.redoStage}
                            </Button>
                          ) : null}
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {stage.error_message || stage.last_message || t.common.waiting}
                        </p>
                        {stage.status === "running" && stageProgress !== null ? (
                          <div className="mt-2 flex items-center gap-3">
                            <Progress value={stageProgress} className="min-w-0 flex-1" />
                            <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">
                              {stageProgress}%
                            </span>
                          </div>
                        ) : null}
                      </div>
                    </li>
                  )
                })}
              </ol>
            ) : null}

            {task?.error_message ? (
              <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {task.error_message}
              </div>
            ) : null}
            <Dialog
              open={redoConfirmStage !== null}
              onOpenChange={(open) => {
                if (!open && redoingStage === null) setRedoConfirmStage(null)
              }}
            >
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>{t.task.redoStageTitle}</DialogTitle>
                  <DialogDescription>
                    {t.task.redoStageDescription} {redoConfirmStageInfo ? stageLabel(redoConfirmStageInfo.name, redoConfirmStageInfo.label) : ""}
                    {t.common.sentenceEnd}
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <DialogClose render={<Button variant="outline" disabled={redoingStage !== null} />}>
                    {t.common.cancel}
                  </DialogClose>
                  <Button onClick={handleConfirmRedoStage} disabled={redoingStage !== null}>
                    {redoingStage ? <Loader2 className="size-4 animate-spin" /> : <RotateCw className="size-4" />}
                    {redoingStage ? t.task.redoingStage : t.task.confirmRedoStage}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            {isPaused ? (
              <div className="mt-4 space-y-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-sky-900">{t.task.continueHelp}</p>
                  <Button onClick={() => handleContinue()} disabled={continuing}>
                    {continuing ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                    {continuing ? t.task.continuing : t.task.continueTask}
                  </Button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-sky-900">{t.task.continueAutoHelp}</p>
                  <Button variant="outline" onClick={() => handleContinue("auto")} disabled={continuing}>
                    {continuing ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                    {continuing ? t.task.continuing : t.task.continueAutoTask}
                  </Button>
                </div>
              </div>
            ) : null}
            {continueError ? (
              <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {continueError}
              </div>
            ) : null}
            {isFailed ? (
              <div className="mt-4 flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-amber-800">
                  {t.task.resumeHelp}
                </p>
                <Button onClick={handleResume} disabled={resuming}>
                  {resuming ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                  {resuming ? t.task.resuming : t.task.resumeTask}
                </Button>
              </div>
            ) : null}
            {resumeError ? (
              <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {resumeError}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-start-1">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>{t.task.runLog}</CardTitle>
            <FileText className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-80 rounded-lg border bg-zinc-950 p-3 text-xs text-zinc-100">
              {log ? (
                <pre className="whitespace-pre-wrap break-words font-mono">{log}</pre>
              ) : (
                <p className="text-zinc-400">{t.task.emptyLog}</p>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="border-red-200 lg:col-start-1">
          <CardHeader>
            <CardTitle className="text-red-700">{t.task.dangerZone}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-muted-foreground">
                {t.task.rerunHelp}
              </p>
              <Dialog open={rerunOpen} onOpenChange={setRerunOpen}>
                <DialogTrigger
                  render={
                    <Button variant="outline" disabled={!task || isRunning}>
                      <RotateCw className="size-4" />
                      {t.task.rerunTask}
                    </Button>
                  }
                />
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t.task.rerunTitle}</DialogTitle>
                    <DialogDescription>
                      {t.task.rerunDescription}
                    </DialogDescription>
                  </DialogHeader>
                  {rerunError ? (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {rerunError}
                    </div>
                  ) : null}
                  <DialogFooter>
                    <DialogClose render={<Button variant="outline" disabled={rerunning} />}>
                      {t.common.cancel}
                    </DialogClose>
                    <Button onClick={handleRerun} disabled={rerunning}>
                      {rerunning ? <Loader2 className="size-4 animate-spin" /> : <RotateCw className="size-4" />}
                      {rerunning ? t.task.rerunning : t.task.confirmRerun}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-muted-foreground">
                {t.task.deleteHelp} <code className="font-mono text-xs">workfolder/</code>
                {t.common.sentenceEnd}
              </p>
              <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <DialogTrigger
                  render={
                    <Button variant="destructive" disabled={!task || isRunning}>
                      <Trash2 className="size-4" />
                      {t.task.deleteTask}
                    </Button>
                  }
                />
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t.task.deleteTitle}</DialogTitle>
                    <DialogDescription>
                      {t.task.deleteDescription}
                    </DialogDescription>
                  </DialogHeader>
                  {deleteError ? (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {deleteError}
                    </div>
                  ) : null}
                  <DialogFooter>
                    <DialogClose render={<Button variant="outline" disabled={deleting} />}>
                      {t.common.cancel}
                    </DialogClose>
                    <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
                      {deleting ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                      {deleting ? t.task.deleting : t.task.confirmDelete}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
            {isRunning ? (
              <p className="text-xs text-amber-600">{t.task.runningLocked}</p>
            ) : null}
          </CardContent>
        </Card>
    </div>
  )
}

export default function TaskDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  return <TaskDetailContent id={id} />
}
