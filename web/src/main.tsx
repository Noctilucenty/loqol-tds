import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

// Base tokens must load before any page stylesheet, or a later-bundled `.wrap`
// rule will out-cascade the same-specificity layout rules that pages rely on.
import "./styles/global.css";

import { SellerFlow } from "./pages/SellerFlow";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { DealDetail } from "./pages/DealDetail";
import { Design } from "./pages/Design";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/agent" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/agent" element={<Dashboard />} />
        <Route path="/agent/deals/:dealId" element={<DealDetail />} />
        <Route path="/s/:token" element={<SellerFlow />} />
        <Route path="/design" element={<Design />} />
        <Route path="*" element={<Navigate to="/agent" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
