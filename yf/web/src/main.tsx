import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { HttpWorkbenchClient } from "./api";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("YieldForge workbench root is missing");

createRoot(root).render(
  <StrictMode>
    <App client={new HttpWorkbenchClient()} />
  </StrictMode>,
);
