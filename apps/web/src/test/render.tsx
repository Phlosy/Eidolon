import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../features/auth/auth-context";
import { CompanyProvider } from "../features/company/company-context";

/** Render a page the way the router does: providers, a route, no retries. */
export function renderPage(element: ReactNode, { path = "/", route = "/" } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <CompanyProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path={path} element={element} />
            </Routes>
          </MemoryRouter>
        </CompanyProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}
