import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RTO Trust Layer — Stop COD returns before the courier leaves",
  description:
    "Pre-dispatch COD return-risk gating with Bahnsen Bayes Minimum Risk decisions, tamper-evident Merkle audit, OC-201B UPI Circle mandates, DDM/ADWIN drift detection, and Drummond-Holte cost curves.",
  keywords: [
    "RTO",
    "Paytm",
    "AI Risk Manager",
    "Bahnsen Bayes Minimum Risk",
    "Drummond-Holte",
    "Merkle audit",
    "OC-201B",
    "Next.js",
  ],
  authors: [{ name: "RTO Trust Layer" }],
  openGraph: {
    title: "RTO Trust Layer — Stop COD returns before the courier leaves",
    description:
      "Pre-dispatch COD return-risk gating for Indian e-commerce, built for Paytm merchants.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "RTO Trust Layer",
    description:
      "Pre-dispatch COD return-risk gating with cost-optimal decisions and a sealed audit trail.",
  },
};

// Root layout owns ONLY the html/body scaffold + fonts + Toaster.
// Route groups own their chrome: (console) wraps AppShell (dark console),
// (marketing) wraps MarketingTopbar/Footer (light landing), /story ships its
// own topbar. Wrapping AppShell here would double-render header/footer on
// every page (regression introduced 2026-11-06, fixed same week).
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        {children}
        <Toaster />
      </body>
    </html>
  );
}
