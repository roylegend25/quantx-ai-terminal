import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotificationBell from "./NotificationBell";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    mlNotifications: vi.fn(),
    mlNotificationRead: vi.fn().mockResolvedValue({}),
    mlNotificationsReadAll: vi.fn().mockResolvedValue({}),
    indicatorNotifications: vi.fn(),
    indicatorNotificationRead: vi.fn().mockResolvedValue({}),
    indicatorNotificationsReadAll: vi.fn().mockResolvedValue({}),
  },
}));

function mlItem(overrides: Partial<any> = {}) {
  return { id: 1, event: "training_completed", severity: "success", title: "Training complete", message: null, read: false, created_at: "2026-07-20T00:00:00Z", ...overrides };
}

function indicatorItem(overrides: Partial<any> = {}) {
  return { id: 5, event: "recommended_for_reactivation", severity: "success", title: "⭐ RSI recommended", message: "70% hit rate", read: false, created_at: "2026-07-21T00:00:00Z", ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("NotificationBell", () => {
  it("merges ML and indicator notifications by created_at and sums the unread badge", async () => {
    (api.mlNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [mlItem()], unread: 1 });
    (api.indicatorNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [indicatorItem()], unread: 2 });

    render(<NotificationBell />);
    await screen.findByText("3"); // unread badge on the bell button

    await userEvent.click(screen.getByTitle("Notifications"));
    const items = await screen.findAllByRole("listitem");
    expect(items.length).toBe(2);
    // Newer indicator item (2026-07-21) sorts before the older ML item (2026-07-20).
    expect(items[0].textContent).toContain("RSI recommended");
    expect(items[1].textContent).toContain("Training complete");
  });

  it("routes mark-read to the indicator endpoint for an indicator-sourced item", async () => {
    (api.mlNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [], unread: 0 });
    (api.indicatorNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [indicatorItem()], unread: 1 });

    render(<NotificationBell />);
    await userEvent.click(screen.getByTitle("Notifications"));
    await screen.findByText(/RSI recommended/);
    await userEvent.click(screen.getByText(/RSI recommended/));

    expect(api.indicatorNotificationRead).toHaveBeenCalledWith(5);
    expect(api.mlNotificationRead).not.toHaveBeenCalled();
  });

  it("mark-all-read calls both backends", async () => {
    (api.mlNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [mlItem()], unread: 1 });
    (api.indicatorNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [indicatorItem()], unread: 1 });

    render(<NotificationBell />);
    await userEvent.click(screen.getByTitle("Notifications"));
    await screen.findByText("Mark all read");
    await userEvent.click(screen.getByText("Mark all read"));

    expect(api.mlNotificationsReadAll).toHaveBeenCalled();
    expect(api.indicatorNotificationsReadAll).toHaveBeenCalled();
  });

  it("keeps working when one feed fails (only shows an error state if both fail)", async () => {
    (api.mlNotifications as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("down"));
    (api.indicatorNotifications as ReturnType<typeof vi.fn>).mockResolvedValue({ notifications: [indicatorItem()], unread: 1 });

    render(<NotificationBell />);
    await userEvent.click(screen.getByTitle("Notifications"));
    await screen.findByText(/RSI recommended/);
    expect(screen.queryByText("Couldn't load notifications")).not.toBeInTheDocument();
  });
});
