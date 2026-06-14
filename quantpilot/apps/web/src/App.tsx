import { Component, lazy, Suspense, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/components/shell/app-shell";
import { useThemeSync } from "@/lib/theme";

const OverviewPage = lazy(() => import("@/pages/overview").then((module) => ({ default: module.OverviewPage })));
const ResearchPage = lazy(() => import("@/pages/research").then((module) => ({ default: module.ResearchPage })));
const PoliciesPage = lazy(() => import("@/pages/policies").then((module) => ({ default: module.PoliciesPage })));
const SignalsPage = lazy(() => import("@/pages/signals").then((module) => ({ default: module.SignalsPage })));
const RunPage = lazy(() => import("@/pages/run").then((module) => ({ default: module.RunPage })));
const JobsPage = lazy(() => import("@/pages/jobs").then((module) => ({ default: module.JobsPage })));
const SettingsPage = lazy(() => import("@/pages/settings").then((module) => ({ default: module.SettingsPage })));

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

function PageLoading() {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center text-[13px] text-muted">
      Loading page...
    </div>
  );
}

function page(element: ReactNode) {
  return <Suspense fallback={<PageLoading />}>{element}</Suspense>;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: page(<OverviewPage />) },
      { path: "research", element: page(<ResearchPage />) },
      { path: "policies", element: page(<PoliciesPage />) },
      { path: "signals", element: page(<SignalsPage />) },
      { path: "run", element: page(<RunPage />) },
      { path: "jobs", element: page(<JobsPage />) },
      { path: "settings", element: page(<SettingsPage />) },
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
