import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function middleware(request: NextRequest) {
  const username = process.env.DASHBOARD_ACCESS_USER;
  const password = process.env.DASHBOARD_ACCESS_PASSWORD;

  if (!username || !password) {
    if (process.env.NODE_ENV === "production") {
      return new NextResponse("Dashboard access is not configured.", { status: 503 });
    }
    return NextResponse.next();
  }

  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Basic ")) {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AGENTE GENERAL", charset="UTF-8"' },
    });
  }

  let suppliedUser = "";
  let suppliedPassword = "";
  try {
    const decoded = Buffer.from(authorization.slice(6), "base64").toString("utf8");
    const separator = decoded.indexOf(":");
    if (separator < 0) throw new Error("invalid basic auth");
    suppliedUser = decoded.slice(0, separator);
    suppliedPassword = decoded.slice(separator + 1);
  } catch {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AGENTE GENERAL", charset="UTF-8"' },
    });
  }

  if (!safeEqual(suppliedUser, username) || !safeEqual(suppliedPassword, password)) {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AGENTE GENERAL", charset="UTF-8"' },
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt).*)"],
};
