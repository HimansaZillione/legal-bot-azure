import { useEffect, useRef, useState, KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import { useChat } from "../hooks/useChat";
import styles from "./ChatWindow.module.css";

interface Props {
  caseId: string;
}

export function ChatWindow({ caseId }: Props) {
  const { messages, isLoading, error, send, reset } = useChat(caseId);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    reset();
  }, [caseId, reset]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    send(input.trim());
    setInput("");
  };

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className={styles.container}>
      <div className={styles.messageList}>
        {messages.length === 0 && (
          <div className={styles.empty}>Ask anything about {caseId}</div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`${styles.message} ${styles[msg.role]}`}>
            <div className={styles.bubble}>
              {msg.role === "assistant" ? (
                <ReactMarkdown>{msg.content}</ReactMarkdown>
              ) : (
                msg.content
              )}
              {msg.streaming && <span className={styles.cursor} />}
            </div>

            {/* Citations */}
            {msg.citations && msg.citations.length > 0 && !msg.streaming && (
              <div className={styles.citations}>
                <span className={styles.citationsLabel}>Sources</span>
                <div className={styles.citationList}>
                  {msg.citations.map((c) => (
                    <a
                      key={c.path}
                      href={`/api/document/${c.path}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={styles.citationLink}
                      title={c.title}
                    >
                      📄 {c.title}
                    </a>
                  ))}
                </div>
              </div>
            )}

            <div className={styles.timestamp}>
              {msg.timestamp.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </div>
          </div>
        ))}

        {isLoading && messages[messages.length - 1]?.content === "" && (
          <div className={`${styles.message} ${styles.assistant}`}>
            <div className={`${styles.bubble} ${styles.thinking}`}>
              <span /><span /><span />
            </div>
          </div>
        )}

        {error && <div className={styles.error}>{error}</div>}
        <div ref={bottomRef} />
      </div>

      <div className={styles.inputRow}>
        <textarea
          className={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder={`Ask about ${caseId}... (Enter to send, Shift+Enter for new line)`}
          disabled={isLoading}
          rows={1}
        />
        <button
          className={styles.sendBtn}
          onClick={handleSend}
          disabled={!input.trim() || isLoading}
        >
          Send
        </button>
      </div>
    </div>
  );
}