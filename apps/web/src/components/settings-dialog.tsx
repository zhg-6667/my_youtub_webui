"use client"

import { FormEvent, useEffect, useState } from "react"
import { Eye, EyeOff, RefreshCw, Settings } from "lucide-react"

import {
  getCookieInfo,
  getMailSettings,
  getOpenAIModels,
  getOpenAISettings,
  getYtdlpSettings,
  saveCookie,
  saveMailSettings,
  saveOpenAISettings,
  saveYtdlpSettings,
} from "@/lib/api"
import { LANGUAGE_OPTIONS, useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
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

type MessageKey = "keySaved" | "saved"

const defaultSettings: SettingsForm = {
  cookie: "",
  baseUrl: "https://api.openai.com/v1",
  apiKey: "",
  model: "gpt-4o-mini",
  translateConcurrency: "50",
  proxyPort: "",
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

export function SettingsDialog() {
  const { language, loadedModelsText, setLanguage, t } = useI18n()
  const [open, setOpen] = useState(false)
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

  const cookieValue =
    settings.cookie === SAVED_COOKIE_SENTINEL ? t.settings.savedCookie : settings.cookie
  const visibleMessage =
    messageKey === "keySaved" ? t.settings.keySaved : messageKey === "saved" ? t.settings.saved : message

  useEffect(() => {
    if (!open) return
    Promise.all([getCookieInfo(), getOpenAISettings(), getYtdlpSettings(), getMailSettings()])
      .then(([cookie, openai, ytdlp, mail]) => {
        setSettings({
          cookie: cookie.exists ? SAVED_COOKIE_SENTINEL : "",
          baseUrl: openai.base_url,
          apiKey: openai.has_api_key ? openai.api_key || SAVED_API_KEY_MASK : "",
          model: openai.model,
          translateConcurrency: openai.translate_concurrency || "50",
          proxyPort: ytdlp.proxy_port,
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
  }, [open])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage("")
    setMessageKey(null)
    try {
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
        mailSmtpPassword: mail.has_smtp_password ? mail.smtp_password || SAVED_SMTP_PASSWORD_MASK : "",
        mailSmtpPort: mail.smtp_port,
        mailSmtpSecurity: mail.smtp_security,
      }))
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

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" />}>
        <Settings className="size-4" />
        {t.settings.button}
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-hidden sm:max-w-2xl">
        <form onSubmit={submit} className="flex max-h-[calc(100dvh-4rem)] min-h-0 flex-col">
          <DialogHeader className="shrink-0 pr-8">
            <DialogTitle>{t.settings.title}</DialogTitle>
            <DialogDescription>{t.settings.description}</DialogDescription>
          </DialogHeader>
          <div className="mt-4 min-h-0 overflow-y-auto pr-1">
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
          <DialogFooter className="shrink-0">
            <Button type="submit">{t.settings.save}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
