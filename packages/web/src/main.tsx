import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { AuthProvider } from "./auth/AuthProvider";
import { NarrativePreferencesProvider } from "./preferences/NarrativePreferences";
import "./styles/index.css";

createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter><AuthProvider><NarrativePreferencesProvider><App /></NarrativePreferencesProvider></AuthProvider></BrowserRouter></React.StrictMode>);
