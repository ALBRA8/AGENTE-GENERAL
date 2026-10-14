"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV = [
  { href: "/", label: "Resumen" },
  { href: "/chat", label: "Chat" },
  { href: "/config", label: "Configuración" },
];

export function Header() {
  const pathname = usePathname();

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        background: "var(--bg)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div
        style={{
          maxWidth: "64rem",
          margin: "0 auto",
          padding: "0.75rem 1rem",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "1.5rem" }}>
          <Link
            href="/"
            style={{
              fontWeight: 600,
              fontSize: "0.95rem",
              color: "var(--fg)",
              textDecoration: "none",
            }}
          >
            AGENTE GENERAL
          </Link>
          <nav style={{ display: "flex", gap: "0.25rem" }}>
            {NAV.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  style={{
                    padding: "0.375rem 0.75rem",
                    borderRadius: "0.375rem",
                    fontSize: "0.875rem",
                    textDecoration: "none",
                    background: active ? "var(--muted)" : "transparent",
                    color: active ? "var(--fg)" : "var(--muted-fg)",
                    fontWeight: active ? 500 : 400,
                  }}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}
