"use client"

import { FormEvent, useState } from "react"
import { Copy, ListPlus, Loader2, Search } from "lucide-react"
import {
  BatchTaskResult, createTasksBatch, createVideoLink, ExecutionMode, lookupYouTubeAuthors,
  YouTubeAuthorResult, YouTubeAuthorVideo,
} from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { useAuthorMetadata } from "@/components/use-author-metadata"
import { SelectionCheckbox, YouTubeAuthorVideoRow } from "@/components/youtube-author-video"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

export function YouTubeAuthorLookup() {
  const { t } = useI18n()
  const [urls, setUrls] = useState("")
  const [count, setCount] = useState("5")
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<YouTubeAuthorResult[] | null>(null)
  const [error, setError] = useState("")
  const [copied, setCopied] = useState(false)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState(new Set<string>())
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("auto")
  const [autoPublish, setAutoPublish] = useState(false)
  const [creating, setCreating] = useState(false)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [summary, setSummary] = useState<{ created: number; existing: number; failed: number } | null>(null)
  const [batchErrors, setBatchErrors] = useState<Record<string, string>>({})
  const [visibleCounts, setVisibleCounts] = useState<Record<string, number>>({})
  const metadata = useAuthorMetadata(setResults)
  const videos = [...new Map(results?.flatMap((channel) => [...channel.videos, ...channel.shorts])
    .map((video) => [video.video_id, video])).values()]
  const available = videos.filter((video) => !video.task_id)
  const showFloatingAction = selected.size > 0 || creating
  const batchCreateLabel = creating
    ? `${t.home.authorLookupBatchCreating} ${progress.done}/${progress.total}`
    : `${t.home.authorLookupBatchCreate} (${selected.size})`

  function toggleGroup(group: YouTubeAuthorVideo[]) {
    const ids = group.filter((video) => !video.task_id).map((video) => video.video_id)
    setSelected((current) => {
      const next = new Set(current)
      const all = ids.every((id) => current.has(id))
      ids.forEach((id) => all ? next.delete(id) : next.add(id))
      return next
    })
  }

  async function query(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const channelUrls = urls.split(/[\s,，;；]+/).map((url) => url.trim()).filter(Boolean)
    if (!channelUrls.length || channelUrls.length > 20) {
      setError(t.home.authorLookupUrlLimit)
      return
    }
    metadata.reset()
    setLoading(true)
    setError("")
    setCopied(false)
    setResults(null)
    setSelected(new Set())
    setSummary(null)
    setBatchErrors({})
    setVisibleCounts({})
    try {
      setResults((await lookupYouTubeAuthors(channelUrls, Number(count))).channels)
    } catch (err) {
      setError(err instanceof Error ? err.message : t.home.authorLookupError)
    } finally {
      setLoading(false)
    }
  }

  async function save(video: YouTubeAuthorVideo) {
    setSaving((current) => ({ ...current, [video.video_id]: true }))
    setError("")
    try {
      await createVideoLink(video.url)
      setSaved((current) => ({ ...current, [video.video_id]: true }))
    } catch (err) {
      setError(err instanceof Error ? err.message : t.home.videoLinkCreateError)
    } finally {
      setSaving((current) => ({ ...current, [video.video_id]: false }))
    }
  }

  async function copyLinks() {
    try {
      await navigator.clipboard.writeText(videos.map((video) => video.url).join("\n"))
      setCopied(true)
    } catch {
      setError(t.home.authorLookupCopyError)
    }
  }

  async function createSelected() {
    const items = available.filter((video) => selected.has(video.video_id))
    if (!items.length) return
    setCreating(true)
    setSummary(null)
    setBatchErrors({})
    setProgress({ done: 0, total: items.length })
    const counts = { created: 0, existing: 0, failed: 0 }
    try {
      for (let offset = 0; offset < items.length; offset += 100) {
        const chunk = items.slice(offset, offset + 100)
        let outcomes: BatchTaskResult[]
        try {
          outcomes = (await createTasksBatch(chunk.map((video) => video.url), executionMode, autoPublish)).results
        } catch (err) {
          outcomes = chunk.map((video) => ({ url: video.url, video_id: video.video_id, task_id: null, task_status: null,
            status: "failed", error: err instanceof Error ? err.message : t.home.authorLookupBatchError }))
        }
        const byUrl = new Map(outcomes.map((outcome) => [outcome.url, outcome]))
        const updates = new Map<string, BatchTaskResult>()
        const failures: Record<string, string> = {}
        for (const video of chunk) {
          const outcome = byUrl.get(video.url) ?? { url: video.url, task_id: null, task_status: null,
            status: "failed" as const, error: t.home.authorLookupBatchError }
          counts[outcome.status] += 1
          if (outcome.status === "failed") failures[video.video_id] = outcome.error || t.home.authorLookupBatchError
          else updates.set(video.video_id, outcome)
        }
        setBatchErrors((current) => ({ ...current, ...failures }))
        setSelected((current) => new Set([...current].filter((id) => !updates.has(id))))
        const apply = (video: YouTubeAuthorVideo) => {
          const update = updates.get(video.video_id)
          return update ? { ...video, task_id: update.task_id, task_status: update.task_status } : video
        }
        setResults((current) => current?.map((channel) => ({ ...channel, videos: channel.videos.map(apply), shorts: channel.shorts.map(apply) })) ?? null)
        setProgress({ done: Math.min(offset + chunk.length, items.length), total: items.length })
      }
      setSummary(counts)
    } finally {
      setCreating(false)
    }
  }

  return (
    <Card className={showFloatingAction ? "mb-20" : undefined}>
      <CardHeader>
        <CardTitle>{t.home.authorLookupTitle}</CardTitle>
        <p className="text-sm text-muted-foreground">{t.home.authorLookupHelp}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={query} className="grid items-end gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div className="space-y-2">
            <Label htmlFor="author-urls">{t.home.authorLookupUrls}</Label>
            <Textarea id="author-urls" value={urls} onChange={(event) => setUrls(event.target.value)}
              placeholder={"https://www.youtube.com/@pokedexfillers\nhttps://www.youtube.com/@WolfeyVGC"}
              rows={3} required disabled={loading || creating} aria-describedby="author-urls-help" />
            <p id="author-urls-help" className="text-xs text-muted-foreground">{t.home.authorLookupUrlHelp}</p>
          </div>
          <div className="flex flex-wrap items-end gap-3 lg:pb-6">
            <div className="space-y-2">
              <Label htmlFor="author-count">{t.home.authorLookupCount}</Label>
              <Input id="author-count" type="number" min={1} max={100} step={1} required className="w-28"
                value={count} disabled={loading || creating} onChange={(event) => setCount(event.target.value)} />
            </div>
            <Button type="submit" disabled={loading || creating || !urls.trim()}>
              {loading ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
              {loading ? t.home.authorLookupLoading : t.home.authorLookupQuery}
            </Button>
          </div>
        </form>
        {error ? <p role="alert" className="break-words rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
        {loading ? <p role="status" className="text-sm text-muted-foreground">{t.home.authorLookupLoadingHelp}</p> : null}
        {results ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span>{t.home.authorLookupResults}: {results.length} · {t.home.authorLookupTotal}: {videos.length}</span>
              <Button type="button" variant="outline" size="sm" onClick={copyLinks} disabled={!videos.length}>
                <Copy className="size-4" />{copied ? t.home.authorLookupCopied : t.home.authorLookupCopy}
              </Button>
            </div>
            <div className="space-y-3 rounded-lg border border-pink-200 bg-pink-50/40 p-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <SelectionCheckbox checked={available.length > 0 && available.every((video) => selected.has(video.video_id))}
                    mixed={available.some((video) => selected.has(video.video_id)) && !available.every((video) => selected.has(video.video_id))}
                    disabled={creating || !available.length} label={t.home.authorLookupSelectAll} onChange={() => toggleGroup(videos)} />
                  {t.home.authorLookupSelectAll}
                </label>
                <span className="text-sm">{t.home.authorLookupSelected}: {selected.size}</span>
                <Button type="button" size="sm" variant="ghost" disabled={creating || !selected.size} onClick={() => setSelected(new Set())}>{t.home.authorLookupClearSelection}</Button>
                <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
                  <Label htmlFor="author-execution-mode">{t.home.executionModeLabel}</Label>
                  <select id="author-execution-mode" value={executionMode} disabled={creating}
                    onChange={(event) => setExecutionMode(event.target.value as ExecutionMode)} className="h-9 max-w-full rounded-md border bg-white px-2 text-sm">
                    <option value="auto">{t.home.executionAuto}</option><option value="manual">{t.home.executionManual}</option>
                  </select>
                  <Button type="button" onClick={createSelected} disabled={creating || !selected.size}>
                    {creating ? <Loader2 className="size-4 animate-spin" /> : null}
                    {batchCreateLabel}
                  </Button>
                </div>
              </div>
              <label className="flex items-start gap-2 text-xs text-muted-foreground" title={t.home.autoBilibiliUploadHelp}>
                <input type="checkbox" className="mt-0.5 accent-pink-500" checked={autoPublish} disabled={creating} onChange={(event) => setAutoPublish(event.target.checked)} />
                {t.home.autoBilibiliUpload}
              </label>
              <p className="text-xs text-muted-foreground">{t.home.authorLookupBatchHelp}</p>
              {summary ? <p role="status" className="text-sm">{t.home.authorLookupBatchCreated}: {summary.created} · {t.home.authorLookupBatchExisting}: {summary.existing} · {t.home.authorLookupBatchFailed}: {summary.failed}</p> : null}
            </div>
            <p className="text-xs text-muted-foreground">{t.home.authorLookupDateHelp}</p>
            {results.map((channel) => (
              <section key={channel.input_url} className="min-w-0 space-y-3 rounded-lg border p-3 sm:p-4">
                <div><h3 className="break-words font-medium">{channel.title}</h3><p className="break-all text-xs text-muted-foreground">{channel.input_url}</p></div>
                {channel.errors.channel ? <p className="break-words text-sm text-red-600">{channel.errors.channel}</p> : (
                  <div className="grid gap-4 xl:grid-cols-2">
                    {(["videos", "shorts"] as const).map((type) => {
                      const group = channel[type]
                      const selectable = group.filter((video) => !video.task_id)
                      const checkedCount = selectable.filter((video) => selected.has(video.video_id)).length
                      const groupKey = `${channel.input_url}:${type}`
                      const visible = visibleCounts[groupKey] ?? 10
                      return (
                        <div key={type} className="min-w-0 space-y-2">
                          <div className="flex items-center justify-between gap-2">
                            <h4 className="text-sm font-medium">{type === "videos" ? t.home.authorLookupVideos : "Shorts"} ({group.length})</h4>
                            <label className="flex items-center gap-2 text-xs">
                              <SelectionCheckbox checked={selectable.length > 0 && checkedCount === selectable.length} mixed={checkedCount > 0 && checkedCount < selectable.length}
                                disabled={creating || !selectable.length} label={`${channel.title} ${type === "videos" ? t.home.authorLookupVideos : "Shorts"} ${t.home.authorLookupSelectGroup}`}
                                onChange={() => toggleGroup(group)} />{t.home.authorLookupSelectGroup}
                            </label>
                          </div>
                          {channel.errors[type] ? <p className="break-words text-xs text-red-600">{channel.errors[type]}</p> : null}
                          {!group.length && !channel.errors[type] ? <p className="text-sm text-muted-foreground">{t.home.authorLookupEmpty}</p> : null}
                          <ol className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
                            {group.slice(0, visible).map((video) => (
                              <YouTubeAuthorVideoRow key={video.video_id} video={video} channelId={channel.channel_id}
                                selected={selected.has(video.video_id)} busy={creating} saving={!!saving[video.video_id]} saved={!!saved[video.video_id]}
                                error={batchErrors[video.video_id]} onSelect={() => toggleGroup([video])} onSave={() => save(video)} onVisible={metadata.enqueue} />
                            ))}
                          </ol>
                          {group.length > visible ? <Button type="button" variant="ghost" size="sm" onClick={() => setVisibleCounts((current) => ({ ...current, [groupKey]: visible + 10 }))}>{t.home.authorLookupShowMore}</Button> : null}
                        </div>
                      )
                    })}
                  </div>
                )}
              </section>
            ))}
          </div>
        ) : null}
      </CardContent>
      {showFloatingAction ? (
        <div className="fixed right-4 bottom-[max(1rem,env(safe-area-inset-bottom))] z-30 max-w-[calc(100vw-2rem)] animate-in fade-in slide-in-from-bottom-2 duration-150 motion-reduce:animate-none sm:right-6 sm:bottom-6 lg:right-8">
          <Button type="button" size="lg" onClick={createSelected} disabled={creating || !selected.size}
            className="h-12 gap-2 rounded-full px-5 shadow-lg shadow-pink-500/25 ring-1 ring-pink-200">
            {creating ? <Loader2 className="size-5 animate-spin" /> : <ListPlus className="size-5" />}
            <span className="tabular-nums">{batchCreateLabel}</span>
          </Button>
        </div>
      ) : null}
    </Card>
  )
}
