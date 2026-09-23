import type { Metadata, Viewport } from "next";

import { PwaRegister } from "@/components/pwa-register";

import "./globals.css";

export const metadata: Metadata = {
  title: "LearnLoop",
  description: "Local-first adaptive learning agent",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = {
  themeColor: "#246c50",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <PwaRegister />
        {children}
      </body>
    </html>
  );
}
