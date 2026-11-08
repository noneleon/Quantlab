import { createContext, useContext, useEffect, useState } from "react";

import { ApiError, apiGet } from "./api";

export const TokenContext = createContext<string | null>(null);

export interface Loadable<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
}

/** GET an API path with the session token; re-fetches when the path changes. */
export function useApi<T>(path: string | null): Loadable<T> {
  const token = useContext(TokenContext);
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null, loading: path !== null });
  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    setState({ data: null, error: null, loading: true });
    apiGet<T>(path, token)
      .then((data) => !cancelled && setState({ data, error: null, loading: false }))
      .catch((error: Error) => !cancelled && setState({ data: null, error, loading: false }));
    return () => {
      cancelled = true;
    };
  }, [path, token]);
  return state;
}
