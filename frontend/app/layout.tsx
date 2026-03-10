import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "WAIL — Website Agent Integration Layer",
  description: "Expose your business to AI agents.",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-void text-primary antialiased">{children}</body>
    </html>
  )
}
