"use client"

import { useMemo, useState } from "react"
import { Check, Copy, Search } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { BILIBILI_CATEGORY_GROUPS } from "@/lib/bilibili-categories"
import { useI18n } from "@/lib/i18n"

export function BilibiliCategoriesContent() {
  const { t } = useI18n()
  const [query, setQuery] = useState("")
  const [copiedTid, setCopiedTid] = useState<number | null>(null)

  const normalizedQuery = query.trim().toLowerCase()
  const groups = useMemo(() => {
    if (!normalizedQuery) return BILIBILI_CATEGORY_GROUPS
    return BILIBILI_CATEGORY_GROUPS
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => {
          const haystack = `${item.tid} ${item.name} ${item.group}`.toLowerCase()
          return haystack.includes(normalizedQuery)
        }),
      }))
      .filter((group) => group.items.length > 0)
  }, [normalizedQuery])

  async function copyTid(tid: number) {
    try {
      await navigator.clipboard.writeText(String(tid))
      setCopiedTid(tid)
      window.setTimeout(() => setCopiedTid((current) => (current === tid ? null : current)), 1200)
    } catch {
      setCopiedTid(null)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <Card>
          <CardHeader className="gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle>{t.categories.title}</CardTitle>
              <Badge className="border-sky-200 bg-sky-50 text-sky-800">
                {t.categories.countPrefix}{BILIBILI_CATEGORY_GROUPS.reduce((sum, group) => sum + group.items.length, 0)}
              </Badge>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t.categories.searchPlaceholder}
                className="h-10 pl-9"
              />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {groups.length ? (
              groups.map((group) => (
                <section key={group.group} className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold">{group.group}</h2>
                    <span className="text-xs text-muted-foreground">{group.items.length}</span>
                  </div>
                  <div className="overflow-hidden rounded-lg border border-border bg-background">
                    <div className="grid grid-cols-[5rem_1fr_auto] border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
                      <span>tid</span>
                      <span>{t.categories.category}</span>
                      <span className="text-right">{t.categories.action}</span>
                    </div>
                    {group.items.map((item) => (
                      <div
                        key={`${group.group}-${item.tid}`}
                        className="grid grid-cols-[5rem_1fr_auto] items-center gap-2 border-b border-border px-3 py-2 text-sm last:border-b-0"
                      >
                        <code className="font-mono text-xs text-[#00aeec]">{item.tid}</code>
                        <span className="min-w-0 break-words">{item.name}</span>
                        <Button
                          type="button"
                          variant="outline"
                          size="icon-sm"
                          onClick={() => copyTid(item.tid)}
                          aria-label={`${t.categories.copyTid} ${item.tid}`}
                        >
                          {copiedTid === item.tid ? <Check className="size-4" /> : <Copy className="size-4" />}
                        </Button>
                      </div>
                    ))}
                  </div>
                </section>
              ))
            ) : (
              <div className="rounded-lg border border-border bg-background px-4 py-8 text-center text-sm text-muted-foreground">
                {t.categories.empty}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t.categories.exampleTitle}</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-lg border bg-zinc-950 p-3 text-xs text-zinc-100">
{`{
  "WolfeyVGC": {
    "tags": "狼叔,宝可梦,VGC",
    "tid": 17
  },
  "SomeCreator": {
    "tags": "科技,科普",
    "tid": 201
  }
}`}
            </pre>
          </CardContent>
        </Card>
    </div>
  )
}

export default function BilibiliCategoriesPage() {
  return <BilibiliCategoriesContent />
}
