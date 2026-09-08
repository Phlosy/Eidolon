import { useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  BookOpen,
  Clock3,
  Cloud,
  FilePlus2,
  FolderKanban,
  FolderPlus,
  LayoutGrid,
  List,
  Search,
  Upload,
} from "lucide-react";
import { useCreateDriveDocument, useDriveTree, useUploadDriveFile } from "../../hooks/useDrive";
import { useEmployees } from "../../hooks/useEmployees";
import { DriveDocumentViewer } from "../../components/drive/drive-document-viewer";
import { DriveFolderGrid } from "../../components/drive/drive-folder-grid";
import { DriveNodeList } from "../../components/drive/drive-node-list";
import { DriveSpaceSidebar, type DriveLocation } from "../../components/drive/drive-space-sidebar";
import { NewFolderForm } from "../../components/drive/new-folder-form";
import { Dialog } from "../../components/common/dialog";
import { Button } from "../../components/common/button";
import { DOC_TYPE_META } from "../../components/drive/constants";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { Input } from "../../components/common/input";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { sortDriveNodes } from "../../utils/drive-tree";
import type { DriveDocType, DriveNode, DriveZone } from "../../types";

type Selection =
  | { kind: "all" }
  | { kind: "recent" }
  | { kind: "zone"; zone: DriveZone }
  | { kind: "node"; nodeId: number };

const DOC_TYPES = Object.keys(DOC_TYPE_META) as DriveDocType[];

function sortByUpdated(nodes: DriveNode[]): DriveNode[] {
  return [...nodes].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );
}

