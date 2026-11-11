/**
 * POST /api/chat/document — upload + parse document, return extracted text
 *
 * Receives multipart/form-data with a "file" field.
 * Returns: { text, filename, fileType, truncated, preview, charCount }
 *
 * The frontend can then send this text to /api/chat as part of the
 * messages array for the LLM to analyze.
 *
 * Supported formats:
 *   PDF (.pdf), Word (.docx, .doc), Excel (.xlsx, .xls),
 *   Text (.txt), CSV (.csv), Markdown (.md), JSON (.json), RTF (.rtf)
 */

import { NextResponse } from "next/server";
import { parseDocument, SUPPORTED_EXTENSIONS } from "../../document-parser";

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") || "";

  if (!contentType.includes("multipart/form-data")) {
    return NextResponse.json(
      { error: "Content-Type debe ser multipart/form-data" },
      { status: 400 }
    );
  }

  try {
    const formData = await request.formData();
    const file = formData.get("file");

    if (!file || !(file instanceof File)) {
      return NextResponse.json(
        { error: "No se encontró ningún archivo. Usa el campo 'file' en el form-data." },
        { status: 400 }
      );
    }

    // Check file size
    if (file.size > MAX_FILE_SIZE) {
      return NextResponse.json(
        { error: `El archivo es demasiado grande (${(file.size / 1024 / 1024).toFixed(1)} MB). Máximo: 10 MB.` },
        { status: 413 }
      );
    }

    // Check extension
    const filename = file.name || "documento";
    const ext = filename.toLowerCase().substring(filename.lastIndexOf("."));
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      return NextResponse.json(
        { error: `Formato no soportado: ${ext}. Formatos aceptados: ${SUPPORTED_EXTENSIONS.join(", ")}` },
        { status: 400 }
      );
    }

    // Read file content
    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);

    // Parse document
    const result = await parseDocument(buffer, filename, file.type || "application/octet-stream");

    // Return extracted text with a preview
    const preview = result.text.slice(0, 500) + (result.text.length > 500 ? "..." : "");

    return NextResponse.json({
      text: result.text,
      filename: result.filename,
      fileType: result.fileType,
      truncated: result.truncated,
      preview,
      charCount: result.text.length,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Error desconocido procesando el archivo";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
