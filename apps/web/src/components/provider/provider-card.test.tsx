import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { ProviderCard } from "./provider-card";
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

function renderCard(provider: Provider) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProviderCard provider={provider} onEdit={() => {}} onDelete={() => {}} />
    </QueryClientProvider>,
  );
}

describe("ProviderCard", () => {
  it("shows the masked credential, never a raw key", () => {
    renderCard(makeProvider());
    expect(screen.getByText(/sk-••••abcd/)).toBeInTheDocument();
  });

  it("shows the scope badge and in-use count", () => {
    renderCard(makeProvider({ scope: "employee", in_use_by: 3 }));
    expect(screen.getByText("Employee Private")).toBeInTheDocument();
    expect(screen.getByText("3 in use")).toBeInTheDocument();
  });

  it("disables delete when the provider is in use", () => {
    renderCard(makeProvider({ in_use_by: 2 }));
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("enables delete when the provider is unused", () => {
    renderCard(makeProvider({ in_use_by: 0 }));
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });
});
