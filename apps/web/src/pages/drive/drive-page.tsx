import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  BookOpen,
  ChartNoAxesColumn,
  Clock3,
  FilePlus2,
  FileText,
  FolderKanban,
  FolderPlus,
  LayoutGrid,
  List,
  Presentation,
  Search,
  SquarePen,
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
import { ErrorState } from "../../components/common/states";
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
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
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
    // 顶部不再放"云文档"大标题：左侧空间栏已经表明这是云文档区（项目文件/公司
    // 文档统一文件层级），工具栏直接从搜索 + 新建/上传开始，内容整体上移。
    <div className="space-y-5 panel-enter">
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
                <input
                  ref={folderInputRef}
                  type="file"
                  hidden
                  tabIndex={-1}
                  aria-hidden="true"
                  {...({ webkitdirectory: "" } as Record<string, string>)}
                  onChange={(event) => {
                    const files = Array.from(event.target.files ?? []);
                    for (const file of files) {
                      if (!file) continue;
                      uploadMutation.mutate({
                        file,
                        zone: newFolderZone,
                        parent_id: newFolderParent?.id,
                        project_id: newFolderParent?.project_id,
                      });
                    }
                    event.target.value = "";
                  }}
                />
                {/* 飞书式：新建/上传 各是一个图标，点开向下展开条目 */}
                <DriveCreateMenu
                  open={createMenuOpen}
                  onToggle={() => setCreateMenuOpen((value) => !value)}
                  onClose={() => setCreateMenuOpen(false)}
                  onNewDocument={() => setNewDocOpen(true)}
                  onNewFolder={() => {
                    setNewFolderOpen((value) => !value);
                  }}
                />
                <DriveUploadMenu
                  open={uploadMenuOpen}
                  onToggle={() => setUploadMenuOpen((value) => !value)}
                  onClose={() => setUploadMenuOpen(false)}
                  onUploadFile={() => uploadInputRef.current?.click()}
                  onUploadFolder={() => folderInputRef.current?.click()}
                  busy={uploadMutation.isPending}
                />
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
function useOutsideClose(
  ref: React.RefObject<HTMLDivElement | null>,
  open: boolean,
  onClose: () => void,
) {
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose, ref]);
}

function DriveCreateMenu({
  open,
  onToggle,
  onClose,
  onNewDocument,
  onNewFolder,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onNewDocument: () => void;
  onNewFolder: () => void;
}) {
  const { t } = useTranslation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<{ left: number; top: number } | null>(null);
  useOutsideClose(menuRef, open, onClose);

  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    setCoords({ left: rect.left, top: rect.bottom + 6 });
  }, [open]);

  const items = [
    { icon: FileText, label: t("drive:newMenu.markdown"), onClick: onNewDocument, enabled: true },
    { icon: FolderPlus, label: t("drive:newMenu.folder"), onClick: onNewFolder, enabled: true },
    {
      icon: ChartNoAxesColumn,
      label: t("drive:newMenu.table"),
      onClick: undefined,
      enabled: false,
    },
    { icon: Presentation, label: t("drive:newMenu.slides"), onClick: undefined, enabled: false },
    { icon: SquarePen, label: t("drive:newMenu.board"), onClick: undefined, enabled: false },
  ];

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        data-tutorial-target="create-document"
        aria-label={t("drive:newMenu.title")}
        aria-expanded={open}
        onClick={onToggle}
        className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title={t("drive:newMenu.title")}
      >
        <FilePlus2 className="h-4 w-4" />
      </button>
      {open && coords
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              data-testid="drive-create-menu"
              className="fixed z-[70] w-52 rounded-xl border border-border bg-popover p-1.5 shadow-xl"
              style={{ left: coords.left, top: coords.top }}
            >
              {items.map(({ icon: ItemIcon, label, onClick: handle, enabled }) => (
                <button
                  key={label}
                  type="button"
                  role="menuitem"
                  disabled={!enabled}
                  onClick={() => {
                    if (!enabled) return;
                    onClose();
                    handle?.();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-45"
                  title={enabled ? undefined : t("drive:newMenu.soon")}
                >
                  <ItemIcon className="h-3.5 w-3.5 text-muted-foreground" />
                  {label}
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

/** 飞书式「上传」下拉：文件 / 文件夹。 */
function DriveUploadMenu({
  open,
  onToggle,
  onClose,
  onUploadFile,
  onUploadFolder,
  busy,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onUploadFile: () => void;
  onUploadFolder: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<{ left: number; top: number } | null>(null);
  useOutsideClose(menuRef, open, onClose);

  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    setCoords({ left: rect.left, top: rect.bottom + 6 });
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label={t("drive:uploadMenu.title")}
        aria-expanded={open}
        onClick={onToggle}
        disabled={busy}
        className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-background/65 text-foreground transition hover:border-border-active hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
        title={t("drive:uploadMenu.title")}
      >
        <Upload className="h-4 w-4" />
      </button>
      {open && coords
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              data-testid="drive-upload-menu"
              className="fixed z-[70] w-48 rounded-xl border border-border bg-popover p-1.5 shadow-xl"
              style={{ left: coords.left, top: coords.top }}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onClose();
                  onUploadFile();
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted"
              >
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                {t("drive:uploadMenu.file")}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onClose();
                  onUploadFolder();
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted"
              >
                <FolderKanban className="h-3.5 w-3.5 text-muted-foreground" />
                {t("drive:uploadMenu.folder")}
              </button>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
