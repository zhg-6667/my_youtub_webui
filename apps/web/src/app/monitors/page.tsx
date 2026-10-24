"use client"

import { FormEvent, useEffect, useState } from "react"
import { Plus, Power, RefreshCw, Trash2 } from "lucide-react"

import {
  YouTubeChannelMonitor,
  checkYouTubeChannelMonitor,
  createYouTubeChannelMonitor,
  deleteYouTubeChannelMonitor,
  listYouTubeChannelMonitors,
  updateYouTubeChannelMonitor,
} from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { YouTubeAuthorLookup } from "@/components/youtube-author-lookup"
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

function formatTime(value: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function ChannelMonitorsContent() {
  const { t } = useI18n()
  const [channelMonitors, setChannelMonitors] = useState<YouTubeChannelMonitor[]>([])
  const [channelUrl, setChannelUrl] = useState("")
  const [monitorInterval, setMonitorInterval] = useState("30")
  const [monitorLatestCount, setMonitorLatestCount] = useState("2")
  const [monitorError, setMonitorError] = useState("")
  const [creatingMonitor, setCreatingMonitor] = useState(false)
  const [deletingMonitorId, setDeletingMonitorId] = useState<string | null>(null)
  const [checkingMonitorId, setCheckingMonitorId] = useState<string | null>(null)
  const [togglingMonitorId, setTogglingMonitorId] = useState<string | null>(null)

  async function refreshChannelMonitors() {
    const { monitors } = await listYouTubeChannelMonitors()
    setChannelMonitors(monitors)
  }

  useEffect(() => {
    let cancelled = false

    const loadChannelMonitors = async () => {
      try {
        const { monitors } = await listYouTubeChannelMonitors()
        if (!cancelled) setChannelMonitors(monitors)
      } catch (err) {
        if (!cancelled) setMonitorError(err instanceof Error ? err.message : t.home.channelMonitorLoadError)
      }
    }

    loadChannelMonitors()
    const interval = window.setInterval(loadChannelMonitors, 10000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [t.home.channelMonitorLoadError])

  async function submitChannelMonitor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const url = channelUrl.trim()
    if (!url) return
    setCreatingMonitor(true)
    setMonitorError("")
    try {
      await createYouTubeChannelMonitor(
        url,
        Number.parseInt(monitorInterval, 10) || 30,
        Number.parseInt(monitorLatestCount, 10) || 2,
      )
      setChannelUrl("")
      await refreshChannelMonitors()
    } catch (err) {
      setMonitorError(err instanceof Error ? err.message : t.home.channelMonitorCreateError)
    } finally {
      setCreatingMonitor(false)
    }
  }

  async function toggleChannelMonitor(item: YouTubeChannelMonitor) {
    setTogglingMonitorId(item.id)
    setMonitorError("")
    try {
      await updateYouTubeChannelMonitor(item.id, { enabled: !item.enabled })
      await refreshChannelMonitors()
    } catch (err) {
      setMonitorError(err instanceof Error ? err.message : t.home.channelMonitorUpdateError)
    } finally {
      setTogglingMonitorId(null)
    }
  }

  async function checkChannelMonitor(item: YouTubeChannelMonitor) {
    setCheckingMonitorId(item.id)
    setMonitorError("")
    try {
      await checkYouTubeChannelMonitor(item.id)
      await refreshChannelMonitors()
    } catch (err) {
      setMonitorError(err instanceof Error ? err.message : t.home.channelMonitorCheckError)
    } finally {
      setCheckingMonitorId(null)
    }
  }

  async function removeChannelMonitor(monitorId: string) {
    setDeletingMonitorId(monitorId)
    setMonitorError("")
    try {
      await deleteYouTubeChannelMonitor(monitorId)
      await refreshChannelMonitors()
    } catch (err) {
      setMonitorError(err instanceof Error ? err.message : t.home.channelMonitorDeleteError)
    } finally {
      setDeletingMonitorId(null)
    }
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-6">
      <YouTubeAuthorLookup />
      <Card>
        <CardHeader>
          <CardTitle>{t.home.channelMonitors} ({channelMonitors.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submitChannelMonitor} className="grid gap-3">
            <div className="space-y-2">
              <Label htmlFor="channel-url">{t.home.channelMonitorLabel}</Label>
              <Input
                id="channel-url"
                value={channelUrl}
                onChange={(event) => setChannelUrl(event.target.value)}
                placeholder="https://www.youtube.com/@WolfeyVGC/videos"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
              <div className="space-y-2">
                <Label htmlFor="monitor-interval">{t.home.channelMonitorInterval}</Label>
                <Input
                  id="monitor-interval"
                  type="number"
                  min={5}
                  max={1440}
                  value={monitorInterval}
                  onChange={(event) => setMonitorInterval(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="monitor-latest-count">{t.home.channelMonitorLatestCount}</Label>
                <Input
                  id="monitor-latest-count"
                  type="number"
                  min={1}
                  max={20}
                  value={monitorLatestCount}
                  onChange={(event) => setMonitorLatestCount(event.target.value)}
                />
              </div>
              <Button type="submit" className="self-end" disabled={!channelUrl.trim() || creatingMonitor}>
                <Plus className="size-4" />
                {creatingMonitor ? t.home.channelMonitorCreating : t.home.channelMonitorCreate}
              </Button>
            </div>
          </form>

          {monitorError ? (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {monitorError}
            </div>
          ) : null}

          {channelMonitors.length === 0 ? (
            <div className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              {t.home.channelMonitorEmpty}
            </div>
          ) : (
            <ScrollArea className="mt-4 max-h-[56vh]">
              <ul className="flex flex-col divide-y divide-border/60">
                {channelMonitors.map((item) => (
                  <li key={item.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-zinc-900">
                        {item.title || item.channel_id}
                      </p>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge className={item.enabled ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-zinc-200 bg-zinc-50 text-zinc-600"}>
                          {item.enabled ? t.home.channelMonitorEnabled : t.home.channelMonitorPaused}
                        </Badge>
                        <span>{t.home.channelMonitorLatestShort}: {item.latest_count}</span>
                        <span>{t.home.channelMonitorIntervalShort}: {item.check_interval_minutes}m</span>
                        {item.last_checked_at ? <span>{formatTime(item.last_checked_at)}</span> : null}
                      </div>
                      {item.last_error ? (
                        <p className="mt-1 truncate text-xs text-red-600">{item.last_error}</p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={checkingMonitorId === item.id}
                        onClick={() => checkChannelMonitor(item)}
                      >
                        <RefreshCw className="size-4" />
                        {checkingMonitorId === item.id ? t.home.channelMonitorChecking : t.home.channelMonitorCheck}
                      </Button>
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        aria-label={item.enabled ? t.home.channelMonitorPause : t.home.channelMonitorResume}
                        disabled={togglingMonitorId === item.id}
                        onClick={() => toggleChannelMonitor(item)}
                      >
                        <Power className="size-4" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t.home.channelMonitorDelete}
                        disabled={deletingMonitorId === item.id}
                        onClick={() => removeChannelMonitor(item.id)}
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
    </div>
  )
}

export default function MonitorsPage() {
  return <ChannelMonitorsContent />
}
