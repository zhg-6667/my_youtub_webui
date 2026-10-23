"use client"

import { PointerEvent, useEffect, useRef, useState } from "react"

import { BilibiliCoverProject } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const EDITOR_WIDTH = 1280

const DEFAULT_PROJECT: BilibiliCoverProject = {
  text: "",
  font_family: "Microsoft YaHei",
  font_size: 88,
  color: "#ffffff",
  x: 0.5,
  y: 0.18,
}

type CoverFontOption = { value: string; label: string; file?: string }

const FONT_OPTIONS: CoverFontOption[] = [
  { value: "Microsoft YaHei", label: "Microsoft YaHei" },
  { value: "SimHei", label: "SimHei" },
  { value: "Arial", label: "Arial" },
  { value: "Georgia", label: "Georgia" },
  { value: "布丁体", label: "布丁体", file: "buding.ttf" },
  { value: "龙书弘一体", label: "龙书弘一体（海风吹）", file: "longshu-hongyi.ttf" },
  { value: "糖果体", label: "糖果体", file: "tangguo.ttf" },
  { value: "青年体", label: "青年体", file: "qingnian.ttf" },
  { value: "美黑体", label: "美黑体", file: "meihei.ttf" },
  { value: "清夏体", label: "清夏体", file: "qingxia.ttf" },
  { value: "濑户体", label: "濑户体", file: "laihu.ttf" },
  { value: "嘟嘟体", label: "嘟嘟体（萌新UP）", file: "dudu.ttf" },
  { value: "青春体", label: "青春体", file: "qingchun.ttf" },
  { value: "超黑体", label: "超黑体", file: "chaohei.ttf" },
]

const fontLoadPromises = new Map<string, Promise<void>>()

function coverFontWeight(fontFamily: string) {
  return FONT_OPTIONS.find((font) => font.value === fontFamily)?.file ? 400 : 700
}

function loadCoverFont(fontFamily: string): Promise<void> {
  const font = FONT_OPTIONS.find((option) => option.value === fontFamily)
  if (!font?.file) return Promise.resolve()

  const cached = fontLoadPromises.get(fontFamily)
  if (cached) return cached

  const fontFace = new FontFace(fontFamily, `url("/fonts/bilibili-cover/${font.file}")`)
  const pending = fontFace.load().then((loaded) => {
    document.fonts.add(loaded)
  }).catch((error: unknown) => {
    fontLoadPromises.delete(fontFamily)
    throw error
  })
  fontLoadPromises.set(fontFamily, pending)
  return pending
}

const FONT_SIZE_MIN = 24
const FONT_SIZE_MAX = 240
const RECENT_TEXT_STYLES_KEY = "youdub-cover-recent-text-styles"
const QUICK_TEXT_STYLES_KEY = "youdub-cover-quick-text-styles"

type TextStyle = {
  fontFamily: string
  fontSize: number
  color: string
}

const DEFAULT_QUICK_TEXT_STYLES: TextStyle[] = [
  { fontFamily: DEFAULT_PROJECT.font_family, fontSize: 48, color: "#ffffff" },
  { fontFamily: DEFAULT_PROJECT.font_family, fontSize: 64, color: "#ffd400" },
  { fontFamily: DEFAULT_PROJECT.font_family, fontSize: 88, color: "#ffffff" },
  { fontFamily: DEFAULT_PROJECT.font_family, fontSize: 120, color: "#ff4d4f" },
  { fontFamily: DEFAULT_PROJECT.font_family, fontSize: 160, color: "#35c7ff" },
]

function clampFontSize(value: number) {
  return Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, value))
}

function isColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value)
}

function styleKey(style: TextStyle) {
  return JSON.stringify([style.fontFamily, style.fontSize, style.color.toLowerCase()])
}

function isTextStyle(value: unknown): value is Pick<TextStyle, "fontSize" | "color"> & { fontFamily?: string } {
  if (!value || typeof value !== "object") return false
  const style = value as Partial<TextStyle>
  return typeof style.fontSize === "number"
    && Number.isFinite(style.fontSize)
    && style.fontSize >= FONT_SIZE_MIN
    && style.fontSize <= FONT_SIZE_MAX
    && isColor(style.color)
}

function uniqueStyles(styles: TextStyle[]) {
  return styles.filter((style, index) => styles.findIndex((item) => styleKey(item) === styleKey(style)) === index)
}

