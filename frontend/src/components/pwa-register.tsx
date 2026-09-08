"use client";

import { useEffect, useState } from "react";

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
};

export function PwaRegister() {
  const [installPrompt, setInstallPrompt] =
    useState<InstallPromptEvent | null>(null);

  useEffect(() => {
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.register("/sw.js");
    }
    const rememberPrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", rememberPrompt);
    return () => {
      window.removeEventListener("beforeinstallprompt", rememberPrompt);
    };
  }, []);

  if (!installPrompt) return null;
  return (
    <button
      className="install-app-button"
      onClick={() => {
        void installPrompt.prompt().finally(() => setInstallPrompt(null));
      }}
      type="button"
    >
      安装 LearnLoop
    </button>
  );
}
