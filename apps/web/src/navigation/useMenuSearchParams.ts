import { useContext, useMemo } from "react";
import { useNavigate, useSearchParams as useRouterSearchParams, type SetURLSearchParams } from "react-router-dom";
import { MenuViewContext, useNavigation } from "./NavigationContext";
import { resolveViewChange, widgetSearch } from "./routes";

/** Page selection is fixed by the route; query parameters contain only widget filters. */
export function useMenuSearchParams(): ReturnType<typeof useRouterSearchParams> {
  const [query, setQuery] = useRouterSearchParams();
  const current = useContext(MenuViewContext);
  const { items } = useNavigation();
  const navigate = useNavigate();
  const params = useMemo(() => {
    if (!current) return query;
    const result = widgetSearch(query);
    for (const [key, value] of Object.entries(current.view_params)) result.set(key, value);
    return result;
  }, [query, current]);
  const setParams: SetURLSearchParams = (next, options) => {
    if (!current) { setQuery(next, options); return; }
    const desired = new URLSearchParams(typeof next === "function" ? next(params) : next);
    const target = resolveViewChange(items, current, desired);
    const search = widgetSearch(desired).toString();
    navigate({ pathname: target.path, search: search ? `?${search}` : "" }, options);
  };
  return [params, setParams];
}