function readStoredStyles(key: string, fallback: TextStyle[]): TextStyle[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(key) || "null")
    if (!Array.isArray(value)) return fallback
    const unique = uniqueStyles(value.filter(isTextStyle).map((style) => ({
      fontFamily: typeof style.fontFamily === "string" && FONT_OPTIONS.some((font) => font.value === style.fontFamily)
        ? style.fontFamily
        : DEFAULT_PROJECT.font_family,
      fontSize: clampFontSize(style.fontSize),
      color: style.color.toLowerCase(),
    })))
    return unique.length ? unique : fallback
  } catch {
    return fallback
  }
}

function rememberRecentStyle(items: TextStyle[], style: TextStyle) {
  return [style, ...items.filter((item) => styleKey(item) !== styleKey(style))].slice(0, 5)
}

function validProject(value: BilibiliCoverProject | null): BilibiliCoverProject {
  if (!value || typeof value.text !== "string") return DEFAULT_PROJECT
  return {
    text: value.text,
    font_family: FONT_OPTIONS.some((font) => font.value === value.font_family)
      ? value.font_family
      : DEFAULT_PROJECT.font_family,
    font_size: Number.isFinite(value.font_size)
      ? clampFontSize(value.font_size)
      : DEFAULT_PROJECT.font_size,
    color: /^#[0-9a-f]{6}$/i.test(value.color) ? value.color : DEFAULT_PROJECT.color,
    x: Number.isFinite(value.x) ? Math.max(0, Math.min(1, value.x)) : DEFAULT_PROJECT.x,
    y: Number.isFinite(value.y) ? Math.max(0, Math.min(1, value.y)) : DEFAULT_PROJECT.y,
  }
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return fetch(url, { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) throw new Error("Failed to load cover image.")
      return response.blob()
    })
    .then((blob) => new Promise<HTMLImageElement>((resolve, reject) => {
      const objectUrl = URL.createObjectURL(blob)
      const image = new Image()
      image.onload = () => {
        URL.revokeObjectURL(objectUrl)
        resolve(image)
      }
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl)
        reject(new Error("Failed to decode cover image."))
      }
      image.src = objectUrl
    }))
}

function drawProject(canvas: HTMLCanvasElement, image: HTMLImageElement, project: BilibiliCoverProject) {
  canvas.width = image.naturalWidth
  canvas.height = image.naturalHeight
  const context = canvas.getContext("2d")
  if (!context) throw new Error("Canvas is unavailable.")
  context.drawImage(image, 0, 0)

  const scale = canvas.width / EDITOR_WIDTH
  const fontSize = project.font_size * scale
  context.fillStyle = project.color
  context.font = `${coverFontWeight(project.font_family)} ${fontSize}px "${project.font_family}"`
  context.textAlign = "center"
  context.textBaseline = "middle"
  const lines = project.text.split(/\r?\n/)
  const lineHeight = fontSize * 1.2
  const startY = canvas.height * project.y - ((lines.length - 1) * lineHeight) / 2
  lines.forEach((line, index) => {
    context.fillText(line, canvas.width * project.x, startY + index * lineHeight)
  })
}

type Props = {
  backgroundUrl: string
  initialProject: BilibiliCoverProject | null
  editedAvailable: boolean
  onSave: (file: File, project: BilibiliCoverProject) => Promise<void>
  onReset: () => Promise<void>
}

