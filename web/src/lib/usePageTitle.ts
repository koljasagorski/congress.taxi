import { useEffect } from "react";

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} – Mitfahrer 40C3`;
  }, [title]);
}
