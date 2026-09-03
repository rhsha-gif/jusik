import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SafetyBanner } from "@/components/shell/safety-banner";

describe("SafetyBanner", () => {
  it("always shows the required safety labels", () => {
    render(<SafetyBanner />);
    expect(screen.getByText(/모의 브로커 활성/)).toBeInTheDocument();
    expect(screen.getByText("실거래 비활성")).toBeInTheDocument();
    expect(screen.getByText("지정가 주문만")).toBeInTheDocument();
    expect(screen.getByText("Fixture 데이터")).toBeInTheDocument();
  });

  it("shows fixture data mode label by default", () => {
    render(<SafetyBanner />);
    expect(screen.getByText("Fixture 데이터")).toBeInTheDocument();
  });

  it("shows paper_trading label when dataMode is paper_trading", () => {
    render(<SafetyBanner dataMode="paper_trading" />);
    expect(screen.getByText("페이퍼 트레이딩")).toBeInTheDocument();
    expect(screen.getByText("실거래 비활성")).toBeInTheDocument();
  });

  it("shows danger label when dataMode is live_trading", () => {
    render(<SafetyBanner dataMode="live_trading" />);
    expect(screen.getByText("실거래 차단됨")).toBeInTheDocument();
  });

  it("shows the raw value for unknown data modes", () => {
    render(<SafetyBanner dataMode="unknown_future_mode" />);
    expect(screen.getByText("unknown_future_mode")).toBeInTheDocument();
  });

  it("keeps the operator token in memory while the safety banner remains visible", () => {
    const storageSetSpy = vi.spyOn(Storage.prototype, "setItem");
    render(<SafetyBanner />);

    fireEvent.change(screen.getByLabelText("운영자 토큰 (메모리에만 보관)"), {
      target: { value: "operator-secret" },
    });

    expect(screen.getByText(/모의 브로커 활성/)).toBeInTheDocument();
    expect(storageSetSpy).not.toHaveBeenCalled();
    storageSetSpy.mockRestore();
  });

  it("shows danger state from a blocked health response", () => {
    render(
      <SafetyBanner
        health={{
          status: "blocked",
          live_trading_enabled: true,
          market_orders_enabled: true,
          guarded_autopilot_enabled: false,
          fully_automated_operator_enabled: true,
          default_broker: "paper",
          data_mode: "paper_trading",
          data_mode_safe: true,
        }}
      />,
    );

    expect(screen.getByText("paper 브로커 구성")).toHaveClass("text-danger");
    expect(screen.getByText("안전 설정 차단")).toHaveClass("text-danger");
    expect(screen.getByText("실거래 활성")).toHaveClass("text-danger");
    expect(screen.getByText("시장가 주문 활성")).toHaveClass("text-danger");
    expect(screen.getByText("완전 자동 운영 활성")).toHaveClass("text-danger");
    expect(screen.getByText("페이퍼 트레이딩")).toHaveClass("text-danger");
  });
});
