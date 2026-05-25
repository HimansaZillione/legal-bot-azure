import { useState } from "react";
import { CaseSelector } from "./components/CaseSelector";
import { ChatWindow } from "./components/ChatWindow";
import styles from "./App.module.css";

export default function App() {
  const [activeCaseId, setActiveCaseId] = useState("");

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandIcon}>⚖</span>
          <span className={styles.brandName}>LegalBot</span>
        </div>
        <CaseSelector selectedCase={activeCaseId} onSelect={setActiveCaseId} />
      </header>

      <main className={styles.main}>
        {activeCaseId ? (
          <ChatWindow caseId={activeCaseId} />
        ) : (
          <div className={styles.splash}>
            <div className={styles.splashIcon}>⚖</div>
            <h2 className={styles.splashTitle}>Legal Case Assistant</h2>
            <p className={styles.splashSub}>
              Select a case from the dropdown above to begin.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}