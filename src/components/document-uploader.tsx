'use client';

import { useRouter } from 'next/navigation';
import { useRef, useState, useTransition } from 'react';

import { apiFetch, messageOf } from '@/lib/client/api';

import { Spinner } from './ui';

interface UploadOutcome {
  filename: string;
  status: 'ok' | 'duplicate' | 'error';
  message: string;
}

/**
 * Uploads files one at a time.
 *
 * Sequential rather than parallel on purpose: each upload embeds every chunk,
 * and firing several at once reliably trips the free-tier embedding rate
 * limit. One at a time is slower but finishes; parallel uploads fail halfway
 * and leave half-ingested documents behind.
 */
export function DocumentUploader({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  /**
   * Which file is in flight, and where it sits in the batch. Uploads are
   * sequential and each one embeds every chunk, so a ten-file drop can run for
   * a while — "Ingesting notes.pdf" alone leaves you unable to tell a slow
   * upload from a stuck one.
   */
  const [current, setCurrent] = useState<{ name: string; index: number; total: number } | null>(
    null,
  );
  const [results, setResults] = useState<UploadOutcome[]>([]);
  const [dragging, setDragging] = useState(false);
  const [, startTransition] = useTransition();

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;

    setBusy(true);
    setResults([]);

    const outcomes: UploadOutcome[] = [];

    for (const [index, file] of list.entries()) {
      setCurrent({ name: file.name, index: index + 1, total: list.length });

      const body = new FormData();
      body.append('file', file);

      try {
        const data = await apiFetch<{ outcome?: string; message?: string }>(
          `/api/documents?workspaceId=${encodeURIComponent(workspaceId)}`,
          { method: 'POST', body },
        );

        outcomes.push({
          filename: file.name,
          status: data.outcome === 'duplicate' ? 'duplicate' : 'ok',
          message: data.message ?? 'Ingested.',
        });
      } catch (caught) {
        // Every file reports its own outcome, so one bad file does not hide
        // the others' results.
        outcomes.push({
          filename: file.name,
          status: 'error',
          message: messageOf(caught, 'Upload failed.'),
        });
      }

      setResults([...outcomes]);
    }

    setCurrent(null);
    setBusy(false);
    if (inputRef.current) inputRef.current.value = '';
    startTransition(() => router.refresh());
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!busy) void uploadFiles(e.dataTransfer.files);
        }}
        className={`rounded-lg border border-dashed px-6 py-8 text-center transition-colors ${
          dragging ? 'border-brand bg-brand-soft' : 'border-border-strong'
        }`}
      >
        {busy && current ? (
          <>
            <p className="flex items-center justify-center gap-2 text-sm font-medium">
              <Spinner size={15} label={`Ingesting ${current.name}`} className="text-brand" />
              <span className="min-w-0 truncate">Ingesting {current.name}…</span>
            </p>
            <p className="mt-1 text-xs tabular-nums text-fg-muted">
              File {current.index} of {current.total} · extracting text, chunking and embedding
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium">Drop files here, or choose them</p>
            <p className="mt-1 text-xs text-fg-muted">
              PDF, DOCX, TXT, Markdown, CSV, JSON · up to 4&nbsp;MB each
            </p>
          </>
        )}

        <input
          ref={inputRef}
          type="file"
          multiple
          disabled={busy}
          accept=".pdf,.docx,.txt,.md,.markdown,.csv,.json,.log,.yaml,.yml"
          onChange={(e) => e.target.files && void uploadFiles(e.target.files)}
          className="sr-only"
          id="file-input"
        />

        <label
          htmlFor="file-input"
          className={`mt-4 inline-flex cursor-pointer items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors ${
            busy
              ? 'cursor-not-allowed bg-surface-2 text-fg-subtle'
              : 'bg-primary text-primary-fg hover:bg-primary-hover'
          }`}
        >
          {busy && <Spinner size={14} label="Upload in progress" />}
          {busy ? 'Uploading…' : 'Choose files'}
        </label>

        <p className="mt-3 text-xs text-fg-subtle">
          Re-uploading the same file is safe — it is detected by content hash and will not create
          duplicate chunks.
        </p>
      </div>

      {results.length > 0 && (
        <ul className="space-y-1.5 text-sm">
          {results.map((result, i) => (
            <li
              key={`${result.filename}-${i}`}
              className={`rounded-md px-3 py-2 ${
                result.status === 'error'
                  ? 'bg-danger-soft text-danger'
                  : result.status === 'duplicate'
                    ? 'bg-warning-soft text-warning'
                    : 'bg-success-soft text-success'
              }`}
            >
              <span className="font-medium">{result.filename}</span> — {result.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
