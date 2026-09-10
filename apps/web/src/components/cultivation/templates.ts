import type { CultivationTemplateId } from "../../api/cultivation";

/** 可选的培养模板（阶段数由后端 `ProgramOut.stages_total` 透出，前端不重复维护）。 */
export const CULTIVATION_TEMPLATES: CultivationTemplateId[] = [
  "academic",
  "vocational",
  "self_taught",
];
