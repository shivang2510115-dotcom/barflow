import React, { useMemo, useRef, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { AlertTriangle, Check, Download, Upload, X } from "lucide-react";
import { toast } from "sonner";

// Kept in step with services/inventory_import.py::MAX_UPLOAD_BYTES. Checked here purely
// so a 40 MB file is refused before it is sent rather than after; the server refuses it
// again either way, and the server is the one that decides.
const MAX_UPLOAD_BYTES = 1024 * 1024;
const MAX_UPLOAD_LABEL = "1 MB";

// What the review screen says each row is going to do. The wording is deliberately plain
// — this is read once, in a hurry, by an owner who is about to change every stock figure
// they have.
const KIND_LABEL = {
  new: "New item",
  update: "Updates existing",
  duplicate: "Duplicate in file",
};

const KIND_CLASS = {
  new: "text-emerald-400",
  update: "text-sky-400",
  duplicate: "text-yellow-400",
};

const num = (v) => (v === "" || v === null || v === undefined ? "" : v);

/**
 * The reviewed bulk upload: pick a file, read what it *would* do, correct it, apply it.
 *
 * The screen exists because a supplier's spreadsheet is not a stock list: names are
 * misspelt, the same item appears twice, quantities are in cases where this system counts
 * bottles. Nothing here writes until Apply is pressed, and Apply sends the rows as the
 * admin left them — not as the file had them.
 *
 * Cell-level validation is deliberately NOT reimplemented here. When a cell is edited its
 * error is cleared optimistically and the server re-parses every value on apply by the
 * same rules it read the file with, refusing the whole import and naming the rows if any
 * of them are still wrong. One set of rules, in one language, in one place — a second copy
 * in JavaScript would drift, and the direction it drifts is "the browser said this was
 * fine".
 */
export default function InventoryImport({ items, onApplied }) {
  const fileInput = useRef(null);
  const [filename, setFilename] = useState("");
  const [report, setReport] = useState(null);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const byId = useMemo(() => Object.fromEntries(items.map((i) => [i.id, i])), [items]);
  const sorted = useMemo(
    () => [...items].sort((a, b) => a.name.localeCompare(b.name)),
    [items],
  );

  const blocked = rows.filter((r) => r.target !== "skip" && r.errors.length);
  const willWrite = rows.filter((r) => r.target !== "skip");
  const counts = {
    create: willWrite.filter((r) => r.target === "").length,
    update: willWrite.filter((r) => r.target !== "").length,
    skip: rows.length - willWrite.length,
  };

  const reset = () => {
    setReport(null);
    setRows([]);
    setFilename("");
    if (fileInput.current) fileInput.current.value = "";
  };

  const downloadTemplate = async () => {
    try {
      // Fetched rather than linked: the endpoint is behind the session, and a bare <a>
      // carries no Authorization header, so a plain link would download a 401.
      const res = await api.get("/inventory/import/template", { responseType: "blob" });
      const url = URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "barflow-stock-template.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail));
    }
  };

  const pick = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setOutcome(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(`That file is larger than ${MAX_UPLOAD_LABEL}. Export just the stock rows as CSV.`);
      reset();
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.post("/inventory/import/preview", form);
      setFilename(file.name);
      setReport(res.data);
      setRows(
        res.data.rows.map((r) => ({
          ...r,
          // One control decides all three outcomes: "" creates, "skip" drops, and an item
          // id updates that item. A duplicate starts dropped — keeping both rows is
          // almost never what was meant — but it is a visible default the admin can undo,
          // not a silent one.
          target: r.kind === "duplicate" ? "skip" : r.kind === "update" ? r.item_id : "",
        })),
      );
    } catch (e) {
      reset();
      toast.error(formatApiErrorDetail(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const edit = (rowNumber, column, value) =>
    setRows((current) =>
      current.map((r) =>
        r.row === rowNumber
          ? { ...r, [column]: value, errors: r.errors.filter((e) => e.column !== column) }
          : r,
      ),
    );

  const retarget = (rowNumber, target) =>
    setRows((current) =>
      current.map((r) => (r.row === rowNumber ? { ...r, target } : r)),
    );

  const apply = async () => {
    setBusy(true);
    setOutcome(null);
    try {
      const res = await api.post("/inventory/import/apply", {
        rows: rows.map((r) => ({
          row: r.row,
          name: r.name,
          unit: r.unit,
          stock: r.stock,
          threshold: r.threshold,
          cost_per_unit: r.cost_per_unit,
          category: r.category,
          action: r.target === "skip" ? "skip" : r.target === "" ? "create" : "update",
          item_id: r.target === "skip" || r.target === "" ? null : r.target,
        })),
      });
      setOutcome(res.data);
      if (res.data.complete) {
        toast.success(
          `${res.data.created} created, ${res.data.updated} updated, ${res.data.skipped} skipped`,
        );
        reset();
      } else {
        // Never a success toast for a partial write. The panel stays open holding the
        // list of rows that did not land.
        toast.error(
          `${res.data.created + res.data.updated} row(s) written, ${res.data.failed.length} failed`,
        );
      }
      onApplied?.();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const refused = detail?.rows || [];
      if (refused.length) {
        // The server refused the whole thing and wrote nothing. Put each reason back on
        // the row it belongs to, so the fix is where the mistake is.
        setRows((current) =>
          current.map((r) => {
            const hit = refused.find((f) => f.row === r.row);
            return hit
              ? { ...r, errors: [...r.errors, { column: "", value: "", message: hit.message }] }
              : r;
          }),
        );
      }
      toast.error(formatApiErrorDetail(detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-8 border border-stone-800 bg-stone-900/40">
      <div className="p-4 flex flex-wrap items-center gap-3 border-b border-stone-800">
        <div className="mr-auto">
          <div className="text-[10px] uppercase tracking-[0.3em] font-mono text-orange-500">
            Bulk import
          </div>
          <p className="text-xs text-stone-500 mt-1">
            CSV or Excel (.xlsx), up to {MAX_UPLOAD_LABEL}. Nothing is saved until you
            review it and press Apply.
          </p>
        </div>
        <button
          type="button"
          onClick={downloadTemplate}
          data-testid="inv-import-template"
          className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 py-2 px-4 text-[10px] font-mono uppercase tracking-widest"
        >
          <Download size={12} /> Template
        </button>
        <label className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 py-2 px-4 text-[10px] font-mono uppercase tracking-widest cursor-pointer">
          <Upload size={12} /> {report ? "Choose another" : "Choose file"}
          <input
            ref={fileInput}
            data-testid="inv-import-file"
            type="file"
            accept=".csv,.xlsx,.xlsm,text/csv"
            onChange={pick}
            className="hidden"
          />
        </label>
      </div>

      {outcome && (
        <div
          className={`p-4 border-b border-stone-800 text-sm ${outcome.complete ? "text-emerald-400" : "text-red-400"}`}
          data-testid="inv-import-outcome"
        >
          <div className="font-mono text-xs uppercase tracking-widest">
            {outcome.created} created · {outcome.updated} updated · {outcome.skipped} skipped
            {outcome.complete ? "" : ` · ${outcome.failed.length} failed`}
          </div>
          {!outcome.complete && (
            <ul className="mt-2 space-y-1 text-xs text-stone-400">
              <li className="text-red-400">
                These rows were not written. Everything above them was.
              </li>
              {outcome.failed.map((f) => (
                <li key={f.row}>
                  Row {f.row} — {f.name}: {f.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {report?.file_errors?.length > 0 && (
        <div className="p-4 text-sm text-red-400" data-testid="inv-import-file-errors">
          {report.file_errors.map((message) => (
            <p key={message} className="flex gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              {message}
            </p>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="p-4 flex flex-wrap items-center gap-4 text-[10px] font-mono uppercase tracking-widest border-b border-stone-800">
            <span className="text-stone-500">{filename}</span>
            <span className="text-emerald-400">{counts.create} to create</span>
            <span className="text-sky-400">{counts.update} to update</span>
            <span className="text-stone-500">{counts.skip} skipped</span>
            {blocked.length > 0 && (
              <span className="text-red-400" data-testid="inv-import-blocked">
                {blocked.length} with problems
              </span>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-stone-800 text-[10px] uppercase tracking-widest font-mono text-stone-500">
                  <th className="p-3">Row</th>
                  <th className="p-3">Item</th>
                  <th className="p-3">Unit</th>
                  <th className="p-3">Stock</th>
                  <th className="p-3">Alert @</th>
                  <th className="p-3">Cost</th>
                  <th className="p-3">Category</th>
                  <th className="p-3">What happens</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const target = r.target === "skip" || r.target === "" ? null : byId[r.target];
                  const problems = r.target !== "skip" ? r.errors : [];
                  const cell = (column, type = "text") => (
                    <input
                      data-testid={`inv-import-${r.row}-${column}`}
                      type={type}
                      value={num(r[column])}
                      onChange={(e) => edit(r.row, column, e.target.value)}
                      className={`bg-transparent border-b py-1 w-full ${
                        problems.some((p) => p.column === column)
                          ? "border-red-500 text-red-400"
                          : "border-stone-700 focus-neon"
                      }`}
                    />
                  );
                  return (
                    <tr
                      key={r.row}
                      data-testid={`inv-import-row-${r.row}`}
                      className={`border-b border-stone-800/60 align-top ${
                        r.target === "skip" ? "opacity-40" : ""
                      }`}
                    >
                      <td className="p-3 font-mono text-xs text-stone-500">{r.row}</td>
                      <td className="p-3 min-w-[12rem]">{cell("name")}</td>
                      <td className="p-3 w-24">{cell("unit")}</td>
                      <td className="p-3 w-32">
                        {cell("stock")}
                        {target && (
                          <div className="mt-1 font-mono text-[10px] text-stone-500">
                            {target.stock} → <span className="text-sky-400">{num(r.stock)}</span>
                          </div>
                        )}
                      </td>
                      <td className="p-3 w-24">{cell("threshold")}</td>
                      <td className="p-3 w-32">
                        {cell("cost_per_unit")}
                        {target && (
                          <div className="mt-1 font-mono text-[10px] text-stone-500">
                            {currency(target.cost_per_unit)} →{" "}
                            <span className="text-sky-400">{currency(r.cost_per_unit)}</span>
                          </div>
                        )}
                      </td>
                      <td className="p-3 w-28">{cell("category")}</td>
                      <td className="p-3 min-w-[16rem]">
                        <select
                          data-testid={`inv-import-${r.row}-target`}
                          value={r.target}
                          onChange={(e) => retarget(r.row, e.target.value)}
                          className="bg-stone-900 border border-stone-700 py-1 px-2 text-xs w-full"
                        >
                          <option value="">Create as a new item</option>
                          <option value="skip">Drop this row</option>
                          <optgroup label="Update existing stock">
                            {sorted.map((i) => (
                              <option key={i.id} value={i.id}>
                                {i.name}
                              </option>
                            ))}
                          </optgroup>
                        </select>
                        <div className={`mt-1 font-mono text-[10px] uppercase tracking-widest ${KIND_CLASS[r.kind]}`}>
                          {KIND_LABEL[r.kind]}
                          {r.duplicate_of ? ` of row ${r.duplicate_of}` : ""}
                        </div>
                        {problems.map((p, index) => (
                          <div
                            key={index}
                            data-testid={`inv-import-${r.row}-problem`}
                            className="mt-1 text-[11px] text-red-400"
                          >
                            {p.message}
                          </div>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="p-4 flex flex-wrap items-center gap-3 border-t border-stone-800">
            {blocked.length > 0 && (
              <p className="text-xs text-red-400 mr-auto flex items-center gap-2">
                <AlertTriangle size={14} />
                {blocked.length} row(s) cannot be imported as they are. Fix the cell, or
                drop the row.
              </p>
            )}
            {blocked.length === 0 && (
              <p className="text-xs text-stone-500 mr-auto">
                {counts.create + counts.update} row(s) will be written. Nothing has been
                saved yet.
              </p>
            )}
            <button
              type="button"
              onClick={reset}
              className="flex items-center gap-2 border border-stone-700 hover:border-stone-500 py-2 px-4 text-[10px] font-mono uppercase tracking-widest"
            >
              <X size={12} /> Cancel
            </button>
            <button
              type="button"
              onClick={apply}
              data-testid="inv-import-apply"
              disabled={busy || blocked.length > 0 || counts.create + counts.update === 0}
              className="flex items-center gap-2 rounded-full bg-orange-600 hover:bg-orange-500 disabled:bg-stone-800 disabled:text-stone-600 text-stone-950 py-2 px-5 text-[10px] font-mono uppercase tracking-widest"
            >
              <Check size={12} /> Apply {counts.create + counts.update} row(s)
            </button>
          </div>
        </>
      )}
    </div>
  );
}
