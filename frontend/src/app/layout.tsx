import { headers } from "next/headers";
import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import { strings } from "@/lib/strings";
import "./globals.css";
export const metadata: Metadata = {
  title: { default: strings.brand, template: `%s · ${strings.brand}` },
  description: strings.tagline,
  robots: { index: false, follow: false },
};
export const dynamic = "force-dynamic";
export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers nonce={(await headers()).get("x-nonce") || ""}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
