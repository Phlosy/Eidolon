import { BookOpen, Box, FolderGit2, FolderOpen, type LucideIcon } from "lucide-react";

/** resource_type → icon (git / docs / workspace; anything else → Box). */
export const RESOURCE_TYPE_ICON: Record<string, LucideIcon> = {
  git: FolderGit2,
  docs: BookOpen,
  workspace: FolderOpen,
};

export function resourceTypeIcon(resourceType: string): LucideIcon {
  return RESOURCE_TYPE_ICON[resourceType] ?? Box;
}
