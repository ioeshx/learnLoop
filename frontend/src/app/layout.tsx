import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "LearnLoop",
  description: "Local-first adaptive learning agent",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
