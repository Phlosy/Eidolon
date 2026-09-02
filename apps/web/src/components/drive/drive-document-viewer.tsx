import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, Pencil } from "lucide-react";
import { useDriveNode, useDriveNodeContent, useDriveRevisions } from "../../hooks/useDrive";
import { DocTypeBadge } from "./doc-type-badge";
import { DriveEditForm } from "./drive-edit-form";
import { DriveRevisions } from "./drive-revisions";
import { DocumentPreview } from "./document-preview";
import {
  driveDocumentFormat,
  exportDriveDocument,
  exportFormatsFor,
  type DriveExportFormat,
} from "./document-export";
import { Button } from "../common/button";
import { ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { formatDateTime } from "../../utils/format";
import type { Employee } from "../../types";

interface DriveDocumentViewerProps {
  nodeId: number;
  employees: Employee[];
}

export function DriveDocumentViewer({ nodeId, employees }: DriveDocumentViewerProps) {
  const { t } = useTranslation();
  const nodeQuery = useDriveNode(nodeId);
  const format = driveDocumentFormat(nodeQuery.data);
  const needsBinary = format === "docx" || format === "pptx" || format === "pdf";
  const binaryQuery = useDriveNodeContent(nodeId, needsBinary);
  const revisionsQuery = useDriveRevisions(nodeId);
  const previewRef = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState(false);
  const [exportFormat, setExportFormat] = useState<DriveExportFormat>(format);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    setExportFormat(format);
    setEditing(false);
    setExportError(null);
  }, [format, nodeId]);

  if (nodeQuery.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (nodeQuery.isError || !nodeQuery.data) {
    return <ErrorState error={nodeQuery.error} onRetry={() => nodeQuery.refetch()} />;
  }
  const node = nodeQuery.data;
  const revisions = revisionsQuery.data ?? [];
  const currentSha =
    revisions.find((revision) => revision.version === node.current_version)?.sha256 ?? null;
  const owner =
    node.owner_employee_id != null
      ? (employees.find((employee) => employee.id === node.owner_employee_id)?.name ??
        `#${node.owner_employee_id}`)
      : "—";
  const exportFormats = exportFormatsFor(format);

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await exportDriveDocument({
        name: node.name,
        sourceFormat: format,
        targetFormat: exportFormat,
        markdown: node.content,
        binary: binaryQuery.data,
        previewElement: previewRef.current,
      });
    } catch (error) {
      setExportError(error instanceof Error ? error.message : t("drive:viewer.exportError"));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold tracking-tight">{node.name}</h2>
          <DocTypeBadge docType={node.doc_type} />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor={`drive-export-${node.id}`}>
              {t("drive:viewer.exportFormat")}
            </label>
            <select
              id={`drive-export-${node.id}`}
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value as DriveExportFormat)}
              className="h-9 rounded-lg border border-border bg-background px-2.5 text-xs outline-none focus:border-border-active"
            >
              {exportFormats.map((target) => (
                <option key={target} value={target}>
                  {t(`drive:viewer.exportFormats.${target}`)}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              size="sm"
              disabled={exporting || (needsBinary && binaryQuery.isLoading)}
              onClick={handleExport}
            >
              <Download className="h-3.5 w-3.5" />
              {exporting ? t("drive:viewer.exporting") : t("drive:viewer.export")}
            </Button>
            {format === "markdown" && !editing ? (
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                <Pencil className="h-3.5 w-3.5" />
                {t("drive:viewer.edit")}
              </Button>
            ) : null}
          </div>
        </div>
        {exportError ? (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {exportError}
          </p>
        ) : null}
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {t("drive:viewer.owner")}: <span className="text-foreground">{owner}</span>
          {" · "}
          {t("drive:viewer.version")}: v{node.current_version}
          {currentSha ? (
            <>
              {" · "}
              {t("drive:viewer.sha")}: {currentSha.slice(0, 8)}
            </>
          ) : null}
          {" · "}
          {formatDateTime(node.updated_at)}
        </p>
        <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{node.path}</p>
      </div>

      {editing ? (
        <DriveEditForm
          node={node}
          initialContent={node.content ?? ""}
          onDone={() => setEditing(false)}
        />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-border bg-slate-100/80 p-2 shadow-inner dark:bg-slate-950/50">
          <DocumentPreview
            format={format}
            markdown={node.content ?? ""}
            binary={binaryQuery.data ?? null}
            isLoading={needsBinary && binaryQuery.isLoading}
            isError={needsBinary && binaryQuery.isError}
            previewRef={previewRef}
          />
        </div>
      )}

      <DriveRevisions
        revisions={revisions}
        employees={employees}
        isLoading={revisionsQuery.isLoading}
      />
    </div>
  );
}
