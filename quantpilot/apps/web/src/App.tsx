import { Component, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/components/shell/app-shell";
import { OverviewPage } from "@/pages/overview";
import { ResearchPage } from "@/pages/research";
import { PoliciesPage } from "@/pages/policies";
import { SignalsPage } from "@/pages/signals";
import { RunPage } from "@/pages/run";
import { JobsPage } from "@/pages/jobs";
import { SettingsPage } from "@/pages/settings";
import { useThemeSync } from "@/lib/theme";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 p-10 text-center">
          <h1 className="text-xl font-semibold">화면을 표시하는 중 오류가 발생했습니다</h1>
          <p className="max-w-md text-[13px] text-muted">{this.state.error.message}</p>
          <button
            type="button"
            onClick={() => location.reload()}
            className="cursor-pointer rounded-full bg-accent px-5 py-2 text-sm font-medium text-white"
          >
            새로고침
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "research", element: <ResearchPage /> },
      { path: "policies", element: <PoliciesPage /> },
      { path: "signals", element: <SignalsPage /> },
      { path: "run", element: <RunPage /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);

export default function App() {
  useThemeSync();
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
