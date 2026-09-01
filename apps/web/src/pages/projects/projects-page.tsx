import { useState } from "react";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useProjects } from "../../hooks/useProjects";
import { ProjectRow } from "../../components/project/project-row";
import { NewProjectDialog } from "../../components/project/new-project-dialog";
import { Button } from "../../components/common/button";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";

export function ProjectsPage() {
  const { t } = useTranslation();
  const projectsQuery = useProjects();
  const [dialogOpen, setDialogOpen] = useState(false);

  const projects = projectsQuery.data ?? [];

  return (
    <div>
      <PageHeader
        title={t("project:listTitle")}
        description={t("project:listDescription")}
        actions={
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="h-4 w-4" />
            {t("project:newProject")}
          </Button>
        }
      />
      {projectsQuery.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : projectsQuery.isError ? (
        <ErrorState error={projectsQuery.error} onRetry={() => projectsQuery.refetch()} />
      ) : projects.length === 0 ? (
        <EmptyState title={t("project:emptyTitle")} hint={t("project:emptyHint")} />
      ) : (
        <div className="space-y-3">
          {projects.map((project) => (
            <ProjectRow key={project.id} project={project} />
          ))}
        </div>
      )}
      <NewProjectDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
