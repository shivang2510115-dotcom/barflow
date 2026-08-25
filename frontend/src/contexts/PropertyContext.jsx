import React, { createContext, useContext, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { DEFAULT_PROPERTY_TYPE } from "@/lib/domains";
import { readsOwnProperty } from "@/lib/tenancy";

/**
 * What is assumed when the read fails outright.
 *
 * Null means "not known yet", and everything downstream renders nothing for it — which is
 * right for the half-second before the answer lands and wrong forever. A request that
 * genuinely failed has to resolve to something, and `both` is what this console showed
 * before a property had a type at all: every section offered, and the API still refusing
 * whatever the caller may not have. The cost is one menu entry that could 403; the
 * alternative is a console that never fills in.
 *
 * There is no `status`, so the pending banner stays hidden — the same silence a failed
 * read produced before.
 */
export const UNREADABLE_PROPERTY = {
  property_type: DEFAULT_PROPERTY_TYPE,
  unreadable: true,
};

/**
 * The caller's own property, fetched once for the whole console.
 *
 * It used to be read inside PendingBanner, where the only question it answered was
 * whether to show a banner. It now decides the navigation, the section chooser and the
 * staff screen's pickers as well — because what a property *is* (a hotel, an outlet with
 * no rooms, or both) is a fact about the tenant, not about the person signed in. An
 * outlet property with a single-domain manager and a hotel property with the same
 * manager look identical from `/auth/me`, and only one of them has a front desk.
 *
 * So it is fetched once, in AppLayout, and handed down. Three components each calling
 * `useOwnProperty` would be three identical requests on every navigation, and — worse —
 * three moments at which the sidebar and the page disagreed about what this place is.
 */
const PropertyContext = createContext(null);

/**
 * The property, or null while it is unknown.
 *
 * Not asked for at all for the platform operator, who is refused it — see
 * `readsOwnProperty`. A failure is not shown to anybody: it resolves to
 * UNREADABLE_PROPERTY, above, rather than leaving the console stuck on "not known yet".
 */
export function useOwnProperty() {
  const { user } = useAuth();
  const [property, setProperty] = useState(null);
  const asks = readsOwnProperty(user);

  useEffect(() => {
    if (!asks) {
      setProperty(null);
      return;
    }
    let live = true;
    api
      .get("/property")
      .then((r) => live && setProperty(r.data))
      .catch(() => live && setProperty(UNREADABLE_PROPERTY));
    return () => {
      live = false;
    };
  }, [asks]);

  return property;
}

export function PropertyProvider({ value, children }) {
  return <PropertyContext.Provider value={value}>{children}</PropertyContext.Provider>;
}

/** The property the console is showing, or null while it is unknown. */
export const useProperty = () => useContext(PropertyContext);
