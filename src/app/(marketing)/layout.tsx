import { MarketingTopbar } from "@/components/marketing/topbar";
import { MarketingFooter } from "@/components/marketing/footer";

// Marketing (landing) layout — navy chrome top + full footer, sticky to the
// bottom of the viewport on short pages, pushed down naturally on long ones.

export default function MarketingLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <MarketingTopbar />
      <main className="flex-1">{children}</main>
      <MarketingFooter />
    </div>
  );
}
