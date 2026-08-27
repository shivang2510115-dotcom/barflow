import axios from "axios";

// Unset means same-origin, which is how the Firebase Hosting deployment works: Hosting
// serves this bundle and rewrites /api/** to the `api` function, so there is one
// origin and no cross-origin request at all. Left as `${undefined}/api` this built the
// literal string "undefined/api" and every call 404'd against the hosting rewrite.
//
// Local development sets it to http://127.0.0.1:8000 because the API is a separate
// process there; a split deployment (Render) sets it to the API's full origin, and then
// CORS_ORIGINS on the server has to name this one.
const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

export const TOKEN_KEY = "barflow_token";

export const api = axios.create({ baseURL: API });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export function formatApiErrorDetail(detail) {
  if (detail == null) return "Something went wrong. Please try again.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  if (detail && typeof detail.message === "string") {
    const dates = Array.isArray(detail.dates) && detail.dates.length
      ? ` (${detail.dates.join(", ")})`
      : "";
    return detail.message + dates;
  }
  return String(detail);
}

export function currency(v) {
  const n = Number(v || 0);
  // Sign outside the symbol: -₹200.00, not ₹-200.00. The latter reads as a typo,
  // and a refund line on a folio is exactly where it shows up.
  return `${n < 0 ? "-" : ""}₹${Math.abs(n).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
