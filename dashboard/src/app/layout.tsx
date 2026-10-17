import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AGENTE GENERAL — Dashboard",
  description: "Chat, configuración de proveedor, Telegram y WhatsApp",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{const t=localStorage.getItem('theme');if(t==='dark'||(!t&&matchMedia('(prefers-color-scheme:dark)').matches))document.documentElement.classList.add('dark')}catch(e){}`,
          }}
        />
        {children}
      </body>
    </html>
  );
}
