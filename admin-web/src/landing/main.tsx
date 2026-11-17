import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LandingPage, loadPlans } from "./LandingPage";
import "./landing.css";

const root = document.getElementById("landing-root");
if (!root) throw new Error("landing root is missing");

void loadPlans().then((plans) => {
  createRoot(root).render(
    <StrictMode>
      <LandingPage plans={plans} />
    </StrictMode>,
  );
});
