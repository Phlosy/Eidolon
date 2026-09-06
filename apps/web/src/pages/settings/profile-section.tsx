import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { Camera, Mail, UserRound } from "lucide-react";
import { requestEmailChange, updateProfile, uploadAvatar } from "../../api/auth";
import { useAuth } from "../../features/auth/auth-context";
import { Button } from "../../components/common/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { Input } from "../../components/common/input";

/** 个人资料：头像、显示名、登录邮箱。邮箱是登录标识，换绑需新邮箱验证。 */
export function ProfileSection() {
  const { t } = useTranslation();
  const { auth, setAuth } = useAuth();
  const [name, setName] = useState(auth?.user.display_name ?? "");
  const [message, setMessage] = useState("");
  const [emailMessage, setEmailMessage] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [avatarMessage, setAvatarMessage] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const save = useMutation({
    mutationFn: () => updateProfile({ display_name: name.trim() }),
    onSuccess: (state) => {
      setAuth(state);
      setMessage(t("settings:profile.saved"));
    },
    onError: () => setMessage(t("auth:errors.unknown")),
  });
  const avatar = useMutation({
    mutationFn: (file: File) => uploadAvatar(file),
    onSuccess: (state) => {
      setAuth(state);
      setAvatarMessage(t("settings:profile.avatarSaved"));
    },
    onError: (error) => setAvatarMessage(error.message),
  });
  const emailChange = useMutation({
    mutationFn: () => requestEmailChange(newEmail.trim()),
    onSuccess: (result) => {
      setNewEmail("");
      setEmailMessage(
        result.development_verification_token
          ? t("settings:profile.emailChangeSentDev")
          : t("settings:profile.emailChangeSent", { email: result.email }),
      );
    },
    onError: (error) => setEmailMessage(error.message),
  });

  if (!auth) return null;
  const { user } = auth;
  const initial = (user.display_name || user.email).trim().charAt(0).toUpperCase() || "?";
  const dirty = name.trim() !== user.display_name;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserRound className="h-4 w-4 text-primary" />
            {t("settings:profile.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              title={t("settings:profile.avatarUpload")}
              className="group relative flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/15 text-lg font-semibold text-primary"
            >
              {user.avatar ? (
                <img src={user.avatar} alt="" className="h-full w-full rounded-full object-cover" />
              ) : (
                initial
              )}
              <span className="absolute inset-0 flex items-center justify-center rounded-full bg-black/55 opacity-0 transition-opacity group-hover:opacity-100">
                <Camera className="h-5 w-5 text-white" />
              </span>
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  setAvatarMessage("");
                  avatar.mutate(file);
                }
                event.target.value = "";
              }}
            />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{user.display_name || user.email}</p>
              <p className="truncate text-xs text-muted-foreground">{user.email}</p>
              <p className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                {auth.membership.role}
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {avatarMessage || t("settings:profile.avatarHint")}
              </p>
            </div>
          </div>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              setMessage("");
              save.mutate();
            }}
          >
            <label className="block space-y-1.5">
              <span className="text-xs text-muted-foreground">
                {t("settings:profile.displayName")}
              </span>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={200}
                placeholder={t("settings:profile.displayNamePlaceholder")}
              />
            </label>
            <div className="flex items-center gap-3">
              <Button type="submit" size="sm" disabled={!dirty || save.isPending}>
                {t("settings:profile.save")}
              </Button>
              {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
            </div>
          </form>
          <div className="divide-y divide-border/60 border-t border-border/60">
            <div className="flex items-center justify-between gap-4 py-2">
              <span className="text-sm text-muted-foreground">{t("settings:profile.locale")}</span>
              <span className="font-mono text-xs">{user.locale}</span>
            </div>
            <div className="flex items-center justify-between gap-4 py-2">
              <span className="text-sm text-muted-foreground">
                {t("settings:profile.timezone")}
              </span>
              <span className="font-mono text-xs">{user.timezone}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-primary" />
            {t("settings:profile.changeEmail")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs leading-5 text-muted-foreground">
            {t("settings:profile.changeEmailHint", { email: user.email })}
          </p>
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setEmailMessage("");
              emailChange.mutate();
            }}
          >
            <Input
              type="email"
              required
              value={newEmail}
              onChange={(event) => setNewEmail(event.target.value)}
              placeholder={t("settings:profile.newEmailPlaceholder")}
              className="min-w-52 flex-1"
            />
            <Button
              type="submit"
              size="sm"
              variant="outline"
              disabled={!newEmail.trim() || emailChange.isPending}
            >
              {t("settings:profile.sendVerification")}
            </Button>
          </form>
          {emailMessage ? (
            <p className="text-xs text-muted-foreground" role="status">
              {emailMessage}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
