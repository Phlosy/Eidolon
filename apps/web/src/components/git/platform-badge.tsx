import { GitBranch, Github, Gitlab } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import type { GitPlatformType } from "../../types";

const PLATFORM_VARIANT: Record<GitPlatformType, "warning" | "success" | "default" | "muted"> = {
  gitlab: "warning",
  gitea: "success",
  github: "default",
  custom: "muted",
};

/** Platform chip: brand-ish icon (lucide fallback) + i18n'd platform label. */
export function PlatformBadge({ platform }: { platform: GitPlatformType }) {
  const { t } = useTranslation();
  const Icon = platform === "gitlab" ? Gitlab : platform === "github" ? Github : GitBranch;
  return (
    <Badge variant={PLATFORM_VARIANT[platform]} data-testid={`platform-badge-${platform}`}>
      <Icon className="h-3 w-3" />
      {enumLabel(t, "git:platform", platform)}
    </Badge>
  );
}
