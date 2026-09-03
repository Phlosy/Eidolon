export type PasswordIssue = "length" | "characterTypes";
export const PASSWORD_MIN_LENGTH = 8;

// Keep this set aligned with the explicit Python whitespace set in RegisterRequest.
// U+FEFF is intentionally absent: Python's str.isspace() treats it as a special character.
const PASSWORD_WHITESPACE_CHARACTERS = new Set(
  Array.from(
    "\u0009\u000A\u000B\u000C\u000D" +
      "\u001C\u001D\u001E\u001F" +
      "\u0020\u0085\u00A0\u1680" +
      "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A" +
      "\u2028\u2029\u202F\u205F\u3000",
  ),
);

export function passwordIssues(password: string): PasswordIssue[] {
  const issues: PasswordIssue[] = [];
  const characterTypeCount = [
    /[A-Za-z]/.test(password),
    /[0-9]/.test(password),
    Array.from(password).some(
      (character) =>
        !/[A-Za-z0-9]/.test(character) && !PASSWORD_WHITESPACE_CHARACTERS.has(character),
    ),
  ].filter(Boolean).length;

  if (Array.from(password).length < PASSWORD_MIN_LENGTH) issues.push("length");
  if (characterTypeCount < 2) issues.push("characterTypes");

  return issues;
}
