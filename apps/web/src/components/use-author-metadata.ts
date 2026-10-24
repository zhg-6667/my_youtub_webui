"use client"

import { Dispatch, SetStateAction, useCallback, useEffect, useRef } from "react"
import { fetchYouTubeVideoMetadata, YouTubeAuthorResult } from "@/lib/api"

export function useAuthorMetadata(setResults: Dispatch<SetStateAction<YouTubeAuthorResult[] | null>>) {
  const queue = useRef(new Map<string, string | null>())
  const attempted = useRef(new Set<string>())
  const generation = useRef(0)
  const processing = useRef<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const controller = useRef<AbortController | null>(null)

  const reset = useCallback(() => {
    generation.current += 1
    controller.current?.abort()
    controller.current = new AbortController()
    queue.current.clear()
    attempted.current.clear()
    processing.current = null
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }, [])

  const drain = useCallback(async () => {
    const current = generation.current
    if (processing.current === current) return
    processing.current = current
    try {
      while (queue.current.size && current === generation.current) {
        const items = [...queue.current].slice(0, 6).map(([video_id, channel_id]) => ({ video_id, channel_id }))
        items.forEach((item) => queue.current.delete(item.video_id))
        let updates
        try {
          updates = (await fetchYouTubeVideoMetadata(items, controller.current?.signal)).videos
        } catch {
          updates = items.map((item) => ({ ...item, published_at: null, published_date: null, metadata_checked: true }))
        }
        if (current !== generation.current) return
        const byId = new Map(updates.map((item) => [item.video_id, item]))
        setResults((channels) => channels?.map((channel) => ({
          ...channel,
          videos: channel.videos.map((video) => ({ ...video, ...byId.get(video.video_id) })),
          shorts: channel.shorts.map((video) => ({ ...video, ...byId.get(video.video_id) })),
        })) ?? null)
      }
    } finally {
      if (processing.current === current) processing.current = null
    }
  }, [setResults])

  const enqueue = useCallback((videoId: string, channelId: string | null) => {
    if (attempted.current.has(videoId)) return
    attempted.current.add(videoId)
    queue.current.set(videoId, channelId)
    if (!timer.current) {
      timer.current = setTimeout(() => {
        timer.current = null
        void drain()
      }, 50)
    }
  }, [drain])

  useEffect(() => () => {
    generation.current += 1
    controller.current?.abort()
    queue.current.clear()
    if (timer.current) clearTimeout(timer.current)
  }, [])

  return { enqueue, reset }
}
