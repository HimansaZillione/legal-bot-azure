const BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface Citation {
  title: string;
  path: string;
}

export interface ImageRef {
  path: string;
  title: string;
}

export interface StreamChunk {
  type: "message" | "completed_message" | "stream_end" | "citations" | "images";
  content?: string;
  citations?: Citation[];
  images?: ImageRef[];
}

export async function fetchCases(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/api/cases`);
  if (!res.ok) throw new Error("Failed to fetch cases");
  const data = await res.json();
  return data.cases as string[];
}

export async function uploadSupportingImage(
  caseId: string,
  file: File,
  title: string,
  tags: string
): Promise<{ path: string; title: string; tags: string[] }> {
  const form = new FormData();
  form.append("case_id", caseId);
  form.append("title", title);
  form.append("tags", tags);
  form.append("file", file);

  const res = await fetch(`${BASE_URL}/api/images/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as any).detail || "Upload failed");
  }
  return res.json();
}

export async function* streamChat(
  messages: Message[],
  caseId: string
): AsyncGenerator<StreamChunk> {
  const res = await fetch(`${BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, case_id: caseId }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as any).detail || "Request failed");
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const chunk = JSON.parse(line.slice(6)) as StreamChunk;
          yield chunk;
        } catch {
          // skip malformed
        }
      }
    }
  }
}