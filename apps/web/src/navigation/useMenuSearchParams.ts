import { useContext, useMemo } from "react";
import { createSearchParams, useNavigate, useSearchParams as useRouterSearchParams, type SetURLSearchParams } from "react-router-dom";
import { MenuViewContext, useNavigation } from "./NavigationContext";
import { resolveViewChange, routeViewParams, widgetSearch } from "./routes";

/** Page selection is fixed by the route; query parameters contain only widget filters. */
export function useMenuSearchParams(): ReturnType<typeof useRouterSearchParams> {
  const [query, setQuery] = useRouterSearchParams();
  const current = useContext(MenuViewContext);
  const { items } = useNavigation();
  const navigate = useNavigate();
  const params = useMemo(() => {
    if (!current) return query;
    return routeViewParams(current, query);
  }, [query, current]);
  const setParams: SetURLSearchParams = (next, options) => {
    if (!current) { setQuery(next, options); return; }
    const desired = createSearchParams(typeof next === "function" ? next(params) : next);
    const target = resolveViewChange(items, current, desired);
    const search = widgetSearch(desired).toString();
    navigate({ pathname: target.path, search: search ? `?${search}` : "" }, options);
  };
  return [params, setParams];
}
