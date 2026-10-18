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
            __html: `try{const t=localStorage.getItem('theme');if(t==='dark'||(!t&&matchMedia('(prefers-color-scheme:dark)').matches))document.documentElement.classList.add('dark')}catch(e){};
(function(){
  const hide=()=>{
    document.querySelectorAll('nextjs-portal, [data-nextjs-toast], [data-nextjs-dev-overlay]').forEach(el=>{
      try{el.style.setProperty('display','none','important');el.style.setProperty('visibility','hidden','important');el.style.setProperty('opacity','0','important');el.style.setProperty('pointer-events','none','important');}catch(e){}
      if(el.shadowRoot){
        try{
          const s=document.createElement('style');
          s.textContent='* { display: none !important; opacity: 0 !important; visibility: hidden !important; pointer-events: none !important; }';
          el.shadowRoot.appendChild(s);
        }catch(e){}
      }
    });
  };
  if(typeof window!=='undefined'){
    setInterval(hide, 500);
    window.addEventListener('DOMContentLoaded', hide);
  }
})();`,
          }}
        />
        {children}
      </body>
    </html>
  );
}
