import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { FolderPlus } from "lucide-react";
import { useCreateDriveFolder } from "../../hooks/useDrive";
import { Button } from "../common/button";
import { Input } from "../common/input";
import type { DriveNode, DriveZone } from "../../types";

interface NewFolderFormProps {
  zone: DriveZone;
  /** Selected folder becomes the parent; null creates at the zone root. */
  parent: DriveNode | null;
  projectId?: number | null;
  onDone: () => void;
}

/** Inline create-folder form for the drive tree panel (POST /drive/folders). */
export function NewFolderForm({ zone, parent, projectId, onDone }: NewFolderFormProps) {
  const { t } = useTranslation();
  const create = useCreateDriveFolder();
  const [name, setName] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate(
      {
        zone,
        name: trimmed,
        parent_id: parent?.id ?? null,
        ...(projectId != null ? { project_id: projectId } : {}),
      },
      { onSuccess: onDone },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-border p-2">
      <Input
        autoFocus
        placeholder={t("drive:newFolder.namePlaceholder")}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      {create.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400" role="alert">
          {create.error.message}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          {t("common:cancel")}
        </Button>
        <Button type="submit" size="sm" disabled={create.isPending || !name.trim()}>
          <FolderPlus className="h-3.5 w-3.5" />
          {t("drive:newFolder.submit")}
        </Button>
      </div>
    </form>
  );
}
