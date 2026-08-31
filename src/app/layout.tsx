import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";

// Inter for UI + JetBrains Mono for IDs/numbers/code — via next/font
// (NEVER CSS @import; render-blocking kills the Lighthouse ≥90 gate).
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "RTO Trust Layer — Stop COD returns before the courier leaves",
  description:
    "Pre-dispatch COD return-risk gating for Indian e-commerce. Cost-optimal Bahnsen BMR decisions in <50ms, a Merkle-sealed audit trail, OC-201B UPI Circle mandates, and OTP-gated reviews for the grey zone.",
  keywords: [
    "RTO",
    "COD",
    "return risk",
    "Razorpay",
    "UPI Circle",
    "OC-201B",
    "Bahnsen BMR",
    "Merkle audit",
    "fraud prevention",
    "Next.js",
  ],
  authors: [{ name: "RTO Trust Layer" }],
  openGraph: {
    title: "RTO Trust Layer — Stop COD returns before the courier leaves",
    description:
      "Merchant console + consumer checkout gate for COD return risk. Light, data-dense, trustworthy.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "RTO Trust Layer",
    description:
      "Pre-dispatch COD return-risk gating with cost-optimal decisions and a sealed audit trail.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased bg-background text-foreground`}
      >
        {children}
        <Toaster />
      </body>
    </html>
  );
}
