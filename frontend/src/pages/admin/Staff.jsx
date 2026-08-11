import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
import { DOMAINS } from "@/lib/domains";

const ROLES = ["admin", "manager", "front_desk", "waiter", "kitchen"];

const BLANK = {
  name: "",
  email: "",
  password: "",
  role: "waiter",
  domains: ["restaurant"],
};

function DomainPicker({ value, onChange, disabled }) {
  const toggle = (d) =>
    onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d]);
  return (
    <div className="flex gap-2 flex-wrap">
      {DOMAINS.map((d) => (
        <button
          key={d}
          type="button"
          disabled={disabled}
          onClick={() => toggle(d)}
          className={`text-[10px] tracking-widest uppercase border rounded-full px-3 py-1 disabled:opacity-40 ${
            value.includes(d)
              ? "border-orange-500 text-orange-400"
              : "border-stone-700 text-stone-500 hover:border-stone-500"
          }`}
        >
          {d}
        </button>
      ))}
    </div>
  );
}

export default function Staff() {
  // The signed-in user comes from the auth context, which already holds the /auth/me
  // payload including the id. Reading it here rather than fetching /auth/me again means
  // the "you" row is known on the first render, so your own Edit and Deactivate are
  // never briefly clickable into the server's 409.
  const { user: me } = useAuth();
  const [rows, setRows] = useState([]);
  const [creating, setCreating] = useState(BLANK);
  const [editing, setEditing] = useState(null); // { id, name, role, domains }
  const [deactivating, setDeactivating] = useState(null); // the staff row being toggled
  const [resetting, setResetting] = useState(null); // { id, name, password }
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/staff")
        .then((r) => setRows(r.data))
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  // Client-side checks run before `run`, so the catch block above only ever handles real
  // API errors — nothing here fabricates an axios-shaped object to fall through it.
  const create = () => {
    if (!creating.name.trim() || !creating.email.trim()) {
      toast.error("Name and email are required");
      return;
    }
    if (creating.role !== "admin" && creating.domains.length === 0) {
      toast.error("Pick at least one work domain");
      return;
    }
    if (creating.password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    run(async () => {
      await api.post("/staff", creating);
      setCreating(BLANK);
      toast.success("Staff member added");
    });
  };

  const saveEdit = () => {
    if (!editing.name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (editing.role !== "admin" && editing.domains.length === 0) {
      toast.error("Pick at least one work domain");
      return;
    }
    run(async () => {
      await api.put(`/staff/${editing.id}`, {
        name: editing.name,
        role: editing.role,
        domains: editing.domains,
      });
      setEditing(null);
      toast.success("Saved");
    });
  };

  const confirmDeactivate = () =>
    run(async () => {
      await api.post(`/staff/${deactivating.id}/active`, {
        active: !deactivating.active,
      });
      toast.success(deactivating.active ? "Deactivated" : "Reactivated");
      setDeactivating(null);
    });

  const confirmReset = () => {
    if (resetting.password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    run(async () => {
      await api.post(`/staff/${resetting.id}/password`, {
        password: resetting.password,
      });
      setResetting(null);
      toast.success("Password reset");
    });
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Staff
      </h1>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-8 max-w-4xl">
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
          Add a staff member
        </h2>
        <div className="flex flex-wrap gap-4 items-end">
          {[
            ["name", "Name", "text"],
            ["email", "Email", "email"],
            ["password", "Password", "password"],
          ].map(([k, label, type]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type={type}
                value={creating[k]}
                onChange={(e) => setCreating({ ...creating, [k]: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Role
            <select
              value={creating.role}
              onChange={(e) => setCreating({ ...creating, role: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <div className="text-xs tracking-widest uppercase text-stone-500">
            Works in
            <div className="mt-2">
              <DomainPicker
                value={creating.domains}
                onChange={(d) => setCreating({ ...creating, domains: d })}
                disabled={creating.role === "admin"}
              />
            </div>
          </div>
          <button
            onClick={create}
            disabled={busy}
            className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Add
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-4">
          An admin reaches everything regardless of domains. Everyone else reaches only the
          areas selected here — enforced by the API, not just hidden in the menu. Passwords
          are at least 8 characters.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              <th className="text-left py-2 px-3 border-b border-stone-800">Name</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Email</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Role</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Works in</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Status</th>
              <th className="border-b border-stone-800" />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => {
              const isSelf = me && u.id === me.id;
              return (
                <tr key={u.id} className={u.active ? "" : "opacity-50"}>
                  <td className="py-2 px-3 border-b border-stone-800">
                    {u.name}
                    {isSelf && (
                      <span className="text-[10px] tracking-widest uppercase text-stone-500 ml-2">
                        you
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs text-stone-400">
                    {u.email}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800">
                    {u.role.replace("_", " ")}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 text-xs text-stone-400">
                    {u.role === "admin" ? "everything" : (u.domains || []).join(", ") || "—"}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800">
                    <span
                      className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${
                        u.active
                          ? "text-orange-400 border-orange-500/40"
                          : "text-stone-500 border-stone-700"
                      }`}
                    >
                      {u.active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right whitespace-nowrap">
                    {/* Edit and the active toggle are disabled on your own row: the API
                        answers both with a 409, so offering the control would only ever
                        produce an error toast. */}
                    <button
                      onClick={() =>
                        setEditing({
                          id: u.id,
                          name: u.name,
                          role: u.role,
                          domains: u.domains || [],
                        })
                      }
                      disabled={busy || isSelf}
                      title={isSelf ? "You cannot change your own role or domains" : undefined}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 disabled:opacity-30 mr-3"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => setResetting({ id: u.id, name: u.name, password: "" })}
                      disabled={busy}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 disabled:opacity-30 mr-3"
                    >
                      Password
                    </button>
                    <button
                      onClick={() => setDeactivating(u)}
                      disabled={busy || isSelf}
                      title={isSelf ? "You cannot deactivate yourself" : undefined}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-red-400 disabled:opacity-30"
                    >
                      {u.active ? "Deactivate" : "Reactivate"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {editing && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Edit {editing.name}
          </h3>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Name
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Role
              <select
                value={editing.role}
                onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <div className="text-xs tracking-widest uppercase text-stone-500">
              Works in
              <div className="mt-2">
                <DomainPicker
                  value={editing.domains}
                  onChange={(d) => setEditing({ ...editing, domains: d })}
                  disabled={editing.role === "admin"}
                />
              </div>
            </div>
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveEdit}
              disabled={busy}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setEditing(null)}
              disabled={busy}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Deactivating is destructive enough to need confirming, so it uses the same
          inline two-step panel as cancelling a booking and voiding a folio entry —
          never window.confirm. Reactivating is not destructive, so the same panel drops
          the red treatment when it is putting somebody back. */}
      {deactivating && (
        <div
          className={`mt-8 rounded p-5 max-w-2xl border ${
            deactivating.active
              ? "border-red-500/40 bg-red-950/20"
              : "border-stone-800 bg-stone-900"
          }`}
        >
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            {deactivating.active ? "Deactivate" : "Reactivate"} {deactivating.name}?
          </h3>
          <p
            className={`text-sm mb-4 ${
              deactivating.active ? "text-red-300" : "text-stone-400"
            }`}
          >
            {deactivating.active
              ? "They will be signed out and unable to log in. Their record stays, so past bills and folio entries still show who posted them."
              : "They will be able to log in again with their existing password."}
          </p>
          <div className="flex gap-3">
            <button
              onClick={confirmDeactivate}
              disabled={busy}
              className={`rounded-full px-6 py-2 text-sm tracking-widest uppercase disabled:opacity-50 ${
                deactivating.active
                  ? "bg-red-600 hover:bg-red-500 text-white"
                  : "bg-orange-600 hover:bg-orange-500 text-white"
              }`}
            >
              {busy
                ? "Working…"
                : deactivating.active
                  ? "Confirm deactivation"
                  : "Reactivate"}
            </button>
            <button
              onClick={() => setDeactivating(null)}
              disabled={busy}
              className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Never mind
            </button>
          </div>
        </div>
      )}

      {resetting && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Set a new password for {resetting.name}
          </h3>
          <input
            autoFocus
            type="password"
            value={resetting.password}
            onChange={(e) => setResetting({ ...resetting, password: e.target.value })}
            placeholder="At least 8 characters"
            className="bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={confirmReset}
              disabled={busy || resetting.password.length < 8}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Setting…" : "Set password"}
            </button>
            <button
              onClick={() => setResetting(null)}
              disabled={busy}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-stone-500 mt-4">
            Tell them the new password directly — there is no email delivery in this app.
          </p>
        </div>
      )}
    </div>
  );
}
