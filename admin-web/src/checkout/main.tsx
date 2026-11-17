import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CheckoutPage } from "./CheckoutPage";
import "./checkout.css";

const root = document.getElementById("checkout-root");
if (!root) throw new Error("checkout root is missing");

createRoot(root).render(
  <StrictMode>
    <CheckoutPage />
  </StrictMode>,
);
