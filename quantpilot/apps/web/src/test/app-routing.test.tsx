import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { lazy, type ComponentType } from "react";
import { createMemoryRouter } from "react-router-dom";
import App, { LazyPage } from "@/App";
import { AppShell } from "@/components/shell/app-shell";

vi.mock("@/lib/queries", () => ({
  useHealth: () => ({ data: undefined, isError: false, isPending: false }),
  usePendingApprovalTickets: () => ({ data: [] }),
}));

function renderAppWithPage(page: ComponentType) {
  const router = createMemoryRouter([
    {
      path: "/",
      element: <AppShell />,
      children: [{ index: true, element: <LazyPage page={page} /> }],
    },
  ]);

  return render(<App router={router} />);
}

describe("App lazy routes", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps the shell mounted while a page chunk is pending", () => {
    const PendingPage = lazy(() => new Promise<{ default: ComponentType }>(() => undefined));

    renderAppWithPage(PendingPage);

    expect(screen.getByText(/Loading page/)).toBeInTheDocument();
    expect(screen.getByText(/Mock Broker/)).toBeInTheDocument();
  });

  it("keeps the safety banner mounted when a page chunk rejects", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const RejectedPage = lazy(() => Promise.reject(new Error("page chunk unavailable")));

    renderAppWithPage(RejectedPage);

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load this page");
    expect(screen.getByRole("button", { name: "Retry loading page" })).toBeInTheDocument();
    expect(screen.getByText(/Mock Broker/)).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
