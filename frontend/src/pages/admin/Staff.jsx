import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import { toast } from "sonner";
import { DOMAIN_LABELS, propertyDomains } from "@/lib/domains";
import { screenInDomains } from "@/lib/sections";
import { formatPhone, hasAnIdentifier } from "@/lib/identity";
import PasswordInput from "@/components/app/PasswordInput";

const ROLES = ["admin", "manager", "front_desk", "waiter", "kitchen"];

const BLANK = {
  name: "",
  email: "",
  // Either identifier, and at least one of the two. A waiter or a kitchen hand very often
  // has no email address and always has a phone, and requiring an address is what made
  // owners invent `waiter1@fake.com` — which cannot receive a password reset and collides
  // with the identical invention at the property down the road.
  phone: "",
  password: "",
  role: "waiter",
  domains: ["restaurant"],
  permissions: [],
};

// "hotel" → "hotel", ["restaurant","bar"] → "restaurant or bar". What a screen needs, said
// the way the domain picker above says it.
function needs(screen) {
  const names = (screen.domains || []).map((d) => DOMAIN_LABELS[d]?.toLowerCase() || d);
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

// The catalogue comes back flat and in display order; the ticks are laid out by section,
// so group it here rather than assuming the sections or their order.
function bySection(catalogue) {
  const out = [];
  for (const screen of catalogue) {
    const found = out.find((g) => g.section === screen.section);
    if (found) found.screens.push(screen);
    else out.push({ section: screen.section, screens: [screen] });
  }
  return out;
}

/**
 * The screen ticks, laid out by section.
 *
 * Built from GET /api/permissions rather than a list written out here: the catalogue is
 * served precisely so this screen and the navigation cannot disagree about what exists.
 *
 * Two cases the API would otherwise refuse are said out loud instead:
 *
 * * a screen outside this person's work areas is 400 on save, because the domain check
 *   runs first at request time and the tick could never take effect — so it is disabled
 *   and labelled with what it would need, not silently dropped;
 * * an admin is never permission-checked, so their ticks decide nothing. Showing them
 *   ticked would claim a constraint that does not exist, so the whole block reads as not
 *   applicable for an admin.
 */
function ScreenPicker({ catalogue, value, onChange, role, domains }) {
  const isAdmin = role === "admin";
  const groups = bySection(catalogue);

  if (!catalogue.length) {
    return <p className="text-xs text-faint">Loading the screen list…</p>;
  }

  const toggle = (key) =>
    onChange(value.includes(key) ? value.filter((k) => k !== key) : [...value, key]);

  return (
    <div className={isAdmin ? "opacity-50" : ""}>
      {isAdmin && (
        <p className="text-xs text-brass/80 mb-4">
          An admin reaches every screen regardless of what is ticked, exactly as they are
          never checked against work areas. These boxes decide nothing here.
        </p>
      )}
      <div className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
        {groups.map(({ section, screens }) => {
          const grantable = screens.filter((s) => screenInDomains(s, domains));
          const allOn =
            grantable.length > 0 && grantable.every((s) => value.includes(s.key));
          const flip = () =>
            onChange(
              allOn
                ? value.filter((k) => !grantable.some((s) => s.key === k))
                : [...value, ...grantable.map((s) => s.key).filter((k) => !value.includes(k))],
            );
          return (
            <div key={section}>
              <div className="flex items-baseline justify-between mb-2">
                <div className="text-[11px] tracking-[0.2em] uppercase text-faint">
                  {section}
                </div>
                <button
                  type="button"
                  onClick={flip}
                  disabled={isAdmin || grantable.length === 0}
                  data-testid={`screens-all-${section.toLowerCase()}`}
                  className="text-[10px] tracking-widest uppercase text-faint hover:text-brass disabled:opacity-30"
                >
                  {allOn ? "Clear all" : "Select all"}
                </button>
              </div>
              <div className="space-y-1">
                {screens.map((s) => {
                  const available = screenInDomains(s, domains);
                  const on = !isAdmin && available && value.includes(s.key);
                  return (
                    <label
                      key={s.key}
                      title={available ? s.key : `Needs ${needs(s)}`}
                      className={`flex items-center gap-2 text-sm ${
                        available ? "text-muted2" : "text-faint"
                      }`}
                    >
                      <input
                        type="checkbox"
                        data-testid={`screen-${s.key}`}
                        checked={isAdmin ? false : on}
                        disabled={isAdmin || !available}
                        onChange={() => toggle(s.key)}
                        className="accent-brass disabled:opacity-40"
                      />
                      <span>{s.label}</span>
                      {!available && (
                        <span className="text-[10px] tracking-widest uppercase text-faint">
                          needs {needs(s)}
                        </span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The work areas somebody can be put in — this property's, not the vocabulary's.
 *
 * A restaurant with no rooms is refused a hotel-domain staff member by the API with a
 * 400, so offering the button would only ever produce an error toast. The list comes from
 * `GET /api/property`, never from the signed-in admin's own domains: an outlet property
 * and a hotel property whose owner happens to work in one area look identical from
 * `/auth/me`, and only one of them has a front desk.
 */
function DomainPicker({ options, value, onChange, disabled }) {
  const toggle = (d) =>
    onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d]);
  return (
    <div className="flex gap-2 flex-wrap">
      {options.map((d) => (
        <button
          key={d}
          type="button"
          disabled={disabled}
          onClick={() => toggle(d)}
          className={`text-[10px] tracking-widest uppercase border rounded-full px-3 py-1 disabled:opacity-40 ${
            value.includes(d)
              ? "border-brass text-brass"
              : "border-hairline-strong text-faint hover:border-hairline-strong"
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
  // What this place is, from GET /api/property. Both pickers below are drawn from it:
  // a restaurant owner is never shown a Hotel button to press or a rooms screen to tick,
  // because the API refuses both with a 400 and a control that can only fail is not a
  // control. Null while it is in flight, which reads as "nothing yet" — the pickers say
  // they are loading rather than briefly offering the whole vocabulary.
  const property = useProperty();
  const [rows, setRows] = useState([]);
  const [catalogue, setCatalogue] = useState([]); // GET /api/permissions
  const [creating, setCreating] = useState(BLANK);
  const [editing, setEditing] = useState(null); // { id, name, role, domains, permissions }
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

  // The catalogue is a constant on the server and never changes while this screen is
  // open, so it is fetched once rather than with every roster reload.
  useEffect(() => {
    api
      .get("/permissions")
      .then((r) => setCatalogue(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, []);

  // The work areas this property actually runs, and the screens that follow from them.
  // The catalogue is the same fifteen keys for every tenant — it is a constant in code —
  // so it is narrowed here rather than at the endpoint.
  const runs = propertyDomains(property);
  const grantable = catalogue.filter((s) => screenInDomains(s, runs));

  // Narrowing somebody's work areas has to narrow their ticks with it: a screen outside
  // them is a 400 on save, and leaving the box ticked would show a grant that the next
  // save refuses. Dropped here, where it is visible, rather than in the payload.
  //
  // Checked against the property as well as the person. Editing somebody who was ticked
  // for a screen this property no longer runs must drop it rather than send it back and
  // be refused — the same reason the server's own edit path drops what it cannot keep.
  const keepGrantable = (permissions, domains) =>
    permissions.filter((k) => {
      const screen = catalogue.find((s) => s.key === k);
      // A key the catalogue does not know is left alone: the server filters retired keys
      // on the way out, and second-guessing it here would drop a screen it still honours.
      if (!screen) return true;
      return screenInDomains(screen, runs) && screenInDomains(screen, domains);
    });

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
    if (!creating.name.trim()) {
      toast.error("A name is required");
      return;
    }
    // The either/or, said before the round trip rather than after it. The server refuses
    // the same thing with a 400 and its wording is the one that matters; this only saves
    // the owner a request to be told what the form already knows.
    if (!hasAnIdentifier(creating)) {
      toast.error(
        "Give an email address or a phone number — they need one of the two to sign in",
      );
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
      // An admin is never permission-checked and nothing is ticked for them, so the field
      // is left off entirely; sending [] would be somebody deliberately granting nothing.
      // For everyone else, no ticks means "the screens this role has always had" — the
      // server's own default — which is why an empty list is omitted rather than sent.
      const { permissions, email, phone, ...rest } = creating;
      const screens = keepGrantable(permissions, creating.domains);
      await api.post("/staff", {
        ...rest,
        // Omitted when blank, never sent as `""`. An empty string is not an absent
        // address: the API types the field `EmailStr | None`, so `""` is a 422 about a
        // malformed email for somebody who deliberately did not give one.
        ...(email.trim() ? { email: email.trim() } : {}),
        // Sent as typed. The canonical form is the server's to decide — a second
        // normaliser here is a second answer to a question that must have exactly one.
        ...(phone.trim() ? { phone: phone.trim() } : {}),
        ...(creating.role !== "admin" && screens.length ? { permissions: screens } : {}),
      });
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
    const screens = keepGrantable(editing.permissions, editing.domains);
    if (editing.role !== "admin" && screens.length === 0) {
      toast.error("Tick at least one screen — an account that reaches nothing is a mistake");
      return;
    }
    run(async () => {
      await api.put(`/staff/${editing.id}`, {
        name: editing.name,
        role: editing.role,
        domains: editing.domains,
        // Omitted for an admin, so their stored screens are carried over untouched rather
        // than being rewritten from boxes that decide nothing.
        ...(editing.role === "admin" ? {} : { permissions: screens }),
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
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Staff
      </h1>

      <div className="border border-hairline bg-surface rounded p-5 mb-8 max-w-4xl">
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
          Add a staff member
        </h2>
        <div className="flex flex-wrap gap-4 items-end">
          {[
            ["name", "Name", "text", ""],
            // Both are offered and neither is starred, because either one on its own is
            // enough and a screen that marked one required would be asking for the
            // invented address this change exists to end. The hints say what each is
            // *for*, which is the question an owner filling this in actually has.
            ["email", "Email", "email", "office staff, anyone with an address"],
            ["phone", "Phone", "tel", "waiters, kitchen — no email needed"],
            ["password", "Password", "password", ""],
          ].map(([k, label, type, hint]) => {
            // The admin is inventing this password for somebody else and then has to read
            // it out to them, so being able to see what was typed is the whole point of
            // the field. `block mt-2` moves to the wrapper: it is what the reveal button
            // is positioned against, and a margin left on the input would drop the icon
            // below the line.
            const password = type === "password";
            const Control = password ? PasswordInput : "input";
            return (
              <label key={k} className="text-xs tracking-widest uppercase text-faint">
                {label}
                <Control
                  {...(password
                    ? { label: "the new staff member's password", autoComplete: "new-password",
                        wrapperClassName: "block mt-2",
                        "data-testid": "staff-create-password" }
                    : { type, "data-testid": `staff-create-${k}` })}
                  value={creating[k]}
                  onChange={(e) => setCreating({ ...creating, [k]: e.target.value })}
                  className={`bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none ${
                    password ? "" : "block mt-2"
                  }`}
                />
                {hint && (
                  <span className="block mt-1 text-[10px] normal-case tracking-normal text-faint">
                    {hint}
                  </span>
                )}
              </label>
            );
          })}
          <label className="text-xs tracking-widest uppercase text-faint">
            Role
            <select
              value={creating.role}
              onChange={(e) => setCreating({ ...creating, role: e.target.value })}
              className="block mt-2 bg-ground border border-hairline-strong text-ink py-1 px-2 rounded"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <div className="text-xs tracking-widest uppercase text-faint">
            Works in
            <div className="mt-2">
              <DomainPicker
                options={runs}
                value={creating.domains}
                onChange={(d) =>
                  setCreating({
                    ...creating,
                    domains: d,
                    permissions: keepGrantable(creating.permissions, d),
                  })
                }
                disabled={creating.role === "admin"}
              />
            </div>
          </div>
          <button
            onClick={create}
            disabled={busy}
            className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Add
          </button>
        </div>

        <div className="mt-8 pt-6 border-t border-hairline">
          <div className="text-xs tracking-widest uppercase text-faint mb-4">
            Screens they can open
          </div>
          <ScreenPicker
            catalogue={grantable}
            value={creating.permissions}
            onChange={(p) => setCreating({ ...creating, permissions: p })}
            role={creating.role}
            domains={creating.domains}
          />
        </div>

        <p className="text-xs text-faint mt-6 max-w-3xl">
          <span className="text-muted2">
            An email address or a phone number — one is enough, and either one works at the
            sign-in box.
          </span>{" "}
          Give a phone for waiters and kitchen staff who have no email; give an address for
          anyone who does, or both for anyone you want reachable either way. A number can
          be typed however you like — 98765 43210, 098765 43210 and +91 98765 43210 are
          stored as the same number, so it does not matter which way you or they type it.
          Do not invent an address: it cannot receive a password reset, and an account with
          neither identifier is refused because nobody could ever sign into it.
        </p>
        <p className="text-xs text-faint mt-3 max-w-3xl">
          An admin reaches everything regardless of domains. Everyone else reaches only the
          areas selected here — enforced by the API, not just hidden in the menu. Leave every
          screen clear and they start with the ones their role has always had. Passwords are
          at least 8 characters.
        </p>
      </div>

      {/* Said here rather than discovered on the first 403. A manager ticked for Rates can
          read the rate sheet and will be refused the moment they change a figure. */}
      <p className="text-xs text-faint mb-8 max-w-3xl">
        <span className="text-muted2">Editing configuration is admin-only.</span> Ticking a
        screen grants the screen, not the right to change what is set up on it: rooms, room
        types, rates, seasons, meal plans, tax slabs, the menu and stock items can only be
        edited by an administrator, and anyone else gets a 403 saying so. Taking a booking,
        checking a guest in, opening and settling a bill are operational, not configuration,
        and follow the ticks as normal.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-faint">
              <th className="text-left py-2 px-3 border-b border-hairline">Name</th>
              {/* One column, not two. What matters to whoever is reading this roster is
                  what each person types to sign in, and half the rows would be empty in
                  whichever of the two columns they do not use. */}
              <th className="text-left py-2 px-3 border-b border-hairline">Signs in with</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Role</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Works in</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Screens</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Status</th>
              <th className="border-b border-hairline" />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => {
              const isSelf = me && u.id === me.id;
              return (
                <tr key={u.id} className={u.active ? "" : "opacity-50"}>
                  <td className="py-2 px-3 border-b border-hairline">
                    {u.name}
                    {isSelf && (
                      <span className="text-[10px] tracking-widest uppercase text-faint ml-2">
                        you
                      </span>
                    )}
                  </td>
                  {/* Both when they have both, because either one gets them in and an
                      owner resetting somebody's password needs to know which they use.
                      Never an empty cell: the API refuses an account with neither, so a
                      blank here would mean a record that predates that rule. */}
                  <td className="py-2 px-3 border-b border-hairline font-mono text-xs text-muted2">
                    {u.email && <span className="block">{u.email}</span>}
                    {u.phone && (
                      <span className="block text-faint">{formatPhone(u.phone)}</span>
                    )}
                    {!u.email && !u.phone && <span className="text-faint">—</span>}
                  </td>
                  <td className="py-2 px-3 border-b border-hairline">
                    {u.role.replace("_", " ")}
                  </td>
                  <td className="py-2 px-3 border-b border-hairline text-xs text-muted2">
                    {u.role === "admin" ? "everything" : (u.domains || []).join(", ") || "—"}
                  </td>
                  <td className="py-2 px-3 border-b border-hairline text-xs text-muted2 tabular-nums">
                    {u.role === "admin"
                      ? "every screen"
                      : `${(u.permissions || []).length} of ${grantable.length || "—"}`}
                  </td>
                  <td className="py-2 px-3 border-b border-hairline">
                    <span
                      className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${
                        u.active
                          ? "text-brass border-brass/40"
                          : "text-faint border-hairline-strong"
                      }`}
                    >
                      {u.active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="py-2 px-3 border-b border-hairline text-right whitespace-nowrap">
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
                          permissions: u.permissions || [],
                        })
                      }
                      disabled={busy || isSelf}
                      title={isSelf ? "You cannot change your own role or domains" : undefined}
                      className="text-[10px] tracking-widest uppercase text-faint hover:text-brass disabled:opacity-30 mr-3"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => setResetting({ id: u.id, name: u.name, password: "" })}
                      disabled={busy}
                      className="text-[10px] tracking-widest uppercase text-faint hover:text-brass disabled:opacity-30 mr-3"
                    >
                      Password
                    </button>
                    <button
                      onClick={() => setDeactivating(u)}
                      disabled={busy || isSelf}
                      title={isSelf ? "You cannot deactivate yourself" : undefined}
                      className="text-[10px] tracking-widest uppercase text-faint hover:text-red-400 disabled:opacity-30"
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
        <div className="mt-8 border border-hairline bg-surface rounded p-5 max-w-4xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
            Edit {editing.name}
          </h3>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-faint">
              Name
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className="block mt-2 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-faint">
              Role
              <select
                value={editing.role}
                onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                className="block mt-2 bg-ground border border-hairline-strong text-ink py-1 px-2 rounded"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <div className="text-xs tracking-widest uppercase text-faint">
              Works in
              <div className="mt-2">
                <DomainPicker
                  options={runs}
                  value={editing.domains}
                  onChange={(d) =>
                    setEditing({
                      ...editing,
                      domains: d,
                      permissions: keepGrantable(editing.permissions, d),
                    })
                  }
                  disabled={editing.role === "admin"}
                />
              </div>
            </div>
          </div>

          <div className="mt-8 pt-6 border-t border-hairline">
            <div className="text-xs tracking-widest uppercase text-faint mb-4">
              Screens they can open
            </div>
            <ScreenPicker
              catalogue={grantable}
              value={editing.permissions}
              onChange={(p) => setEditing({ ...editing, permissions: p })}
              role={editing.role}
              domains={editing.domains}
            />
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveEdit}
              disabled={busy}
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setEditing(null)}
              disabled={busy}
              className="border border-hairline-strong text-muted2 hover:text-ink disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
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
              : "border-hairline bg-surface"
          }`}
        >
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-2">
            {deactivating.active ? "Deactivate" : "Reactivate"} {deactivating.name}?
          </h3>
          <p
            className={`text-sm mb-4 ${
              deactivating.active ? "text-red-300" : "text-muted2"
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
                  : "bg-brass hover:bg-brass-deep text-white"
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
              className="border border-hairline-strong text-muted2 hover:border-hairline-strong disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Never mind
            </button>
          </div>
        </div>
      )}

      {resetting && (
        <div className="mt-8 border border-hairline bg-surface rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
            Set a new password for {resetting.name}
          </h3>
          {/* autoFocus stays on the *field*, never on the reveal button: the person
              opening this panel has come here to type. */}
          <PasswordInput
            autoFocus
            label={`the new password for ${resetting.name}`}
            autoComplete="new-password"
            data-testid="staff-reset-password"
            value={resetting.password}
            onChange={(e) => setResetting({ ...resetting, password: e.target.value })}
            placeholder="At least 8 characters"
            wrapperClassName="inline-block"
            className="bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={confirmReset}
              disabled={busy || resetting.password.length < 8}
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Setting…" : "Set password"}
            </button>
            <button
              onClick={() => setResetting(null)}
              disabled={busy}
              className="border border-hairline-strong text-muted2 hover:text-ink disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-faint mt-4">
            Tell them the new password directly — there is no email delivery in this app.
          </p>
        </div>
      )}
    </div>
  );
}
