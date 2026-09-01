import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCreateProject } from "../../hooks/useProjects";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input, Textarea } from "../common/input";

interface NewProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * New Project = placing a customer order (§5): posts {name, description}
 * to /api/v1/projects; the backend then kicks off the order_review flow.
 */
export function NewProjectDialog({ open, onOpenChange }: NewProjectDialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const createProject = useCreateProject();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const reset = () => {
    setName("");
    setDescription("");
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    createProject.mutate(
      { name: name.trim(), description: description.trim() },
      {
        onSuccess: (project) => {
          reset();
          onOpenChange(false);
          navigate(`/projects/${project.id}`);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("project:dialog.title")}
      description={t("project:dialog.description")}
    >
      <form onSubmit={onSubmit} className="space-y-3">
        <div className="space-y-1.5">
          <label htmlFor="project-name" className="text-xs font-medium">
            {t("project:dialog.nameLabel")}
          </label>
          <Input
            id="project-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("project:dialog.namePlaceholder")}
            required
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="project-description" className="text-xs font-medium">
            {t("project:dialog.descriptionLabel")}
          </label>
          <Textarea
            id="project-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("project:dialog.descriptionPlaceholder")}
            required
          />
        </div>
        {createProject.isError ? (
          <p className="text-xs text-red-500">
            {createProject.error instanceof Error
              ? createProject.error.message
              : t("project:dialog.errorFallback")}
          </p>
        ) : null}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" disabled={createProject.isPending}>
            {createProject.isPending
              ? t("project:dialog.submitting")
              : t("project:dialog.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
