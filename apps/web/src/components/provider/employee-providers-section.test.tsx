import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmployeeProvidersSection } from "./employee-providers-section";
import type { Provider } from "../../types";

function makeProvider(overrides: Partial<Provider> = {}): Provider {
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
    ...overrides,
  };
}

const PROVIDERS = [
  makeProvider({ id: 1, name: "Company OpenRouter", scope: "company" }),
  makeProvider({
    id: 2,
    name: "Ada Anthropic",
    scope: "employee",
    owner_employee_id: 7,
    provider_type: "anthropic",
  }),
];

vi.mock("../../hooks/useProviders", () => ({
  useEmployeeProviders: () => ({
    data: PROVIDERS,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateEmployeeProvider: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useTestProvider: () => ({ mutate: vi.fn(), isPending: false, isSuccess: false }),
}));

describe("EmployeeProvidersSection", () => {
  it("renders the employee's accounts with scope badges", () => {
    render(<EmployeeProvidersSection employeeId={7} />);

    expect(screen.getByText("Company OpenRouter")).toBeInTheDocument();
    expect(screen.getByText("Ada Anthropic")).toBeInTheDocument();
    expect(screen.getByText("Company")).toBeInTheDocument();
    expect(screen.getByText("Employee Private")).toBeInTheDocument();
  });

  it("offers an Add Provider Account action", () => {
    render(<EmployeeProvidersSection employeeId={7} />);
    expect(screen.getByRole("button", { name: "Add Provider Account" })).toBeInTheDocument();
  });
});
