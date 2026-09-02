import { useEffect, useState } from "react";
import DOMPurify from "dompurify";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { FileWarning } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { DriveDocumentFormat } from "./document-export";

interface DocumentPreviewProps {
  format: DriveDocumentFormat;
  markdown: string;
  binary: Blob | null;
  isLoading: boolean;
  isError: boolean;
  previewRef: React.RefObject<HTMLDivElement | null>;
}

function readBlob(binary: Blob): Promise<ArrayBuffer> {
  if (typeof binary.arrayBuffer === "function") return binary.arrayBuffer();
  return new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(binary);
  });
}

function MarkdownPreview({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: (props) => <h1 className="mb-6 mt-2 text-3xl font-bold tracking-tight" {...props} />,
        h2: (props) => (
          <h2
            className="mb-3 mt-8 border-b pb-2 text-xl font-semibold"
            style={{ borderColor: "#e2e8f0" }}
            {...props}
          />
        ),
        h3: (props) => <h3 className="mb-2 mt-6 text-lg font-semibold" {...props} />,
        p: (props) => <p className="my-4 leading-7" {...props} />,
        ul: (props) => <ul className="my-4 list-disc space-y-1 pl-6" {...props} />,
        ol: (props) => <ol className="my-4 list-decimal space-y-1 pl-6" {...props} />,
        blockquote: (props) => (
          <blockquote
            className="my-5 border-l-4 px-4 py-2"
            style={{ borderColor: "#93c5fd", backgroundColor: "#eff6ff", color: "#475569" }}
            {...props}
          />
        ),
        code: (props) => (
          <code
            className="rounded px-1.5 py-0.5 font-mono text-[0.9em]"
            style={{ backgroundColor: "#f1f5f9", color: "#1d4ed8" }}
            {...props}
          />
        ),
        pre: (props) => (
          <pre
            className="my-5 overflow-auto rounded-xl p-4 text-sm leading-6"
            style={{ backgroundColor: "#020617", color: "#f1f5f9" }}
            {...props}
          />
        ),
        table: (props) => (
          <div className="my-5 overflow-x-auto">
            <table className="w-full border-collapse text-sm" {...props} />
          </div>
        ),
        th: (props) => (
          <th
            className="border px-3 py-2 text-left"
            style={{ borderColor: "#e2e8f0", backgroundColor: "#f8fafc" }}
            {...props}
          />
        ),
        td: (props) => (
          <td className="border px-3 py-2" style={{ borderColor: "#e2e8f0" }} {...props} />
        ),
        a: (props) => (
          <a
            className="underline underline-offset-2"
            style={{ color: "#2563eb" }}
            target="_blank"
            rel="noreferrer"
            {...props}
          />
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function DocxPreview({ binary }: { binary: Blob }) {
  const { t } = useTranslation();
  const [html, setHtml] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    readBlob(binary)
      .then(async (arrayBuffer) => {
        const mammoth = await import("mammoth/mammoth.browser");
        const result = await mammoth.convertToHtml({ arrayBuffer });
        if (active) setHtml(DOMPurify.sanitize(result.value));
      })
      .catch(() => active && setError(true));
    return () => {
      active = false;
    };
  }, [binary]);
  if (error) return <PreviewError message={t("drive:viewer.previewError")} />;
  if (!html) return <PreviewLoading />;
  return <div className="docx-preview leading-7" dangerouslySetInnerHTML={{ __html: html }} />;
}

function PdfPreview({ binary }: { binary: Blob }) {
  const { t } = useTranslation();
  const [pages, setPages] = useState<string[]>([]);
  const [limited, setLimited] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    let loadingTask: { destroy: () => Promise<void> } | null = null;
    setPages([]);
    setLimited(false);
    setError(false);
    readBlob(binary)
      .then(async (arrayBuffer) => {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const task = pdfjs.getDocument({ data: new Uint8Array(arrayBuffer) });
        loadingTask = task;
        const document = await task.promise;
        const pageLimit = Math.min(document.numPages, 50);
        const rendered: string[] = [];
        for (let index = 1; index <= pageLimit; index += 1) {
          if (!active) return;
          const page = await document.getPage(index);
          const viewport = page.getViewport({ scale: 1.5 });
          const canvas = window.document.createElement("canvas");
          canvas.width = Math.ceil(viewport.width);
          canvas.height = Math.ceil(viewport.height);
          const canvasContext = canvas.getContext("2d");
          if (!canvasContext) throw new Error("Canvas is unavailable");
          await page.render({ canvas, canvasContext, viewport }).promise;
          rendered.push(canvas.toDataURL("image/png"));
          if (active) setPages([...rendered]);
        }
        if (active) setLimited(document.numPages > pageLimit);
      })
      .catch(() => active && setError(true));
    return () => {
      active = false;
      void loadingTask?.destroy();
    };
  }, [binary]);

  if (error) return <PreviewError message={t("drive:viewer.previewError")} />;
  if (pages.length === 0) return <PreviewLoading />;
  return (
    <div className="space-y-3 bg-slate-200 p-3 sm:p-5">
      {pages.map((page, index) => (
        <img
          key={page}
          src={page}
          alt={t("drive:viewer.pdfPage", { page: index + 1 })}
          className="mx-auto h-auto w-full max-w-[900px] bg-white shadow-sm"
        />
      ))}
      {limited ? (
        <p className="py-3 text-center text-xs text-slate-600">{t("drive:viewer.pdfLimited")}</p>
      ) : null}
    </div>
  );
}

function PreviewLoading() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-72 items-center justify-center text-sm text-slate-500">
      {t("drive:viewer.loadingPreview")}
    </div>
  );
}

function PreviewError({ message }: { message: string }) {
  return (
    <div className="flex min-h-72 flex-col items-center justify-center gap-3 text-sm text-slate-500">
      <FileWarning className="h-8 w-8" />
      <span>{message}</span>
    </div>
  );
}

function PresentationPreview({ binary }: { binary: Blob }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-[480px] flex-col items-center justify-center rounded-xl bg-white px-8 text-center text-slate-700">
      <FileWarning className="h-10 w-10 text-violet-500" />
      <h3 className="mt-4 text-lg font-semibold">{t("drive:viewer.presentationReady")}</h3>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
        {t("drive:viewer.presentationHint", { size: Math.ceil(binary.size / 1024) })}
      </p>
    </div>
  );
}

export function DocumentPreview({
  format,
  markdown,
  binary,
  isLoading,
  isError,
  previewRef,
}: DocumentPreviewProps) {
  const { t } = useTranslation();

  if (isLoading) return <PreviewLoading />;
  if (isError || (["docx", "pptx", "pdf"].includes(format) && !binary)) {
    return <PreviewError message={t("drive:viewer.previewError")} />;
  }
  if (format === "pdf") {
    return <PdfPreview binary={binary!} />;
  }
  if (format === "pptx") {
    return <PresentationPreview binary={binary!} />;
  }
  return (
    <div
      ref={previewRef}
      className="min-h-[480px] rounded-xl px-6 py-8 sm:px-10 lg:px-14"
      style={{ backgroundColor: "#ffffff", color: "#0f172a" }}
    >
      {format === "docx" && binary ? (
        <DocxPreview binary={binary} />
      ) : (
        <MarkdownPreview content={markdown} />
      )}
    </div>
  );
}
