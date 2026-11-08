import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { captureToken } from "./api";
import "./styles.css";

const token = captureToken(window.location.hash, window.sessionStorage, () =>
  window.history.replaceState(null, "", window.location.pathname + window.location.search),
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App token={token} />
    </BrowserRouter>
  </StrictMode>,
);
