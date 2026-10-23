"use client"

import { FormEvent, useEffect, useRef, useState } from "react"
import { Eye, EyeOff, Plus, RefreshCw, Settings, Trash2 } from "lucide-react"

import {
  BilibiliSeason,
  getBilibiliSeasons,
  getBilibiliSettings,
  getCookieInfo,
  getMailSettings,
  getOpenAIModels,
  getOpenAISettings,
  getYtdlpSettings,
  resolveBilibiliTopic,
  saveBilibiliSettings,
  saveCookie,
  saveMailSettings,
  saveOpenAISettings,
  saveYtdlpSettings,
} from "@/lib/api"
import { LANGUAGE_OPTIONS, useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

type SettingsForm = {
  cookie: string
  baseUrl: string
  apiKey: string
  model: string
  translateConcurrency: string
  proxyPort: string
  bilibiliAuthorConfigJson: string
  bilibiliPokemonTopicConfigJson: string
  mailEnabled: boolean
  mailSmtpHost: string
  mailSmtpPort: string
  mailSmtpUsername: string
  mailSmtpPassword: string
  mailFromAddress: string
  mailToAddresses: string
  mailSmtpSecurity: "none" | "tls" | "ssl"
  mailNotifyOnSuccess: boolean
  mailNotifyOnFailure: boolean
}

const SAVED_API_KEY_MASK = "********"
const SAVED_SMTP_PASSWORD_MASK = "********"
const SAVED_COOKIE_SENTINEL = "__YOUDUB_SAVED_COOKIE__"
const AUTHOR_ROW_COLUMNS = "minmax(130px, 1fr) minmax(170px, 1.15fr) 88px 412px 200px 40px"

type MessageKey = "keySaved" | "saved"

type BilibiliAuthorConfigRow = {
  id: string
  author: string
  tags: string
  tid: string
  topicName: string
  topicId: string
  topicMissionId: string
  seasonId: string
  sectionId: string
}

type BilibiliAuthorProfile = {
  tags?: string
  tid?: number
  topic_name?: string
  topic_id?: number
  topic_mission_id?: number
  season_id?: number
  section_id?: number
}

type BilibiliPokemonTopicRow = {
  id: string
  name: string
  topicId: string
}

const defaultSettings: SettingsForm = {
  cookie: "",
  baseUrl: "https://api.openai.com/v1",
  apiKey: "",
  model: "gpt-4o-mini",
  translateConcurrency: "50",
  proxyPort: "",
  bilibiliAuthorConfigJson: "",
  bilibiliPokemonTopicConfigJson: "",
  mailEnabled: false,
  mailSmtpHost: "",
  mailSmtpPort: "587",
  mailSmtpUsername: "",
  mailSmtpPassword: "",
  mailFromAddress: "",
  mailToAddresses: "",
  mailSmtpSecurity: "tls",
  mailNotifyOnSuccess: true,
  mailNotifyOnFailure: true,
}

function uniqueModels(models: string[]) {
  return Array.from(new Set(models.map((model) => model.trim()).filter(Boolean)))
}

function newAuthorConfigRow(): BilibiliAuthorConfigRow {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    author: "",
    tags: "",
    tid: "",
    topicName: "",
    topicId: "",
    topicMissionId: "",
    seasonId: "",
    sectionId: "",
  }
}

function newPokemonTopicRow(): BilibiliPokemonTopicRow {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: "",
    topicId: "",
  }
}

function parseAuthorConfigRows(value: string): BilibiliAuthorConfigRow[] {
  const raw = value.trim()
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return []
    return Object.entries(parsed).map(([author, profile]) => {
      const item = profile && typeof profile === "object" && !Array.isArray(profile) ? profile as Record<string, unknown> : {}
      return {
        id: `${author}-${Math.random().toString(16).slice(2)}`,
        author,
        tags: typeof item.tags === "string" ? item.tags : "",
        tid: typeof item.tid === "number" && Number.isInteger(item.tid) ? String(item.tid) : "",
        topicName: typeof item.topic_name === "string" ? item.topic_name : "",
        topicId: typeof item.topic_id === "number" && Number.isInteger(item.topic_id) ? String(item.topic_id) : "",
        topicMissionId: typeof item.topic_mission_id === "number" && Number.isInteger(item.topic_mission_id) ? String(item.topic_mission_id) : "",
        seasonId: typeof item.season_id === "number" && Number.isInteger(item.season_id) ? String(item.season_id) : "",
        sectionId: typeof item.section_id === "number" && Number.isInteger(item.section_id) ? String(item.section_id) : "",
      }
    })
  } catch {
    return []
  }
}

