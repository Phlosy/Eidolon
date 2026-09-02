import { ProjectIntakeWizard } from "./project-intake-wizard";

interface NewProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * New Project = placing a customer order (§5): posts {name, description}
 * to /api/v1/projects; the backend then kicks off the order_review flow.
 */
export function NewProjectDialog({ open, onOpenChange }: NewProjectDialogProps) {
  return <ProjectIntakeWizard open={open} onOpenChange={onOpenChange} />;
}
