import { useState } from "react";
import "@/App.css";
import { BrowserRouter, HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";

// The offline demo is opened straight off disk, where the History API throws a
// SecurityError because a file:// document has a null origin. Hash routing is the only
// thing that navigates there. Hosted builds are unaffected and keep clean URLs.
const Router = process.env.REACT_APP_DEMO === "1" ? HashRouter : BrowserRouter;

import { Toaster } from "sonner";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
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
import FrontDesk from "@/pages/hotel/FrontDesk";
import Calendar from "@/pages/hotel/Calendar";
import Rates from "@/pages/hotel/Rates";
import Guests from "@/pages/hotel/Guests";
import CustomerMenu from "@/pages/CustomerMenu";
import PaymentReturn from "@/pages/PaymentReturn";
import AppLayout from "@/components/app/AppLayout";
import Splash from "@/components/app/Splash";

function Protected({ children, roles }) {
  const { user } = useAuth();
  if (user === null)
    return (
      <div className="min-h-screen flex items-center justify-center text-stone-500 font-mono text-xs uppercase tracking-widest">
        Loading…
      </div>
    );
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/app" replace />;
  return children;
}

function AppShell() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
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
        <Route path="/hotel/calendar" element={<Protected roles={["admin", "manager", "front_desk"]}><Calendar /></Protected>} />
        <Route path="/hotel/rates" element={<Protected roles={["admin", "manager"]}><Rates /></Protected>} />
        <Route path="/hotel/guests" element={<Protected roles={["admin", "manager", "front_desk"]}><Guests /></Protected>} />
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
            <Route path="/t/:tableId" element={<TableRouteSwitch />} />
            <Route path="/app/*" element={<Protected><AppShell /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Router>
      </AuthProvider>
    </div>
  );
}

export default App;