function stringifyAuthorConfigRows(rows: BilibiliAuthorConfigRow[]) {
  const normalized: Record<string, BilibiliAuthorProfile> = {}
  for (const row of rows) {
    const author = row.author.trim()
    if (!author) continue
    const item: BilibiliAuthorProfile = {}
    const tags = row.tags.trim()
    const tid = row.tid.trim()
    if (tags) item.tags = tags
    if (/^[1-9]\d*$/.test(tid)) item.tid = Number(tid)
    if (row.topicName.trim()) item.topic_name = row.topicName.trim()
    if (/^[1-9]\d*$/.test(row.topicId)) item.topic_id = Number(row.topicId)
    if (/^[1-9]\d*$/.test(row.topicMissionId)) item.topic_mission_id = Number(row.topicMissionId)
    if (/^[1-9]\d*$/.test(row.seasonId) && /^[1-9]\d*$/.test(row.sectionId)) {
      item.season_id = Number(row.seasonId)
      item.section_id = Number(row.sectionId)
    }
    normalized[author] = item
  }
  return Object.keys(normalized).length ? JSON.stringify(normalized, null, 2) : ""
}

function authorSeasonLabel(row: BilibiliAuthorConfigRow, seasons: BilibiliSeason[], noneLabel: string) {
  if (!row.seasonId || !row.sectionId) return noneLabel
  const season = seasons.find((item) => String(item.season_id) === row.seasonId && String(item.section_id) === row.sectionId)
  return season ? `${season.season_title} / ${season.section_title}` : `${row.seasonId} / ${row.sectionId}`
}

function authorTopicLabel(row: BilibiliAuthorConfigRow, topics: BilibiliPokemonTopicRow[], manualLabel: string) {
  return topics.find((item) => item.name === row.topicName && item.topicId === row.topicId)?.name || row.topicName || manualLabel
}

function parsePokemonTopicRows(value: string): BilibiliPokemonTopicRow[] {
  const raw = value.trim()
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return []
    return Object.entries(parsed).map(([name, topicId]) => ({
      id: `${name}-${Math.random().toString(16).slice(2)}`,
      name,
      topicId: typeof topicId === "number" && Number.isInteger(topicId) ? String(topicId) : "",
    }))
  } catch {
    return []
  }
}

function stringifyPokemonTopicRows(rows: BilibiliPokemonTopicRow[]) {
  const normalized: Record<string, number> = {}
  for (const row of rows) {
    const name = row.name.trim()
    const topicId = row.topicId.trim()
    if (!name || !/^[1-9]\d*$/.test(topicId)) continue
    normalized[name] = Number(topicId)
  }
  return Object.keys(normalized).length ? JSON.stringify(normalized, null, 2) : ""
}

