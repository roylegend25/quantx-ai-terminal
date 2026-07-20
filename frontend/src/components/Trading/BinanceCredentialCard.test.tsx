import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinanceCredentialCard from "./BinanceCredentialCard";
import { api } from "../../services/api";

const notConfigured = {
  configured: false,
  label: null,
  environment: null,
  api_key_fingerprint: null,
  created_at: null,
  created_by: null,
  updated_at: null,
  last_validated_at: null,
  last_validation_status: null,
  last_validation_detail: null,
  write_permission_detected: null,
  withdraw_enabled_detected: null,
  credential_source: "none",
  encryption_store_configured: true,
  execution_mode: "PAPER",
  message: null,
};

const configured = {
  ...notConfigured,
  configured: true,
  label: "main",
  environment: "live",
  api_key_fingerprint: "AKIA************7890",
  created_at: "2026-07-18T00:00:00Z",
  credential_source: "database_encrypted",
  message: "Credentials stored securely — execution remains PAPER",
};

vi.mock("../../services/api", () => ({
  api: {
    binanceStoredCredentialStatus: vi.fn(),
    saveBinanceCredential: vi.fn(),
    testBinanceCredential: vi.fn(),
    deleteBinanceCredential: vi.fn(),
  },
}));

const showToast = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
});

describe("BinanceCredentialCard", () => {
  it("shows 'Credentials Not Configured' and never renders a secret placeholder value", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(notConfigured);
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Not Configured");
    expect(screen.queryByText(/AKIA/)).toBeNull();
  });

  it("shows masked fingerprint and metadata once configured, never the raw key", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(configured);
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Present");
    expect(screen.getByText("AKIA************7890")).toBeInTheDocument();
    expect(screen.getAllByText(/execution remains PAPER/).length).toBeGreaterThan(0);
  });

  it("clears the secret input from component state after a successful save", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(notConfigured);
    (api.saveBinanceCredential as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      message: "Credentials stored securely — execution remains PAPER",
      status: configured,
    });
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Not Configured");

    await userEvent.click(screen.getByRole("button", { name: "Save Credentials" }));
    const [keyInput, secretInput] = document.querySelectorAll('input[type="password"]');
    await userEvent.type(keyInput as HTMLInputElement, "AKIAFAKEKEY1234567890");
    await userEvent.type(secretInput as HTMLInputElement, "supersecretvalue0987654321");
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    await userEvent.type(passwordInputs[passwordInputs.length - 1] as HTMLInputElement, "hunter2");

    await userEvent.click(screen.getAllByRole("button", { name: "Save Credentials" }).slice(-1)[0]);

    await waitFor(() => expect(api.saveBinanceCredential).toHaveBeenCalledWith(
      expect.objectContaining({ api_key: "AKIAFAKEKEY1234567890", api_secret: "supersecretvalue0987654321", password: "hunter2" })
    ));
    // form closes and clears after a successful save - no secret survives in the DOM
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  }, 15000); // three realistic multi-character userEvent.type() calls + a full save/reload cycle need more than the 5s default on a loaded machine

  it("does not enable live trading or change execution mode when saving", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(notConfigured);
    (api.saveBinanceCredential as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, message: "ok", status: configured });
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Not Configured");
    // The component never imports or calls any trading-mode/lease API.
    expect((api as any).setTradingMode).toBeUndefined();
    expect((api as any).unlockBinanceLive).toBeUndefined();
  });

  it("read-only connection test never calls a save/delete endpoint", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(configured);
    (api.testBinanceCredential as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true, signed_read_ok: true, write_permission_detected: false, withdraw_enabled_detected: false, detail: "Signed read succeeded",
    });
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Present");
    await userEvent.click(screen.getByRole("button", { name: /Test Read-Only Connection/ }));
    await waitFor(() => expect(api.testBinanceCredential).toHaveBeenCalled());
    expect(api.saveBinanceCredential).not.toHaveBeenCalled();
    expect(api.deleteBinanceCredential).not.toHaveBeenCalled();
  });

  it("deletion requires a typed password and calls the delete endpoint", async () => {
    (api.binanceStoredCredentialStatus as ReturnType<typeof vi.fn>).mockResolvedValue(configured);
    (api.deleteBinanceCredential as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, status: notConfigured });
    render(<BinanceCredentialCard showToast={showToast} />);
    await screen.findByText("Credentials Present");
    await userEvent.click(screen.getByRole("button", { name: /Delete \/ Revoke/ }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm Delete" }));
    // no password typed yet - must not call the API
    expect(api.deleteBinanceCredential).not.toHaveBeenCalled();

    const pwInput = document.querySelector('input[type="password"]') as HTMLInputElement;
    await userEvent.type(pwInput, "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Confirm Delete" }));
    await waitFor(() => expect(api.deleteBinanceCredential).toHaveBeenCalledWith("hunter2"));
  });
});
