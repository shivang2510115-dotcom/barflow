// Keep first: installs the offline demo transport before any module builds an axios
// client of its own. No effect unless REACT_APP_DEMO=1.
import "@/demo/autoInstall";

import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
// Before the first paint, so no screen flashes the wrong palette on load.
import { initTheme } from "@/lib/theme-choice";
import App from "@/App";

initTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
