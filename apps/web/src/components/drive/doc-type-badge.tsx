import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { DOC_TYPE_FALLBACK_META, DOC_TYPE_META } from "./constants";
import type { DriveDocType } from "../../types";

/** Small tinted chip for a document's doc_type; renders nothing for folders (null). */
export function DocTypeBadge({ docType }: { docType: DriveDocType | null }) {
  const { t } = useTranslation();
  if (docType == null) return null;
  const meta = DOC_TYPE_META[docType] ?? DOC_TYPE_FALLBACK_META;
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        meta.chipClass,
      )}
    >
      <Icon className="h-3 w-3" />
      {enumLabel(t, "drive:docType", docType)}
    </span>
  );
}
