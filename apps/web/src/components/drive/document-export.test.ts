import { afterEach, describe, expect, it, vi } from "vitest";
import { driveDocumentFormat, exportDriveDocument, exportFormatsFor } from "./document-export";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("document export formats", () => {
  it("offers Markdown, DOCX, and PDF conversion for Markdown documents", () => {
    expect(driveDocumentFormat({ path: "drive/knowledge/guide.md", doc_type: "note" })).toBe(
      "markdown",
    );
    expect(exportFormatsFor("markdown")).toEqual(["markdown", "docx", "pdf"]);
  });

  it("keeps binary export options honest", () => {
    expect(driveDocumentFormat({ path: "drive/knowledge/brief.docx", doc_type: "docx" })).toBe(
      "docx",
    );
    expect(exportFormatsFor("docx")).toEqual(["docx", "pdf"]);
    expect(exportFormatsFor("pdf")).toEqual(["pdf"]);
  });

  it("downloads native Markdown and generates a real DOCX blob", async () => {
    const blobs: Blob[] = [];
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob);
        return "blob:test";
      }),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    await exportDriveDocument({
      name: "Launch.md",
      sourceFormat: "markdown",
      targetFormat: "markdown",
      markdown: "# Launch",
    });
    await exportDriveDocument({
      name: "Launch.md",
      sourceFormat: "markdown",
      targetFormat: "docx",
      markdown: "# Launch",
    });

    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(2);
    expect(blobs[0].type).toBe("text/markdown;charset=utf-8");
    expect(blobs[0].size).toBe(8);
    expect(blobs[1].type).toBe(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
    expect(blobs[1].size).toBeGreaterThan(1000);
  });
});
