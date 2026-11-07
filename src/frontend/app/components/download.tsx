"use client";

import { useState, useEffect, useRef } from "react";
import TagInput from "./TagInput";
import { FormState, DEFAULT_FORM, getWrapperStatus, useSubmitTask } from "../service";
import type { WrapperStatus } from "../service";
import { useI18n } from "../i18n";
import { useToast } from "./Toast";

const AUDIO_FORMATS = [
  { value: "", label: "None" },
  { value: "mp3", label: "MP3" },
  { value: "flac", label: "FLAC" },
  { value: "wav", label: "WAV" },
  { value: "aac", label: "AAC" },
  { value: "m4a", label: "M4A" },
  { value: "ogg", label: "OGG" },
];

const AUDIO_FORMATS_ZH = [
  { value: "", label: "不转换" },
  { value: "mp3", label: "MP3" },
  { value: "flac", label: "FLAC" },
  { value: "wav", label: "WAV" },
  { value: "aac", label: "AAC" },
  { value: "m4a", label: "M4A" },
  { value: "ogg", label: "OGG" },
];

declare global {
  interface Window {
    pywebview?: { api: { open_file: () => Promise<string>; open_folder: () => Promise<string> } };
  }
}

export default function Download({ onNavigate }: { onNavigate?: (id: string) => void }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const submitTask = useSubmitTask();
  const cookiesInput = useRef<HTMLInputElement>(null);
  const outputInput = useRef<HTMLInputElement>(null);
  const existingLibraryInput = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [existingLibraryPath, setExistingLibraryPath] = useState("");
  const [hover, setHover] = useState(false);
  const [wrapperStatus, setWrapperStatus] = useState<WrapperStatus | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;

    // 恢复上次的 url 输入
    const savedUrls = sessionStorage.getItem("amdl_pending_urls") || "";

    fetch("/api/settings")
      .then((r) => r.json())
      .then((data) => {
        setForm(() => ({
          ...DEFAULT_FORM,
          ...(data && Object.keys(data).length > 0 ? data : {}),
          urls: savedUrls,
        }));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!form.use_wrapper) return;
    getWrapperStatus(form.wrapper_url)
      .then(setWrapperStatus)
      .catch(() => setWrapperStatus(null));
  }, [form.use_wrapper, form.wrapper_url]);

  const set = (key: keyof FormState, value: string | boolean | number) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      // 排除 urls 字段，不保存到后端 settings
      const { urls: _urls, ...rest } = next;
      void _urls;
      // 随时保存 urls 到 sessionStorage
      if (key === "urls") {
        sessionStorage.setItem("amdl_pending_urls", String(value));
      }
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rest),
      }).catch(() => {});
      return next;
    });
  };

  const setSourceCodec = (codec: string) => {
    setForm((prev) => {
      const next = {
        ...prev,
        codec_song: codec,
        audio_format: codec === "alac" ? "" : prev.audio_format,
      };
      const { urls: _urls, ...rest } = next;
      void _urls;
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rest),
      }).catch(() => {});
      return next;
    });
  };

  const handleSubmit = async () => {
    if (!form.use_wrapper && (!form.cookies_path || !form.cookies_path.trim())) {
      toast.error(t("download.cookies_empty"));
      return;
    }
    if (form.use_wrapper && !wrapperReady) {
      toast.error(t("download.wrapper_not_ready"));
      return;
    }
    try {
      const submitted = await submitTask(form, existingLibraryPath);
      if (!submitted) return;
      // 提交成功后清空暂存
      sessionStorage.removeItem("amdl_pending_urls");
      onNavigate?.("queue");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg === "NETWORK_ERROR") {
        toast.error(t("download.failed.backend"));
      } else {
        toast.error(`${t("download.failed")}: ${msg}`);
      }
    }
  };

  const wrapperReady = Boolean(
    wrapperStatus?.reachable
    && wrapperStatus.compatible
    && wrapperStatus.authenticated
    && wrapperStatus.playback_ready
  );

  const browseFile = () => {
    if (window.pywebview?.api) {
      window.pywebview.api.open_file().then((path: string) => { if (path) set("cookies_path", path); });
    } else {
      cookiesInput.current?.focus();
    }
  };

  const browseFolder = async (input: HTMLInputElement | null, onSelect: (path: string) => void) => {
    if (window.pywebview?.api) {
      const path = await window.pywebview.api.open_folder();
      if (path) onSelect(path);
    } else {
      input?.focus();
    }
  };

  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto">
      <div className="w-full max-w-[480px] px-8">

        {/* ── Language switcher ──
        <div className="mb-6 flex justify-center">
          <div className="flex rounded-md border p-0.5" style={{ borderColor: "var(--card-border)" }}>
            <button
              onClick={() => setLocale("zh")}
              className={[
                "rounded px-2.5 py-0.5 text-[10px] font-medium transition-all duration-150",
                locale === "zh"
                  ? "bg-[#ef4444] text-white"
                  : "text-foreground/35 hover:text-foreground/60",
              ].join(" ")}
            >
              中
            </button>
            <button
              onClick={() => setLocale("en")}
              className={[
                "rounded px-2.5 py-0.5 text-[10px] font-medium transition-all duration-150",
                locale === "en"
                  ? "bg-[#ef4444] text-white"
                  : "text-foreground/35 hover:text-foreground/60",
              ].join(" ")}
            >
              EN
            </button>
          </div>
        </div> */}

        {/* ── Hero ── */}
        <div className="mb-10 text-center animate-fade-in-up">
          <div
            className="mx-auto mb-5 flex h-13 w-13 items-center justify-center rounded-2xl"
            style={{
              background: "var(--card-bg)",
              border: "1px solid var(--card-border)",
              boxShadow: hover ? "0 4px 20px rgba(239, 68, 68, 0.08)" : "0 1px 3px rgba(0,0,0,0.04)",
              transition: "box-shadow 0.35s var(--ease-spring), transform 0.35s var(--ease-spring-bounce)",
              transform: hover ? "scale(1.03)" : "scale(1)",
            }}
            onMouseEnter={() => setHover(true)}
            onMouseLeave={() => setHover(false)}
          >
            <svg
              width="22" height="22" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
              style={{
                color: hover ? "#ef4444" : "var(--text-dim)",
                transition: "color 0.3s var(--ease-spring), transform 0.35s var(--ease-spring-bounce)",
                transform: hover ? "scale(1.08)" : "scale(1)",
              }}
            >
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </div>
          <h2
            className="text-[16px] font-semibold tracking-[-0.01em]"
            style={{ color: "var(--text-heading)" }}
          >
            {t("download.title")}
          </h2>
          <p className="mt-1.5 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {t("download.url.placeholder")}
          </p>
        </div>

        {/* ── URL Input ── */}
        <TagInput
          value={form.urls}
          onChange={(v) => set("urls", v)}
          placeholder={t("download.url.placeholder")}
        />

        {form.use_wrapper && !wrapperReady && (
          <div className="card mb-5 px-3.5 py-3 text-[11px]" style={{ color: "var(--text-dim)" }}>
            <div>{t("download.wrapper_not_ready")}</div>
            <button className="btn-secondary mt-2 px-2.5 py-1 text-[10.5px]" onClick={() => onNavigate?.("settings")}>
              {t("download.wrapper_settings")}
            </button>
          </div>
        )}

        {/* ── Path rows ── */}
        <div className="mb-5 space-y-2">
          {!form.use_wrapper && (
            <div className="flex gap-2">
              <input
                className="input"
                ref={cookiesInput}
                placeholder={t("download.cookies")}
                value={form.cookies_path}
                onChange={(e) => set("cookies_path", e.target.value)}
              />
              <button className="btn-secondary shrink-0 text-[11px]" onClick={browseFile}>
                {t("download.cookies.browse")}
              </button>
            </div>
          )}
          <div className="flex gap-2">
            <input
              className="input"
              ref={outputInput}
              placeholder={t("download.output")}
              value={form.output_path}
              onChange={(e) => set("output_path", e.target.value)}
            />
            <button className="btn-secondary shrink-0 text-[11px]" onClick={() => browseFolder(outputInput.current, (path) => set("output_path", path))}>
              {t("download.output.browse")}
            </button>
          </div>
          <label htmlFor="existing-library" className="block pt-1 text-[10.5px] font-medium" style={{ color: "var(--text-dim)" }}>
            {t("download.existing_library")}
          </label>
          <div className="flex gap-2">
            <input
              ref={existingLibraryInput}
              id="existing-library"
              className="input"
              aria-describedby="existing-library-hint"
              placeholder={t("download.existing_library.folder")}
              value={existingLibraryPath}
              onChange={(e) => setExistingLibraryPath(e.target.value)}
            />
            <button
              className="btn-secondary shrink-0 text-[11px]"
              aria-label={t("download.existing_library.browse")}
              onClick={() => browseFolder(existingLibraryInput.current, setExistingLibraryPath)}
            >
              {t("download.output.browse")}
            </button>
          </div>
          <p id="existing-library-hint" className="text-[10.5px]" style={{ color: "var(--text-dim)" }}>
            {t("download.existing_library.hint")}
          </p>
        </div>

        {/* ── Source quality ── */}
        <div className="mb-5">
          <label className="mb-1.5 block text-[10.5px] font-medium" style={{ color: "var(--text-dim)" }}>
            {t("download.source_quality")}
          </label>
          <select
            className="input"
            value={form.codec_song}
            onChange={(e) => setSourceCodec(e.target.value)}
          >
            <option value="aac-web">AAC 256 kbps</option>
            <option value="aac-he-web">AAC-HE (web)</option>
            <option value="atmos">Dolby Atmos</option>
            <option value="alac" disabled={!form.use_wrapper}>{t("download.alac")}</option>
          </select>
        </div>

        {/* ── Format selection: Audio conversion & Lyrics format ── */}
        <div className="mb-5 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-[10.5px] font-medium" style={{ color: "var(--text-dim)" }}>
              {t("download.audio_format")}
            </label>
            <select
              className="input"
              value={form.audio_format}
              disabled={form.codec_song === "alac"}
              onChange={(e) => set("audio_format", e.target.value)}
            >
              {(locale === "zh" ? AUDIO_FORMATS_ZH : AUDIO_FORMATS).map((fmt) => (
                <option key={fmt.value} value={fmt.value}>{fmt.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-[10.5px] font-medium" style={{ color: "var(--text-dim)" }}>
              {t("settings.lyrics_format")}
            </label>
            <select
              className="input"
              value={form.synced_lyrics_format}
              onChange={(e) => set("synced_lyrics_format", e.target.value)}
            >
              <option value="lrc">LRC ({locale === "zh" ? "标准" : "Standard"})</option>
              <option value="ttml">TTML ({locale === "zh" ? "逐字" : "Word-by-word"})</option>
              <option value="srt">SRT ({locale === "zh" ? "字幕" : "Subtitles"})</option>
            </select>
          </div>
        </div>

        {/* ── Submit ── */}
        <button
          className="btn-primary w-full py-[12px] text-[13px] font-semibold tracking-[0.01em]"
          disabled={form.use_wrapper && !wrapperReady}
          style={{
            borderRadius: 10,
            letterSpacing: "0.01em",
          }}
          onClick={handleSubmit}
        >
          {t("download.submit")}
        </button>

        <p className="mt-3.5 text-center text-[10.5px]" style={{ color: "var(--text-muted)" }}>
          {t("download.settings.hint")}
        </p>
      </div>
    </div>
  );
}
