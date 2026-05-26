import { useState, useCallback, useRef } from "react";
import { streamChat, Citation, ImageRef } from "../api/client";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
  citations?: Citation[];
  images?: ImageRef[];
}

export function useChat(caseId: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const historyRef = useRef<{ role: "user" | "assistant"; content: string }[]>([]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || !caseId || isLoading) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);
      setError(null);

      historyRef.current = [
        ...historyRef.current,
        { role: "user", content: text },
      ];

      const assistantId = crypto.randomUUID();
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          streaming: true,
          citations: [],
          images: [],
        },
      ]);

      try {
        let fullContent = "";

        for await (const chunk of streamChat(historyRef.current, caseId)) {
          if (chunk.type === "message" && chunk.content) {
            fullContent += chunk.content;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: fullContent, streaming: true }
                  : m
              )
            );
          }

          if (chunk.type === "completed_message" && chunk.content) {
            fullContent = chunk.content;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: chunk.content!, streaming: false }
                  : m
              )
            );
          }

          if (chunk.type === "citations" && chunk.citations) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, citations: chunk.citations }
                  : m
              )
            );
          }

          if (chunk.type === "images" && chunk.images) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, images: chunk.images }
                  : m
              )
            );
          }

          if (chunk.type === "stream_end") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, streaming: false } : m
              )
            );
          }
        }

        historyRef.current = [
          ...historyRef.current,
          { role: "assistant", content: fullContent },
        ];
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      } finally {
        setIsLoading(false);
      }
    },
    [caseId, isLoading]
  );

  const reset = useCallback(() => {
    setMessages([]);
    setError(null);
    historyRef.current = [];
  }, []);

  return { messages, isLoading, error, send, reset };
}