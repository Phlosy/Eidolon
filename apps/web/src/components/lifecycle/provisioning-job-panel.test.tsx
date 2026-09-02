import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProvisioningJob, ProvisioningStep } from "../../types";

// The panel talks to hooks; swap them for a controllable stub.
const mocks = vi.hoisted(() => ({
  job: null as ProvisioningJob | null,
  retryMutate: vi.fn(),
}));

vi.mock("../../hooks/useLifecycle", () => ({
  useProvisioningJob: () => ({
    isLoading: mocks.job == null,
    isError: false,
    error: null,
    data: mocks.job,
  }),
  useRetryProvisioningJob: () => ({
    mutate: mocks.retryMutate,
    isPending: false,
  }),
}));

import { ProvisioningJobPanel } from "./provisioning-job-panel";

function makeStep(overrides: Partial<ProvisioningStep> = {}): ProvisioningStep {
  return {
    id: 1,
    seq: 1,
    resource_type: "workspace",
    provider_key: "workspace:local",
    action: "create_workspace",
    description: "Create workspace",
    status: "done",
    attempts: 1,
    error: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function makeJob(overrides: Partial<ProvisioningJob> = {}): ProvisioningJob {
  return {
    id: 7,
    employee_id: 1,
    kind: "onboarding",
    status: "done",
    total_steps: 1,
    done_steps: 1,
    reason: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z",
    steps: [makeStep()],
    ...overrides,
  };
}

describe("ProvisioningJobPanel", () => {
  beforeEach(() => {
    mocks.job = null;
    mocks.retryMutate.mockClear();
  });

  it("shows steps with status icons and no retry for a clean done job", () => {
    mocks.job = makeJob();
    render(<ProvisioningJobPanel jobId={7} />);
    expect(screen.getByTestId("step-icon-done")).toBeInTheDocument();
    expect(screen.queryByTestId("job-retry")).not.toBeInTheDocument();
  });

  it("shows the retry button and the step error when a step failed", () => {
    mocks.job = makeJob({
      status: "partial",
      steps: [
        makeStep(),
        makeStep({ id: 2, seq: 2, status: "failed", error: "gitea not running" }),
      ],
    });
    render(<ProvisioningJobPanel jobId={7} />);
    expect(screen.getByTestId("step-icon-failed")).toBeInTheDocument();
    expect(screen.getByText("gitea not running")).toBeInTheDocument();
    const retry = screen.getByTestId("job-retry");
    retry.click();
    expect(mocks.retryMutate).toHaveBeenCalledWith(7);
  });

  it("shows the retry button for a failed job even without steps", () => {
    mocks.job = makeJob({ status: "failed", steps: [] });
    render(<ProvisioningJobPanel jobId={7} />);
    expect(screen.getByTestId("job-retry")).toBeInTheDocument();
  });
});
