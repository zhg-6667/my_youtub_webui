"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ArrowLeft, ListTree, Settings } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useI18n } from "@/lib/i18n"

export function AppHeader({ backHref }: { backHref?: string }) {
  const { t } = useI18n()
  const pathname = usePathname()
  const showCategoriesLink = pathname !== "/bilibili-categories"

  return (
    <header className="flex flex-col gap-4 border-b border-[#00aeec]/25 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-3">
        {backHref ? (
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<Link href={backHref} aria-label={t.common.back} />}
          >
            <ArrowLeft className="size-4" />
          </Button>
        ) : null}
        <Link href="/" className="flex items-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/youdub-logo.svg"
            alt="YouDub"
            className="h-9 w-auto sm:h-11"
          />
        </Link>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {showCategoriesLink ? (
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link href="/bilibili-categories" />}
          >
            <ListTree className="size-4" />
            {t.common.bilibiliCategories}
          </Button>
        ) : null}
        <Button
          variant="outline"
          nativeButton={false}
          render={<Link href="/settings" />}
        >
          <Settings className="size-4" />
          {t.settings.button}
        </Button>
      </div>
    </header>
  )
}
