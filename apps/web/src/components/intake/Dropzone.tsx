"use client";

import * as React from "react";

import { cn, EmptyState } from "@shield/design-system";

import {
  type ArtifactSummary,
  ArtifactUploadError,
  uploadArtifact,
} from "@/lib/intake/artifacts";

export interface DropzoneProps {
  /** Called after each successful upload with the API's artifact row. */
  onUploaded: (artifact: ArtifactSummary) => void;
  /** Bytes; default 50 MB matches the API. */
  maxBytes?: number;
  /** Accept attribute for the native input; the API also enforces this. */
  accept?: string;
}

const DEFAULT_ACCEPT =
  ".pdf,.docx,.doc,.xlsx,.csv,.txt,.png,.jpg,.jpeg,.zip,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/csv,text/plain,image/png,image/jpeg,application/zip";

interface UploadingItem {
  id: string;
  name: string;
  size: number;
  status: "uploading" | "done" | "error";
  message?: string;
}

export function Dropzone({
  onUploaded,
  maxBytes = 50 * 1024 * 1024,
  accept = DEFAULT_ACCEPT,
}: DropzoneProps): JSX.Element {
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [items, setItems] = React.useState<UploadingItem[]>([]);

  function setItem(id: string, patch: Partial<UploadingItem>): void {
    setItems((curr) =>
      curr.map((it) => (it.id === id ? { ...it, ...patch } : it)),
    );
  }

  async function handleFiles(fileList: FileList | null): Promise<void> {
    if (!fileList) return;
    for (const file of Array.from(fileList)) {
      const id = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`;
      if (file.size > maxBytes) {
        setItems((curr) => [
          ...curr,
          {
            id,
            name: file.name,
            size: file.size,
            status: "error",
            message: `File exceeds ${Math.round(maxBytes / 1024 / 1024)} MB upload limit.`,
          },
        ]);
        continue;
      }
      setItems((curr) => [
        ...curr,
        { id, name: file.name, size: file.size, status: "uploading" },
      ]);
      try {
        const artifact = await uploadArtifact(file);
        setItem(id, { status: "done" });
        onUploaded(artifact);
      } catch (err) {
        const msg =
          err instanceof ArtifactUploadError
            ? (() => {
                const payload = err.payload as
                  { error?: { message?: string }; detail?: string } | undefined;
                return (
                  payload?.error?.message ??
                  payload?.detail ??
                  `Upload failed (${err.status})`
                );
              })()
            : err instanceof Error
              ? err.message
              : "Upload failed.";
        setItem(id, { status: "error", message: msg });
      }
    }
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>): void {
    e.preventDefault();
    setDragging(true);
  }

  function onDragLeave(): void {
    setDragging(false);
  }

  async function onDrop(e: React.DragEvent<HTMLDivElement>): Promise<void> {
    e.preventDefault();
    setDragging(false);
    await handleFiles(e.dataTransfer.files);
  }

  function clearItem(id: string): void {
    setItems((curr) => curr.filter((it) => it.id !== id));
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Upload artifact - drop a file here or press Enter to open file picker"
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-6 py-8 text-center transition-colors",
          dragging
            ? "border-brand-500 bg-brand-50"
            : "border-border bg-surface-card hover:border-border-strong",
        )}
      >
        <p className="text-sm font-medium text-ink-primary">
          Drop a file here or click to browse
        </p>
        <p className="text-xs text-ink-tertiary">
          PDF, DOCX, XLSX, CSV, TXT, PNG, JPG, ZIP up to{" "}
          {Math.round(maxBytes / 1024 / 1024)} MB.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple
          onChange={(e) => void handleFiles(e.target.files)}
          className="hidden"
        />
      </div>

      {items.length === 0 ? null : (
        <ul aria-label="Upload queue" className="flex flex-col gap-1.5">
          {items.map((it) => (
            <li
              key={it.id}
              className={cn(
                "flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm",
                it.status === "done" &&
                  "border-status-success-border bg-status-success-bg text-status-success-fg",
                it.status === "error" &&
                  "border-status-danger-border bg-status-danger-bg text-status-danger-fg",
                it.status === "uploading" &&
                  "border-border-subtle bg-surface-card text-ink-secondary",
              )}
            >
              <div className="flex min-w-0 flex-col">
                <span className="truncate font-medium" title={it.name}>
                  {it.name}
                </span>
                {it.message ? (
                  <span className="text-xs opacity-90">{it.message}</span>
                ) : (
                  <span className="text-xs opacity-70">
                    {it.status === "uploading"
                      ? "Uploading…"
                      : it.status === "done"
                        ? "Saved."
                        : "Failed."}
                  </span>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => clearItem(it.id)}
                className="text-sm opacity-70 hover:opacity-100"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EmptyArtifactsHint(): JSX.Element {
  return (
    <EmptyState
      title="No documents uploaded yet"
      description="Drop a file above to attach it to this intake. Optional — you can submit without any uploads."
    />
  );
}
