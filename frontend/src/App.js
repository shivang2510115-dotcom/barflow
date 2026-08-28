import { useState } from "react";
import "@/App.css";
import { BrowserRouter, HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { ANY_CONSOLE, routeDecision } from "@/lib/tenancy";

// The offline demo is opened straight off disk, where the History API throws a
// SecurityError because a file:// document has a null origin. Hash routing is the only
// thing that navigates there. Hosted builds are unaffected and keep clean URLs.
const Router = process.env.REACT_APP_DEMO === "1" ? HashRouter : BrowserRouter;

import { Toaster } from "sonner";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import Platform from "@/pages/platform/Platform";
import SectionChooser from "@/pages/SectionChooser";
import Tables from "@/pages/Tables";
import Reservations from "@/pages/Reservations";
import POS from "@/pages/POS";
import KOT from "@/pages/KOT";
import Inventory from "@/pages/Inventory";
import MenuManage from "@/pages/MenuManage";
import Reports from "@/pages/Reports";
import Rooms from "@/pages/hotel/Rooms";
import NewBooking from "@/pages/hotel/NewBooking";
import Bookings from "@/pages/hotel/Bookings";
import BookingDetail from "@/pages/hotel/BookingDetail";
import Folio from "@/pages/hotel/Folio";
import FrontDesk from "@/pages/hotel/FrontDesk";
import Housekeeping from "@/pages/hotel/Housekeeping";
import Calendar from "@/pages/hotel/Calendar";
import Rates from "@/pages/hotel/Rates";
import Guests from "@/pages/hotel/Guests";
import Messaging from "@/pages/Messaging";
import Planner from "@/pages/Planner";
import Staff from "@/pages/admin/Staff";
import Console from "@/pages/admin/Console";
import Analytics from "@/pages/admin/Analytics";
import Expenses from "@/pages/admin/Expenses";
import Notifications from "@/pages/admin/Notifications";
import Account from "@/pages/Account";
import Settings from "@/pages/admin/Settings";
import CustomerMenu from "@/pages/CustomerMenu";
import GuestRoomRequest from "@/pages/GuestRoomRequest";
import PaymentReturn from "@/pages/PaymentReturn";
import AppLayout from "@/components/app/AppLayout";
import Splash from "@/components/app/Splash";

/**
 * `area` is which of the two consoles the route belongs to, and it defaults to the hotel
 * app because all but one route is in it.
 *
 * `ANY_CONSOLE` is the third answer, and `/account` is the only route that gives it: your
 * own password is neither console's, both need it, and everything on that screen hangs
 * off `get_current_user`, so neither one is refused it.
 *
 * The two do not overlap at all: the platform operator belongs to no hotel and is refused
 * every hotel endpoint, `/api/property` and `/auth/me` included, while a hotel user is
 * refused every `/api/platform/*` route. So sending either into the other's shell renders
 * a page whose every request fails — an operator would get the app frame with an empty
 * sidebar, which is exactly the "merely broken" screen this work exists to remove. A
 * mismatch goes home instead, and `homePathFor` decides which home that is.
 */
function Protected({ children, roles, area = "hotel" }) {
  const { user } = useAuth();
  // The decision itself is pure and lives in lib/tenancy.js, so who lands where can be
  // checked without clicking through three logins in a browser.
  const to = routeDecision(user, { area, roles });
  if (to === "loading")
    return (
      <div className="min-h-screen flex items-center justify-center text-stone-500 font-mono text-xs uppercase tracking-widest">
        Loading…
      </div>
    );
  if (to) return <Navigate to={to} replace />;
  return children;
}

function AppShell() {
  return (
    <AppLayout>
      <Routes>
        {/* /app is the section chooser, not a dashboard: a property running both halves
            of the business asks which one you are in before it shows a menu. */}
        <Route path="/" element={<SectionChooser />} />
        <Route path="/tables" element={<Tables />} />
        <Route path="/reservations" element={<Reservations />} />
        <Route path="/pos/:tableId?" element={<POS />} />
        <Route path="/kot" element={<KOT />} />
        <Route path="/inventory" element={<Protected roles={["admin", "manager", "kitchen"]}><Inventory /></Protected>} />
        <Route path="/menu" element={<Protected roles={["admin", "manager"]}><MenuManage /></Protected>} />
        <Route path="/reports" element={<Protected roles={["admin", "manager"]}><Reports /></Protected>} />
        <Route path="/hotel/rooms" element={<Protected roles={["admin", "manager"]}><Rooms /></Protected>} />
        <Route path="/hotel/front-desk" element={<Protected roles={["admin", "manager", "front_desk"]}><FrontDesk /></Protected>} />
        <Route path="/hotel/bookings" element={<Protected roles={["admin", "manager", "front_desk"]}><Bookings /></Protected>} />
        {/* /new must stay declared before the /:id route below, or react-router
            would otherwise be at risk of treating "new" as a booking id. */}
        <Route path="/hotel/bookings/new" element={<Protected roles={["admin", "manager", "front_desk"]}><NewBooking /></Protected>} />
        <Route path="/hotel/bookings/:id" element={<Protected roles={["admin", "manager", "front_desk"]}><BookingDetail /></Protected>} />
        <Route path="/hotel/folios/:id" element={<Protected roles={["admin", "manager", "front_desk"]}><Folio /></Protected>} />
        <Route path="/hotel/calendar" element={<Protected roles={["admin", "manager", "front_desk"]}><Calendar /></Protected>} />
        <Route path="/hotel/rates" element={<Protected roles={["admin", "manager"]}><Rates /></Protected>} />
        {/* The one screen a `housekeeping` account reaches, which is why that role is in
            the list here and nowhere else in this file. The endpoints behind it name the
            same four roles and additionally require the `hotel.housekeeping` key, so a
            receptionist without the tick is refused by the API rather than by this line —
            the route is the coarse check and `require_access` is the real one. */}
        <Route path="/hotel/housekeeping" element={<Protected roles={["admin", "manager", "front_desk", "housekeeping"]}><Housekeeping /></Protected>} />
        <Route path="/hotel/guests" element={<Protected roles={["admin", "manager", "front_desk"]}><Guests /></Protected>} />
        {/* Sending a greeting is operational work — the front desk and the waiter
            know the guest. The endpoints behind this name the same four roles. */}
        <Route path="/messaging" element={<Protected roles={["admin", "manager", "front_desk", "waiter"]}><Messaging /></Protected>} />
        {/* The planning calendar. No `roles` at all, which is the only route in this file
            without one and is the point of the screen: a fire drill on Thursday is posted
            for everybody who works here, and the API declares the domain alone on its read
            routes for the same reason. Writing is what is restricted — admin and manager,
            behind `property.planner` — and `require_access` is where that is enforced, not
            here. */}
        <Route path="/planner" element={<Planner />} />
        <Route path="/admin" element={<Protected roles={["admin"]}><Console /></Protected>} />
        <Route path="/admin/staff" element={<Protected roles={["admin"]}><Staff /></Protected>} />
        <Route path="/admin/analytics" element={<Protected roles={["admin", "manager"]}><Analytics /></Protected>} />
        {/* No role list, deliberately, and the only route in this file without one apart
            from the section chooser. The owner's brief for this screen was "everyone who
            has access", so the read endpoints behind it name no role either: the tick on
            the staff screen is the whole decision, and `require_access` is what enforces
            it. A route naming roles here would be a second, coarser rule that quietly
            refused an accountant the owner had deliberately ticked. Recording an expense
            is still admin and manager only — the API says so, and the screen hides the
            form rather than offering a button that 403s. */}
        <Route path="/admin/expenses" element={<Expenses />} />
        <Route path="/admin/notifications" element={<Protected roles={["admin"]}><Notifications /></Protected>} />
        {/* Admin only, twice: this route names the role, and `PUT /api/property` behind
            it names "admin" as well. A waiter who types the address gets the screen's
            redirect; one who calls the API gets a 403. */}
        <Route path="/admin/settings" element={<Protected roles={["admin"]}><Settings /></Protected>} />
      </Routes>
    </AppLayout>
  );
}

function TableRouteSwitch() {
  // /t/:tableId — if paid= query is present, show PaymentReturn, else Customer Menu.
  const params = new URLSearchParams(window.location.search);
  if (params.has("paid")) return <PaymentReturn />;
  return <CustomerMenu />;
}

function App() {
  const [showSplash, setShowSplash] = useState(() => {
    try {
      return sessionStorage.getItem("barflow_splash_shown") !== "1";
    } catch {
      return true;
    }
  });

  const done = () => {
    try {
      sessionStorage.setItem("barflow_splash_shown", "1");
    } catch {}
    setShowSplash(false);
  };

  return (
    <div className="App grain">
      {showSplash && <Splash onDone={done} />}
      <AuthProvider>
        <Router>
          <Toaster
            position="top-right"
            theme="dark"
            toastOptions={{
              style: {
                background: "#1c1917",
                border: "1px solid #292524",
                borderRadius: 0,
                color: "#f5f5f4",
                fontFamily: "Manrope, sans-serif",
              },
            }}
          />
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/t/:tableId" element={<TableRouteSwitch />} />
            {/* The in-room QR, the same shape as the table one above it: no login, and the
                id in the printed URL is the only thing that names the hotel. Outside
                /app on purpose — a guest has no console to be inside. */}
            <Route path="/room/:roomId" element={<GuestRoomRequest />} />
            {/* Outside /app on purpose: the operator is sent home from every route in
                that shell, and their own password is not the hotel's. */}
            <Route
              path="/account"
              element={
                <Protected area={ANY_CONSOLE}>
                  <Account />
                </Protected>
              }
            />
            <Route path="/app/*" element={<Protected><AppShell /></Protected>} />
            {/* The operator's only screen, and the only route with area="platform". A
                hotel user who types this address is sent back to /app rather than shown a
                console whose every request would 403. */}
            <Route path="/platform" element={<Protected area="platform"><Platform /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Router>
      </AuthProvider>
    </div>
  );
}

export default App;
