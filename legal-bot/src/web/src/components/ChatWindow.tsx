import { useEffect, useRef, useState, KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import { useChat } from "../hooks/useChat";
import { uploadSupportingImage } from "../api/client";
import styles from "./ChatWindow.module.css";

interface Props {
  caseId: string;
}

export function ChatWindow({ caseId }: Props) {
  const { messages, isLoading, error, send, reset } = useChat(caseId);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { reset(); }, [caseId, reset]);

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

  const handleUploadSubmit = async () => {
    if (!uploadFile) return;
    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);
    try {
      const result = await uploadSupportingImage(caseId, uploadFile, uploadTitle, uploadTags);
      setUploadSuccess(`Uploaded: ${result.title}`);
      setUploadFile(null);
      setUploadTitle("");
      setUploadTags("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleUploadClose = () => {
    setUploadOpen(false);
    setUploadFile(null);
    setUploadTitle("");
    setUploadTags("");
    setUploadError(null);
    setUploadSuccess(null);
  };

  return (
    <div className={styles.container}>

      {uploadOpen && (
        <div className={styles.modalOverlay}>
          <div className={styles.modal}>
            <div className={styles.modalHeader}>
              <span>Upload supporting image</span>
              <button className={styles.modalClose} onClick={handleUploadClose}>✕</button>
            </div>
            <div className={styles.modalBody}>
              <label className={styles.fieldLabel}>Image file</label>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp"
                className={styles.fileInput}
                onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
              />
              <label className={styles.fieldLabel}>Title / description</label>
              <input
                type="text"
                className={styles.textInput}
                placeholder="e.g. Front view of disputed land parcel"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
              />
              <label className={styles.fieldLabel}>Tags (optional, comma-separated)</label>
              <input
                type="text"
                className={styles.textInput}
                placeholder="e.g. land, exterior, north entrance"
                value={uploadTags}
                onChange={(e) => setUploadTags(e.target.value)}
              />
              {uploadError && <div className={styles.uploadError}>{uploadError}</div>}
              {uploadSuccess && <div className={styles.uploadSuccess}>{uploadSuccess}</div>}
            </div>
            <div className={styles.modalFooter}>
              <button
                className={styles.sendBtn}
                onClick={handleUploadSubmit}
                disabled={!uploadFile || uploading}
              >
                {uploading ? "Uploading…" : "Upload"}
              </button>
              <button className={styles.cancelBtn} onClick={handleUploadClose}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

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

            {msg.images && msg.images.length > 0 && !msg.streaming && (
              <div className={styles.imageGallery}>
                <span className={styles.citationsLabel}>Supporting images</span>
                <div className={styles.imageGrid}>
                  {msg.images.map((img) => (
                    <a
                      key={img.path}
                      href={`/api/document/${img.path}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={styles.imageThumb}
                      title={img.title}
                    >
                      <img
                        src={`/api/document/${img.path}`}
                        alt={img.title}
                        loading="lazy"
                        className={styles.thumbImg}
                      />
                      <span className={styles.thumbLabel}>{img.title}</span>
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
        <button
          className={styles.uploadBtn}
          onClick={() => setUploadOpen(true)}
          title="Upload supporting image"
        >
          📎
        </button>
        <textarea
          className={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder={`Ask about ${caseId}… (Enter to send, Shift+Enter for new line)`}
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