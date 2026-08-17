import { createContext, useContext } from "react";

/**
 * The section the signed-in person is working in, shared between the layout that renders
 * the sidebar and the pages inside it — the chooser sets it, the sidebar reads it.
 *
 * The provider lives in AppLayout rather than here, because the nav array it resolves
 * against lives there too and a context module that imported it would close a cycle.
 * This file is only the handle.
 *
 * `section` is null while nobody has chosen, which is a real state and not a loading one:
 * the sidebar is empty then, on purpose.
 */
const SectionContext = createContext({
  section: null,
  sections: [],
  setSection: () => {},
  pathFor: () => null,
});

export const SectionProvider = SectionContext.Provider;

export const useSection = () => useContext(SectionContext);
