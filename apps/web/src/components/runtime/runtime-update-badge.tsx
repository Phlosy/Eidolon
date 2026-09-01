import { useTranslation } from "react-i18next";
import { ArrowUp, TriangleAlert } from "lucide-react";
import { Badge } from "../common/badge";
import type { RuntimeImageInfo } from "../../types";

interface RuntimeUpdateBadgeProps {
  updateAvailable: boolean;
  compatibilityStatus: RuntimeImageInfo["compatibility_status"];
}

/** Update/Compatibility indicators for a runtime image. Renders nothing when up to date. */
export function RuntimeUpdateBadge({
  updateAvailable,
  compatibilityStatus,
}: RuntimeUpdateBadgeProps) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1">
      {updateAvailable ? (
        <Badge variant="info" className="normal-case">
          <ArrowUp className="h-3 w-3" />
          {t("runtime:updateAvailable")}
        </Badge>
      ) : null}
      {compatibilityStatus === "unverified" ? (
        <Badge variant="warning" className="normal-case">
          <TriangleAlert className="h-3 w-3" />
          {t("runtime:compatibilityUnverified")}
        </Badge>
      ) : null}
    </span>
  );
}
