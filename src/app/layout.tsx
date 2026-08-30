import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { AppShell } from "@/components/app-shell";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RTO Trust Layer — Risk Console",
  description:
    "Pre-dispatch COD return-risk gating with Bahnsen Bayes Minimum Risk decisions, tamper-evident Merkle audit, OC-201B UPI Circle mandates, DDM/ADWIN drift detection, and Drummond-Holte cost curves.",
  keywords: [
    "RTO",
    "Razorpay",
    "AI Risk Manager",
    "Bahnsen Bayes Minimum Risk",
    "Drummond-Holte",
    "Merkle audit",
    "OC-201B",
    "Next.js",
  ],
  authors: [{ name: "RTO Trust Layer" }],
  // Next.js 16 auto-detects src/app/icon.svg and serves it as the favicon.
  // No external CDN dependency — see public/logo.svg for the same branded icon.
  openGraph: {
    title: "RTO Trust Layer — Risk Console",
    description:
      "Stripe-like dashboard for pre-dispatch COD return-risk gating.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "RTO Trust Layer — Risk Console",
    description:
      "Stripe-like dashboard for pre-dispatch COD return-risk gating.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className="dark">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        <AppShell>{children}</AppShell>
        <Toaster />
      </body>
    </html>
  );
}
