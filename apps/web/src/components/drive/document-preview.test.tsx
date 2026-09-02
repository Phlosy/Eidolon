import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { Document, Packer, Paragraph } from "docx";
import { describe, expect, it } from "vitest";
import { DocumentPreview } from "./document-preview";

describe("DocumentPreview", () => {
  it("converts an actual DOCX blob into a readable preview", async () => {
    const binary = await Packer.toBlob(
      new Document({ sections: [{ children: [new Paragraph("Eidolon DOCX preview")] }] }),
    );

    render(
      <DocumentPreview
        format="docx"
        markdown=""
        binary={binary}
        isLoading={false}
        isError={false}
        previewRef={createRef<HTMLDivElement>()}
      />,
    );

    expect(await screen.findByText("Eidolon DOCX preview")).toBeInTheDocument();
  });
});
