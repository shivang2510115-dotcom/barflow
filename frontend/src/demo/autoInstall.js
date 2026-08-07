/**
 * Side-effect module: installs the offline demo transport at import time.
 *
 * Must be the FIRST import in index.js. Static imports are hoisted, but they still
 * evaluate in source order, so being first is what guarantees this runs before
 * CustomerMenu.jsx calls axios.create() at its own module scope. axios 1.x copies
 * defaults.adapter into an instance when the instance is created, so an adapter
 * installed after that point would never reach that client.
 */
import { installDemo } from "@/demo/installDemo";

if (process.env.REACT_APP_DEMO === "1") {
  installDemo();
}
