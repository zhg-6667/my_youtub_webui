"use client"

import Link from "next/link"
import { useEffect, useRef, useState } from "react"
import { ExternalLink, Film, Loader2, Plus } from "lucide-react"
import { API_BASE, YouTubeAuthorVideo } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"

export function SelectionCheckbox({ checked, mixed = false, disabled, label, onChange }: {
  checked: boolean; mixed?: boolean; disabled?: boolean; label: string; onChange: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = mixed }, [mixed])
  return <input ref={ref} type="checkbox" checked={checked} disabled={disabled} onChange={onChange}
    aria-label={label} className="size-4 shrink-0 cursor-pointer accent-pink-500 disabled:cursor-default" />
}

function durationText(seconds: number | null) {
  if (seconds === null) return ""
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds / 60) % 60
  const tail = `${minutes}:${String(seconds % 60).padStart(2, "0")}`
  return hours ? `${hours}:${tail.padStart(5, "0")}` : tail
}

export function YouTubeAuthorVideoRow({ video, channelId, selected, busy, saving, saved, error, onSelect, onSave, onVisible }: {
  video: YouTubeAuthorVideo; channelId: string | null; selected: boolean; busy: boolean
  saving: boolean; saved: boolean; error?: string; onSelect: () => void; onSave: () => void
  onVisible: (videoId: string, channelId: string | null) => void
}) {
  const { t, language, statusLabel } = useI18n()
  const ref = useRef<HTMLLIElement>(null)
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => {
    if (video.metadata_checked || !ref.current) return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect()
        onVisible(video.video_id, channelId)
      }
    }, { rootMargin: "100px" })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [video.video_id, video.metadata_checked, channelId, onVisible])

  let dateText = video.published_date
  if (video.published_at) {
    const date = new Date(video.published_at)
    if (!Number.isNaN(date.getTime())) dateText = date.toLocaleString(language === "zh" ? "zh-CN" : "en-GB", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    })
  }

  return (
    <li ref={ref} className={`flex min-w-0 items-start gap-2 rounded-md border p-2.5 ${selected ? "border-pink-300 bg-pink-50/50" : "border-border/70 bg-muted/25"}`}>
      <div className="pt-1"><SelectionCheckbox checked={selected} disabled={busy || !!video.task_id}
        label={`${t.home.authorLookupSelectVideo} ${video.title}`} onChange={onSelect} /></div>
      <div className="flex h-[45px] w-20 shrink-0 items-center justify-center overflow-hidden rounded bg-zinc-100 sm:h-[54px] sm:w-24">
        {imageFailed ? <Film className="size-5 text-muted-foreground" aria-hidden="true" /> : (
          // Thumbnail dimensions stay fixed for both ordinary videos and Shorts.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`${API_BASE}${video.thumbnail_url}`} alt="" width={96} height={54} loading="lazy" decoding="async"
            className="h-full w-full object-cover" onError={() => setImageFailed(true)} />
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <a href={video.url} target="_blank" rel="noopener noreferrer" title={video.title}
          className="flex items-start gap-1 text-sm font-medium hover:underline">
          <span className="line-clamp-2 min-w-0 break-words">{video.title}</span><ExternalLink className="mt-1 size-3 shrink-0" />
        </a>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{t.home.authorLookupPublished}: {dateText || (video.metadata_checked ? t.home.authorLookupDateUnknown : t.home.authorLookupDateLoading)}</span>
          {video.duration_seconds !== null ? <span>{durationText(video.duration_seconds)}</span> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {video.task_id ? (
            <Link href={`/tasks/${encodeURIComponent(video.task_id)}`} className="text-xs text-primary hover:underline">
              {t.home.authorLookupViewTask} · {statusLabel(video.task_status)}
            </Link>
          ) : <Button type="button" size="sm" variant="outline" onClick={onSave} disabled={busy || saving || saved}>
            {saving ? <Loader2 className="size-3 animate-spin" /> : <Plus className="size-3" />}
            {saved ? t.home.authorLookupSaved : saving ? t.home.videoLinkRecording : t.home.videoLinkRecord}
          </Button>}
        </div>
        {error ? <p role="alert" className="break-words text-xs text-red-600">{error}</p> : null}
      </div>
    </li>
  )
}
