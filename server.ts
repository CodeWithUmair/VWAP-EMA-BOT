import express from "express";
import path from "path";
import { execFile } from "child_process";
import { createServer as createViteServer } from "vite";

async function startServer() {
  const app = express();
  const PORT = 3000;

  app.use(express.json());

  // API 1: Health
  app.get("/api/health", (_req, res) => {
    res.json({ status: "ok", timestamp: new Date().toISOString() });
  });

  // Helper to execute Python api_service
  const runPythonApi = (args: string[]): Promise<any> => {
    return new Promise((resolve, reject) => {
      execFile("python3", ["trading_bot/api_service.py", ...args], { maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
        if (error) {
          console.error("Python exec error:", stderr || error.message);
          return reject(new Error(stderr || error.message));
        }
        try {
          const parsed = JSON.parse(stdout.trim());
          resolve(parsed);
        } catch (parseErr) {
          reject(new Error(`JSON parse error: ${stdout.slice(0, 200)}...`));
        }
      });
    });
  };

  // API 2: Market data & checklist
  app.get("/api/market-data", async (_req, res) => {
    try {
      const data = await runPythonApi(["market_data"]);
      res.json(data);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // API 3: Run Causal Backtest
  app.post("/api/run-backtest", async (req, res) => {
    try {
      const payloadStr = JSON.stringify(req.body || {});
      const result = await runPythonApi(["run_backtest", payloadStr]);
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // API 4: Run Unit Tests
  app.get("/api/run-unit-tests", async (_req, res) => {
    try {
      const result = await runPythonApi(["run_tests"]);
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // API 5: Dispatch simulated / demo order
  app.post("/api/dispatch-order", (req, res) => {
    const { direction, volume, price, sl, tp, isDemo } = req.body;
    if (isDemo === false) {
      return res.status(403).json({
        success: false,
        message: "SAFETY REFUSAL: Live accounts strictly blocked. Demo accounts only."
      });
    }
    const ticket = Math.floor(1000000 + Math.random() * 9000000);
    res.json({
      success: true,
      ticket,
      direction,
      volume: volume || 0.1,
      price: price || 2380.50,
      sl,
      tp,
      magic: 9212001,
      message: `Demo ${direction} order #${ticket} executed successfully on XAUUSD.`
    });
  });

  // Vite middleware in development
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (_req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`XAU/USD Triple Filter Bot Server listening on http://0.0.0.0:${PORT}`);
  });
}

startServer().catch((err) => {
  console.error("Failed to start server:", err);
});
