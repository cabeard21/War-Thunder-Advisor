import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AdvisorApp } from "./app";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode><AdvisorApp /></StrictMode>,
);
