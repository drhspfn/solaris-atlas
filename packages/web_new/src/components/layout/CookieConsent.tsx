"use client";

import { useEffect, useState } from "react";
import {
  COOKIE_CHOICE_KEY,
  OPEN_COOKIE_SETTINGS,
  readCookieChoice,
  saveCookieChoice,
  type CookieChoice,
} from "@/preferences/cookieConsent";

export function CookieSettingsButton() {
  return (
    <button
      type="button"
      className="cookie-settings-link"
      onClick={() => window.dispatchEvent(new Event(OPEN_COOKIE_SETTINGS))}
    >
      Cookie settings
    </button>
  );
}

export function CookieConsent() {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(false);
  const [currentChoice, setCurrentChoice] = useState<CookieChoice | null>(null);

  useEffect(() => {
    function storedChoice() {
      try {
        return readCookieChoice(window.localStorage);
      } catch {
        return null;
      }
    }
    const initial = storedChoice();
    setCurrentChoice(initial);
    setOpen(!initial);
    function showSettings() {
      setError(false);
      setOpen(true);
    }
    function sync(event: StorageEvent) {
      if (event.key === COOKIE_CHOICE_KEY || event.key === null) {
        const choice = storedChoice();
        setCurrentChoice(choice);
        setOpen(!choice);
      }
    }
    window.addEventListener(OPEN_COOKIE_SETTINGS, showSettings);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(OPEN_COOKIE_SETTINGS, showSettings);
      window.removeEventListener("storage", sync);
    };
  }, []);

  function choose(choice: CookieChoice) {
    let saved = false;
    try {
      saved = saveCookieChoice(window.localStorage, choice);
    } catch {
      /* Browser storage is unavailable. */
    }
    if (!saved) {
      setError(true);
      return;
    }
    setError(false);
    setCurrentChoice(choice);
    setOpen(false);
  }

  if (!open) return null;
  return (
    <section
      className="cookie-consent"
      role="region"
      aria-label="Cookie choices"
    >
      <div className="cookie-consent-copy">
        <span className="cookie-consent-kicker">SOLARIS ATLAS / PRIVACY</span>
        <h2>Your cookie choices</h2>
        <p>
          We use essential storage for sign-in, security and your site settings.
          We are preparing Google advertising; ads are not active yet.
        </p>
        <p>
          If advertising is enabled, Google and its partners may use cookies for
          ads. You can change this choice at any time. Google will ask for
          advertising consent when required.
        </p>
        <a href="/legal#storage">Read our cookie and privacy policy</a>
        {currentChoice && <p>Current choice: {currentChoice === "all" ? "Accept all" : "Only necessary"}.</p>}
        {error && (
          <p role="alert">
            Your choice could not be saved. Enable browser storage and try
            again.
          </p>
        )}
      </div>
      <div className="cookie-consent-actions">
        <button type="button" aria-pressed={currentChoice === "essential"} onClick={() => choose("essential")}>
          Only necessary
        </button>
        <button type="button" aria-pressed={currentChoice === "all"} onClick={() => choose("all")}>
          Accept all
        </button>
      </div>
    </section>
  );
}
