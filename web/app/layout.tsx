import "@fontsource/ibm-plex-sans/300.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "AEGIS",
  description: "Investigación y paper trading de una cartera masivamente diversificada en Solana",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
