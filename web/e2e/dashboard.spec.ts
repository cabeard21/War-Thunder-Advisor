import { expect, test } from "@playwright/test";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webRoot, "../..");
const port = 18_766;
const origin = `http://127.0.0.1:${port}`;
const profileId = "acceptance";
const runDir = mkdtempSync(path.join(tmpdir(), "wt-advisor-e2e-"));
const database = path.join(runDir, "advisor.sqlite");
const pythonExecutable = process.env.WT_ADVISOR_E2E_PYTHON ?? "python";
const serverCwd = process.env.WT_ADVISOR_E2E_CWD ?? repoRoot;
let server: ChildProcess | undefined;

async function waitForServer() {
  await expect.poll(async () => {
    try { return (await fetch(`${origin}/api/data-status`)).status; }
    catch { return 0; }
  }, { timeout: 20_000 }).toBe(200);
}

async function startServer() {
  const environment = { ...process.env, WT_ADVISOR_DB: database };
  if (!process.env.WT_ADVISOR_E2E_PYTHON) {
    environment.PYTHONPATH = path.join(repoRoot, "src");
  } else {
    delete environment.PYTHONPATH;
  }
  server = spawn(pythonExecutable, ["-m", "wt_advisor.cli.main", "dashboard", "--port", String(port)], {
    cwd: serverCwd,
    env: {
      ...environment,
    },
    stdio: "ignore",
    windowsHide: true,
  });
  await waitForServer();
}

function stopServer() {
  if (!server?.pid) return;
  if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(server.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
  else server.kill("SIGTERM");
  server = undefined;
}

async function api(pathname: string, init?: RequestInit) {
  const response = await fetch(`${origin}${pathname}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  expect(response.ok, `${init?.method ?? "GET"} ${pathname}`).toBeTruthy();
  return (await response.json()).data;
}

test.beforeAll(startServer);
test.afterAll(() => {
  stopServer();
  rmSync(runDir, { recursive: true, force: true });
});

test("shows readable names when the vehicle API returns internal identifiers", async ({ page }) => {
  await page.route("**/api/vehicles", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    const first = payload.data[0];
    payload.data[0] = { ...first, name: first.vehicle_id };
    await route.fulfill({ response, json: payload });
  });
  await page.goto(`${origin}/?profile=${profileId}`);
  await expect(page.locator("article.vehicle").first().locator("strong")).toHaveText("M2A4");
});

test("community refresh updates the dashboard without clearing a draft", async ({ page }) => {
  let refreshCalls = 0;
  await page.route("**/api/data/refresh-community", async (route) => {
    refreshCalls += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      data: { outcome: "updated", bundle_id: "browser-test", accepted_statistics_rows: 3,
        quarantined_statistics_rows: 1, statistics_status: "usable", message: "Evidence updated." },
    }) });
  });
  await page.goto(`${origin}/?profile=${profileId}`);
  const firstName = await page.locator("article.vehicle").first().locator("strong").textContent();
  await page.locator("article.vehicle").first().getByRole("button", { name: "Draft" }).click();

  await page.getByRole("button", { name: "Refresh community evidence" }).click();

  await expect(page.getByRole("status")).toContainText("Evidence updated.");
  await expect(page.locator("ol.slots")).toContainText(firstName ?? "");
  await expect(page.getByText(/Community refresh: updated/)).toBeVisible();
  expect(refreshCalls).toBe(1);
});

test("complete dashboard workflow survives conflicts and an actual process restart", async ({ page }) => {
  const vehicles = await api("/api/vehicles") as Array<{ vehicle_id: string; name: string }>;
  expect(vehicles.length).toBeGreaterThanOrEqual(3);
  const [first, second, third] = vehicles.slice(0, 3).map((vehicle) => vehicle.vehicle_id);
  for (const vehicleId of [first, second, third]) {
    await api(`/api/profiles/${profileId}/vehicles/${vehicleId}`, { method: "PATCH", body: JSON.stringify({ status: "owned" }) });
  }

  await page.goto(`${origin}/?profile=${profileId}`);
  await expect(page.getByRole("heading", { name: "War Thunder Advisor" })).toBeVisible();
  const firstRow = page.locator("article.vehicle").nth(0);
  const secondRow = page.locator("article.vehicle").nth(1);
  const thirdRow = page.locator("article.vehicle").nth(2);

  await firstRow.getByRole("button", { name: "Draft" }).click();
  await secondRow.getByRole("button", { name: "Draft" }).click();
  await firstRow.getByRole("button", { name: "Pin" }).click();
  await thirdRow.getByRole("button", { name: "Exclude" }).click();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.locator("ol.slots")).toContainText(vehicles[0].name);
  await expect(page.locator("ol.slots")).toContainText(vehicles[1].name);

  await page.getByRole("button", { name: "Apply constraints" }).click();
  await expect(page.getByRole("status")).toContainText("Constraints applied");
  await page.getByRole("button", { name: "Evaluate draft" }).click();
  await expect(page.getByRole("status")).toContainText("Draft evaluated");
  const comparisonResponse = page.waitForResponse((response) => response.url().endsWith("/api/compare"));
  await page.getByRole("button", { name: "Compare draft / play now" }).click();
  const comparisonHttp = await comparisonResponse;
  expect(comparisonHttp.status(), await comparisonHttp.text()).toBe(200);
  await expect(page.getByRole("status")).toContainText("Comparison refreshed");

  await page.getByRole("textbox", { name: "Preset name" }).fill("Restart lineup");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Restart lineup", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByRole("textbox", { name: "Preset name" }).fill("Restart lineup renamed");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByRole("button", { name: "Select context" }).click();
  await expect(page.getByText("Selected context", { exact: true })).toBeVisible();

  const progress = await api(`/api/profiles/${profileId}/progress`) as { revision: string };
  await api(`/api/profiles/${profileId}/vehicles/${first}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "locked", expected_revision: progress.revision }),
  });
  await firstRow.getByRole("combobox").selectOption("researching");
  await expect(page.getByRole("button", { name: new RegExp(`Retry status for`) })).toBeVisible();
  await expect(firstRow.getByRole("combobox")).toHaveValue("researching");
  await page.getByRole("button", { name: new RegExp(`Retry status for`) }).click();
  await expect(page.getByRole("button", { name: new RegExp(`Retry status for`) })).toHaveCount(0);

  stopServer();
  await startServer();
  await page.reload();
  await expect(page.getByText("Restart lineup renamed", { exact: true })).toBeVisible();
  await expect(page.getByText("Selected context", { exact: true })).toBeVisible();
  await expect(page.locator("article.vehicle").nth(0).getByRole("combobox")).toHaveValue("researching");
  await page.getByRole("button", { name: "Load" }).click();
  await expect(page.locator("ol.slots")).toContainText(vehicles[0].name);
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Restart lineup renamed", { exact: true })).toHaveCount(0);
});
