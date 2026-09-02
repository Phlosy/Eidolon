export type PasswordIssue = "length";

export function passwordIssues(password: string): PasswordIssue[] {
  return password.length >= 12 ? [] : ["length"];
}
