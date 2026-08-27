import { useId, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

/**
 * A password field you can look at.
 *
 * Every password in this app is typed rather than pasted — an owner inventing one at
 * signup, an admin inventing one for a new waiter, that waiter typing it back an hour
 * later off a scrap of paper — and a masked field cannot tell a typo from a wrong key.
 * The cost of that is a support call, or a locked-out account on a Friday night.
 *
 * One component rather than four copies, because the parts that are easy to get wrong
 * are the parts nobody re-checks on the fourth copy:
 *
 * * it starts **hidden**, always. A field that remembers being revealed is a password on
 *   a screen behind a bar;
 * * the control is a real `<button type="button">`, so it is reachable by Tab and
 *   cannot submit the form it sits in — an `onClick` on a `<span>` is neither, and a
 *   toggle that submits the login form is the bug this shape exists to make impossible;
 * * its accessible name says **what pressing it will do**, and changes when it does:
 *   "Show password" then "Hide password". An icon with no name is an unlabelled button
 *   to a screen reader, and `aria-pressed` on top of a name that already changes reads
 *   out as "Hide password, pressed", which is a state and an action contradicting each
 *   other. One or the other; this is the one that also works as a tooltip;
 * * `label` distinguishes two fields on one screen — "Show current password" and "Show
 *   new password" — because two buttons both called "Show password" are two buttons a
 *   screen reader user cannot tell apart;
 * * it is never autofocused. Focus belongs in the field.
 *
 * Everything else is passed straight through, so each screen keeps the input treatment
 * it already had: `className` is the input's, `wrapperClassName` is the positioning box
 * that the button is placed against. They are separate because the box is what the
 * button is centred on — a bottom margin left on the input would push the icon below
 * the line it belongs to.
 */
export default function PasswordInput({
  label = "password",
  className = "",
  wrapperClassName = "",
  toggleTestId,
  ...props
}) {
  const [revealed, setRevealed] = useState(false);
  const generatedId = useId();
  const inputId = props.id || generatedId;
  const Icon = revealed ? EyeOff : Eye;
  // What the press will do, not what the field currently is.
  const action = `${revealed ? "Hide" : "Show"} ${label}`;
  // Derived rather than asked for twice: a screen that has named its input has named
  // its toggle, and the two stay together when one of them is renamed.
  const testId =
    toggleTestId || (props["data-testid"] ? `${props["data-testid"]}-toggle` : undefined);

  return (
    <div className={`relative ${wrapperClassName}`}>
      <input
        {...props}
        id={inputId}
        type={revealed ? "text" : "password"}
        /* Room for the button, so a long password never runs underneath it. */
        className={`pr-9 ${className}`}
      />
      <button
        type="button"
        onClick={() => setRevealed((on) => !on)}
        aria-label={action}
        aria-controls={inputId}
        title={action}
        data-testid={testId}
        className="absolute right-0 top-1/2 -translate-y-1/2 p-1 rounded text-stone-500 hover:text-orange-400 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500 transition-colors"
      >
        <Icon size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
