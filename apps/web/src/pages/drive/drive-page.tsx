import { Cloud } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { work as workApi } from "../../api";
import type { Artifact } from "../../api/types";
import { ErrorNote } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { EmptyState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { keys, useProjects } from "../../hooks/queries";
import { formatBytes, formatDateTime } from "../../utils/format";

/** Every artifact of every project: content-addressed files with their attribution. */
export function DrivePage() {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const projects = useProjects(me.company_id);
  const details = useQueries({
    queries: (projects.data ?? []).map((p) => ({
      queryKey: keys.project(p.project_id),
      queryFn: () => workApi.project(p.project_id),
    })),
  });
  const loading = projects.isLoading || details.some((d) => d.isLoading);
  const rows = useMemo(
    () =>
      details
        .flatMap(
          (d) =>
            d.data?.artifacts.map((a) => ({
              artifact: a,
              project: d.data!.title,
              tasks: d.data!.tasks,
            })) ?? [],
        )
        .sort((a, b) => b.artifact.created_at.localeCompare(a.artifact.created_at)),
    [details],
  );
  const [selected, setSelected] = useState<Artifact | null>(null);
  return (
    <>
      <PageHeader title={t("drive.title")} description={t("drive.description")} icon={Cloud} />
      {loading ? <Skeleton className="h-40 w-full" /> : null}
      {!loading && rows.length === 0 ? <EmptyState title={t("drive.empty")} /> : null}
      {rows.length ? (
        <div className="grid gap-6 xl:grid-cols-[2fr_3fr]">
          <div className="command-panel overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">{t("drive.path")}</th>
                  <th className="px-3 py-2">{t("drive.project")}</th>
                  <th className="px-3 py-2">{t("drive.task")}</th>
                  <th className="px-3 py-2">{t("drive.size")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ artifact, project, tasks }) => (
                  <tr
                    key={artifact.artifact_id}
                    className={`cursor-pointer border-t border-border hover:bg-muted/40 ${selected?.artifact_id === artifact.artifact_id ? "bg-primary/5" : ""}`}
                    onClick={() => setSelected(artifact)}
                  >
                    <td className="px-3 py-2 font-mono">{artifact.path}</td>
                    <td className="px-3 py-2">
                      <Link
                        to={`/projects/${artifact.project_id}`}
                        className="hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {project}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {tasks.find((x) => x.task_id === artifact.task_id)?.title ??
                        artifact.task_id.slice(0, 8)}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {formatBytes(artifact.size)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected ? <ArtifactView artifact={selected} /> : null}
        </div>
      ) : null}
    </>
  );
}

function ArtifactView({ artifact }: { artifact: Artifact }) {
  const { t } = useTranslation("app");
  const [text, setText] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let revoked: string | null = null;
    setText(null);
    setUrl(null);
    setError(null);
    workApi
      .artifactContent(artifact.artifact_id)
      .then(async ({ blob, type }) => {
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
        if (type.startsWith("text/") || type.includes("json") || artifact.size < 64 * 1024)
          setText(await blob.text());
      })
      .catch((e: unknown) => setError(e));
    return () => {
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [artifact]);
  return (
    <section className="command-panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-sm">{artifact.path}</p>
          <p className="text-[11px] text-muted-foreground">
            {artifact.media_type} · {formatBytes(artifact.size)} ·{" "}
            {formatDateTime(artifact.created_at)} · {artifact.blob_ref.slice(0, 19)}…
          </p>
        </div>
        {url ? (
          <Button size="sm" variant="outline" onClick={() => window.open(url, "_blank")}>
            {t("drive.open")}
          </Button>
        ) : null}
      </div>
      {error ? (
        <div className="mt-3">
          <ErrorNote error={error} />
        </div>
      ) : null}
      {text != null ? (
        <pre className="scroll-area mt-3 max-h-[60vh] overflow-auto rounded-lg bg-muted/40 p-3 font-mono text-[11px] whitespace-pre-wrap">
          {text}
        </pre>
      ) : null}
    </section>
  );
}
