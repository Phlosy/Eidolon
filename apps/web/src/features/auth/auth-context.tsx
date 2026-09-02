/* eslint-disable react-refresh/only-export-components */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { getAuthState } from "../../api/auth";
import { ApiError } from "../../api/client";
import type { AuthState } from "../../types";

interface AuthContextValue {
  auth: AuthState | null;
  isLoading: boolean;
  error: Error | null;
  setAuth: (auth: AuthState | null) => void;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
export const AUTH_QUERY_KEY = ["auth", "me"] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: getAuthState,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 1,
    staleTime: 30_000,
  });
  return (
    <AuthContext.Provider
      value={{
        auth: query.data ?? null,
        isLoading: query.isLoading,
        error: query.error,
        setAuth: (auth) => queryClient.setQueryData(AUTH_QUERY_KEY, auth),
        refresh: query.refetch,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