export function DrivePage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const treeQuery = useDriveTree();
  const employeesQuery = useEmployees();
  const deepLinkedNodeId = Number(searchParams.get("node"));
  const [selection, setSelection] = useState<Selection>(
    Number.isInteger(deepLinkedNodeId) && deepLinkedNodeId > 0
      ? { kind: "node", nodeId: deepLinkedNodeId }
      : { kind: "all" },
  );
  const [docTypeFilter, setDocTypeFilter] = useState<DriveDocType | "all">("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"name" | "updated">("updated");
  const [view, setView] = useState<"list" | "grid">("list");
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newDocOpen, setNewDocOpen] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadDriveFile();
  const nodes = useMemo(() => treeQuery.data ?? [], [treeQuery.data]);
  const selectedNode =
    selection.kind === "node" ? (nodes.find((node) => node.id === selection.nodeId) ?? null) : null;
  const query = search.trim().toLowerCase();

  const openNode = (node: DriveNode) => {
    setSelection({ kind: "node", nodeId: node.id });
    setSearchParams({ node: String(node.id) }, { replace: true });
  };

  const baseNodes = useMemo(() => {
    if (query) return nodes.filter((node) => node.name.toLowerCase().includes(query));
    if (selection.kind === "all") return nodes.filter((node) => node.parent_id == null);
    if (selection.kind === "recent") return nodes.filter((node) => node.kind === "document");
    if (selection.kind === "zone") {
      const root = nodes.find((node) => node.path === `drive/${selection.zone}`);
      return root
        ? nodes.filter((node) => node.parent_id === root.id)
        : nodes.filter((node) => node.zone === selection.zone && node.parent_id == null);
    }
    if (selectedNode?.kind === "folder") {
      return nodes.filter((node) => node.parent_id === selectedNode.id);
    }
    return [];
  }, [nodes, query, selectedNode, selection]);

  const sortedBase = sort === "name" ? sortDriveNodes(baseNodes) : sortByUpdated(baseNodes);
  const folders = sortedBase.filter((node) => node.kind === "folder");
  const documentSource =
    selection.kind === "all" && !query
      ? nodes.filter((node) => node.kind === "document")
      : baseNodes.filter((node) => node.kind === "document");
  const documents = (
    sort === "name" ? sortDriveNodes(documentSource) : sortByUpdated(documentSource)
  )
    .filter((node) => docTypeFilter === "all" || node.doc_type === docTypeFilter)
    .slice(0, selection.kind === "all" && !query ? 16 : undefined);

  let activeLocation: DriveLocation | null = selectedNode?.zone ?? null;
  if (selection.kind === "all" || selection.kind === "recent") activeLocation = selection.kind;
  if (selection.kind === "zone") activeLocation = selection.zone;

  let newFolderParent: DriveNode | null = null;
  if (selectedNode?.kind === "folder") newFolderParent = selectedNode;
  if (selectedNode?.kind === "document" && selectedNode.parent_id != null) {
    newFolderParent =
      nodes.find((node) => node.id === selectedNode.parent_id && node.kind === "folder") ?? null;
  }
  const newFolderZone: DriveZone =
    selection.kind === "zone" ? selection.zone : (selectedNode?.zone ?? "projects");
  let workspaceTitle = selectedNode?.name ?? t("drive:workspace.allFiles");
  if (selection.kind === "all") workspaceTitle = t("drive:workspace.allFiles");
  if (selection.kind === "recent") workspaceTitle = t("drive:workspace.recent");
  if (selection.kind === "zone") workspaceTitle = t(`drive:zone.${selection.zone}`);
  if (query) workspaceTitle = t("drive:workspace.searchResults");

  const selectLocation = (location: DriveLocation) => {
    setSelection(
      location === "all" || location === "recent"
        ? { kind: location }
        : { kind: "zone", zone: location },
    );
    setSearch("");
    setSearchParams({}, { replace: true });
    setNewFolderOpen(false);
  };

  const goBack = () => {
    if (!selectedNode) return;
    const parent =
      selectedNode.parent_id != null
        ? nodes.find((node) => node.id === selectedNode.parent_id)
        : null;
    setSelection(
      parent ? { kind: "node", nodeId: parent.id } : { kind: "zone", zone: selectedNode.zone },
    );
    if (parent) setSearchParams({ node: String(parent.id) }, { replace: true });
    else setSearchParams({}, { replace: true });
  };

  if (treeQuery.isError) {
    return <ErrorState error={treeQuery.error} onRetry={() => treeQuery.refetch()} />;
  }

  const quickActions = [
    {
      icon: FolderPlus,
      label: t("drive:workspace.createFolder"),
      hint: t("drive:workspace.createFolderHint"),
      onClick: () => setNewFolderOpen(true),
    },
    {
      icon: Clock3,
      label: t("drive:workspace.recent"),
      hint: t("drive:workspace.recentHint"),
      onClick: () => selectLocation("recent"),
    },
    {
      icon: FolderKanban,
      label: t("drive:zone.projects"),
      hint: t("drive:workspace.projectsHint"),
      onClick: () => selectLocation("projects"),
    },
    {
      icon: BookOpen,
      label: t("drive:zone.knowledge"),
      hint: t("drive:workspace.knowledgeHint"),
      onClick: () => selectLocation("knowledge"),
    },
  ];

  return (
    <div className="space-y-5 panel-enter">
      <PageHeader icon={Cloud} title={t("drive:title")} description={t("drive:description")} />
      {treeQuery.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
          <Skeleton className="h-[720px] rounded-[var(--radius-panel)]" />
          <Skeleton className="h-[720px] rounded-[var(--radius-panel)]" />
        </div>
      ) : (
        <section className="command-panel overflow-hidden">
          <div className="grid lg:grid-cols-[240px_minmax(0,1fr)]">
            <DriveSpaceSidebar
              active={activeLocation}
              nodes={nodes}
              selectedNodeId={selectedNode?.id ?? null}
              onOpenLocation={selectLocation}
              onOpenNode={openNode}
            />
            <div className="min-w-0 p-4 sm:p-5 xl:p-7">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <label className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    type="search"
                    className="h-11 rounded-xl bg-background/65 pl-10"
                    aria-label={t("drive:searchPlaceholder")}
                    placeholder={t("drive:searchPlaceholder")}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  data-tutorial-target="create-document"
                  className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setNewDocOpen(true)}
                >
                  <FilePlus2 className="h-4 w-4" />
                  {t("drive:newDocument.button")}
                </button>
                <button
                  type="button"
                  className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setNewFolderOpen((open) => !open)}
                >
                  <FolderPlus className="h-4 w-4" />
                  {t("drive:newFolder.button")}
                </button>
                <input
                  ref={uploadInputRef}
                  type="file"
                  hidden
                  tabIndex={-1}
                  aria-hidden="true"
                  accept=".md,.markdown,.docx,.pdf"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    uploadMutation.mutate(
                      {
                        file,
                        zone: newFolderZone,
                        parent_id: newFolderParent?.id,
                        project_id: newFolderParent?.project_id,
                      },
                      { onSuccess: openNode },
                    );
                    event.target.value = "";
                  }}
                />
                <button
                  type="button"
                  disabled={uploadMutation.isPending}
                  className="flex min-h-11 items-center justify-center gap-2 rounded-xl border border-border bg-background/65 px-4 text-sm font-semibold text-foreground transition hover:border-border-active hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
                  title={t("drive:workspace.uploadHint")}
                  onClick={() => uploadInputRef.current?.click()}
                >
                  <Upload className="h-4 w-4" />
                  {uploadMutation.isPending
                    ? t("drive:workspace.uploading")
                    : t("drive:workspace.upload")}
                </button>
              </div>

              {uploadMutation.isError ? (
                <p role="alert" className="mt-2 text-xs text-destructive">
                  {uploadMutation.error instanceof Error
                    ? uploadMutation.error.message
                    : t("drive:workspace.uploadError")}
                </p>
              ) : null}

              <div className="mt-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
                {quickActions.map(({ icon: ActionIcon, label, hint, onClick }) => (
                  <button
                    key={label}
                    type="button"
                    className="flex min-h-28 flex-col items-start gap-3 rounded-2xl border border-border bg-background/55 p-4 text-left transition-colors hover:border-border-active hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-24 sm:flex-row sm:items-center"
                    onClick={onClick}
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                      <ActionIcon className="h-5 w-5" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold">{label}</span>
                      <span className="mt-1 block text-[11px] leading-4 text-muted-foreground">
                        {hint}
                      </span>
                    </span>
                  </button>
                ))}
              </div>

              {newFolderOpen ? (
                <div className="mt-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
                  <NewFolderForm
                    zone={newFolderZone}
                    parent={newFolderParent}
                    projectId={newFolderParent?.project_id ?? null}
                    onDone={() => setNewFolderOpen(false)}
                  />
                </div>
              ) : null}

              <CreateDocumentDialog
                open={newDocOpen}
                zone={newFolderZone}
                parent={newFolderParent}
                onClose={() => setNewDocOpen(false)}
                onCreate={openNode}
              />

              <div className="mt-7 border-t border-border pt-6">
                <div className="flex flex-wrap items-center gap-3">
                  {selection.kind === "node" ? (
                    <button
                      type="button"
                      onClick={goBack}
                      className="flex h-11 w-11 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
                      aria-label={t("drive:workspace.back")}
                    >
                      <ArrowLeft className="h-4 w-4" />
                    </button>
                  ) : null}
                  <div className="mr-auto min-w-0">
                    <p className="type-kicker text-primary">
                      {t("drive:workspace.cloudWorkspace")}
                    </p>
                    <h2 className="mt-1 truncate text-xl font-semibold tracking-tight">
                      {workspaceTitle}
                    </h2>
                  </div>
                  {selectedNode?.kind !== "document" ? (
                    <>
                      <label className="sr-only" htmlFor="drive-type-filter">
                        {t("drive:workspace.filterType")}
                      </label>
                      <select
                        id="drive-type-filter"
                        value={docTypeFilter}
                        onChange={(event) =>
                          setDocTypeFilter(event.target.value as DriveDocType | "all")
                        }
                        className="h-11 rounded-xl border border-border bg-background/65 px-3 text-xs outline-none focus:border-border-active"
                      >
                        <option value="all">{t("drive:filterAll")}</option>
                        {DOC_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {enumLabel(t, "drive:docType", type)}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="h-11 rounded-xl border border-border bg-background/65 px-3 text-xs text-muted-foreground hover:text-foreground"
                        onClick={() => setSort((value) => (value === "name" ? "updated" : "name"))}
                      >
                        {sort === "name"
                          ? t("drive:workspace.sortName")
                          : t("drive:workspace.sortUpdated")}
                      </button>
                      <div className="flex rounded-xl border border-border bg-background/65 p-1">
                        <button
                          type="button"
                          aria-label={t("drive:workspace.listView")}
                          aria-pressed={view === "list"}
                          onClick={() => setView("list")}
                          className={cn(
                            "flex h-9 w-9 items-center justify-center rounded-lg",
                            view === "list"
                              ? "bg-primary/10 text-primary"
                              : "text-muted-foreground",
                          )}
                        >
                          <List className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          aria-label={t("drive:workspace.gridView")}
                          aria-pressed={view === "grid"}
                          onClick={() => setView("grid")}
                          className={cn(
                            "flex h-9 w-9 items-center justify-center rounded-lg",
                            view === "grid"
                              ? "bg-primary/10 text-primary"
                              : "text-muted-foreground",
                          )}
                        >
                          <LayoutGrid className="h-4 w-4" />
                        </button>
                      </div>
                    </>
                  ) : null}
                </div>

                {selectedNode?.kind === "document" ? (
                  <div className="mt-6">
                    <DriveDocumentViewer
                      nodeId={selectedNode.id}
                      employees={employeesQuery.data ?? []}
                    />
                  </div>
                ) : (
                  <div className="mt-6 space-y-8">
                    <section>
                      <div className="mb-3 flex items-center justify-between">
                        <h3 className="text-base font-semibold">{t("drive:workspace.folders")}</h3>
                        <span className="text-xs text-muted-foreground">{folders.length}</span>
                      </div>
                      <DriveFolderGrid folders={folders} allNodes={nodes} onSelect={openNode} />
                    </section>
                    <section>
                      <div className="mb-3">
                        <h3 className="text-base font-semibold">
                          {selection.kind === "all" && !query
                            ? t("drive:workspace.recentDocuments")
                            : t("drive:workspace.documents")}
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {t("drive:workspace.documentCount", { count: documents.length })}
                        </p>
                      </div>
                      <DriveNodeList
                        nodes={documents}
                        employees={employeesQuery.data ?? []}
                        view={view}
                        onSelectNode={openNode}
                      />
                    </section>
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

interface CreateDocumentDialogProps {
  open: boolean;
  zone: DriveZone;
  parent: DriveNode | null;
  onClose: () => void;
  onCreate: (node: DriveNode) => void;
}

/** 原生新建 Markdown 文档（教程 cloud_docs 步教的就是这个按钮）。 */
function CreateDocumentDialog({
  open,
  zone,
  parent,
  onClose,
  onCreate,
}: CreateDocumentDialogProps) {
  const { t } = useTranslation();
  const create = useCreateDriveDocument();
  const [name, setName] = useState("");
  const [content, setContent] = useState("");

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate(
      {
        zone,
        name: trimmed,
        content,
        parent_id: parent?.id ?? null,
        project_id: parent?.project_id ?? null,
      },
      {
        onSuccess: (node) => {
          setName("");
          setContent("");
          onClose();
          onCreate(node);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
      title={t("drive:newDocument.button")}
      description={t("drive:newDocument.hint")}
    >
      <div className="space-y-3">
        <label className="block text-xs font-medium text-muted-foreground">
          {t("drive:newDocument.namePlaceholder")}
          <Input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1.5"
            placeholder={t("drive:newDocument.namePlaceholder")}
          />
        </label>
        <label className="block text-xs font-medium text-muted-foreground">
          {t("drive:newDocument.contentPlaceholder")}
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            rows={6}
            className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder={t("drive:newDocument.contentPlaceholder")}
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("common:close")}
          </Button>
          <Button onClick={submit} disabled={create.isPending || !name.trim()}>
            {t("drive:newDocument.submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
