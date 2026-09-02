import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvisioningPreviewList } from "./provisioning-preview";
import type { ProvisioningPreviewStep } from "../../types";

function makeStep(overrides: Partial<ProvisioningPreviewStep> = {}): ProvisioningPreviewStep {
  return {
    resource_type: "workspace",
    provider_key: "workspace:local",
    action: "create_workspace",
    description: "Create private workspace",
    available: true,
    ...overrides,
  };
}

describe("ProvisioningPreviewList", () => {
  it("marks available steps with a check and no warning", () => {
    render(<ProvisioningPreviewList steps={[makeStep()]} />);
    expect(screen.getByTestId("preview-step-available")).toBeInTheDocument();
    expect(screen.queryByTestId("preview-step-unavailable")).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-unavailable-warning")).not.toBeInTheDocument();
  });

  it("marks unavailable steps with a warning marker and an aggregate warning", () => {
    render(
      <ProvisioningPreviewList
        steps={[
          makeStep(),
          makeStep({
            resource_type: "git",
            provider_key: "git:gitea",
            description: "Create git account",
            available: false,
          }),
        ]}
      />,
    );
    const steps = screen.getAllByTestId("preview-step");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveAttribute("data-available", "true");
    expect(steps[1]).toHaveAttribute("data-available", "false");
    expect(screen.getByTestId("preview-step-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("preview-unavailable-warning")).toBeInTheDocument();
  });
});
