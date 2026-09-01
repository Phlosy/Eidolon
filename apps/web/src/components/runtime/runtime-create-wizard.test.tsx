import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { canAdvance, INITIAL_WIZARD_STATE, type WizardState } from "./wizard-steps";
import { RuntimeCreateWizard } from "./runtime-create-wizard";
import type { Provider, RuntimeCapabilities, RuntimeTypeInfo } from "../../types";

const ALL_CAPABILITIES: RuntimeCapabilities = {
  chat: true,
  task: true,
  filesystem: true,
  terminal: true,
  web: true,
  memory: true,
  skills: true,
  scheduler: true,
  streaming: true,
  artifacts: true,
};

function makeRuntimeType(overrides: Partial<RuntimeTypeInfo> = {}): RuntimeTypeInfo {
  return {
    type: "hermes",
    implemented: true,
    docker_available: true,
    deployment_modes: ["docker"],
    capabilities: ALL_CAPABILITIES,
    supported_providers: ["openai", "anthropic"],
    ...overrides,
  };
}

function makeProvider(): Provider {
  return {
    id: 1,
    name: "OpenAI Main",
    provider_type: "openai",
    base_url: null,
    scope: "company",
    owner_employee_id: null,
    enabled: true,
    has_credential: true,
    credential_mask: "sk-••••abcd",
    metadata: {},
    in_use_by: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const READY: WizardState = {
  ...INITIAL_WIZARD_STATE,
  runtimeType: "hermes",
  providerId: 1,
  model: "gpt-4o-mini",
};

describe("canAdvance", () => {
  const types = [makeRuntimeType()];

  it("blocks the type step until an implemented type is chosen", () => {
    expect(canAdvance("type", INITIAL_WIZARD_STATE, types)).toBe(false);
    expect(canAdvance("type", READY, types)).toBe(true);
    const unimplemented = [makeRuntimeType({ implemented: false })];
    expect(canAdvance("type", READY, unimplemented)).toBe(false);
  });

  it("blocks the deployment step when docker is unavailable", () => {
    const noDocker = [makeRuntimeType({ docker_available: false })];
    expect(canAdvance("deployment", READY, noDocker)).toBe(false);
    expect(canAdvance("deployment", READY, types)).toBe(true);
  });

  it("requires a provider, a model, and sane resources", () => {
    expect(canAdvance("provider", READY, types)).toBe(true);
    expect(canAdvance("provider", { ...READY, providerId: null }, types)).toBe(false);
    expect(canAdvance("model", { ...READY, model: "  " }, types)).toBe(false);
    expect(canAdvance("resources", { ...READY, memoryLimitMb: 64 }, types)).toBe(false);
    expect(canAdvance("resources", READY, types)).toBe(true);
  });
});

function renderWizard(runtimeTypes: RuntimeTypeInfo[]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RuntimeCreateWizard
        open
        onOpenChange={() => {}}
        employeeId={1}
        runtimeTypes={runtimeTypes}
        providers={[makeProvider()]}
      />
    </QueryClientProvider>,
  );
}

describe("RuntimeCreateWizard navigation", () => {
  it("walks through all steps and reaches the confirm step", () => {
    renderWizard([makeRuntimeType()]);

    // type
    const next = () => screen.getByTestId("wizard-next");
    expect(next()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Hermes/ }));
    expect(next()).toBeEnabled();
    fireEvent.click(next());

    // deployment
    expect(screen.queryByTestId("docker-unavailable")).not.toBeInTheDocument();
    fireEvent.click(next());

    // provider
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByTestId("provider-selector"), { target: { value: "1" } });
    expect(next()).toBeEnabled();
    fireEvent.click(next());

    // model
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByTestId("model-input"), { target: { value: "gpt-4o-mini" } });
    expect(next()).toBeEnabled();
    fireEvent.click(next());

    // resources
    expect(next()).toBeEnabled();
    fireEvent.click(next());

    // confirm
    expect(screen.getByTestId("wizard-create")).toHaveTextContent("Create runtime");
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
  });

  it("blocks create when docker is unavailable", () => {
    renderWizard([makeRuntimeType({ docker_available: false })]);
    fireEvent.click(screen.getByRole("button", { name: /Hermes/ }));
    fireEvent.click(screen.getByTestId("wizard-next"));
    expect(screen.getByTestId("docker-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("wizard-next")).toBeDisabled();
  });
});
