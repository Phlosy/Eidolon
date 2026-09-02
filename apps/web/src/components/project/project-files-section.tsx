import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useDriveTree } from "../../hooks/useDrive";
import { DriveTree } from "../drive/drive-tree";
import { DriveDocumentViewer } from "../drive/drive-document-viewer";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { Dialog } from "../common/dialog";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import type { Employee } from "../../types";

interface ProjectFilesSectionProps {
  projectId: number;
  employees: Employee[];
}

/**
 * Project detail → file tree: the project's folder inside the projects drive
 * zone (drive nodes filtered by project_id). Clicking a document opens the
 * viewer (content + revisions) in a dialog.
 */
export function ProjectFilesSection({ projectId, employees }: ProjectFilesSectionProps) {
  const { t } = useTranslation();
  const treeQuery = useDriveTree("projects");
  const [docId, setDocId] = useState<number | null>(null);

  const nodes = (treeQuery.data ?? []).filter((n) => n.project_id === projectId);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("project:files")}</CardTitle>
      </CardHeader>
      <CardContent>
        {treeQuery.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : treeQuery.isError ? (
          <ErrorState error={treeQuery.error} onRetry={() => treeQuery.refetch()} />
        ) : nodes.length === 0 ? (
          <EmptyState title={t("project:filesEmpty")} />
        ) : (
          <DriveTree
            nodes={nodes}
            showZones={false}
            selectedNodeId={docId}
            onSelectNode={(node) => {
              if (node.kind === "document") setDocId(node.id);
            }}
          />
        )}
      </CardContent>

      <Dialog
        open={docId != null}
        onOpenChange={(open) => !open && setDocId(null)}
        title={t("project:files")}
        className="max-w-2xl"
      >
        {docId != null ? <DriveDocumentViewer nodeId={docId} employees={employees} /> : null}
      </Dialog>
    </Card>
  );
}