export function SettingsDialog({ asPanel = false }: { asPanel?: boolean } = {}) {
  const { language, loadedModelsText, setLanguage, t } = useI18n()
  const [open, setOpen] = useState(asPanel)
  const [settings, setSettings] = useState(defaultSettings)
  const [message, setMessage] = useState("")
  const [messageKey, setMessageKey] = useState<MessageKey | null>(null)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [modelsLoading, setModelsLoading] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [showSmtpPassword, setShowSmtpPassword] = useState(false)
  const [cookieDirty, setCookieDirty] = useState(false)
  const [apiKeyDirty, setApiKeyDirty] = useState(false)
  const [smtpPasswordDirty, setSmtpPasswordDirty] = useState(false)
  const [bilibiliAuthorRows, setBilibiliAuthorRows] = useState<BilibiliAuthorConfigRow[]>([])
  const bilibiliAuthorRowsRef = useRef<BilibiliAuthorConfigRow[]>([])
  const [bilibiliPokemonTopicRows, setBilibiliPokemonTopicRows] = useState<BilibiliPokemonTopicRow[]>([])
  const [bilibiliSeasons, setBilibiliSeasons] = useState<BilibiliSeason[]>([])
  const [bilibiliSeasonsLoading, setBilibiliSeasonsLoading] = useState(false)
  const [bilibiliSeasonsError, setBilibiliSeasonsError] = useState("")
  const [resolvingAuthorTopic, setResolvingAuthorTopic] = useState<string | null>(null)
  const [authorTopicErrors, setAuthorTopicErrors] = useState<Record<string, string>>({})

  const cookieValue =
    settings.cookie === SAVED_COOKIE_SENTINEL ? t.settings.savedCookie : settings.cookie
  const visibleMessage =
    messageKey === "keySaved" ? t.settings.keySaved : messageKey === "saved" ? t.settings.saved : message

  useEffect(() => {
    if (!open && !asPanel) return
    Promise.all([getCookieInfo(), getOpenAISettings(), getYtdlpSettings(), getBilibiliSettings(), getMailSettings()])
      .then(([cookie, openai, ytdlp, bilibili, mail]) => {
        setSettings({
          cookie: cookie.exists ? SAVED_COOKIE_SENTINEL : "",
          baseUrl: openai.base_url,
          apiKey: openai.has_api_key ? openai.api_key || SAVED_API_KEY_MASK : "",
          model: openai.model,
          translateConcurrency: openai.translate_concurrency || "50",
          proxyPort: ytdlp.proxy_port,
          bilibiliAuthorConfigJson: bilibili.author_config_json,
          bilibiliPokemonTopicConfigJson: bilibili.pokemon_topic_config_json,
          mailEnabled: mail.enabled,
          mailSmtpHost: mail.smtp_host,
          mailSmtpPort: mail.smtp_port || "587",
          mailSmtpUsername: mail.smtp_username,
          mailSmtpPassword: mail.has_smtp_password ? mail.smtp_password || SAVED_SMTP_PASSWORD_MASK : "",
          mailFromAddress: mail.from_address,
          mailToAddresses: mail.to_addresses,
          mailSmtpSecurity: mail.smtp_security,
          mailNotifyOnSuccess: mail.notify_on_success,
          mailNotifyOnFailure: mail.notify_on_failure,
        })
        const authorRows = parseAuthorConfigRows(bilibili.author_config_json)
        bilibiliAuthorRowsRef.current = authorRows
        setBilibiliAuthorRows(authorRows)
        setAuthorTopicErrors({})
        setBilibiliPokemonTopicRows(parsePokemonTopicRows(bilibili.pokemon_topic_config_json))
        setModelOptions(uniqueModels([openai.model]))
        setModelsLoaded(false)
        setShowApiKey(false)
        setShowSmtpPassword(false)
        setCookieDirty(false)
        setApiKeyDirty(false)
        setSmtpPasswordDirty(false)
        setMessage("")
        setMessageKey(openai.has_api_key ? "keySaved" : null)
      })
      .catch((err) => {
        setMessageKey(null)
        setMessage(err.message)
      })
  }, [asPanel, open])

  useEffect(() => {
    if (!open && !asPanel) return
    let cancelled = false
    setBilibiliSeasonsLoading(true)
    setBilibiliSeasonsError("")
    getBilibiliSeasons()
      .then((result) => {
        if (!cancelled) setBilibiliSeasons(result.seasons)
      })
      .catch((err) => {
        if (!cancelled) setBilibiliSeasonsError(err instanceof Error ? err.message : t.task.bilibiliSeasonLoadError)
      })
      .finally(() => {
        if (!cancelled) setBilibiliSeasonsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [asPanel, open, t.task.bilibiliSeasonLoadError])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage("")
    setMessageKey(null)
    try {
      const authorConfigJson = stringifyAuthorConfigRows(bilibiliAuthorRowsRef.current)
      const cookie = cookieDirty ? await saveCookie(settings.cookie) : null
      const clearApiKey = apiKeyDirty && !settings.apiKey.trim()
      const openai = await saveOpenAISettings({
        base_url: settings.baseUrl,
        api_key: apiKeyDirty ? settings.apiKey : "",
        clear_api_key: clearApiKey,
        model: settings.model,
        translate_concurrency: settings.translateConcurrency,
      })
      const ytdlp = await saveYtdlpSettings({ proxy_port: settings.proxyPort })
      const bilibili = await saveBilibiliSettings({
        author_config_json: authorConfigJson,
        author_tags_json: "",
        author_tids_json: "",
        pokemon_topic_config_json: settings.bilibiliPokemonTopicConfigJson,
      })
      const clearSmtpPassword = smtpPasswordDirty && !settings.mailSmtpPassword.trim()
      const mail = await saveMailSettings({
        enabled: settings.mailEnabled,
        smtp_host: settings.mailSmtpHost,
        smtp_port: settings.mailSmtpPort,
        smtp_username: settings.mailSmtpUsername,
        smtp_password: smtpPasswordDirty ? settings.mailSmtpPassword : "",
        clear_smtp_password: clearSmtpPassword,
        from_address: settings.mailFromAddress,
        to_addresses: settings.mailToAddresses,
        smtp_security: settings.mailSmtpSecurity,
        notify_on_success: settings.mailNotifyOnSuccess,
        notify_on_failure: settings.mailNotifyOnFailure,
      })
      setMessageKey("saved")
      setSettings((current) => ({
        ...current,
        apiKey: openai.has_api_key ? openai.api_key || SAVED_API_KEY_MASK : "",
        cookie: cookieDirty ? (cookie?.exists ? SAVED_COOKIE_SENTINEL : "") : current.cookie,
        translateConcurrency: openai.translate_concurrency || current.translateConcurrency,
        proxyPort: ytdlp.proxy_port,
        bilibiliAuthorConfigJson: bilibili.author_config_json,
        bilibiliPokemonTopicConfigJson: bilibili.pokemon_topic_config_json,
        mailSmtpPassword: mail.has_smtp_password ? mail.smtp_password || SAVED_SMTP_PASSWORD_MASK : "",
        mailSmtpPort: mail.smtp_port,
        mailSmtpSecurity: mail.smtp_security,
      }))
      const authorRows = parseAuthorConfigRows(bilibili.author_config_json)
      bilibiliAuthorRowsRef.current = authorRows
      setBilibiliAuthorRows(authorRows)
      setAuthorTopicErrors({})
      setBilibiliPokemonTopicRows(parsePokemonTopicRows(bilibili.pokemon_topic_config_json))
      setCookieDirty(false)
      setApiKeyDirty(false)
      setSmtpPasswordDirty(false)
    } catch (err) {
      setMessageKey(null)
      setMessage(err instanceof Error ? err.message : t.settings.saveError)
    }
  }

  async function fetchModels() {
    setMessage("")
    setMessageKey(null)
    setModelsLoading(true)
    try {
      const response = await getOpenAIModels({
        base_url: settings.baseUrl,
        api_key: apiKeyDirty ? settings.apiKey : "",
      })
      const models = uniqueModels([settings.model, ...response.models])
      setModelOptions(models)
      setModelsLoaded(true)
      setSettings((current) => ({ ...current, model: current.model || models[0] || "" }))
      setMessage(models.length ? loadedModelsText(models.length) : t.settings.noModels)
    } catch (err) {
      setMessageKey(null)
      setMessage(err instanceof Error ? err.message : t.settings.loadModelsError)
    } finally {
      setModelsLoading(false)
    }
  }

  const form = (
    <form
      onSubmit={submit}
      className={asPanel ? "flex min-h-0 min-w-0 flex-col" : "flex max-h-[calc(100dvh-4rem)] min-h-0 min-w-0 flex-col"}
    >
      {asPanel ? (
        <div className="shrink-0">
          <h1 className="font-heading text-base leading-none font-medium">{t.settings.title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{t.settings.description}</p>
        </div>
      ) : (
        <DialogHeader className="shrink-0 pr-8">
            <DialogTitle>{t.settings.title}</DialogTitle>
            <DialogDescription>{t.settings.description}</DialogDescription>
        </DialogHeader>
      )}
          <div className="mt-4 min-h-0 min-w-0 overflow-y-auto pr-1">
            <div className="grid gap-4 pb-4">
              <div className="grid gap-2">
                <Label htmlFor="uiLanguage">{t.settings.language}</Label>
                <Select
                  value={language}
                  onValueChange={(value) => {
                    if (value === "en" || value === "zh") setLanguage(value)
                  }}
                >
                  <SelectTrigger id="uiLanguage">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LANGUAGE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="cookie">{t.settings.cookie}</Label>
                <Textarea
                  id="cookie"
                  value={cookieValue}
                  onFocus={(event) => {
                    if (!cookieDirty && settings.cookie === SAVED_COOKIE_SENTINEL) {
                      event.currentTarget.select()
                    }
                  }}
                  onChange={(event) => {
                    setCookieDirty(true)
                    setSettings((current) => ({
                      ...current,
                      cookie:
                        current.cookie === SAVED_COOKIE_SENTINEL
                          ? event.target.value.replace(t.settings.savedCookie, "")
                          : event.target.value,
                    }))
                  }}
                  placeholder={t.settings.cookiePlaceholder}
                  className="min-h-44 max-h-[42dvh] overflow-auto font-mono text-xs leading-relaxed"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="proxyPort">{t.settings.proxyPort}</Label>
                <Input
                  id="proxyPort"
                  inputMode="numeric"
                  value={settings.proxyPort}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, proxyPort: event.target.value }))
                  }
                  placeholder="7890"
                />
              </div>
              <div className="grid gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Label>{t.settings.bilibiliAuthorConfig}</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateBilibiliAuthorRows([...bilibiliAuthorRows, newAuthorConfigRow()])}
                  >
                    <Plus className="size-3" />
                    {t.settings.bilibiliAuthorAdd}
                  </Button>
                </div>
                <div className="overflow-x-auto rounded-lg border border-border bg-background">
                  <div className="min-w-[1120px]">
                  <div className="grid items-center gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground" style={{ gridTemplateColumns: AUTHOR_ROW_COLUMNS }}>
                    <span>{t.settings.bilibiliAuthorName}</span>
                    <span>{t.settings.bilibiliAuthorTagsColumn}</span>
                    <span>{t.settings.bilibiliAuthorTid}</span>
                    <div className="grid grid-cols-[150px_254px] gap-2">
                      <span>{t.settings.bilibiliAuthorDefaultTopic}</span>
                      <span>{t.settings.bilibiliAuthorTopicSearch}</span>
                    </div>
                    <span>{t.settings.bilibiliAuthorDefaultSeason}</span>
                    <span />
                  </div>
                  {bilibiliAuthorRows.length ? (
                    <div className="divide-y divide-border">
                      {bilibiliAuthorRows.map((row, index) => (
                        <div
                          key={row.id}
                          className="grid items-center gap-2 px-3 py-3"
                          style={{ gridTemplateColumns: AUTHOR_ROW_COLUMNS }}
                        >
                          <div className="contents">
                            <div className="grid gap-1">
                              <Label htmlFor={`bilibili-author-${row.id}`} className="sr-only">
                                {t.settings.bilibiliAuthorName}
                              </Label>
                              <Input
                                id={`bilibili-author-${row.id}`}
                                value={row.author}
                                onChange={(event) => updateBilibiliAuthorRow(row.id, "author", event.target.value)}
                                placeholder={index === 0 ? "WolfeyVGC" : ""}
                              />
                            </div>
                            <div className="grid gap-1">
                              <Label htmlFor={`bilibili-tags-${row.id}`} className="sr-only">
                                {t.settings.bilibiliAuthorTagsColumn}
                              </Label>
                              <Input
                                id={`bilibili-tags-${row.id}`}
                                value={row.tags}
                                onChange={(event) => updateBilibiliAuthorRow(row.id, "tags", event.target.value)}
                                placeholder={index === 0 ? "狼叔,宝可梦,VGC" : ""}
                              />
                            </div>
                            <div className="grid gap-1">
                              <Label htmlFor={`bilibili-tid-${row.id}`} className="sr-only">
                                {t.settings.bilibiliAuthorTid}
                              </Label>
                              <Input
                                id={`bilibili-tid-${row.id}`}
                                inputMode="numeric"
                                value={row.tid}
                                onChange={(event) => updateBilibiliAuthorRow(row.id, "tid", event.target.value)}
                                placeholder="17"
                              />
                            </div>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="col-start-6 row-start-1 justify-self-end"
                              aria-label={t.settings.bilibiliAuthorRemove}
                              onClick={() =>
                                updateBilibiliAuthorRows(bilibiliAuthorRows.filter((item) => item.id !== row.id))
                              }
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                          <div className="contents">
                            <div className="col-start-4 row-start-1 grid min-w-0 grid-cols-[150px_254px] items-center gap-2">
                              <Label htmlFor={`bilibili-author-topic-${row.id}`} className="sr-only">
                                {t.settings.bilibiliAuthorDefaultTopic}
                              </Label>
                              <Select
                                value={bilibiliPokemonTopicRows.some((option) => option.name === row.topicName && option.topicId === row.topicId)
                                  ? `${row.topicName}:${row.topicId}` : "manual"}
                                onValueChange={(value) => {
                                  if (value === "manual") {
                                    updateBilibiliAuthorTopic(row.id, "", "")
                                    return
                                  }
                                  const option = bilibiliPokemonTopicRows.find((item) => `${item.name}:${item.topicId}` === value)
                                  if (option) updateBilibiliAuthorTopic(row.id, option.name, option.topicId)
                                }}
                              >
                                <SelectTrigger className="h-9 w-full" aria-label={t.settings.bilibiliAuthorDefaultTopic}>
                                  <span className="min-w-0 truncate text-left">
                                    {authorTopicLabel(row, bilibiliPokemonTopicRows, t.task.bilibiliTopicConfigNone)}
                                  </span>
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="manual">{t.task.bilibiliTopicConfigNone}</SelectItem>
                                  {bilibiliPokemonTopicRows.filter((option) => option.name.trim() && /^[1-9]\d*$/.test(option.topicId)).map((option) => (
                                    <SelectItem key={option.id} value={`${option.name}:${option.topicId}`}>
                                      {option.name}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <div className="flex min-w-0 gap-2">
                                <Input
                                  id={`bilibili-author-topic-${row.id}`}
                                  className="min-w-0 flex-1"
                                  value={row.topicName}
                                  onChange={(event) => updateBilibiliAuthorRow(row.id, "topicName", event.target.value)}
                                  placeholder={t.task.bilibiliTopicPlaceholder}
                                  title={row.topicId ? `${t.task.bilibiliTopicResolved}: ${row.topicId}` : undefined}
                                />
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  onClick={() => resolveAuthorTopic(row.id)}
                                  disabled={!row.topicName.trim() || resolvingAuthorTopic !== null}
                                  className="shrink-0"
                                >
                                  <RefreshCw className={`size-3 ${resolvingAuthorTopic === row.id ? "animate-spin" : ""}`} />
                                  {t.task.bilibiliTopicResolve}
                                </Button>
                              </div>
                            </div>
                            <div className="col-start-5 row-start-1 min-w-0">
                              <Label htmlFor={`bilibili-author-season-${row.id}`} className="sr-only">
                                {t.settings.bilibiliAuthorDefaultSeason}
                              </Label>
                              <Select
                                value={row.seasonId && row.sectionId ? `${row.seasonId}:${row.sectionId}` : "none"}
                                onValueChange={(value) => {
                                  const [seasonId, sectionId] = value === "none" ? ["", ""] : (value || "").split(":")
                                  updateBilibiliAuthorRows(bilibiliAuthorRowsRef.current.map((item) => (
                                    item.id === row.id ? { ...item, seasonId, sectionId } : item
                                  )))
                                }}
                                disabled={bilibiliSeasonsLoading}
                              >
                                <SelectTrigger id={`bilibili-author-season-${row.id}`} className="h-9 w-full">
                                  <span className="min-w-0 truncate text-left">
                                    {authorSeasonLabel(row, bilibiliSeasons, t.task.bilibiliSeasonNone)}
                                  </span>
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="none">{t.task.bilibiliSeasonNone}</SelectItem>
                                  {bilibiliSeasons.map((season) => (
                                    <SelectItem key={`${season.season_id}:${season.section_id}`} value={`${season.season_id}:${season.section_id}`}>
                                      {season.season_title} / {season.section_title}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                      {t.settings.bilibiliAuthorEmpty}
                    </div>
                  )}
                  </div>
                </div>
                {settings.bilibiliAuthorConfigJson ? (
                  <pre className="max-h-36 overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
                    {settings.bilibiliAuthorConfigJson}
                  </pre>
                ) : null}
                {Object.entries(authorTopicErrors).filter(([, error]) => error).map(([rowId, error]) => (
                  <p key={rowId} className="text-xs text-red-600">
                    {bilibiliAuthorRows.find((row) => row.id === rowId)?.author || t.settings.bilibiliAuthorName}: {error}
                  </p>
                ))}
                {bilibiliSeasonsError ? <p className="text-xs text-red-600">{bilibiliSeasonsError}</p> : null}
                <p className="text-xs text-muted-foreground">{t.settings.bilibiliAuthorConfigHelp}</p>
              </div>
              <div className="grid gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Label>{t.settings.bilibiliPokemonTopicConfig}</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateBilibiliPokemonTopicRows([...bilibiliPokemonTopicRows, newPokemonTopicRow()])}
                  >
                    <Plus className="size-3" />
                    {t.settings.bilibiliPokemonTopicAdd}
                  </Button>
                </div>
                <div className="overflow-hidden rounded-lg border border-border bg-background">
                  <div className="hidden grid-cols-[minmax(0,1fr)_9rem_2.5rem] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground md:grid">
                    <span>{t.settings.bilibiliPokemonTopicName}</span>
                    <span>{t.settings.bilibiliPokemonTopicId}</span>
                    <span />
                  </div>
                  {bilibiliPokemonTopicRows.length ? (
                    <div className="divide-y divide-border">
                      {bilibiliPokemonTopicRows.map((row, index) => (
                        <div
                          key={row.id}
                          className="grid gap-2 px-3 py-3 md:grid-cols-[minmax(0,1fr)_9rem_2.5rem] md:items-end"
                        >
                          <div className="grid gap-1">
                            <Label htmlFor={`bilibili-pokemon-topic-${row.id}`} className="text-xs md:hidden">
                              {t.settings.bilibiliPokemonTopicName}
                            </Label>
                            <Input
                              id={`bilibili-pokemon-topic-${row.id}`}
                              value={row.name}
                              onChange={(event) => updateBilibiliPokemonTopicRow(row.id, "name", event.target.value)}
                              placeholder={index === 0 ? "宝可梦集结" : ""}
                            />
                          </div>
                          <div className="grid gap-1">
                            <Label htmlFor={`bilibili-pokemon-topic-id-${row.id}`} className="text-xs md:hidden">
                              {t.settings.bilibiliPokemonTopicId}
                            </Label>
                            <Input
                              id={`bilibili-pokemon-topic-id-${row.id}`}
                              inputMode="numeric"
                              value={row.topicId}
                              onChange={(event) => updateBilibiliPokemonTopicRow(row.id, "topicId", event.target.value)}
                              placeholder="1245603"
                            />
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="justify-self-end"
                            aria-label={t.settings.bilibiliPokemonTopicRemove}
                            onClick={() =>
                              updateBilibiliPokemonTopicRows(bilibiliPokemonTopicRows.filter((item) => item.id !== row.id))
                            }
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                      {t.settings.bilibiliPokemonTopicEmpty}
                    </div>
                  )}
                </div>
                {settings.bilibiliPokemonTopicConfigJson ? (
                  <pre className="max-h-36 overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
                    {settings.bilibiliPokemonTopicConfigJson}
                  </pre>
                ) : null}
                <p className="text-xs text-muted-foreground">{t.settings.bilibiliPokemonTopicConfigHelp}</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="baseUrl">{t.settings.baseUrl}</Label>
                <Input
                  id="baseUrl"
                  value={settings.baseUrl}
                  onChange={(event) =>
                    setSettings((current) => ({ ...current, baseUrl: event.target.value }))
                  }
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="apiKey">{t.settings.apiKey}</Label>
                <div className="relative">
                  <Input
                    id="apiKey"
                    type={showApiKey ? "text" : "password"}
                    value={settings.apiKey}
                    onFocus={(event) => {
                      if (!apiKeyDirty && settings.apiKey === SAVED_API_KEY_MASK) {
                        event.currentTarget.select()
                      }
                    }}
                    onChange={(event) => {
                      setApiKeyDirty(true)
                      setSettings((current) => ({
                        ...current,
                        apiKey: event.target.value.replace(SAVED_API_KEY_MASK, ""),
                      }))
                    }}
                    placeholder={t.settings.apiKeyPlaceholder}
                    className="pr-9"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="absolute top-0.5 right-0.5"
                    onClick={() => setShowApiKey((current) => !current)}
                  >
                    {showApiKey ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                    <span className="sr-only">{showApiKey ? t.settings.hideApiKey : t.settings.showApiKey}</span>
                  </Button>
                </div>
              </div>
              <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                <div className="grid gap-2">
                  <Label htmlFor="model">{t.settings.model}</Label>
                  {modelsLoaded && modelOptions.length > 0 ? (
                    <Select
                      value={settings.model}
                      onValueChange={(value) =>
                        setSettings((current) => ({ ...current, model: value || "" }))
                      }
                    >
                      <SelectTrigger id="model">
                        <SelectValue placeholder={t.settings.selectModel} />
                      </SelectTrigger>
                      <SelectContent>
                        {modelOptions.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      id="model"
                      value={settings.model}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, model: event.target.value }))
                      }
                    />
                  )}
                </div>
                <div className="grid gap-2 sm:self-end">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={fetchModels}
                    disabled={modelsLoading || !settings.baseUrl.trim()}
                  >
                    <RefreshCw className="size-4" />
                    {modelsLoading ? t.settings.loading : t.settings.getModels}
                  </Button>
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="translateConcurrency">{t.settings.translateConcurrency}</Label>
                <Input
                  id="translateConcurrency"
                  inputMode="numeric"
                  value={settings.translateConcurrency}
                  onChange={(event) =>
                    setSettings((current) => ({
                      ...current,
                      translateConcurrency: event.target.value.replace(/[^0-9]/g, ""),
                    }))
                  }
                  placeholder="50"
                />
                <p className="text-xs text-muted-foreground">
                  {t.settings.concurrencyHelp}
                </p>
              </div>
              <div className="mt-2 grid gap-4 rounded-xl border border-border p-4">
                <div className="grid gap-1">
                  <p className="text-sm font-medium">{t.settings.mailTitle}</p>
                  <p className="text-xs text-muted-foreground">{t.settings.mailDescription}</p>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <Input
                    type="checkbox"
                    checked={settings.mailEnabled}
                    onChange={(event) =>
                      setSettings((current) => ({ ...current, mailEnabled: event.target.checked }))
                    }
                    className="size-4"
                  />
                  {t.settings.mailEnabled}
                </label>
                <div className="grid gap-2 sm:grid-cols-[1fr_8rem_9rem]">
                  <div className="grid gap-2">
                    <Label htmlFor="mailSmtpHost">{t.settings.mailSmtpHost}</Label>
                    <Input
                      id="mailSmtpHost"
                      value={settings.mailSmtpHost}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailSmtpHost: event.target.value }))
                      }
                      placeholder="smtp.example.com"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mailSmtpPort">{t.settings.mailSmtpPort}</Label>
                    <Input
                      id="mailSmtpPort"
                      inputMode="numeric"
                      value={settings.mailSmtpPort}
                      onChange={(event) =>
                        setSettings((current) => ({
                          ...current,
                          mailSmtpPort: event.target.value.replace(/[^0-9]/g, ""),
                        }))
                      }
                      placeholder="587"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mailSmtpSecurity">{t.settings.mailSmtpSecurity}</Label>
                    <Select
                      value={settings.mailSmtpSecurity}
                      onValueChange={(value) => {
                        if (value === "none" || value === "tls" || value === "ssl") {
                          setSettings((current) => ({ ...current, mailSmtpSecurity: value }))
                        }
                      }}
                    >
                      <SelectTrigger id="mailSmtpSecurity">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">None</SelectItem>
                        <SelectItem value="tls">STARTTLS</SelectItem>
                        <SelectItem value="ssl">SSL/TLS</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="mailSmtpUsername">{t.settings.mailSmtpUsername}</Label>
                    <Input
                      id="mailSmtpUsername"
                      value={settings.mailSmtpUsername}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailSmtpUsername: event.target.value }))
                      }
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mailSmtpPassword">{t.settings.mailSmtpPassword}</Label>
                    <div className="relative">
                      <Input
                        id="mailSmtpPassword"
                        type={showSmtpPassword ? "text" : "password"}
                        value={settings.mailSmtpPassword}
                        onFocus={(event) => {
                          if (!smtpPasswordDirty && settings.mailSmtpPassword === SAVED_SMTP_PASSWORD_MASK) {
                            event.currentTarget.select()
                          }
                        }}
                        onChange={(event) => {
                          setSmtpPasswordDirty(true)
                          setSettings((current) => ({
                            ...current,
                            mailSmtpPassword: event.target.value.replace(SAVED_SMTP_PASSWORD_MASK, ""),
                          }))
                        }}
                        placeholder={t.settings.mailSmtpPasswordPlaceholder}
                        className="pr-9"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="absolute top-0.5 right-0.5"
                        onClick={() => setShowSmtpPassword((current) => !current)}
                      >
                        {showSmtpPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                        <span className="sr-only">
                          {showSmtpPassword ? t.settings.hideSmtpPassword : t.settings.showSmtpPassword}
                        </span>
                      </Button>
                    </div>
                  </div>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="mailFromAddress">{t.settings.mailFromAddress}</Label>
                    <Input
                      id="mailFromAddress"
                      value={settings.mailFromAddress}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailFromAddress: event.target.value }))
                      }
                      placeholder="youdub@example.com"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mailToAddresses">{t.settings.mailToAddresses}</Label>
                    <Input
                      id="mailToAddresses"
                      value={settings.mailToAddresses}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailToAddresses: event.target.value }))
                      }
                      placeholder="user@example.com,ops@example.com"
                    />
                  </div>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="flex items-center gap-2 text-sm">
                    <Input
                      type="checkbox"
                      checked={settings.mailNotifyOnSuccess}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailNotifyOnSuccess: event.target.checked }))
                      }
                      className="size-4"
                    />
                    {t.settings.mailNotifyOnSuccess}
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Input
                      type="checkbox"
                      checked={settings.mailNotifyOnFailure}
                      onChange={(event) =>
                        setSettings((current) => ({ ...current, mailNotifyOnFailure: event.target.checked }))
                      }
                      className="size-4"
                    />
                    {t.settings.mailNotifyOnFailure}
                  </label>
                </div>
              </div>
              {visibleMessage ? <p className="text-sm text-muted-foreground">{visibleMessage}</p> : null}
            </div>
          </div>
      {asPanel ? (
        <div className="flex justify-end border-t border-border pt-4">
          <Button type="submit">{t.settings.save}</Button>
        </div>
      ) : (
        <DialogFooter className="shrink-0">
          <Button type="submit">{t.settings.save}</Button>
        </DialogFooter>
      )}
    </form>
  )

  if (asPanel) {
    return (
      <div className="mx-auto flex w-full max-w-[1760px] flex-col gap-6">
        <Card>
          <CardContent className="p-6">{form}</CardContent>
        </Card>
      </div>
    )
  }

  function updateBilibiliAuthorRows(nextRows: BilibiliAuthorConfigRow[]) {
    bilibiliAuthorRowsRef.current = nextRows
    setBilibiliAuthorRows(nextRows)
    setSettings((current) => ({
      ...current,
      bilibiliAuthorConfigJson: stringifyAuthorConfigRows(nextRows),
    }))
  }

  function updateBilibiliAuthorRow(
    rowId: string,
    field: keyof Omit<BilibiliAuthorConfigRow, "id">,
    value: string,
  ) {
    const nextValue = field === "tid" ? value.replace(/\D/g, "") : value
    if (field === "topicName") {
      setAuthorTopicErrors((current) => ({ ...current, [rowId]: "" }))
    }
    updateBilibiliAuthorRows(
      bilibiliAuthorRowsRef.current.map((row) => (
        row.id === rowId
          ? { ...row, [field]: nextValue, ...(field === "topicName" ? { topicId: "", topicMissionId: "" } : {}) }
          : row
      )),
    )
  }

  function updateBilibiliAuthorTopic(rowId: string, topicName: string, topicId: string, topicMissionId = "") {
    updateBilibiliAuthorRows(bilibiliAuthorRowsRef.current.map((row) => (
      row.id === rowId ? { ...row, topicName, topicId, topicMissionId } : row
    )))
    setAuthorTopicErrors((current) => ({ ...current, [rowId]: "" }))
  }

  async function resolveAuthorTopic(rowId: string) {
    const name = bilibiliAuthorRowsRef.current.find((row) => row.id === rowId)?.topicName.trim()
    if (!name) return
    setResolvingAuthorTopic(rowId)
    setAuthorTopicErrors((current) => ({ ...current, [rowId]: "" }))
    try {
      const topic = await resolveBilibiliTopic(name)
      updateBilibiliAuthorTopic(rowId, topic.topic_name, String(topic.topic_id), topic.mission_id ? String(topic.mission_id) : "")
    } catch (err) {
      setAuthorTopicErrors((current) => ({ ...current, [rowId]: err instanceof Error ? err.message : t.task.bilibiliTopicResolveError }))
    } finally {
      setResolvingAuthorTopic(null)
    }
  }

  function updateBilibiliPokemonTopicRows(nextRows: BilibiliPokemonTopicRow[]) {
    setBilibiliPokemonTopicRows(nextRows)
    setSettings((current) => ({
      ...current,
      bilibiliPokemonTopicConfigJson: stringifyPokemonTopicRows(nextRows),
    }))
  }

  function updateBilibiliPokemonTopicRow(
    rowId: string,
    field: keyof Omit<BilibiliPokemonTopicRow, "id">,
    value: string,
  ) {
    const nextValue = field === "topicId" ? value.replace(/\D/g, "") : value
    updateBilibiliPokemonTopicRows(
      bilibiliPokemonTopicRows.map((row) => (row.id === rowId ? { ...row, [field]: nextValue } : row)),
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" />}>
        <Settings className="size-4" />
        {t.settings.button}
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-hidden sm:max-w-[min(96vw,1760px)]">
        {form}
      </DialogContent>
    </Dialog>
  )
}
