import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PR Automation — Multi-Tenant Outreach Platform",
  description: "Journalist intelligence, grounded pitch generation, and PR analytics.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}

