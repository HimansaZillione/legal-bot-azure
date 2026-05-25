import { useEffect, useState } from "react";
import { fetchCases } from "../api/client";
import styles from "./CaseSelector.module.css";


interface Props {
  selectedCase: string;
  onSelect: (caseId: string) => void;
}

export function CaseSelector({ selectedCase, onSelect }: Props) {
  const [cases, setCases] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetchCases()
      .then(setCases)
      .catch(() => setErr(true))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className={styles.wrapper}>
      <label className={styles.label} htmlFor="case-select">
        Active Case
      </label>
      <select
        id="case-select"
        className={styles.select}
        value={selectedCase}
        onChange={(e) => onSelect(e.target.value)}
        disabled={loading}
      >
        <option value="" disabled>
          {loading ? "Loading..." : err ? "Failed to load" : "Select a case..."}
        </option>
        {cases.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </div>
  );
}