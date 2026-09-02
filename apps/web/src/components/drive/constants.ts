import {
  BookOpen,
  CheckCheck,
  Code2,
  File,
  FileArchive,
  FileText,
  FlaskConical,
  FolderKanban,
  Lightbulb,
  Network,
  Rocket,
  ScrollText,
  Sparkles,
  StickyNote,
  type LucideIcon,
} from "lucide-react";
import type { DriveDocType, DriveZone } from "../../types";

/**
 * Per-doc-type icon + tinted chip, reusing the artifact type color coding from
 * the old artifacts page (status tokens): prd=researching, research=learning,
 * architecture=meeting, source=working, test=reflecting, release=accent.
 */
export const DOC_TYPE_META: Record<DriveDocType, { icon: LucideIcon; chipClass: string }> = {
  prd: {
    icon: FileText,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  research_report: {
    icon: FlaskConical,
    chipClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  architecture: {
    icon: Network,
    chipClass: "border-status-meeting/25 bg-status-meeting/10 text-status-meeting",
  },
  source_file: {
    icon: Code2,
    chipClass: "border-status-working/25 bg-status-working/10 text-status-working",
  },
  test_report: {
    icon: CheckCheck,
    chipClass: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  },
  readme: {
    icon: BookOpen,
    chipClass: "border-border bg-muted text-muted-foreground",
  },
  release: { icon: Rocket, chipClass: "border-accent/25 bg-accent/10 text-accent" },
  note: { icon: StickyNote, chipClass: "border-border bg-muted text-muted-foreground" },
  knowledge: {
    icon: Lightbulb,
    chipClass: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  },
  skill_doc: {
    icon: Sparkles,
    chipClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  handbook: {
    icon: ScrollText,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  docx: {
    icon: FileText,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  pptx: {
    icon: FileArchive,
    chipClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  pdf: {
    icon: FileArchive,
    chipClass: "border-accent/25 bg-accent/10 text-accent",
  },
};

export const DOC_TYPE_FALLBACK_META = {
  icon: File,
  chipClass: "border-border bg-muted text-muted-foreground",
};

export const DRIVE_ZONES: DriveZone[] = ["projects", "knowledge", "skills", "handbook"];

/** Zone root presentation: distinct icon + tint (accent / amber / violet / blue). */
export const ZONE_META: Record<DriveZone, { icon: LucideIcon; tileClass: string }> = {
  projects: {
    icon: FolderKanban,
    tileClass: "border-accent/25 bg-accent/10 text-accent",
  },
  knowledge: {
    icon: BookOpen,
    tileClass: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  },
  skills: {
    icon: Sparkles,
    tileClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  handbook: {
    icon: ScrollText,
    tileClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
};
