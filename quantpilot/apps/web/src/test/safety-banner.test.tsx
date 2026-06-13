import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
