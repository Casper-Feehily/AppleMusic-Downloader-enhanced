"use client";

import { useI18n } from "../i18n";
import { type DesktopUpdateController, type UpdateInfo } from "../service";

export default function UpdateModal({
  updateInfo,
  desktopUpdate,
  onClose,
}: {
  updateInfo: UpdateInfo;
  desktopUpdate: DesktopUpdateController;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { status, starting, start } = desktopUpdate;
  const busy = !!status && !["idle", "error"].includes(status.phase);
  const percent = status?.total ? Math.min(100, Math.floor(status.downloaded / status.total * 100)) : 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in-up"
      style={{
        background: "rgba(0, 0, 0, 0.55)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
      }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-[420px] rounded-2xl p-6 shadow-2xl animate-spring-in"
        style={{
          background: "var(--card-bg)",
          border: "1px solid var(--card-border)",
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.45)",
          backdropFilter: "blur(20px)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-500/20 text-amber-500 text-xs font-bold">
              ↑
            </span>
            <h3 className="text-[14px] font-semibold" style={{ color: "var(--text-heading)" }}>
              {t("update.title")}
            </h3>
          </div>
          {updateInfo.has_update && <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
            {t("update.available")}
          </span>}
        </div>

        <div
          className="mb-4 flex items-center justify-between rounded-lg p-2.5"
          style={{ background: "var(--input-bg)", border: "1px solid var(--input-border)" }}
        >
          <div className="text-[11px]" style={{ color: "var(--text-dim)" }}>
            <span>{t("update.current")}: </span>
            <span className="font-mono text-foreground font-medium">v{updateInfo.current_version}</span>
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-dim)" }}>
            <span>{t("update.latest")}: </span>
            <span className="font-mono font-semibold text-emerald-400">v{updateInfo.latest_version}</span>
          </div>
        </div>

        {updateInfo.release_notes && (
          <div className="mb-5">
            <div className="text-[10.5px] font-medium mb-1.5" style={{ color: "var(--text-dim)" }}>
              {t("update.changelog")}
            </div>
            <div
              className="max-h-[160px] overflow-y-auto rounded-lg p-3 text-[11px] leading-relaxed font-mono whitespace-pre-wrap"
              style={{
                background: "var(--log-bg)",
                color: "var(--log-text)",
                border: "1px solid var(--card-border)",
              }}
            >
              {updateInfo.release_notes}
            </div>
          </div>
        )}

        <div className="mb-4 space-y-2 text-[11px]" aria-live="polite">
          <p style={{ color: "var(--text-dim)" }}>
            {status?.supported ? t("update.auto_description") : t("update.desktop_only")}
          </p>
          {status && !status.supported && status.reason && (
            <p style={{ color: "var(--text-muted)" }}>{status.reason}</p>
          )}
          {busy && (
            <>
              <p style={{ color: "var(--text-heading)" }}>
                {t(`update.phase.${status.phase}`)}
                {status.phase === "downloading" && ` ${percent}%`}
              </p>
              {status.phase === "downloading" && (
                <>
                  <progress className="w-full h-2 accent-emerald-500" value={status.downloaded} max={status.total || 1} aria-label={t("update.progress")} />
                  <p style={{ color: "var(--text-muted)" }}>
                    {(status.downloaded / 1048576).toFixed(1)} / {(status.total / 1048576).toFixed(1)} MB
                  </p>
                </>
              )}
            </>
          )}
          {status?.phase === "error" && (
            <p role="alert" className="text-red-400 break-words">{t("update.install_failed")}: {status.error}</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="btn-secondary flex-1 py-2 text-[12px] font-medium"
            style={{ borderRadius: 8 }}
          >
            {busy ? t("update.background") : t("update.close")}
          </button>
          {status?.supported && (
            <button onClick={() => void start()} disabled={busy || starting}
              className="btn-primary flex-1 py-2 text-[12px] font-medium disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ borderRadius: 8 }}>
              {busy || starting ? t("update.in_progress") : t("update.install_btn")}
            </button>
          )}
          <button
            onClick={() => {
              window.open(updateInfo.release_url, "_blank", "noopener,noreferrer");
              onClose();
            }}
            className={`${status?.supported ? "btn-secondary" : "btn-primary"} flex-1 py-2 text-[12px] font-medium`}
            style={{ borderRadius: 8 }}
          >
            {t("update.download_btn")}
          </button>
        </div>
      </div>
    </div>
  );
}
