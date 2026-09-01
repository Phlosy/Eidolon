import { get } from "./client";
import type { Company } from "../types";

export function getCompany(): Promise<Company> {
  return get<Company>("/company");
}
