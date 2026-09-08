import { del, get, patch, post, postFile } from "./client";
import type {
  AuthState,
  Passkey,
  RegisterInput,
  RegistrationResult,
  UserSession,
  WebAuthnOptions,
} from "../types";

export const getAuthState = () => get<AuthState>("/auth/me");
export const registerAccount = (input: RegisterInput) =>
  post<RegistrationResult>("/auth/register", input);
export const verifyEmail = (token: string) => post<AuthState>("/auth/verify-email", { token });
/** 账户安全操作（改密码/改邮箱/注销）与注册验证重发，都只是"发起"：邮件确认后才生效。 */
export interface AccountActionRequestResult {
  email: string;
  verification_required: boolean;
  development_verification_token: string | null;
}
/** 重发注册验证邮件：后端 60s 限频，旧链接作废。 */
export const resendVerificationEmail = (email: string) =>
  post<AccountActionRequestResult>("/auth/verify-email/resend", { email });
export const loginWithPassword = (identifier: string, password: string) =>
  post<AuthState>("/auth/login", {
    [identifier.includes("@") ? "email" : "username"]: identifier,
    password,
  });
export const logout = () => post<void>("/auth/logout");
export const logoutAll = () => post<void>("/auth/logout-all");
export const updateProfile = (input: { display_name: string }) =>
  patch<AuthState>("/auth/me", input);
export const requestAccountDeletion = () => post<AccountActionRequestResult>("/auth/me/delete");
export const changePassword = (input: { current_password: string; new_password: string }) =>
  post<AccountActionRequestResult>("/auth/me/password", input);
export const requestEmailChange = (email: string) =>
  post<AccountActionRequestResult>("/auth/me/email", { new_email: email });
export const confirmAccountAction = (token: string) =>
  post<{ action: string; email: string | null }>("/auth/account-actions/confirm", { token });
export const uploadAvatar = (file: File) => postFile<AuthState>("/auth/me/avatar", file, file.type);
export const getSessions = () => get<UserSession[]>("/auth/sessions");
export const revokeSession = (id: number) => del<void>(`/auth/sessions/${id}`);
export const getPasskeys = () => get<Passkey[]>("/auth/passkeys");
export const createPasskeyRegistrationOptions = (name: string) =>
  post<WebAuthnOptions>("/auth/passkeys/registration/options", { name });
export const verifyPasskeyRegistration = (challengeId: number, credential: unknown) =>
  post<Passkey>("/auth/passkeys/registration/verify", {
    challenge_id: challengeId,
    credential,
  });
export const createPasskeyAuthenticationOptions = () =>
  post<WebAuthnOptions>("/auth/passkeys/authentication/options");
export const verifyPasskeyAuthentication = (challengeId: number, credential: unknown) =>
  post<AuthState>("/auth/passkeys/authentication/verify", {
    challenge_id: challengeId,
    credential,
  });
export const renamePasskey = (id: number, name: string) =>
  patch<Passkey>(`/auth/passkeys/${id}`, { name });
export const removePasskey = (id: number) => del<void>(`/auth/passkeys/${id}`);
