import { useCallback } from "react";
import { useApp } from "../context/AppContext";
import { translate } from "./translations";

export function useTranslation() {
  const { state } = useApp();
  const t = useCallback((key) => translate(state.language, key), [state.language]);
  return { t, lang: state.language };
}
