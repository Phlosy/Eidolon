/* eslint-disable react-refresh/only-export-components */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, type ReactNode } from "react";
import { auth as authApi } from "../../api";
import { ApiError } from "../../api/client";
import type { Me } from "../../api/types";

interface AuthContextValue {
  me: Me | null;
  isLoading: boolean;
  error: Error | null;
  setMe: (me: Me | null) => void;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
export const AUTH_QUERY_KEY = ["auth", "me"] as const;

/**
 * The session cookie is HttpOnly, but `eidolon_csrf` is set and cleared with it, so
 * its presence is a reliable local hint. Without it we skip the probe instead of
 * logging a 401 on every guest page load.
 */
export function hasSessionHint(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie.split("; ").some((entry) => entry.startsWith("eidolon_csrf="));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const setMe = useCallback(
    (me: Me | null) => {
      void queryClient.cancelQueries({ queryKey: AUTH_QUERY_KEY, exact: true });
      const previous = queryClient.getQueryData<Me | null>(AUTH_QUERY_KEY);
      if (previous?.user_id !== me?.user_id) {
        // another identity: nothing cached for the previous one may leak over
        queryClient.removeQueries({
          predicate: ({ queryKey }) => queryKey[0] !== AUTH_QUERY_KEY[0],
        });
      }
      queryClient.setQueryData(AUTH_QUERY_KEY, me);
    },
    [queryClient],
  );
  const query = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: async () => {
      try {
        return await authApi.me();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    enabled: hasSessionHint(),
    retry: 1,
    staleTime: 30_000,
  });
  return (
    <AuthContext.Provider
      value={{
        me: query.data ?? null,
        isLoading: query.isLoading,
        error: query.error,
        setMe,
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
