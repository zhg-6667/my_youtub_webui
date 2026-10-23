"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { FolderTree, Home, Radar, Settings, X } from "lucide-react"
import { ReactNode, Suspense, useEffect, useMemo, useState } from "react"

import { BilibiliCategoriesContent } from "@/app/bilibili-categories/page"
import { HomeContent } from "@/app/page"
import { ChannelMonitorsContent } from "@/app/monitors/page"
import { TaskDetailContent } from "@/app/tasks/[id]/page"
import { SettingsDialog } from "@/components/settings-dialog"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

type AppTab = {
  key: string
  href: string
  title: string
  closeable: boolean
}

type CloseTabEventDetail = {
  key: string
}

function taskIdFromPath(pathname: string) {
  const match = pathname.match(/^\/tasks\/([^/]+)/)
  return match?.[1] ? decodeURIComponent(match[1]) : null
}

function shortTaskId(id: string) {
  return id.length > 8 ? id.slice(0, 8) : id
}

function taskIdFromTabKey(key: string) {
  return key.startsWith("task:") ? key.slice("task:".length) : null
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { t } = useI18n()

  const homeTab = useMemo<AppTab>(
    () => ({
      key: "home",
      href: "/",
      title: t.common.home,
      closeable: true,
    }),
    [t.common.home],
  )

  const currentTab = useMemo<AppTab>(() => {
    if (pathname === "/") return homeTab
    if (pathname === "/bilibili-categories") {
      return {
        key: "categories",
        href: "/bilibili-categories",
        title: t.common.bilibiliCategories,
        closeable: true,
      }
    }
    if (pathname === "/monitors") {
      return {
        key: "monitors",
        href: "/monitors",
        title: t.common.monitors,
        closeable: true,
      }
    }
    if (pathname === "/settings") {
      return {
        key: "settings",
        href: "/settings",
        title: t.settings.button,
        closeable: true,
      }
    }
    const taskId = taskIdFromPath(pathname)
    if (taskId) {
      return {
        key: `task:${taskId}`,
        href: `/tasks/${encodeURIComponent(taskId)}`,
        title: `${t.common.taskDetail} ${shortTaskId(taskId)}`,
        closeable: true,
      }
    }
    return {
      key: pathname,
      href: pathname,
      title: pathname,
      closeable: true,
    }
  }, [homeTab, pathname, t.common.bilibiliCategories, t.common.monitors, t.common.taskDetail, t.settings.button])

  const [tabs, setTabs] = useState<AppTab[]>([currentTab])
  const [refreshKeys, setRefreshKeys] = useState<Record<string, number>>({})

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setTabs((current) => {
        const normalized = current.map((tab) => {
          if (tab.key === "home") return { ...tab, title: t.common.home }
          if (tab.key === "categories") return { ...tab, title: t.common.bilibiliCategories }
          if (tab.key === "monitors") return { ...tab, title: t.common.monitors }
          if (tab.key === "settings") return { ...tab, title: t.settings.button }
          if (tab.key.startsWith("task:")) {
            const id = tab.key.slice("task:".length)
            return { ...tab, title: `${t.common.taskDetail} ${shortTaskId(id)}` }
          }
          return tab
        })
        const existing = normalized.find((tab) => tab.key === currentTab.key)
        if (existing) {
          return normalized.map((tab) => (tab.key === currentTab.key ? { ...tab, ...currentTab } : tab))
        }
        return [...normalized, currentTab]
      })
    }, 0)
    return () => window.clearTimeout(timeout)
  }, [currentTab, t.common.bilibiliCategories, t.common.home, t.common.monitors, t.common.taskDetail, t.settings.button])

  function openUniqueTab(key: string, href: string) {
    if (key === currentTab.key || pathname === href) return
    router.push(href)
  }

  function activateTab(tab: AppTab) {
    if (tab.key === currentTab.key) return
    router.push(tab.href)
  }

  function refreshTab(tab: AppTab) {
    if (tab.key !== currentTab.key) {
      router.push(tab.href)
    }
    setRefreshKeys((current) => ({
      ...current,
      [tab.key]: (current[tab.key] || 0) + 1,
    }))
  }

  function closeTab(key: string) {
    const closingIndex = tabs.findIndex((tab) => tab.key === key)
    const nextTabs = tabs.filter((tab) => tab.key !== key)
    setRefreshKeys((keys) => {
      const next = { ...keys }
      delete next[key]
      return next
    })
    if (!nextTabs.length) {
      setTabs([homeTab])
      if (currentTab.key !== homeTab.key) router.push(homeTab.href)
      return
    }
    setTabs(nextTabs)
    if (currentTab.key === key) {
      const nextActive = nextTabs[Math.max(0, closingIndex - 1)] || nextTabs[0]
      router.push(nextActive.href)
    }
  }

  useEffect(() => {
    const handleCloseTab = (event: Event) => {
      const customEvent = event as CustomEvent<CloseTabEventDetail>
      if (!customEvent.detail?.key) return
      event.preventDefault()
      closeTab(customEvent.detail.key)
    }

    window.addEventListener("youdub:close-tab", handleCloseTab)
    return () => window.removeEventListener("youdub:close-tab", handleCloseTab)
  })

  function closeAllTabs() {
    setTabs([homeTab])
    setRefreshKeys({})
    if (currentTab.key !== homeTab.key) router.push(homeTab.href)
  }

  const navItems = [
    { href: "/", key: "home", label: t.common.home, icon: Home },
    { href: "/monitors", key: "monitors", label: t.common.monitors, icon: Radar },
    { href: "/bilibili-categories", key: "categories", label: t.common.bilibiliCategories, icon: FolderTree },
    { href: "/settings", key: "settings", label: t.settings.button, icon: Settings },
  ]

  function renderTabContent(tab: AppTab) {
    const refreshKey = refreshKeys[tab.key] || 0
    if (tab.key === "home") {
      return (
        <Suspense key={refreshKey} fallback={null}>
          <HomeContent />
        </Suspense>
      )
    }
    if (tab.key === "categories") {
      return <BilibiliCategoriesContent key={refreshKey} />
    }
    if (tab.key === "monitors") {
      return <ChannelMonitorsContent key={refreshKey} />
    }
    if (tab.key === "settings") {
      return <SettingsDialog key={refreshKey} asPanel />
    }
    const taskId = taskIdFromTabKey(tab.key)
    if (taskId) {
      return <TaskDetailContent key={refreshKey} id={taskId} />
    }
    return <div key={refreshKey}>{children}</div>
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-[linear-gradient(135deg,#fff5f5_0%,#f2fbff_48%,#fff4fa_100%)] text-foreground">
      <aside className="hidden h-dvh w-56 shrink-0 overflow-y-auto border-r border-[#00aeec]/25 bg-white/80 px-3 py-4 shadow-sm backdrop-blur md:flex md:flex-col">
        <Link href="/" className="mb-5 flex items-center px-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/youdub-logo.svg" alt="YouDub" className="h-10 w-auto" />
        </Link>
        <nav className="grid gap-1">
          {navItems.map((item) => {
            const Icon = item.icon
            const active = currentTab.key === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => openUniqueTab(item.key, item.href)}
                onDoubleClick={() =>
                  refreshTab({
                    key: item.key,
                    href: item.href,
                    title: item.label,
                    closeable: true,
                  })
                }
                className={cn(
                  "flex h-10 items-center gap-2 rounded-lg px-3 text-left text-sm font-medium transition-colors",
                  active
                    ? "bg-[#e5f8ff] text-[#006f99]"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                <span className="truncate">{item.label}</span>
              </button>
            )
          })}
        </nav>
      </aside>

      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        <div className="shrink-0 border-b border-[#00aeec]/25 bg-white/75 backdrop-blur">
          <div className="flex items-center gap-2 overflow-x-auto px-3 py-2">
            <Link href="/" className="mr-1 shrink-0 md:hidden">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/youdub-icon.svg" alt="YouDub" className="size-8" />
            </Link>
            {tabs.map((tab) => {
              const active = tab.key === currentTab.key
              return (
                <div
                  key={tab.key}
                  className={cn(
                    "flex h-8 min-w-0 shrink-0 items-center rounded-lg border text-sm",
                    active ? "border-[#00aeec]/35 bg-white text-foreground shadow-sm" : "border-transparent bg-muted/60 text-muted-foreground",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => activateTab(tab)}
                    onDoubleClick={() => refreshTab(tab)}
                    className="min-w-0 max-w-48 truncate px-3"
                  >
                    {tab.title}
                  </button>
                  {tab.closeable ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="mr-1"
                      aria-label={`${t.common.close} ${tab.title}`}
                      onClick={() => closeTab(tab.key)}
                    >
                      <X className="size-3" />
                    </Button>
                  ) : null}
                </div>
              )
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto shrink-0"
              onClick={closeAllTabs}
            >
              <X className="size-3" />
              {t.common.closeAll}
            </Button>
          </div>
        </div>

        <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          {tabs.map((tab) => (
            <section
              key={tab.key}
              className={cn(
                "absolute inset-0 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8",
                tab.key === currentTab.key ? "block" : "hidden",
              )}
            >
              {renderTabContent(tab)}
            </section>
          ))}
        </main>
      </div>
    </div>
  )
}
