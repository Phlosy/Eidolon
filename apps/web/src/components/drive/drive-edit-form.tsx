import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useUpdateDriveNode } from "../../hooks/useDrive";
import { Button } from "../common/button";
import { Input, Textarea } from "../common/input";
import type { DriveNode } from "../../types";

interface DriveEditFormProps {
  node: DriveNode;
  initialContent: string;
  onDone: () => void;
}

/**
 * Document edit mode: content textarea + optional revision message, saved via
 * PATCH /drive/nodes/{id}. MVP assumes editor permission — a 403 from the
 * backend surfaces here as an inline error.
 */
export function DriveEditForm({ node, initialContent, onDone }: DriveEditFormProps) {
  const { t } = useTranslation();
  const update = useUpdateDriveNode();
  const [content, setContent] = useState(initialContent);
  const [message, setMessage] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    update.mutate(
      {
        id: node.id,
        body: { content, ...(message.trim() ? { message: message.trim() } : {}) },
      },
      { onSuccess: onDone },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">
          {t("drive:edit.contentLabel")}
        </span>
        <Textarea
          data-testid="drive-edit-content"
          className="min-h-72 font-mono text-xs leading-relaxed"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">
          {t("drive:edit.messageLabel")}
        </span>
        <Input
          placeholder={t("drive:edit.messagePlaceholder")}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
      </label>
      {update.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400" role="alert">
          {update.error.message}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onDone}>
          {t("common:cancel")}
        </Button>
        <Button type="submit" size="sm" disabled={update.isPending}>
          {update.isPending ? t("drive:edit.saving") : t("drive:edit.save")}
        </Button>
      </div>
    </form>
  );
}
