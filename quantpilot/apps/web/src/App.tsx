import { Component, lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, type RouterProviderProps } from "react-router-dom";
import { AppShell } from "@/components/shell/app-shell";
import { useThemeSync } from "@/lib/theme";

const OverviewPage = lazy(() =>
  import("@/pages/overview").then(({ OverviewPage }) => ({ default: OverviewPage })),
);
const ResearchPage = lazy(() =>
  import("@/pages/research").then(({ ResearchPage }) => ({ default: ResearchPage })),
);
const PoliciesPage = lazy(() =>
  import("@/pages/policies").then(({ PoliciesPage }) => ({ default: PoliciesPage })),
);
const SignalsPage = lazy(() =>
  import("@/pages/signals").then(({ SignalsPage }) => ({ default: SignalsPage })),
);
const RunPage = lazy(() => import("@/pages/run").then(({ RunPage }) => ({ default: RunPage })));
const StudioPage = lazy(() =>
  import("@/pages/studio").then(({ StudioPage }) => ({ default: StudioPage })),
);
const BriefingPage = lazy(() =>
  import("@/pages/briefing").then(({ BriefingPage }) => ({ default: BriefingPage })),
);
const ExecutionPage = lazy(() =>
  import("@/pages/execution").then(({ ExecutionPage }) => ({ default: ExecutionPage })),
);
const OperatorPage = lazy(() =>
  import("@/pages/operator").then(({ OperatorPage }) => ({ default: OperatorPage })),
);
const JobsPage = lazy(() => import("@/pages/jobs").then(({ JobsPage }) => ({ default: JobsPage })));
const SettingsPage = lazy(() =>
  import("@/pages/settings").then(({ SettingsPage }) => ({ default: SettingsPage })),
);

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

function PageLoadingFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-64 items-center justify-center rounded-2xl border border-hairline bg-surface-solid/40 p-10 text-sm text-muted"
    >
      Loading page…
    </div>
  );
}

class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-2xl border border-danger/30 bg-danger-soft/30 p-10 text-center"
        >
          <h2 className="text-lg font-semibold">Unable to load this page</h2>
          <p className="max-w-md text-[13px] text-muted">Please check your connection and retry.</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="cursor-pointer rounded-full bg-accent px-5 py-2 text-sm font-medium text-white"
          >
            Retry loading page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function LazyPage({ page: Page }: { page: ComponentType }) {
  return (
    <PageErrorBoundary>
      <Suspense fallback={<PageLoadingFallback />}>
        <Page />
      </Suspense>
    </PageErrorBoundary>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <LazyPage page={OverviewPage} /> },
      { path: "research", element: <LazyPage page={ResearchPage} /> },
      { path: "policies", element: <LazyPage page={PoliciesPage} /> },
      { path: "signals", element: <LazyPage page={SignalsPage} /> },
      { path: "run", element: <LazyPage page={RunPage} /> },
      { path: "briefing", element: <LazyPage page={BriefingPage} /> },
      { path: "studio", element: <LazyPage page={StudioPage} /> },
      { path: "execution", element: <LazyPage page={ExecutionPage} /> },
      { path: "operator", element: <LazyPage page={OperatorPage} /> },
      { path: "jobs", element: <LazyPage page={JobsPage} /> },
      { path: "settings", element: <LazyPage page={SettingsPage} /> },
    ],
  },
]);

export default function App({
  router: activeRouter = router,
}: {
  router?: RouterProviderProps["router"];
}) {
  useThemeSync();
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={activeRouter} />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
