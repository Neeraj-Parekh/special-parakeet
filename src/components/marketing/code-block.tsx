"use client";

// Dark code block with copy-to-clipboard — the Stripe docs pattern
// (light chrome, dark code). One of the ONLY dark surfaces in the product.

import * as React from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";

export function CodeBlock({
  title,
  code,
  className,
}: {
  title: string;
  code: string;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable — ignore */
    }
  }

  return (
    <div className={cn("code-dark overflow-hidden rounded-lg", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2">
        <span className="font-mono text-xs text-white/60">{title}</span>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-white/70 transition-colors duration-200 ease-brand hover:bg-white/10 hover:text-white"
          aria-label={`Copy ${title}`}
        >
          {copied ? (
            <>
              <Check className="size-3.5" aria-hidden /> Copied
            </>
          ) : (
            <>
              <Copy className="size-3.5" aria-hidden /> Copy
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed">
        <code>{code}</code>
      </pre>
    </div>
  );
}
