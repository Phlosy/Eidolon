import { FolderKanban } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { StatusBadge } from "../../components/app/widgets";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCompanyQuery, useProjects } from "../../hooks/queries";
import { formatRelativeTime, formatUnits } from "../../utils/format";

export function ProjectsPage() {
  const { t } = useTranslation("app");
  const { company_id: companyId } = useCurrentCompany();
  const company = useCompanyQuery(companyId);
  const projects = useProjects(companyId);
  return (
    <>
      <PageHeader
        title={t("projects.title")}
        description={t("projects.description")}
        icon={FolderKanban}
      />
      {projects.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {projects.error ? (
        <ErrorState error={projects.error} onRetry={() => void projects.refetch()} />
      ) : null}
      {projects.data && projects.data.length === 0 ? (
        <EmptyState title={t("projects.empty")} />
      ) : null}
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {projects.data?.map((p) => (
          <li key={p.project_id}>
            <Link
              to={`/projects/${p.project_id}`}
              className="command-panel block p-4 transition hover:border-border-active"
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-sm font-semibold">{p.title}</h2>
                <StatusBadge status={p.status} />
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {t("projects.manager")}: {p.manager ?? "—"} · {t("projects.budget")}:{" "}
                {formatUnits(p.budget, company.data?.currency)}
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {formatRelativeTime(p.updated_at)}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