export function BilibiliCoverEditor({ backgroundUrl, initialProject, editedAvailable, onSave, onReset }: Props) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [project, setProject] = useState<BilibiliCoverProject>(() => validProject(initialProject))
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState("")
  const [fontSizeInput, setFontSizeInput] = useState(String(project.font_size))
  const [colorInput, setColorInput] = useState(project.color)
  const [recentTextStyles, setRecentTextStyles] = useState<TextStyle[]>([])
  const [quickTextStyles, setQuickTextStyles] = useState<TextStyle[]>(DEFAULT_QUICK_TEXT_STYLES)
  const previewRef = useRef<HTMLDivElement | null>(null)
  const previewCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const backgroundImageRef = useRef<HTMLImageElement | null>(null)
  const projectRef = useRef(project)
  const draggingRef = useRef<number | null>(null)
  const preferencesLoadedRef = useRef(false)

  useEffect(() => {
    projectRef.current = project
  }, [project])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setRecentTextStyles(readStoredStyles(RECENT_TEXT_STYLES_KEY, []))
      setQuickTextStyles(readStoredStyles(QUICK_TEXT_STYLES_KEY, DEFAULT_QUICK_TEXT_STYLES))
      preferencesLoadedRef.current = true
    }, 0)
    return () => window.clearTimeout(timer)
  }, [])

  useEffect(() => {
    if (preferencesLoadedRef.current) window.localStorage.setItem(RECENT_TEXT_STYLES_KEY, JSON.stringify(recentTextStyles))
  }, [recentTextStyles])

  useEffect(() => {
    if (preferencesLoadedRef.current) window.localStorage.setItem(QUICK_TEXT_STYLES_KEY, JSON.stringify(quickTextStyles))
  }, [quickTextStyles])

  useEffect(() => {
    let cancelled = false
    loadImage(backgroundUrl)
      .then((image) => {
        if (cancelled) return
        backgroundImageRef.current = image
        void loadCoverFont(projectRef.current.font_family)
          .then(() => {
            if (!cancelled && previewCanvasRef.current) drawProject(previewCanvasRef.current, image, projectRef.current)
          })
          .catch(() => {
            if (!cancelled) setError(t.task.bilibiliCoverFontLoadError)
          })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [backgroundUrl, t.task.bilibiliCoverFontLoadError, t.task.bilibiliCoverPreviewError])

  useEffect(() => {
    let cancelled = false
    void loadCoverFont(project.font_family)
      .then(() => {
        if (cancelled) return
        const image = backgroundImageRef.current
        const canvas = previewCanvasRef.current
        if (image && canvas) drawProject(canvas, image, project)
        setError((current) => current === t.task.bilibiliCoverFontLoadError ? "" : current)
      })
      .catch(() => {
        if (!cancelled) setError(t.task.bilibiliCoverFontLoadError)
      })
    return () => {
      cancelled = true
    }
  }, [project, t.task.bilibiliCoverFontLoadError])

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      const nextProject = validProject(initialProject)
      setProject(nextProject)
      setFontSizeInput(String(nextProject.font_size))
      setColorInput(nextProject.color)
      setError("")
    }
    setOpen(nextOpen)
  }

  const updateFontSize = (value: number) => {
    const fontSize = clampFontSize(value)
    setProject((current) => ({ ...current, font_size: fontSize }))
    setFontSizeInput(String(fontSize))
  }

  const updateColor = (color: string) => {
    if (!isColor(color)) return
    const normalizedColor = color.toLowerCase()
    setProject((current) => ({ ...current, color: normalizedColor }))
    setColorInput(normalizedColor)
  }

  const commitManualFontSize = (): number => {
    const parsed = Number(fontSizeInput)
    const fontSize = fontSizeInput.trim() && Number.isFinite(parsed)
      ? clampFontSize(parsed)
      : project.font_size
    updateFontSize(fontSize)
    return fontSize
  }

  const currentTextStyle = (): TextStyle => {
    const parsed = Number(fontSizeInput)
    return {
      fontFamily: project.font_family,
      fontSize: fontSizeInput.trim() && Number.isFinite(parsed) ? clampFontSize(parsed) : project.font_size,
      color: isColor(colorInput.trim()) ? colorInput.trim().toLowerCase() : project.color.toLowerCase(),
    }
  }

  const applyTextStyle = (style: TextStyle) => {
    const normalizedStyle = { ...style, fontSize: clampFontSize(style.fontSize), color: style.color.toLowerCase() }
    setProject((current) => ({ ...current, font_family: normalizedStyle.fontFamily, font_size: normalizedStyle.fontSize, color: normalizedStyle.color }))
    setFontSizeInput(String(normalizedStyle.fontSize))
    setColorInput(normalizedStyle.color)
    setRecentTextStyles((current) => rememberRecentStyle(current, normalizedStyle))
  }

  const addCurrentTextStyle = () => {
    const style = currentTextStyle()
    setQuickTextStyles((current) => current.some((item) => styleKey(item) === styleKey(style)) ? current : [...current, style])
  }

  const removeQuickTextStyle = (style: TextStyle) => {
    setQuickTextStyles((current) => current.filter((item) => styleKey(item) !== styleKey(style)))
  }

  const updatePosition = (clientX: number, clientY: number) => {
    const bounds = previewRef.current?.getBoundingClientRect()
    if (!bounds) return
    setProject((current) => ({
      ...current,
      x: Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (clientY - bounds.top) / bounds.height)),
    }))
  }

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    draggingRef.current = event.pointerId
    event.currentTarget.setPointerCapture(event.pointerId)
    updatePosition(event.clientX, event.clientY)
  }

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (draggingRef.current === event.pointerId) updatePosition(event.clientX, event.clientY)
  }

  const handlePointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    if (draggingRef.current === event.pointerId) draggingRef.current = null
  }

  const commitManualColor = (): string | null => {
    const color = colorInput.trim()
    if (isColor(color)) {
      updateColor(color)
      if (error === t.task.bilibiliCoverColorInvalid) setError("")
      return color.toLowerCase()
    }
    setError(t.task.bilibiliCoverColorInvalid)
    setColorInput(project.color)
    window.alert(t.task.bilibiliCoverColorInvalid)
    return null
  }

  const renderFile = async (projectToRender: BilibiliCoverProject = project) => {
    const [image] = await Promise.all([
      loadImage(backgroundUrl),
      loadCoverFont(projectToRender.font_family).catch(() => {
        throw new Error(t.task.bilibiliCoverFontLoadError)
      }),
    ])
    const canvas = document.createElement("canvas")
    drawProject(canvas, image, projectToRender)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95))
    if (!blob) throw new Error("Failed to export cover image.")
    return new File([blob], "edited.jpg", { type: "image/jpeg" })
  }

  const handleSave = async () => {
    if (!project.text.trim()) {
      setError(t.task.bilibiliCoverTextRequired)
      return
    }
    const fontSize = commitManualFontSize()
    const color = commitManualColor()
    if (!color) return
    const projectToSave = { ...project, font_size: fontSize, color }
    setRecentTextStyles((current) => rememberRecentStyle(current, {
      fontFamily: projectToSave.font_family,
      fontSize: projectToSave.font_size,
      color: projectToSave.color,
    }))
    setSaving(true)
    setError("")
    try {
      await onSave(await renderFile(projectToSave), projectToSave)
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : t.task.bilibiliCoverSaveError)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setResetting(true)
    setError("")
    try {
      await onReset()
      setProject(DEFAULT_PROJECT)
      setFontSizeInput(String(DEFAULT_PROJECT.font_size))
      setColorInput(DEFAULT_PROJECT.color)
    } catch (err) {
      setError(err instanceof Error ? err.message : t.task.bilibiliCoverResetError)
    } finally {
      setResetting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <Button type="button" variant="outline" size="sm" onClick={() => handleOpenChange(true)}>
        {t.task.bilibiliCoverEdit}
      </Button>
      <DialogContent className="max-w-5xl p-5" showCloseButton={!saving && !resetting}>
        <DialogHeader>
          <DialogTitle>{t.task.bilibiliCoverEditorTitle}</DialogTitle>
          <DialogDescription>{t.task.bilibiliCoverEditorHelp}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-5 md:grid-cols-[calc(15rem_+_50px)_minmax(0,1fr)]">
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="bilibili-cover-text">{t.task.bilibiliCoverTextLabel}</Label>
              <Textarea
                id="bilibili-cover-text"
                rows={4}
                value={project.text}
                onChange={(event) => setProject((current) => ({ ...current, text: event.target.value }))}
                disabled={saving || resetting}
              />
            </div>
            <div className="space-y-2">
              <Label>{t.task.bilibiliCoverFontLabel}</Label>
              <Select
                value={project.font_family}
                onValueChange={(value) => setProject((current) => ({ ...current, font_family: value || DEFAULT_PROJECT.font_family }))}
                disabled={saving || resetting}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {FONT_OPTIONS.map((font) => <SelectItem key={font.value} value={font.value}>{font.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="bilibili-cover-font-size">{t.task.bilibiliCoverFontSizeLabel}</Label>
              <Input
                id="bilibili-cover-font-size"
                type="number"
                min="24"
                max="240"
                value={fontSizeInput}
                onChange={(event) => setFontSizeInput(event.target.value)}
                onBlur={commitManualFontSize}
                disabled={saving || resetting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bilibili-cover-color">{t.task.bilibiliCoverColorLabel}</Label>
              <div className="flex gap-2">
                <Input
                  id="bilibili-cover-color"
                  type="color"
                  value={project.color}
                  onChange={(event) => updateColor(event.target.value)}
                  disabled={saving || resetting}
                  className="w-12 p-1"
                />
                <Input
                  value={colorInput}
                  onChange={(event) => setColorInput(event.target.value)}
                  onBlur={commitManualColor}
                  aria-invalid={Boolean(error && !isColor(colorInput.trim()))}
                  placeholder="#RRGGBB"
                  disabled={saving || resetting}
                  className="font-mono"
                />
              </div>
              <div className="space-y-2 pt-1">
                <p className="text-xs font-medium text-muted-foreground">{t.task.bilibiliCoverQuickOptions}</p>
                <div className="flex flex-wrap gap-1.5">
                  {quickTextStyles.map((style) => (
                    <div key={styleKey(style)} className="flex items-center rounded-lg border border-border bg-background">
                      <Button type="button" size="xs" variant={styleKey(currentTextStyle()) === styleKey(style) ? "secondary" : "ghost"} onClick={() => applyTextStyle(style)} disabled={saving || resetting} title={`${style.fontFamily} · ${style.fontSize}px · ${style.color}`}>
                        <span className="size-3 rounded-full border border-black/20" style={{ backgroundColor: style.color }} />
                        {style.fontFamily} · {style.fontSize}px
                      </Button>
                      <Button type="button" size="icon-xs" variant="ghost" onClick={() => removeQuickTextStyle(style)} disabled={saving || resetting} aria-label={t.task.bilibiliCoverRemoveQuickOption} title={t.task.bilibiliCoverRemoveQuickOption}>
                        ×
                      </Button>
                    </div>
                  ))}
                </div>
                <Button type="button" size="xs" variant="ghost" onClick={addCurrentTextStyle} disabled={saving || resetting || quickTextStyles.some((style) => styleKey(style) === styleKey(currentTextStyle()))}>
                  {t.task.bilibiliCoverAddQuickOption}
                </Button>
                {recentTextStyles.length ? (
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">{t.task.bilibiliCoverRecentOptions}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {recentTextStyles.map((style) => (
                        <Button key={styleKey(style)} type="button" size="xs" variant={styleKey(currentTextStyle()) === styleKey(style) ? "secondary" : "outline"} onClick={() => applyTextStyle(style)} disabled={saving || resetting} title={`${style.fontFamily} · ${style.fontSize}px · ${style.color}`}>
                          <span className="size-3 rounded-full border border-black/20" style={{ backgroundColor: style.color }} />
                          {style.fontFamily} · {style.fontSize}px
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
          <div className="flex min-w-0 items-center justify-center rounded-lg border bg-muted/30 p-3">
            <div
              ref={previewRef}
              className="relative w-full max-w-3xl touch-none overflow-hidden rounded-md bg-black shadow-sm"
              style={{ aspectRatio: "16 / 9", containerType: "inline-size" }}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerEnd}
              onPointerCancel={handlePointerEnd}
            >
              <canvas ref={previewCanvasRef} className="size-full object-cover" aria-label={t.task.bilibiliCoverPreview} />
              {project.text ? (
                <div
                  className="absolute cursor-grab select-none whitespace-pre text-center font-bold leading-[1.2] opacity-0 active:cursor-grabbing"
                  style={{
                    left: `${project.x * 100}%`,
                    top: `${project.y * 100}%`,
                    color: project.color,
                    fontFamily: project.font_family,
                    fontWeight: coverFontWeight(project.font_family),
                    fontSize: `${project.font_size / 12.8}cqw`,
                    transform: "translate(-50%, -50%)",
                  }}
                  onPointerDown={handlePointerDown}
                >
                  {project.text}
                </div>
              ) : null}
            </div>
          </div>
        </div>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <DialogFooter>
          {editedAvailable ? (
            <Button type="button" variant="outline" onClick={handleReset} disabled={saving || resetting}>
              {resetting ? t.task.bilibiliCoverResetting : t.task.bilibiliCoverReset}
            </Button>
          ) : null}
          <DialogClose render={<Button type="button" variant="outline" disabled={saving || resetting} />}>
            {t.common.cancel}
          </DialogClose>
          <Button type="button" onClick={handleSave} disabled={saving || resetting}>
            {saving ? t.task.bilibiliCoverSaving : t.task.bilibiliCoverSave}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
